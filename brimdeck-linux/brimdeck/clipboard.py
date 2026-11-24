"""Clipboard history and the file shelf ("file transfer")."""
import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import time
import urllib.parse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .config import DATA_DIR  # noqa: E402

CLIP_FILE = os.path.join(DATA_DIR, "clipboard.json")
SHELF_FILE = os.path.join(DATA_DIR, "shelf.json")
IMG_DIR = os.path.join(DATA_DIR, "clip-images")

URL_RE = re.compile(r"^(https?|ftp)://\S+$", re.I)
COLOR_RE = re.compile(r"^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%]+\)|hsla?\([\d\s.,%deg]+\))$", re.I)
CODE_HINT = re.compile(r"[{};]\s*$|^\s*(def|class|import|from|function|const|let|var|if|for|while|return|#include|public|private|SELECT|fn|pub)\b", re.M)


def classify(text):
    t = text.strip()
    if URL_RE.match(t):
        return "link"
    if COLOR_RE.match(t):
        return "color"
    if "\n" in t and (len(CODE_HINT.findall(t)) >= 2 or re.search(r"^( {2,}|\t)\S", t, re.M)):
        return "code"
    return "text"


def parse_color(t):
    t = t.strip()
    rgba = Gdk.RGBA()
    if rgba.parse(t):
        return (rgba.red, rgba.green, rgba.blue, rgba.alpha)
    return None


class Clip:
    __slots__ = ("id", "kind", "text", "image", "time", "pinned", "thumb")

    def __init__(self, kind, text="", image=None, t=None, pinned=False, cid=None):
        self.kind = kind
        self.text = text
        self.image = image  # path of saved PNG
        self.time = t or time.time()
        self.pinned = pinned
        self.thumb = None
        self.id = cid or hashlib.sha1((kind + (text or "") + (image or "")).encode()).hexdigest()[:16]

    def to_json(self):
        return {"id": self.id, "kind": self.kind, "text": self.text, "image": self.image, "time": self.time, "pinned": self.pinned}


class ShelfFile:
    __slots__ = ("path", "added", "size", "is_dir")

    def __init__(self, path, added=None):
        self.path = path
        self.added = added or time.time()
        try:
            st = os.stat(path)
            self.is_dir = os.path.isdir(path)
            self.size = 0 if self.is_dir else st.st_size
        except OSError:
            self.is_dir = False
            self.size = None

    @property
    def name(self):
        return os.path.basename(self.path.rstrip("/")) or self.path

    @property
    def exists(self):
        return os.path.exists(self.path)

    @property
    def uri(self):
        return Gio.File.new_for_path(self.path).get_uri()


# ---------------------------------------------------------------- key injection (XTest, no extra packages)

class _XTest:
    def __init__(self):
        self.ok = False
        try:
            self.x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
            self.xtst = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst") or "libXtst.so.6")
            self.x11.XOpenDisplay.restype = ctypes.c_void_p
            self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
            self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
            self.x11.XStringToKeysym.restype = ctypes.c_ulong
            self.x11.XFlush.argtypes = [ctypes.c_void_p]
            self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            self.dpy = self.x11.XOpenDisplay(None)
            self.ok = bool(self.dpy)
        except OSError:
            self.ok = False

    def combo(self, *keys):
        if not self.ok:
            return False
        codes = [self.x11.XKeysymToKeycode(self.dpy, self.x11.XStringToKeysym(k.encode())) for k in keys]
        for c in codes:
            self.xtst.XTestFakeKeyEvent(self.dpy, c, True, 0)
        for c in reversed(codes):
            self.xtst.XTestFakeKeyEvent(self.dpy, c, False, 0)
        self.x11.XFlush(self.dpy)
        return True


_xtest = None

TERMINALS = ("gnome-terminal", "terminal", "kitty", "alacritty", "wezterm", "tilix", "konsole", "xterm",
             "terminator", "foot", "ghostty", "warp", "tabby", "urxvt", "st-256color", "kgx", "ptyxis", "blackbox")


def active_window_class():
    try:
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck
        scr = Wnck.Screen.get_default()
        scr.force_update()
        w = scr.get_active_window()
        if w:
            return ((w.get_class_group_name() or "") + " " + (w.get_class_instance_name() or "")).lower()
    except Exception:
        pass
    return ""


def send_paste():
    global _xtest
    if _xtest is None:
        _xtest = _XTest()
    cls = active_window_class()
    if any(t in cls for t in TERMINALS):
        return _xtest.combo("Control_L", "Shift_L", "v")
    return _xtest.combo("Control_L", "v")


# ---------------------------------------------------------------- service

class ClipboardService:
    def __init__(self, config, on_change):
        self.config = config
        self.on_change = on_change
        self.items = []
        self.files = []
        self._ignore_until = 0
        self._ignore_text = None
        self._owner_widget = None
        self._file_targets = []
        self.cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        os.makedirs(IMG_DIR, exist_ok=True)
        self._load()

    def start(self):
        self.cb.connect("owner-change", self._owner_change)
        # Whatever is on the clipboard at start-up goes in too.
        GLib.timeout_add(500, lambda: (self._owner_change(self.cb, None), False)[1])

    # ---- persistence
    def _load(self):
        keep = self.config["clipboard_keep_days"]
        try:
            with open(CLIP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for d in data:
                c = Clip(d["kind"], d.get("text") or "", d.get("image"), d.get("time"), d.get("pinned", False), d.get("id"))
                if c.image and not os.path.exists(c.image):
                    continue
                if c.pinned or keep < 0 or (keep > 0 and now - c.time < keep * 86400):
                    self.items.append(c)
        except (OSError, ValueError, KeyError):
            pass
        try:
            with open(SHELF_FILE, "r", encoding="utf-8") as f:
                self.files = [ShelfFile(d["path"], d.get("added")) for d in json.load(f)]
        except (OSError, ValueError, KeyError):
            pass
        self._cleanup_images()

    def save(self, exiting=False):
        keep = self.config["clipboard_keep_days"]
        items = [c for c in self.items if c.pinned or keep != 0 or not exiting]
        if exiting and keep == 0:
            items = [c for c in self.items if c.pinned]
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(CLIP_FILE, "w", encoding="utf-8") as f:
                json.dump([c.to_json() for c in items], f, ensure_ascii=False)
            with open(SHELF_FILE, "w", encoding="utf-8") as f:
                json.dump([{"path": s.path, "added": s.added} for s in self.files], f, ensure_ascii=False)
        except OSError as e:
            print("brimdeck: saving clipboard failed:", e)
        if exiting:
            self.items = items
            self._cleanup_images()

    def _cleanup_images(self):
        used = {c.image for c in self.items if c.image}
        try:
            for n in os.listdir(IMG_DIR):
                p = os.path.join(IMG_DIR, n)
                if p not in used:
                    os.remove(p)
        except OSError:
            pass

    # ---- capture
    def _owner_change(self, cb, _event):
        if time.time() < self._ignore_until:
            return
        cb.request_targets(self._got_targets)

    def _got_targets(self, cb, atoms, *args):
        names = [a.name() for a in (atoms or [])]
        if any(n in ("x-special/gnome-copied-files", "text/uri-list") for n in names) and not any(
                n.startswith("text/plain") for n in names if n != "text/uri-list"):
            return  # file copies belong to the file manager
        if any(n.startswith("image/") for n in names) and not any(n in ("UTF8_STRING", "text/plain;charset=utf-8") for n in names):
            cb.request_image(self._got_image)
        else:
            cb.request_text(self._got_text)

    def _got_text(self, cb, text, *args):
        if not text or not text.strip() or len(text) > 200_000:
            return
        if text == self._ignore_text:
            self._ignore_text = None
            return
        self._add(Clip(classify(text), text))

    def _got_image(self, cb, pixbuf, *args):
        if pixbuf is None:
            return
        data = pixbuf.get_pixels()
        digest = hashlib.sha1(bytes(data[: 1 << 20]) + f"{pixbuf.get_width()}x{pixbuf.get_height()}".encode()).hexdigest()[:16]
        path = os.path.join(IMG_DIR, digest + ".png")
        if not os.path.exists(path):
            try:
                pixbuf.savev(path, "png", [], [])
            except GLib.Error:
                return
        c = Clip("image", f"{pixbuf.get_width()} × {pixbuf.get_height()}", path)
        self._add(c)

    def _add(self, clip):
        old = next((c for c in self.items if c.id == clip.id), None)
        if old:
            self.items.remove(old)
            clip.pinned = old.pinned
        self.items.insert(0, clip)
        limit = max(10, int(self.config["clipboard_max"]))
        unpinned = [c for c in self.items if not c.pinned]
        for c in unpinned[limit:]:
            self.items.remove(c)
        if self.config["clipboard_keep_days"] != 0:
            self.save()
        self.on_change()

    # ---- actions
    def thumb(self, clip, size):
        if clip.thumb is None and clip.image:
            try:
                clip.thumb = GdkPixbuf.Pixbuf.new_from_file_at_scale(clip.image, size, size, True)
            except GLib.Error:
                clip.thumb = False
        return clip.thumb or None

    def copy(self, clip):
        self._ignore_until = time.time() + 0.6
        if clip.kind == "image" and clip.image:
            try:
                self.cb.set_image(GdkPixbuf.Pixbuf.new_from_file(clip.image))
            except GLib.Error:
                return
        else:
            self._ignore_text = clip.text
            self.cb.set_text(clip.text, -1)
        self.cb.store()
        # Move it to the top like a fresh copy.
        if clip in self.items:
            self.items.remove(clip)
            self.items.insert(0, clip)
            clip.time = time.time()
        self.on_change()

    def toggle_pin(self, clip):
        clip.pinned = not clip.pinned
        self.save()
        self.on_change()

    def remove(self, clip):
        if clip in self.items:
            self.items.remove(clip)
            self.save()
            self.on_change()

    def clear(self):
        self.items = [c for c in self.items if c.pinned]
        self.save()
        self.on_change()

    # ---- shelf
    def add_files(self, paths):
        have = {f.path for f in self.files}
        for p in paths:
            if p and p not in have and os.path.exists(p):
                self.files.insert(0, ShelfFile(p))
                have.add(p)
        self.save()
        self.on_change()

    def remove_file(self, sf):
        if sf in self.files:
            self.files.remove(sf)
            self.save()
            self.on_change()

    def clear_files(self):
        self.files = []
        self.save()
        self.on_change()

    def copy_files(self, files, widget):
        """Puts files on the clipboard the way file managers do, so Ctrl+V pastes them in Nautilus, chats, editors."""
        files = [f for f in files if f.exists]
        if not files:
            return False
        self._file_targets = files
        self._ignore_until = time.time() + 0.6
        clip_atom = Gdk.Atom.intern("CLIPBOARD", False)
        if self._owner_widget is not widget:
            self._owner_widget = widget
            for i, name in enumerate(("x-special/gnome-copied-files", "text/uri-list", "UTF8_STRING", "text/plain")):
                widget.selection_add_target(clip_atom, Gdk.Atom.intern(name, False), i)
            widget.connect("selection-get", self._selection_get)
        Gtk.selection_owner_set(widget, clip_atom, Gtk.get_current_event_time())
        return True

    def _selection_get(self, widget, sel, info, time_):
        files = self._file_targets
        uris = [f.uri for f in files]
        target = sel.get_target().name()
        if target == "x-special/gnome-copied-files":
            data = ("copy\n" + "\n".join(uris)).encode()
            sel.set(sel.get_target(), 8, data)
        elif target == "text/uri-list":
            sel.set_uris(uris)
        else:
            sel.set_text("\n".join(f.path for f in files), -1)


def uris_to_paths(uris):
    out = []
    for u in uris:
        u = u.strip()
        if not u or u.startswith("#"):
            continue
        if u.startswith("file://"):
            out.append(urllib.parse.unquote(urllib.parse.urlparse(u).path))
        elif u.startswith("/"):
            out.append(u)
    return out

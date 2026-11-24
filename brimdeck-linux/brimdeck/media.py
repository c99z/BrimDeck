"""Media players over MPRIS (D-Bus), the Linux counterpart of Windows media controls."""
import hashlib
import os
import threading
import time
import urllib.parse
import urllib.request

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from .config import CACHE_DIR  # noqa: E402

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
IFACE = "org.mpris.MediaPlayer2.Player"


def _unpack(v):
    return v.unpack() if isinstance(v, GLib.Variant) else v


class Player:
    def __init__(self, service, bus_name, proxy, identity):
        self.service = service
        self.bus_name = bus_name
        self.proxy = proxy
        self.identity = identity or bus_name[len(PREFIX):].split(".")[0].title()
        self.metadata = {}
        self.status = "Stopped"
        self.position = 0.0  # seconds, at position_time
        self.position_time = time.monotonic()
        self.rate = 1.0
        self.can = {}
        self.shuffle = None
        self.loop = None
        self.volume = None
        self.last_active = 0.0
        self.art = None  # GdkPixbuf
        self.art_url = None
        self._load()

    def _prop(self, name):
        v = self.proxy.get_cached_property(name)
        return v.unpack() if v is not None else None

    def _load(self):
        self.metadata = self._prop("Metadata") or {}
        self.status = self._prop("PlaybackStatus") or "Stopped"
        self.rate = self._prop("Rate") or 1.0
        self.shuffle = self._prop("Shuffle")
        self.loop = self._prop("LoopStatus")
        self.volume = self._prop("Volume")
        for k in ("CanGoNext", "CanGoPrevious", "CanPlay", "CanPause", "CanSeek", "CanControl"):
            v = self._prop(k)
            self.can[k] = True if v is None else bool(v)
        if self.status == "Playing":
            self.last_active = time.time()
        self.poll_position()

    def on_changed(self, changed):
        ch = changed.unpack() if isinstance(changed, GLib.Variant) else changed
        if "Metadata" in ch:
            self.metadata = ch["Metadata"] or {}
            self.poll_position()
        if "PlaybackStatus" in ch:
            self.status = ch["PlaybackStatus"]
            self.poll_position()
            if self.status == "Playing":
                self.last_active = time.time()
        for k in ("CanGoNext", "CanGoPrevious", "CanPlay", "CanPause", "CanSeek"):
            if k in ch:
                self.can[k] = bool(ch[k])
        if "Rate" in ch:
            self.rate = ch["Rate"] or 1.0
        if "Shuffle" in ch:
            self.shuffle = ch["Shuffle"]
        if "LoopStatus" in ch:
            self.loop = ch["LoopStatus"]
        if "Volume" in ch:
            self.volume = ch["Volume"]
        if "Position" in ch:
            self.position = ch["Position"] / 1e6
            self.position_time = time.monotonic()

    def poll_position(self):
        """Position is not announced by signals; ask for it."""
        def done(conn, res):
            try:
                v = conn.call_finish(res).unpack()[0]
                self.position = float(v) / 1e6
                self.position_time = time.monotonic()
            except Exception:
                pass
        try:
            self.proxy.get_connection().call(
                self.bus_name, PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (IFACE, "Position")), GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NONE, 1000, None, done)
        except Exception:
            pass

    # ---- metadata
    @property
    def title(self):
        return self.metadata.get("xesam:title") or ""

    @property
    def artist(self):
        a = self.metadata.get("xesam:artist") or []
        return ", ".join(a) if isinstance(a, (list, tuple)) else str(a)

    @property
    def album(self):
        return self.metadata.get("xesam:album") or ""

    @property
    def length(self):
        v = self.metadata.get("mpris:length")
        return (v or 0) / 1e6

    @property
    def track_id(self):
        return self.metadata.get("mpris:trackid") or "/org/mpris/MediaPlayer2/TrackList/NoTrack"

    @property
    def playing(self):
        return self.status == "Playing"

    def current_position(self):
        pos = self.position
        if self.playing:
            pos += (time.monotonic() - self.position_time) * (self.rate or 1.0)
        if self.length:
            pos = min(pos, self.length)
        return max(0.0, pos)

    # ---- controls
    def _call(self, method, args=None):
        try:
            self.proxy.call(method, args, Gio.DBusCallFlags.NONE, 2000, None, None, None)
        except Exception as e:
            print("brimdeck: mpris", method, e)

    def play_pause(self):
        self._call("PlayPause")

    def next(self):
        self._call("Next")

    def previous(self):
        self._call("Previous")

    def seek_to(self, seconds):
        tid = self.track_id
        self._call("SetPosition", GLib.Variant("(ox)", (tid if tid.startswith("/") else "/org/mpris/MediaPlayer2/TrackList/NoTrack",
                                                         int(seconds * 1e6))))
        self.position, self.position_time = seconds, time.monotonic()

    def _set(self, name, variant):
        try:
            self.proxy.get_connection().call(
                self.bus_name, PATH, "org.freedesktop.DBus.Properties", "Set",
                GLib.Variant("(ssv)", (IFACE, name, variant)), None, Gio.DBusCallFlags.NONE, 1000, None, None)
        except Exception as e:
            print("brimdeck: mpris set", name, e)

    def toggle_shuffle(self):
        if self.shuffle is not None:
            self._set("Shuffle", GLib.Variant("b", not self.shuffle))

    def cycle_loop(self):
        if self.loop is not None:
            nxt = {"None": "Playlist", "Playlist": "Track", "Track": "None"}.get(self.loop, "None")
            self._set("LoopStatus", GLib.Variant("s", nxt))

    def set_volume(self, v):
        if self.volume is not None:
            self._set("Volume", GLib.Variant("d", max(0.0, min(1.0, v))))


class MediaService:
    """Tracks MPRIS players; calls on_change() on the main loop whenever anything visible changes."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.players = {}
        self.pinned = None
        self.selected = None
        self.bus = None
        self._art_cache = {}
        self._art_loading = set()
        os.makedirs(os.path.join(CACHE_DIR, "art"), exist_ok=True)

    def start(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            print("brimdeck: no session bus:", e)
            return
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self._owner_changed)
        try:
            names = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                       "ListNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except Exception:
            names = []
        for n in names:
            if n.startswith(PREFIX):
                self._add(n)
        GLib.timeout_add_seconds(2, self._tick)

    def _owner_changed(self, conn, sender, path, iface, signal, params):
        name, old, new = params.unpack()
        if not name.startswith(PREFIX):
            return
        if old and name in self.players:
            del self.players[name]
            if self.selected == name:
                self.selected = None
            self.on_change()
        if new:
            GLib.timeout_add(300, lambda: (self._add(name), False)[1])

    def _add(self, name):
        if name in self.players:
            return
        def ready(_src, res):
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(res)
            except Exception as e:
                print("brimdeck: mpris proxy", name, e)
                return
            identity = None
            try:
                v = self.bus.call_sync(name, PATH, "org.freedesktop.DBus.Properties", "Get",
                                       GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "Identity")),
                                       GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 1000, None)
                identity = v.unpack()[0]
            except Exception:
                pass
            p = Player(self, name, proxy, identity)
            self.players[name] = p
            proxy.connect("g-properties-changed", lambda _p, changed, _inv: self._changed(p, changed))
            self._ensure_art(p)
            self.on_change()
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES, None,
                                  name, PATH, IFACE, None, ready)

    def _changed(self, p, changed):
        p.on_changed(changed)
        self._ensure_art(p)
        self.on_change()

    def _tick(self):
        for p in self.players.values():
            if p.playing:
                p.poll_position()
        return True

    # ---- selection
    def current(self):
        if not self.players:
            return None
        for key in (self.pinned, self.selected):
            if key and key in self.players:
                return self.players[key]
        playing = [p for p in self.players.values() if p.playing]
        if playing:
            return max(playing, key=lambda p: p.last_active)
        with_title = [p for p in self.players.values() if p.title]
        pool = with_title or list(self.players.values())
        return max(pool, key=lambda p: p.last_active)

    def cycle(self, step=1):
        names = sorted(self.players)
        if len(names) < 2:
            return
        cur = self.current()
        i = names.index(cur.bus_name) if cur else 0
        self.selected = names[(i + step) % len(names)]
        self.pinned = None
        self.on_change()

    # ---- cover art
    def _ensure_art(self, p):
        url = p.metadata.get("mpris:artUrl") or ""
        if url == p.art_url:
            return
        p.art_url = url
        p.art = self._art_cache.get(url)
        if p.art is not None or not url or url in self._art_loading:
            return
        self._art_loading.add(url)

        def work():
            pb = None
            try:
                if url.startswith("file://"):
                    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 320, 320, True)
                elif url.startswith(("http://", "https://")):
                    cache = os.path.join(CACHE_DIR, "art", hashlib.sha1(url.encode()).hexdigest())
                    if not os.path.exists(cache):
                        req = urllib.request.Request(url, headers={"User-Agent": "brimdeck-linux"})
                        with urllib.request.urlopen(req, timeout=15) as r:
                            data = r.read(8 << 20)
                        with open(cache, "wb") as f:
                            f.write(data)
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(cache, 320, 320, True)
            except Exception as e:
                print("brimdeck: cover art", e)

            def done():
                self._art_loading.discard(url)
                if pb is not None:
                    self._art_cache[url] = pb
                    if len(self._art_cache) > 40:
                        self._art_cache.pop(next(iter(self._art_cache)))
                for pl in self.players.values():
                    if pl.art_url == url:
                        pl.art = pb
                self.on_change()
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

"""The island window: a transparent, always-on-top dock window at the top center of a monitor.

The window is as large as the expanded panel, and everything is drawn with Cairo. Its X11 input
shape follows what is drawn, so clicks outside the notch go straight through to the windows below.
"""
import math
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import cairo  # noqa: E402

from . import draw as D  # noqa: E402
from . import pages  # noqa: E402
from .clipboard import send_paste, uris_to_paths  # noqa: E402
from .draw import T  # noqa: E402

NOTCH_H = 32
EXP_W, EXP_H = 820, 420
MARGIN = 30           # room for the shadow around the expanded panel
SHOULDER = 9
HEADER_H = 50
PAD = 18


class Hot:
    __slots__ = ("x", "y", "w", "h", "key", "cb", "tip", "right", "drag")

    def __init__(self, x, y, w, h, key, cb, tip, right, drag):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.key, self.cb, self.tip, self.right, self.drag = key, cb, tip, right, drag

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


class Island(Gtk.Window):
    def __init__(self, app, config, usage, media, sysmon, clip, devices):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.config = config
        self.usage = usage
        self.media = media
        self.sysmon = sysmon
        self.clip = clip
        self.devices = devices
        self.usage_states = dict(usage.states) if usage else {}
        self.sys_snapshot = None

        self.page = config["start_page"] if config["start_page"] in config["pages"] else config["pages"][0]
        self.t = 0.0           # 0 collapsed .. 1 expanded (animated)
        self.target = 0.0
        self._anim_from = 0.0
        self._anim_start = 0.0
        self._anim_dur = 0.3
        self._tick_id = None
        self.cw = pages.collapsed_width(self)  # animated collapsed width
        self._cw_target = self.cw
        self.eh = pages.page_height(self, self.page)  # animated expanded height
        self._eh_target = self.eh
        self.hover = None
        self.hots = []
        self.scrolls = []
        self.usage_view = "chatapi"
        self.balance_scroll = 0.0
        self.clip_scroll = 0.0
        self.shelf_scroll = 0.0
        self.proc_sort = "cpu"
        self.toast = None
        self.inside = False
        self.dnd_over = False
        self.dragging = False
        self.menu_open = False
        self.sticky_open = False
        self.last_pointer = (0, 0)
        self._press = None
        self._hover_timer = None
        self._collapse_timer = None
        self._hover_since = 0
        self._eq_energy = 0.0
        self._eq_last = time.monotonic()
        self._accent_cache = {}
        self._input_key = None
        self._fullscreen_hidden = False
        self.panel_host = False
        self.panel_hover = False
        self._redraw_soon_id = None

        self.set_title("BrimDeck")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.stick()
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        else:
            print("brimdeck: no compositor, transparency unavailable")

        self.area = Gtk.DrawingArea()
        self.area.set_can_focus(False)
        self.area.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                             Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                             Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.SCROLL_MASK |
                             Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.add(self.area)
        self.area.connect("draw", self._on_draw)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("enter-notify-event", self._on_enter)
        self.area.connect("leave-notify-event", self._on_leave)
        self.area.connect("scroll-event", self._on_scroll)
        # files dropped onto the notch or the panel go to the file shelf
        self.area.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT, [], Gdk.DragAction.COPY | Gdk.DragAction.LINK)
        self.area.drag_dest_add_uri_targets()
        self.area.connect("drag-motion", self._on_drag_motion)
        self.area.connect("drag-leave", self._on_drag_leave)
        self.area.connect("drag-drop", self._on_drag_drop)
        self.area.connect("drag-data-received", self._on_drag_received)
        self.area.connect("drag-data-get", self._on_drag_get)
        self.area.connect("drag-end", self._on_drag_end)
        self._drag_files = []

        screen.connect("monitors-changed", lambda *_: GLib.timeout_add(500, self._relayout_once))
        screen.connect("size-changed", lambda *_: GLib.timeout_add(500, self._relayout_once))
        self.connect("realize", lambda *_: self._update_input())
        self.connect("map-event", lambda *_: (setattr(self, "_input_key", None), self._update_input(), False)[2])
        self.connect("key-press-event", self._on_key)
        self.connect("focus-out-event", self._on_focus_out)
        self._sticky_at = 0
        self._prev_active = None
        self._wnck = None
        self._setup_fullscreen_watch()
        self.relayout()
        GLib.timeout_add(1000, self._second_tick)

    def set_panel_host(self, available):
        self.panel_host = available
        if not self._in_panel():
            self.panel_hover = False
        self._sync_visibility()
        if self.panel_hover:
            self._pointer_changed()

    def set_panel_hover(self, hovered):
        self.panel_hover = hovered and self._in_panel()
        self._pointer_changed()

    def _in_panel(self):
        return self.panel_host and self.config["compact_location"] == "panel"

    def _sync_visibility(self):
        if self._fullscreen_hidden or (self._in_panel() and self.target == 0):
            self._cancel_timers()
            self.inside = False
            self.hide()
        else:
            self.show_all()


    # ================================================================== geometry
    def monitor(self):
        disp = Gdk.Display.get_default()
        want = self.config["monitor"]
        if want and want != "primary":
            for i in range(disp.get_n_monitors()):
                m = disp.get_monitor(i)
                if m.get_model() == want or str(i) == str(want):
                    return m
        return disp.get_primary_monitor() or disp.get_monitor(0)

    def compute_scale(self, mon):
        s = float(self.config["scale"] or 0)
        if s > 0:
            return max(0.75, min(3.0, s))
        dpi = (Gtk.Settings.get_default().get_property("gtk-xft-dpi") or 96 * 1024) / 1024
        geo = mon.get_geometry()
        k = dpi / 96
        if geo.height >= 2000:
            k *= 1.2
        return round(max(1.0, min(2.5, k)) * 4) / 4

    def relayout(self):
        mon = self.monitor()
        self.S = self.compute_scale(mon)
        geo = mon.get_geometry()
        wa = mon.get_workarea()
        top = wa.y if geo.y <= wa.y < geo.y + 120 else geo.y
        top += int(self.config["offset_y"] or 0)
        self.win_w = int(math.ceil((EXP_W + 2 * MARGIN) * self.S))
        self.win_h = int(math.ceil((EXP_H + MARGIN) * self.S))
        self.set_size_request(self.win_w, self.win_h)
        self.resize(self.win_w, self.win_h)
        self.move(geo.x + (geo.width - self.win_w) // 2, top)
        self._input_key = None
        self._update_input()
        self.area.queue_draw()

    def _relayout_once(self):
        self.relayout()
        return False

    def panel_rect(self, t=None):
        """Panel geometry (logical units) for progress t: x, w, h, bottom radius."""
        e = max(0.0, self.t if t is None else t)
        w = D.lerp(self.cw, EXP_W, e)
        h = D.lerp(NOTCH_H, self.eh, e)
        r = D.lerp(12, 30, min(1, e))
        x = (EXP_W + 2 * MARGIN - w) / 2
        return x, w, h, r

    def _update_input(self):
        gw = self.get_window()
        if gw is None:
            return
        if self.target >= 0.5 or self.t > 0.5:
            x, w, h, _ = self.panel_rect(1.0)
            h = max(h, self._eh_target)
            rect = (x, 0, w, h)
        else:
            x, w, h, _ = self.panel_rect(0.0)
            rect = (x - SHOULDER, 0, w + 2 * SHOULDER, h + 2)
        S = self.S
        key = tuple(int(round(v * S)) for v in rect)
        if key == self._input_key:
            return
        self._input_key = key
        region = cairo.Region(cairo.RectangleInt(*key))
        # The widget-level call is kept by GTK and re-applied on (re)map; a bare GdkWindow shape gets reset.
        self.input_shape_combine_region(region)

    # ================================================================== state
    def expand(self, sticky=False):
        if self._fullscreen_hidden:
            return
        self.show_all()
        self.sticky_open = sticky
        self._cancel_timers()
        if self.target < 0.5:
            self._animate_to(1.0)
        self.sysmon.detailed = self.page == "monitor"
        if self.sysmon.detailed:
            self.sysmon.wake()
        if sticky:
            # Opened by a shortcut: take the keyboard so Esc closes it and clicking elsewhere does too.
            # The window that had the keyboard gets it back when the panel closes.
            self._sticky_at = time.monotonic()
            if self._wnck is not None and not self.is_active():
                prev = self._wnck.get_active_window()
                self._prev_active = prev if prev is not None and prev.get_name() != "BrimDeck" else None
            self.set_accept_focus(True)
            self.present_with_time(Gtk.get_current_event_time() or GLib.get_monotonic_time() // 1000)
            self.get_window() and self.get_window().focus(Gtk.get_current_event_time())

    def collapse(self):
        self._cancel_timers()
        if self.sticky_open or self.get_accept_focus():
            had_focus = self.is_active()
            self.set_accept_focus(False)
            prev, self._prev_active = self._prev_active, None
            if had_focus and prev is not None:
                try:
                    prev.activate(Gtk.get_current_event_time() or self._x_time())
                except Exception:
                    pass
        self.sticky_open = False
        self.hover = None
        self.sysmon.detailed = False
        if not self.config["remember_page"]:
            self.page = self.config["start_page"] if self.config["start_page"] in self.config["pages"] else self.config["pages"][0]
        if self.target > 0.5:
            self._animate_to(0.0)
            if self._in_panel():
                self.t = 0.0
                self._sync_visibility()

    def toggle(self, page=None):
        if page and page in self.config["pages"]:
            if self.target > 0.5 and self.page == page:
                self.collapse()
                return
            self.set_page(page)
            self.expand(sticky=True)
            return
        if self.target > 0.5:
            self.collapse()
        else:
            self.expand(sticky=True)

    def _animate_to(self, target):
        self._anim_from = self.t
        self.target = target
        self._anim_start = time.monotonic()
        animate = self.config["animations"] and Gtk.Settings.get_default().get_property("gtk-enable-animations")
        self._anim_dur = (0.42 if target > 0.5 else 0.28) if animate else 0.0
        self._update_input()
        self._ensure_tick()

    def set_page(self, page):
        if page != self.page:
            self.page = page
            self.hover = None
            self.sysmon.detailed = page == "monitor" and self.target > 0.5
            self._eh_target = pages.page_height(self, page)
            if self.target < 0.5:
                self.eh = self._eh_target
            self._input_key = None
            self._update_input()
            self.redraw()
            self._ensure_tick()

    def _cancel_timers(self):
        for name in ("_hover_timer", "_collapse_timer"):
            tid = getattr(self, name)
            if tid:
                GLib.source_remove(tid)
                setattr(self, name, None)

    # ================================================================== animation
    def _needs_frames(self):
        if self.t != self.target:
            return True
        p = self.media.current() if self.media else None
        playing = bool(p and p.playing)
        if self.cw != self._cw_target or self.eh != self._eh_target:
            return True
        if (playing or self._eq_energy > 0.01) and (self.target < 0.5 or self.page == "music"):
            return True
        return False

    def _ensure_tick(self):
        if self._tick_id is None and self._needs_frames():
            self._last_frame = 0
            self._tick_id = self.area.add_tick_callback(self._on_tick)

    def _on_tick(self, widget, clock):
        now = time.monotonic()
        if self.t != self.target:
            k = 1.0 if self._anim_dur <= 0 else min(1.0, (now - self._anim_start) / self._anim_dur)
            eased = D.ease_out_back(k, 0.8) if self.target > 0.5 else D.ease_out_cubic(k)
            self.t = D.lerp(self._anim_from, self.target, eased)
            if k >= 1.0:
                self.t = self.target
                self._update_input()
            self.redraw()
        elif self.cw != self._cw_target or self.eh != self._eh_target:
            d = self._cw_target - self.cw
            self.cw = self._cw_target if abs(d) < 0.5 else self.cw + d * 0.2
            d = self._eh_target - self.eh
            self.eh = self._eh_target if abs(d) < 0.5 else self.eh + d * 0.25
            self._update_input()
            self.redraw()
        else:
            # equalizer only: ~30 fps is plenty, and only redraw what moves
            if now - self._last_frame >= 1 / 30:
                self._last_frame = now
                if self.target < 0.5:
                    x, w, h, _ = self.panel_rect()
                    S = self.S
                    self.area.queue_draw_area(int((x - SHOULDER) * S), 0, int((w + 2 * SHOULDER) * S) + 2, int(h * S) + 2)
                else:
                    self.redraw()
        if not self._needs_frames():
            self._tick_id = None
            self.redraw()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _second_tick(self):
        self._cw_target = pages.collapsed_width(self)
        self._eh_target = pages.page_height(self, self.page)
        if self.target < 0.5:
            self.eh = self._eh_target
        if self.toast and time.time() > self.toast[1]:
            self.toast = None
        self._ensure_tick()
        if self._tick_id is None:
            self.redraw()
        if self.target > 0.5 and not self.sticky_open and not self.dragging and not self.menu_open:
            self._check_pointer_outside()
        elif self.sticky_open and not self.inside and not self.is_active() and not self.dnd_over \
                and time.monotonic() - self._sticky_at > 10:
            self.collapse()
        return True

    def _check_pointer_outside(self):
        if self.panel_hover:
            return
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            _, px, py = seat.get_pointer().get_position()
        except Exception:
            return
        ox, oy = self.get_position()
        x, w, h, _ = self.panel_rect(1.0)
        h = max(h, self._eh_target)
        lx, ly = (px - ox) / self.S, (py - oy) / self.S
        if not (x - 4 <= lx <= x + w + 4 and -4 <= ly <= h + 4):
            if not self._collapse_timer:
                self._collapse_timer = GLib.timeout_add(int(self.config["collapse_delay_ms"]), self._collapse_cb)

    def _x_time(self):
        try:
            gi.require_version("GdkX11", "3.0")
            from gi.repository import GdkX11
            return GdkX11.x11_get_server_time(self.get_window())
        except Exception:
            return 0

    def redraw(self):
        self.area.queue_draw()

    def redraw_soon(self):
        if self._redraw_soon_id is None:
            def go():
                self._redraw_soon_id = None
                self.redraw()
                self._ensure_tick()
                return False
            self._redraw_soon_id = GLib.timeout_add(250, go)

    # ================================================================== data callbacks (main loop)
    def on_usage(self, states):
        self.usage_states = dict(states)
        self.app.update_panel_status()
        self._cw_target = pages.collapsed_width(self)
        self._ensure_tick()
        self.redraw()

    def on_sys(self, snap):
        self.sys_snapshot = snap
        self.app.update_panel_status()
        if self.target > 0.5 and self.page == "monitor":
            self.redraw()

    def on_media(self):
        self._cw_target = pages.collapsed_width(self)
        self._ensure_tick()
        self.redraw()

    def on_simple_change(self):
        self.redraw()

    # ================================================================== helpers for pages
    def enabled_providers(self):
        prov = self.config["providers"]
        return [p for p in ("claude", "codex") if prov.get(p, True)]

    def set_usage_view(self, view):
        self.usage_view = view
        self.hover = None
        self.redraw()

    def hot(self, x, y, w, h, key, cb, tip=None, right=None, drag=None):
        self.hots.append(Hot(x, y, w, h, key, cb, tip, right, drag))

    def scroll_area(self, x, y, w, h, name, max_scroll):
        self.scrolls.append((x, y, w, h, name, max_scroll))

    def show_toast(self, msg, secs=1.6):
        self.toast = (msg, time.time() + secs)
        self.redraw()

    def accent_for(self, p):
        if not p or not p.art:
            return T.accent
        key = id(p.art)
        c = self._accent_cache.get(key)
        if c is None:
            try:
                small = p.art.scale_simple(8, 8, 2)
                px = small.get_pixels()
                n = small.get_n_channels()
                rs = small.get_rowstride()
                best, best_score = None, -1
                for yy in range(8):
                    for xx in range(8):
                        o = yy * rs + xx * n
                        r, g, b = px[o] / 255, px[o + 1] / 255, px[o + 2] / 255
                        mx, mn = max(r, g, b), min(r, g, b)
                        score = (mx - mn) * (0.3 + mx)
                        if score > best_score:
                            best, best_score = (r, g, b), score
                r, g, b = best
                mx = max(r, g, b, 1e-6)
                k = 0.85 / mx if mx < 0.85 else 1.0
                c = (min(1, r * k), min(1, g * k), min(1, b * k), 1.0)
                if best_score < 0.08:
                    c = T.accent
            except Exception:
                c = T.accent
            self._accent_cache[key] = c
            if len(self._accent_cache) > 50:
                self._accent_cache.pop(next(iter(self._accent_cache)))
        return c

    def eq_levels(self, n, p):
        now = time.monotonic()
        dt = min(0.1, now - self._eq_last)
        self._eq_last = now
        goal = 1.0 if (p and p.playing) else 0.0
        self._eq_energy += (goal - self._eq_energy) * min(1, dt * 6)
        e = self._eq_energy
        t = time.time()
        out = []
        for i in range(n):
            f = i / max(1, n - 1)
            v = (0.55 + 0.45 * math.sin(t * (5.1 + f * 3.7) + i * 1.7)) * (0.6 + 0.4 * math.sin(t * 2.3 + i * 0.9))
            v *= 0.9 - 0.35 * f
            v += 0.12 * math.sin(t * 11 + i * 2.9)
            out.append(max(0.04, min(1.0, v * e + 0.04)))
        return out

    def lyric_or_title(self, p):
        if p.artist:
            return f"{p.title} — {p.artist}"
        return p.title or p.identity

    # ================================================================== actions
    def refresh_all(self):
        if self.usage:
            self.usage.refresh()
        if self.devices:
            self.devices.refresh()
        self.show_toast("正在刷新…")

    def open_settings(self):
        self.app.open_settings()

    def paste_clip(self, c):
        self.clip.copy(c)
        if self.config["paste_on_click"]:
            self.collapse()
            GLib.timeout_add(220, lambda: (send_paste(), False)[1])
        else:
            self.show_toast("已复制")

    def copy_clip(self, c):
        self.clip.copy(c)
        self.show_toast("已复制")

    def paste_files(self, files):
        if not self.clip.copy_files(files, self.area):
            self.show_toast("文件已不存在")
            return
        if self.config["paste_on_click"]:
            self.collapse()
            GLib.timeout_add(240, lambda: (send_paste(), False)[1])
        else:
            self.show_toast("已复制文件")

    # ================================================================== drawing
    def _on_draw(self, widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.scale(self.S, self.S)
        self.hots = []
        self.scrolls = []
        x, w, h, r = self.panel_rect()
        e = max(0.0, min(1.0, self.t))
        # shadow
        if e > 0.02:
            for i in range(10):
                spread = 2 + i * 2.4
                a = 0.05 * e * (1 - i / 10)
                D.rounded_rect(cr, x - spread * 0.6, -r, w + spread * 1.2, h + r + spread, r + spread)
                cr.set_source_rgba(0, 0, 0, a)
                cr.fill()
        # body
        D.notch_path(cr, x, w, h, r, SHOULDER)
        D.set_color(cr, T.bg)
        cr.fill_preserve()
        cr.save()
        cr.clip()
        # subtle inner border at the bottom edge
        if e > 0.05:
            D.rounded_rect(cr, x + 0.5, -r, w - 1, h + r - 0.5, r)
            cr.set_source_rgba(1, 1, 1, 0.07 * e)
            cr.set_line_width(1)
            cr.stroke()
        ca = max(0.0, 1 - e / 0.25)
        if ca > 0:
            pages.draw_collapsed(self, cr, x, w, h, ca)
        ea = max(0.0, min(1.0, (e - 0.3) / 0.55))
        if ea > 0:
            cr.push_group()
            ox = (EXP_W + 2 * MARGIN - EXP_W) / 2
            pages.draw_header(self, cr, ox + PAD, 12, EXP_W - 2 * PAD)
            fn = pages.PAGES.get(self.page)
            if fn:
                try:
                    fn(self, cr, ox + PAD, HEADER_H, EXP_W - 2 * PAD, self._eh_target - HEADER_H - PAD)
                except Exception:
                    import traceback
                    traceback.print_exc()
            cr.pop_group_to_source()
            cr.paint_with_alpha(ea)
            if self.target < 0.5:
                self.hots = []
        cr.restore()
        if ea >= 1 and self.hover:
            self._draw_tooltip(cr)
        return True

    def _draw_tooltip(self, cr):
        hs = next((h for h in self.hots if h.key == self.hover), None)
        if not hs or not hs.tip or time.monotonic() - self._hover_since < 0.6:
            if hs and hs.tip:
                GLib.timeout_add(650, lambda: (self.redraw(), False)[1])
            return
        tw, th = D.text_size(cr, hs.tip, 11)
        px, py = self.last_pointer
        bx = min(max(px - tw / 2 - 8, MARGIN + 4), EXP_W + MARGIN - tw - 20)
        by = py + 20
        if by + th + 10 > self.eh:
            by = py - th - 18
        D.fill_rounded(cr, bx, by, tw + 16, th + 10, 7, (0.17, 0.17, 0.19, 0.98))
        D.text(cr, hs.tip, bx + 8, by + 5, 11, T.fg)

    # ================================================================== input
    def _on_key(self, w, ev):
        key = Gdk.keyval_name(ev.keyval)
        if key == "Escape":
            self.collapse()
            return True
        pg = self.config["pages"]
        if key in ("Left", "Right", "Tab", "ISO_Left_Tab") and self.target > 0.5:
            step = -1 if key in ("Left", "ISO_Left_Tab") else 1
            i = pg.index(self.page) if self.page in pg else 0
            self.set_page(pg[(i + step) % len(pg)])
            return True
        if key and key.isdigit() and 1 <= int(key) <= len(pg):
            self.set_page(pg[int(key) - 1])
            return True
        return False

    def _on_focus_out(self, w, ev):
        if self.sticky_open and not self.menu_open and not self.dragging and time.monotonic() - self._sticky_at > 0.3:
            GLib.timeout_add(120, lambda: (self.collapse() if self.sticky_open and not self.inside else None, False)[1])
        return False

    def _logical(self, ev):
        return ev.x / self.S, ev.y / self.S

    def _hit(self, lx, ly):
        for h in reversed(self.hots):
            if h.contains(lx, ly):
                return h
        return None

    def _on_motion(self, w, ev):
        lx, ly = self._logical(ev)
        self.last_pointer = (lx, ly)
        if self._press and self._press.drag and ev.state & Gdk.ModifierType.BUTTON1_MASK:
            px, py = self._press_pos
            if abs(lx - px) + abs(ly - py) > 6:
                self._begin_drag(ev, [self._press.drag])
                self._press = None
                return True
        h = self._hit(lx, ly)
        key = h.key if h else None
        if key != self.hover:
            self.hover = key
            self._hover_since = time.monotonic()
            self.redraw()
        elif self.page == "music" and key == ("seek", 0):
            self.redraw()
        win = self.get_window()
        if win:
            cursor = Gdk.Cursor.new_from_name(win.get_display(), "pointer") if h and (h.cb or h.drag) else None
            win.set_cursor(cursor)
        return True

    def _on_press(self, w, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True
        lx, ly = self._logical(ev)
        self._press = self._hit(lx, ly) if self.target > 0.5 else None
        self._press_pos = (lx, ly)
        return True

    def _on_release(self, w, ev):
        lx, ly = self._logical(ev)
        if self.target < 0.5:
            if ev.button == 1:
                self.expand()
            elif ev.button == 3:
                self._context_menu(ev)
            return True
        h = self._hit(lx, ly)
        pressed, self._press = self._press, None
        if ev.button == 1 and h is not None and pressed is not None and h.key == pressed.key and h.cb:
            h.cb()
            self.redraw()
        elif ev.button == 3:
            if h is not None and h.right:
                h.right()
            else:
                self._context_menu(ev)
        elif ev.button == 2:
            self.collapse()
        return True

    def _on_enter(self, w, ev):
        self.inside = True
        self._pointer_changed()
        if self.sticky_open and not self.dnd_over:
            self.sticky_open = False
        return False

    def _hover_cb(self):
        self._hover_timer = None
        if (self.inside or self.panel_hover) and self.config["expand_on"] == "hover":
            self.expand()
        return False

    def _on_leave(self, w, ev):
        if ev.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.inside = False
        if self.hover is not None:
            self.hover = None
            self.redraw()
        self._pointer_changed()
        return False

    def _pointer_changed(self):
        if self.inside or self.panel_hover:
            if self._collapse_timer:
                GLib.source_remove(self._collapse_timer)
                self._collapse_timer = None
            if self.target < 0.5 and self.config["expand_on"] == "hover" and not self._hover_timer:
                self._hover_timer = GLib.timeout_add(int(self.config["hover_delay_ms"]), self._hover_cb)
            return
        if self._hover_timer:
            GLib.source_remove(self._hover_timer)
            self._hover_timer = None
        if self.target > 0.5 and not self.menu_open and not self.dragging and not self._collapse_timer:
            self._collapse_timer = GLib.timeout_add(int(self.config["collapse_delay_ms"]), self._collapse_cb)

    def _collapse_cb(self):
        self._collapse_timer = None
        if not (self.inside or self.panel_hover or self.menu_open or self.dragging or self.sticky_open):
            self.collapse()
        return False

    def _on_scroll(self, w, ev):
        if self.target < 0.5:
            return True
        lx, ly = self._logical(ev)
        dy = 0
        if ev.direction == Gdk.ScrollDirection.SMOOTH:
            _, _, dy = ev.get_scroll_deltas()
            dy *= 40
        elif ev.direction == Gdk.ScrollDirection.UP:
            dy = -46
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            dy = 46
        for x, y, sw, sh, name, mx in self.scrolls:
            if x <= lx < x + sw and y <= ly < y + sh:
                attr = name + "_scroll"
                setattr(self, attr, max(0, min(mx, getattr(self, attr) + dy)))
                self.redraw()
                return True
        # elsewhere on the panel: wheel switches pages
        if abs(dy) >= 20 and ly < HEADER_H:
            pg = self.config["pages"]
            i = pg.index(self.page) if self.page in pg else 0
            self.set_page(pg[(i + (1 if dy > 0 else -1)) % len(pg)])
        return True

    # ================================================================== drag & drop
    def _begin_drag(self, ev, files):
        files = [f for f in files if f.exists]
        if not files:
            return
        self._drag_files = files
        self.dragging = True
        targets = Gtk.TargetList.new([])
        targets.add_uri_targets(0)
        targets.add_text_targets(1)
        ctx = self.area.drag_begin_with_coordinates(targets, Gdk.DragAction.COPY | Gdk.DragAction.LINK, 1, ev, int(ev.x), int(ev.y))
        if ctx is not None:
            Gtk.drag_set_icon_name(ctx, "folder" if files[0].is_dir else "text-x-generic", 0, 0)

    def _on_drag_get(self, w, ctx, data, info, t):
        if info == 0:
            data.set_uris([f.uri for f in self._drag_files])
        else:
            data.set_text("\n".join(f.path for f in self._drag_files), -1)

    def _on_drag_end(self, w, ctx):
        self.dragging = False
        self._drag_files = []
        self.inside = False
        self._check_pointer_outside()

    def _on_drag_motion(self, w, ctx, x, y, t):
        if Gtk.drag_get_source_widget(ctx) is self.area:
            Gdk.drag_status(ctx, 0, t)
            return True
        if self.target < 0.5:
            self.set_page("clipboard") if "clipboard" in self.config["pages"] else None
            self.expand(sticky=True)
        if not self.dnd_over:
            self.dnd_over = True
            self.redraw()
        Gdk.drag_status(ctx, Gdk.DragAction.COPY, t)
        return True

    def _on_drag_leave(self, w, ctx, t):
        self.dnd_over = False
        self.redraw()

    def _on_drag_drop(self, w, ctx, x, y, t):
        if Gtk.drag_get_source_widget(ctx) is self.area:
            Gtk.drag_finish(ctx, False, False, t)
            return True
        target = self.area.drag_dest_find_target(ctx, None)
        if target is None:
            return False
        self.area.drag_get_data(ctx, target, t)
        return True

    def _on_drag_received(self, w, ctx, x, y, data, info, t):
        uris = data.get_uris() or []
        if not uris:
            txt = data.get_text() or ""
            uris = txt.splitlines()
        paths = uris_to_paths(uris)
        if paths:
            self.clip.add_files(paths)
            self.show_toast(f"已暂存 {len(paths)} 个文件")
        Gtk.drag_finish(ctx, bool(paths), False, t)
        self.dnd_over = False
        self.sticky_open = False

    # ================================================================== menu
    def _context_menu(self, ev):
        menu = Gtk.Menu()

        def item(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: cb())
            menu.append(mi)
        if self.target < 0.5:
            item("展开面板", lambda: self.expand(sticky=True))
        else:
            item("收起面板", self.collapse)
        item("刷新", self.refresh_all)
        item("设置…", self.open_settings)
        menu.append(Gtk.SeparatorMenuItem())
        item("退出 BrimDeck", self.app.quit_app)
        menu.show_all()
        self.menu_open = True

        def closed(*_):
            self.menu_open = False
            GLib.timeout_add(200, lambda: (self._check_pointer_outside() if self.target > 0.5 and not self.inside else None, False)[1])
        menu.connect("deactivate", closed)
        menu.attach_to_widget(self.area, None)
        menu.popup_at_pointer(ev)

    # ================================================================== fullscreen
    def _setup_fullscreen_watch(self):
        try:
            gi.require_version("Wnck", "3.0")
            from gi.repository import Wnck
            self._wnck = Wnck.Screen.get_default()
        except Exception:
            self._wnck = None
            return
        GLib.timeout_add(1000, self._check_fullscreen)

    def _check_fullscreen(self):
        if not self._wnck:
            return False
        hide = False
        if self.config["hide_in_fullscreen"]:
            w = self._wnck.get_active_window()
            if w is not None and w.is_fullscreen() and w.get_window_type().value_nick not in ("desktop", "dock"):
                mon = self.monitor().get_geometry()
                gx, gy, gw, gh = w.get_geometry()
                cx, cy = gx + gw // 2, gy + gh // 2
                hide = mon.x <= cx < mon.x + mon.width and mon.y <= cy < mon.y + mon.height
        if hide and not self._fullscreen_hidden:
            self._fullscreen_hidden = True
            self.collapse()
            self.hide()
        elif not hide and self._fullscreen_hidden:
            self._fullscreen_hidden = False
            self._sync_visibility()
            self.relayout()
        return True

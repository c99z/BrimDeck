"""Application entry: single instance, command line (--toggle, --page, --settings, --quit)."""
import os
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .clipboard import ClipboardService  # noqa: E402
from .config import Config  # noqa: E402
from .devices import DeviceService  # noqa: E402
from .island import Island  # noqa: E402
from .media import MediaService  # noqa: E402
from .sysmon import SystemMonitor  # noqa: E402
from .usage import UsageService  # noqa: E402

APP_ID = "io.github.brimdeck.Linux"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BrimDeckApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "展开或收起面板", None)
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "打开指定页面: usage|music|monitor|clipboard|devices", "PAGE")
        self.add_main_option("settings", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "打开设置", None)
        self.add_main_option("quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "退出正在运行的 BrimDeck", None)
        self.island = None
        self.settings_win = None
        self.config = None

    def launcher(self):
        local = os.path.expanduser("~/.local/bin/brimdeck")
        if os.path.exists(local):
            return local
        return f"{sys.executable} {os.path.join(ROOT, 'brimdeck.py')}"

    # ---- lifecycle
    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        icon_dir = os.path.join(ROOT, "data")
        Gtk.IconTheme.get_default().append_search_path(icon_dir)
        Gtk.Window.set_default_icon_name("brimdeck")
        self.config = Config()

        def main(fn):
            return lambda *a: GLib.idle_add(lambda: (fn(*a), False)[1])

        self.usage = UsageService(self.config, main(lambda s: self.island and self.island.on_usage(s)))
        self.media = MediaService(lambda: self.island and self.island.on_media())
        self.sysmon = SystemMonitor(main(lambda s: self.island and self.island.on_sys(s)))
        self.clip = ClipboardService(self.config, lambda: self.island and self.island.on_simple_change())
        self.devices = DeviceService(lambda: self.island and self.island.on_simple_change())
        self.island = Island(self, self.config, self.usage, self.media, self.sysmon, self.clip, self.devices)
        self.add_window(self.island)
        self.panel_action = Gio.SimpleAction.new_stateful("panel", None, self.panel_status())
        self.panel_action.connect("activate", lambda *_: self.island.toggle())
        self.add_action(self.panel_action)
        hover_action = Gio.SimpleAction.new("panel-hover", GLib.VariantType.new("b"))
        hover_action.connect("activate", lambda _, value: self.island.set_panel_hover(value.get_boolean()))
        self.add_action(hover_action)
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", lambda *_: self.open_settings())
        self.add_action(settings_action)
        self.island.set_panel_host(False)
        self.panel_watch = Gio.bus_watch_name(
            Gio.BusType.SESSION, "io.github.brimdeck.PanelHost", Gio.BusNameWatcherFlags.NONE,
            lambda *_: self.island.set_panel_host(True), lambda *_: self.island.set_panel_host(False))
        self.hold()
        self.usage.start()
        self.media.start()
        self.sysmon.start()
        self.clip.start()
        self.devices.start()
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)

    def _on_signal(self):
        self.quit_app()
        return GLib.SOURCE_REMOVE

    def do_command_line(self, cmd):
        opts = cmd.get_options_dict().end().unpack()
        if opts.get("quit"):
            self.quit_app()
            return 0
        if opts.get("settings"):
            self.open_settings()
        elif opts.get("page"):
            self.island.toggle(opts["page"])
        elif opts.get("toggle"):
            self.island.toggle()
        return 0

    def do_activate(self):
        pass

    def quit_app(self):
        Gio.bus_unwatch_name(self.panel_watch)
        try:
            self.clip.save(exiting=True)
        except Exception as e:
            print("brimdeck:", e)
        self.usage.stop()
        self.sysmon.stop()
        self.release()
        self.quit()

    # ---- settings
    def open_settings(self):
        from .settings import SettingsWindow
        if self.settings_win is None:
            self.settings_win = SettingsWindow(self)
            self.settings_win.connect("destroy", lambda *_: setattr(self, "settings_win", None))
            self.add_window(self.settings_win)
            self.settings_win.show_all()
        self.settings_win.present()

    def apply_settings(self):
        isl = self.island
        self.update_panel_status()
        isl.set_panel_host(isl.panel_host)
        if isl.page not in self.config["pages"]:
            isl.page = self.config["pages"][0]
        isl.relayout()
        isl._cw_target = __import__("brimdeck.pages", fromlist=["x"]).collapsed_width(isl)
        isl._ensure_tick()
        isl.redraw()

    def refresh_usage(self):
        self.usage.refresh()

    def panel_status(self):
        island = self.island
        quotas = {}
        for pid in island.enabled_providers():
            state = island.usage_states.get(pid)
            quotas[pid] = round(state.peak) if state and state.peak is not None else -1
        cpu = round(island.sys_snapshot.cpu) if island.sys_snapshot else -1
        return GLib.Variant("(bia{si})", (self.config["compact_location"] == "panel", cpu, quotas))

    def update_panel_status(self):
        self.panel_action.set_state(self.panel_status())


def main():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and "GDK_BACKEND" not in os.environ:
        # Input shapes and always-on-top placement need X11; run through XWayland.
        os.environ["GDK_BACKEND"] = "x11"
    app = BrimDeckApp()
    return app.run(sys.argv)

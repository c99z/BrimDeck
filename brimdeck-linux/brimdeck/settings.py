"""Settings window (a regular GTK window)."""
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .config import ALL_PAGES, CONFIG_DIR, PAGE_NAMES, autostart_enabled, set_autostart  # noqa: E402


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="BrimDeck 设置")
        self.app = app
        self.config = app.config
        self.set_default_size(520, 640)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("brimdeck")
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        nb = Gtk.Notebook()
        self.add(nb)
        nb.append_page(self._general(), Gtk.Label(label="通用"))
        nb.append_page(self._pages(), Gtk.Label(label="页面"))
        nb.append_page(self._data(), Gtk.Label(label="AI 工具与剪贴板"))
        nb.append_page(self._about(), Gtk.Label(label="关于"))

    # ---- helpers
    def _grid(self):
        g = Gtk.Grid(column_spacing=16, row_spacing=12, margin=20)
        g._row = 0
        return g

    def _row(self, g, label, widget, hint=None):
        lab = Gtk.Label(label=label, xalign=0)
        lab.set_hexpand(True)
        g.attach(lab, 0, g._row, 1, 1)
        widget.set_halign(Gtk.Align.END)
        g.attach(widget, 1, g._row, 1, 1)
        g._row += 1
        if hint:
            h = Gtk.Label(label=hint, xalign=0, wrap=True)
            h.get_style_context().add_class("dim-label")
            h.set_max_width_chars(60)
            g.attach(h, 0, g._row, 2, 1)
            g._row += 1

    def _section(self, g, title):
        lab = Gtk.Label(xalign=0)
        lab.set_markup(f"<b>{title}</b>")
        if g._row:
            lab.set_margin_top(8)
        g.attach(lab, 0, g._row, 2, 1)
        g._row += 1

    def _switch(self, key, sub=None, after=None):
        sw = Gtk.Switch()
        val = self.config[key][sub] if sub else self.config[key]
        sw.set_active(bool(val))

        def changed(s, _p):
            if sub:
                d = dict(self.config[key])
                d[sub] = s.get_active()
                self.config[key] = d
            else:
                self.config[key] = s.get_active()
            self._apply()
            if after:
                after()
        sw.connect("notify::active", changed)
        return sw

    def _combo(self, key, options, cast=str, after=None):
        cb = Gtk.ComboBoxText()
        cur = self.config[key]
        for i, (val, label) in enumerate(options):
            cb.append(str(val), label)
            if val == cur:
                cb.set_active(i)

        def changed(c):
            self.config[key] = cast(c.get_active_id())
            self._apply()
            if after:
                after()
        cb.connect("changed", changed)
        return cb

    def _spin(self, key, lo, hi, step, digits=0, after=None):
        sp = Gtk.SpinButton.new_with_range(lo, hi, step)
        sp.set_digits(digits)
        sp.set_value(float(self.config[key] or 0))

        def changed(s):
            v = s.get_value()
            self.config[key] = round(v, 2) if digits else int(v)
            self._apply()
            if after:
                after()
        sp.connect("value-changed", changed)
        return sp

    def _apply(self, relayout=False):
        self.config.save()
        self.app.apply_settings()

    # ---- pages
    def _general(self):
        g = self._grid()
        self._section(g, "外观与位置")
        self._row(g, "收起时显示为", self._combo("compact_location", [("panel", "系统日期旁的状态条"), ("notch", "独立刘海")]))
        disp = Gdk.Display.get_default()
        mons = [("primary", "主显示器")]
        for i in range(disp.get_n_monitors()):
            m = disp.get_monitor(i)
            geo = m.get_geometry()
            mons.append((m.get_model() or str(i), f"{m.get_model() or i}  {geo.width}×{geo.height}"))
        self._row(g, "显示在", self._combo("monitor", mons))
        self._row(g, "缩放", self._spin("scale", 0, 3, 0.25, 2), "0 = 自动（按 DPI 和分辨率）")
        self._row(g, "垂直偏移 (px)", self._spin("offset_y", -100, 400, 1),
                  "默认挂在 GNOME 顶栏下方；设为负数可向上覆盖顶栏")
        self._section(g, "悬停与动画")
        self._row(g, "面板展开方式", self._combo("expand_on", [("hover", "悬停展开"), ("click", "点击展开")]))
        self._row(g, "悬停延迟 (毫秒)", self._spin("hover_delay_ms", 0, 2000, 50))
        self._row(g, "离开后收起延迟 (毫秒)", self._spin("collapse_delay_ms", 0, 3000, 50))
        self._row(g, "展开 / 收起动画", self._switch("animations"))
        self._section(g, "行为")
        self._row(g, "全屏应用时隐藏", self._switch("hide_in_fullscreen"))
        sw = Gtk.Switch()
        sw.set_active(autostart_enabled())
        sw.connect("notify::active", lambda s, _p: set_autostart(s.get_active(), self.app.launcher()))
        self._row(g, "登录时自动启动", sw)
        scroller = Gtk.ScrolledWindow()
        scroller.add(g)
        return scroller

    def _pages(self):
        g = self._grid()
        self._section(g, "展开后显示的页面")
        self._page_checks = {}
        for p in ALL_PAGES:
            cb = Gtk.CheckButton(label=PAGE_NAMES[p])
            cb.set_active(p in self.config["pages"])
            cb.connect("toggled", self._pages_changed)
            self._page_checks[p] = cb
            g.attach(cb, 0, g._row, 2, 1)
            g._row += 1
        self._section(g, "默认页面")
        self._row(g, "展开时显示", self._combo("start_page", [(p, PAGE_NAMES[p]) for p in ALL_PAGES]))
        self._row(g, "记住上次的页面", self._switch("remember_page"))
        self._section(g, "设备电量")
        self._row(g, "偏低提示 (%)", self._spin("battery_warn", 5, 80, 5))
        self._row(g, "电量低 (%)", self._spin("battery_low", 5, 50, 5))
        return g

    def _pages_changed(self, *_):
        pages = [p for p in ALL_PAGES if self._page_checks[p].get_active()]
        if not pages:
            self._page_checks["usage"].set_active(True)
            return
        self.config["pages"] = pages
        self._apply()

    def _data(self):
        g = self._grid()
        self._section(g, "AI 工具")
        self._row(g, "Claude Code", self._switch("providers", "claude", self.app.refresh_usage),
                  "统计 ~/.claude/projects 下的本地会话；有 Claude 订阅登录 (~/.claude/.credentials.json) 时读取在线额度")
        self._row(g, "Codex", self._switch("providers", "codex", self.app.refresh_usage),
                  "统计 ~/.codex/sessions；用 Codex 已有的登录读取在线额度，失败时用会话记录里的额度")
        self._row(g, "在线读取 Codex 额度", self._switch("codex_online"))
        self._row(g, "在线读取 Claude 额度", self._switch("claude_online"))
        self._row(g, "额度刷新间隔 (秒)", self._spin("quota_refresh_s", 60, 3600, 30))
        self._section(g, "剪贴板")
        self._row(g, "历史保留", self._combo("clipboard_keep_days", [(0, "退出时清除"), (1, "1 天"), (7, "7 天"), (30, "30 天"), (-1, "永久")], int),
                  "收藏的条目始终保留；历史只保存在本机 ~/.local/share/brimdeck")
        self._row(g, "最多条数", self._spin("clipboard_max", 10, 1000, 10))
        self._row(g, "点击条目后直接粘贴", self._switch("paste_on_click"),
                  "开启时点击会复制并向当前窗口发送 Ctrl+V（终端为 Ctrl+Shift+V）；关闭时只复制")
        return g

    def _about(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=24)
        lab = Gtk.Label(xalign=0, wrap=True)
        lab.set_markup("<big><b>BrimDeck for Linux</b></big>\n\n"
                       "BrimDeck（Windows 版灵动岛）的 Linux 移植版，基于 Python + GTK3 + Cairo。\n"
                       "支持 GNOME / X11：AI 额度与用量、MPRIS 音乐、系统监控、剪贴板与文件中转、UPower 设备电量。\n\n"
                       "快捷键：可在 GNOME 设置 › 键盘 › 自定义快捷键 中绑定命令\n"
                       f"<tt>{self.app.launcher()} --toggle</tt>\n"
                       "或 <tt>--page music</tt> / <tt>--page clipboard</tt> 打开指定页面。")
        box.pack_start(lab, False, False, 0)
        btn = Gtk.Button(label="打开配置目录")
        btn.connect("clicked", lambda *_: subprocess.Popen(["xdg-open", CONFIG_DIR]))
        btn.set_halign(Gtk.Align.START)
        box.pack_start(btn, False, False, 0)
        return box

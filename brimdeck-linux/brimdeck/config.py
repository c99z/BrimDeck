"""Settings, stored as JSON under ~/.config/brimdeck/config.json."""
import copy
import json
import os
import threading

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "brimdeck")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "brimdeck")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"), "brimdeck")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

ALL_PAGES = ["usage", "music", "monitor", "clipboard", "devices"]
PAGE_NAMES = {"usage": "AI 用量", "music": "音乐", "monitor": "系统", "clipboard": "剪贴板", "devices": "设备"}

DEFAULTS = {
    "compact_location": "panel",
    # 0 means automatic: derived from Xft.dpi and the monitor's resolution.
    "scale": 0,
    # "primary", or a monitor's connector name such as "HDMI-0" / "DP-0".
    "monitor": "primary",
    # "hover" opens the panel when the pointer rests on the notch, "click" needs a click.
    "expand_on": "hover",
    "hover_delay_ms": 150,
    "collapse_delay_ms": 400,
    "pages": list(ALL_PAGES),
    "start_page": "usage",
    "remember_page": True,
    "hide_in_fullscreen": True,
    "animations": True,
    "offset_y": 0,
    "providers": {"claude": True, "codex": True},
    # 0: cleared when BrimDeck exits; n > 0: kept n days; -1: kept forever. Favorites always stay.
    "clipboard_keep_days": 0,
    "clipboard_max": 200,
    "paste_on_click": True,
    "codex_online": True,
    "claude_online": True,
    "usage_refresh_s": 60,
    "quota_refresh_s": 300,
    "battery_warn": 30,
    "battery_low": 15,
}


class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in DEFAULTS and isinstance(DEFAULTS[k], dict) and isinstance(v, dict):
                        self.data[k] = {**DEFAULTS[k], **v}
                    elif k in DEFAULTS:
                        self.data[k] = v
        except FileNotFoundError:
            pass
        except Exception as e:  # keep defaults, never lose the file
            print("brimdeck: config unreadable, using defaults:", e)
        pages = [p for p in self.data.get("pages", []) if p in ALL_PAGES]
        self.data["pages"] = pages or list(ALL_PAGES)

    def save(self):
        with self._lock:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_FILE)

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


AUTOSTART_FILE = os.path.join(HOME, ".config", "autostart", "brimdeck.desktop")


def autostart_enabled():
    return os.path.exists(AUTOSTART_FILE)


def set_autostart(enabled, launcher):
    if enabled:
        os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
        with open(AUTOSTART_FILE, "w", encoding="utf-8") as f:
            f.write(
                "[Desktop Entry]\nType=Application\nName=BrimDeck\nComment=Dynamic Island for Linux\n"
                f"Exec={launcher}\nIcon=brimdeck\nX-GNOME-Autostart-enabled=true\n"
                "X-GNOME-Autostart-Delay=3\nNoDisplay=false\n"
            )
    else:
        try:
            os.remove(AUTOSTART_FILE)
        except FileNotFoundError:
            pass

"""Batteries of wireless devices and this machine, from UPower (which also covers Bluetooth batteries)."""
from gi.repository import Gio, GLib

KINDS = {
    2: ("电池", "battery-symbolic"), 3: ("UPS", "battery-symbolic"), 5: ("鼠标", "input-mouse-symbolic"),
    6: ("键盘", "input-keyboard-symbolic"), 8: ("手机", "phone-symbolic"), 9: ("播放器", "audio-x-generic-symbolic"),
    10: ("平板", "computer-symbolic"), 12: ("手柄", "input-gaming-symbolic"), 13: ("触控笔", "input-mouse-symbolic"),
    14: ("触控板", "input-mouse-symbolic"), 17: ("耳麦", "audio-headset-symbolic"), 18: ("音箱", "audio-x-generic-symbolic"),
    19: ("耳机", "audio-headphones-symbolic"), 21: ("音频设备", "audio-headphones-symbolic"), 22: ("遥控器", "input-gaming-symbolic"),
    26: ("穿戴设备", "phone-symbolic"), 28: ("蓝牙设备", "phone-symbolic"),
}
# UPower BatteryLevel for devices that only report coarse levels.
LEVELS = {3: 10, 4: 15, 5: 30, 6: 55, 7: 80, 8: 100}


class Device:
    __slots__ = ("path", "kind", "kind_name", "icon", "model", "percent", "charging", "full", "laptop")


class DeviceService:
    def __init__(self, on_change):
        self.on_change = on_change
        self.devices = []
        self.bus = None

    def start(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception as e:
            print("brimdeck: no system bus:", e)
            return
        for sig in ("DeviceAdded", "DeviceRemoved"):
            self.bus.signal_subscribe("org.freedesktop.UPower", "org.freedesktop.UPower", sig, "/org/freedesktop/UPower",
                                      None, Gio.DBusSignalFlags.NONE, lambda *a: GLib.timeout_add(500, self._refresh_once))
        self.bus.signal_subscribe("org.freedesktop.UPower", "org.freedesktop.DBus.Properties", "PropertiesChanged", None,
                                  "org.freedesktop.UPower.Device", Gio.DBusSignalFlags.NONE,
                                  lambda *a: GLib.timeout_add(300, self._refresh_once))
        self.refresh()
        GLib.timeout_add_seconds(60, lambda: (self.refresh(), True)[1])

    def _refresh_once(self):
        self.refresh()
        return False

    def refresh(self):
        if not self.bus:
            return

        def got_list(conn, res):
            try:
                paths = conn.call_finish(res).unpack()[0]
            except Exception as e:
                print("brimdeck: upower", e)
                return
            out = []
            for p in paths:
                try:
                    props = conn.call_sync("org.freedesktop.UPower", p, "org.freedesktop.DBus.Properties", "GetAll",
                                           GLib.Variant("(s)", ("org.freedesktop.UPower.Device",)),
                                           GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                except Exception:
                    continue
                kind = props.get("Type", 0)
                if kind in (0, 1) or not props.get("IsPresent", True) and kind == 2:
                    continue
                if kind == 2 and not props.get("PowerSupply", False):
                    continue
                pct = props.get("Percentage")
                level = props.get("BatteryLevel", 0)
                if level in LEVELS and not pct:
                    pct = LEVELS[level]
                if pct is None:
                    continue
                d = Device()
                d.path = p
                d.kind = kind
                d.kind_name, d.icon = KINDS.get(kind, ("设备", "battery-symbolic"))
                d.model = (props.get("Model") or "").strip() or d.kind_name
                d.laptop = kind == 2
                if d.laptop:
                    d.model = "本机电池"
                d.percent = float(pct)
                state = props.get("State", 0)
                d.charging = state in (1, 5)
                d.full = state == 4
                out.append(d)
            out.sort(key=lambda d: (not d.laptop, d.kind, d.model))
            self.devices = out
            self.on_change()

        self.bus.call("org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", "EnumerateDevices",
                      None, GLib.VariantType("(ao)"), Gio.DBusCallFlags.NONE, 2000, None, got_list)

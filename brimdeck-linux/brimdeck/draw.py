"""Cairo/Pango drawing helpers and the dark panel palette."""
import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402


def hex_rgb(h, a=1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


class T:
    """Dark theme."""
    bg = hex_rgb("#0B0B0D")
    bg_panel = hex_rgb("#111114", 0.97)
    card = (1, 1, 1, 0.045)
    card_hover = (1, 1, 1, 0.085)
    border = (1, 1, 1, 0.08)
    fg = hex_rgb("#F2F2F7")
    fg2 = (0.95, 0.95, 0.97, 0.62)
    fg3 = (0.95, 0.95, 0.97, 0.38)
    track = (1, 1, 1, 0.10)
    accent = hex_rgb("#0A84FF")
    green = hex_rgb("#30D158")
    yellow = hex_rgb("#FFD60A")
    orange = hex_rgb("#FF9F0A")
    red = hex_rgb("#FF453A")
    teal = hex_rgb("#64D2FF")
    purple = hex_rgb("#BF5AF2")
    pink = hex_rgb("#FF375F")
    claude = hex_rgb("#E5A385")
    codex = hex_rgb("#9CB9FF")
    cpu = hex_rgb("#64D2FF")
    mem = hex_rgb("#BF5AF2")
    gpu = hex_rgb("#30D158")
    net_down = hex_rgb("#0A84FF")
    net_up = hex_rgb("#FF9F0A")
    disk = hex_rgb("#FFD60A")


def level_color(pct, base=None, warn=70, crit=90):
    if pct is None:
        return T.fg3
    if pct >= crit:
        return T.red
    if pct >= warn:
        return T.orange
    return base or T.green


def with_alpha(c, a):
    return (c[0], c[1], c[2], (c[3] if len(c) > 3 else 1) * a)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def set_color(cr, c, alpha=1.0):
    cr.set_source_rgba(c[0], c[1], c[2], (c[3] if len(c) > 3 else 1.0) * alpha)


def rounded_rect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def fill_rounded(cr, x, y, w, h, r, color, alpha=1.0):
    rounded_rect(cr, x, y, w, h, r)
    set_color(cr, color, alpha)
    cr.fill()


def notch_path(cr, x, w, h, r, shoulder):
    """A notch hanging from y=0: flat top, concave shoulders, rounded bottom corners."""
    r = max(0.0, min(r, w / 2, h))
    s = max(0.0, min(shoulder, h - r if h > r else 0, 20))
    cr.new_path()
    cr.move_to(x - s, 0)
    if s > 0:
        cr.curve_to(x - s * 0.35, 0, x, s * 0.35, x, s)
    cr.line_to(x, h - r)
    cr.arc_negative(x + r, h - r, r, math.pi, math.pi / 2)
    cr.line_to(x + w - r, h)
    cr.arc_negative(x + w - r, h - r, r, math.pi / 2, 0)
    cr.line_to(x + w, s)
    if s > 0:
        cr.curve_to(x + w, s * 0.35, x + w + s * 0.35, 0, x + w + s, 0)
    cr.close_path()


def ring(cr, cx, cy, r, width, frac, color, track=T.track, start=-math.pi / 2):
    cr.set_line_width(width)
    cr.set_line_cap(1)  # round
    set_color(cr, track)
    cr.new_path()
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.stroke()
    if frac is not None and frac > 0.001:
        frac = min(frac, 1.0)
        set_color(cr, color)
        cr.new_path()
        cr.arc(cx, cy, r, start, start + 2 * math.pi * frac)
        cr.stroke()
    cr.set_line_cap(0)


def bar(cr, x, y, w, h, frac, color, track=T.track):
    fill_rounded(cr, x, y, w, h, h / 2, track)
    if frac and frac > 0:
        fw = max(h, w * min(frac, 1.0))
        fill_rounded(cr, x, y, fw, h, h / 2, color)


def sparkline(cr, x, y, w, h, values, color, vmax=None, fill=True, width=1.4, slots=60):
    """History graph; with fewer values than slots, it grows in from the right edge."""
    vals = list(values)[-slots:]
    if len(vals) < 2:
        return
    top = vmax if vmax else max(max(vals), 1e-9)
    n = len(vals)
    step = w / (slots - 1)
    x0 = x + (slots - n) * step
    pts = [(x0 + i * step, y + h - h * min(v / top, 1.0)) for i, v in enumerate(vals)]
    cr.new_path()
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    if fill:
        path = cr.copy_path()
        cr.line_to(x + w, y + h)
        cr.line_to(x0, y + h)
        cr.close_path()
        set_color(cr, color, 0.18)
        cr.fill()
        cr.append_path(path)
    set_color(cr, color)
    cr.set_line_width(width)
    cr.set_line_join(1)
    cr.stroke()


# ---------------------------------------------------------------- text

_FAMILY = None


def ui_family():
    global _FAMILY
    if _FAMILY is None:
        name = Gtk.Settings.get_default().get_property("gtk-font-name") or "Sans 11"
        _FAMILY = Pango.FontDescription.from_string(name).get_family() or "Sans"
    return _FAMILY


_desc_cache = {}


def _desc(size, weight, mono):
    key = (size, weight, mono)
    d = _desc_cache.get(key)
    if d is None:
        d = Pango.FontDescription()
        d.set_family("Monospace" if mono else ui_family())
        d.set_absolute_size(size * Pango.SCALE)
        d.set_weight({"bold": Pango.Weight.BOLD, "semibold": Pango.Weight.SEMIBOLD,
                      "medium": Pango.Weight.MEDIUM, "light": Pango.Weight.LIGHT}.get(weight, Pango.Weight.NORMAL))
        _desc_cache[key] = d
    return d


def layout(cr, s, size=12, weight="normal", max_w=None, mono=False, lines=1, align="left"):
    lay = PangoCairo.create_layout(cr)
    lay.set_font_description(_desc(size, weight, mono))
    lay.set_text(s if s is not None else "", -1)
    if max_w is not None:
        lay.set_width(int(max(1, max_w) * Pango.SCALE))
        lay.set_ellipsize(Pango.EllipsizeMode.END)
        if lines > 1:
            lay.set_height(-lines)
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        lay.set_alignment({"center": Pango.Alignment.CENTER, "right": Pango.Alignment.RIGHT}.get(align, Pango.Alignment.LEFT))
    return lay


def text_size(cr, s, size=12, weight="normal", mono=False):
    lay = layout(cr, s, size, weight, mono=mono)
    _, log = lay.get_pixel_extents()
    return log.width, log.height


def text(cr, s, x, y, size=12, color=T.fg, weight="normal", align="left", max_w=None, mono=False,
         valign="top", lines=1, alpha=1.0):
    """Draws text. With max_w, the text is ellipsized and aligned inside [x, x+max_w].
    Without it, align positions the text relative to x. valign="middle" centers on y. Returns width drawn."""
    lay = layout(cr, s, size, weight, max_w, mono, lines, align)
    _, log = lay.get_pixel_extents()
    if max_w is None:
        if align == "center":
            x -= log.width / 2
        elif align == "right":
            x -= log.width
    if valign == "middle":
        y -= log.height / 2
    elif valign == "bottom":
        y -= log.height
    set_color(cr, color, alpha)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, lay)
    return log.width


# ---------------------------------------------------------------- icons

_icon_cache = {}


def icon(cr, name, x, y, size, color=T.fg, scale=1.0, alpha=1.0):
    """Draws a symbolic theme icon tinted with color, at logical (x, y) and logical size."""
    px = max(8, int(round(size * scale)))
    key = (name, px, tuple(round(v, 3) for v in color[:3]))
    pb = _icon_cache.get(key)
    if pb is None:
        theme = Gtk.IconTheme.get_default()
        info = theme.lookup_icon(name, px, Gtk.IconLookupFlags.FORCE_SIZE | Gtk.IconLookupFlags.FORCE_SYMBOLIC)
        if info is None:
            return
        rgba = Gdk.RGBA(color[0], color[1], color[2], 1.0)
        try:
            pb, _ = info.load_symbolic(rgba, None, None, None)
        except Exception:
            pb = info.load_icon()
        _icon_cache[key] = pb
    cr.save()
    cr.translate(x, y)
    cr.scale(size / pb.get_width(), size / pb.get_height())
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    cr.paint_with_alpha(alpha)
    cr.restore()


def pixbuf_rounded(cr, pb, x, y, w, h, r, alpha=1.0):
    """Draws a pixbuf cover-fitted into a rounded rect."""
    cr.save()
    rounded_rect(cr, x, y, w, h, r)
    cr.clip()
    pw, ph = pb.get_width(), pb.get_height()
    k = max(w / pw, h / ph)
    cr.translate(x + (w - pw * k) / 2, y + (h - ph * k) / 2)
    cr.scale(k, k)
    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
    cr.get_source().set_filter(2)  # GOOD
    cr.paint_with_alpha(alpha)
    cr.restore()


# ---------------------------------------------------------------- formatting

def fmt_tokens(n):
    n = float(n or 0)
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M" if n >= 1e7 else f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(int(n))


def fmt_cost(c):
    if c is None:
        return "—"
    if c >= 1000:
        return f"${c:,.0f}"
    if c >= 100:
        return f"${c:.0f}"
    return f"${c:.2f}"


def fmt_bytes(n, rate=False):
    n = float(n or 0)
    units = ["B", "K", "M", "G", "T"]
    i = 0
    while n >= 1000 and i < len(units) - 1:
        n /= 1024
        i += 1
    s = f"{n:.0f}" if n >= 100 or i == 0 else f"{n:.1f}"
    return s + units[i] + ("/s" if rate else "")


def fmt_duration(seconds):
    if seconds is None:
        return ""
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}天{h}小时"
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分钟"
    return f"{s}秒"


def fmt_clock(sec):
    sec = int(max(0, sec or 0))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_out_back(t, k=1.25):
    t -= 1
    return 1 + (k + 1) * t ** 3 + k * t ** 2


def ease_in_out(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp(a, b, t):
    return a + (b - a) * t

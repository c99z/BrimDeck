"""Drawing of the collapsed notch and of each expanded page.

Every function draws in logical units (the window applies the scale) and registers clickable
areas through ui.hot(...), so the island can hit-test without widgets.
"""
import datetime as dt
import math
import os
import time

from . import draw as D
from .draw import T
from .clipboard import parse_color

PROVIDER_COLORS = {"claude": T.claude, "codex": T.codex}


# ====================================================================== sizes

def page_height(ui, page):
    """Expanded panel height for a page (logical units); the panel animates between them."""
    if page == "music":
        return 358 if (ui.media and ui.media.current()) else 260
    if page == "devices":
        n = len(ui.devices.devices) if ui.devices else 0
        return 260 if n == 0 else 270 if n <= 5 else 450
    return 420


# ====================================================================== collapsed

def collapsed_width(ui):
    p = ui.media.current() if ui.media else None
    base = 96 + 26 * len(ui.enabled_providers())
    if p and p.playing:
        return max(base, 300)
    return max(base + 40, 190)


def draw_collapsed(ui, cr, x, w, h, alpha):
    pad = 14
    cy = h / 2
    # quota rings
    cx = x + pad + 9
    for pid in ui.enabled_providers():
        st = ui.usage_states.get(pid)
        color = PROVIDER_COLORS[pid]
        peak = st.peak if st else None
        frac = (peak or 0) / 100
        c = D.level_color(peak, color) if peak is not None else D.with_alpha(color, 0.5)
        D.ring(cr, cx, cy, 8, 2.6, frac, D.with_alpha(c, alpha), track=D.with_alpha(T.track, alpha * 1.4))
        D.text(cr, pid[0].upper() if pid != "codex" else "X", cx, cy, 7.5, D.with_alpha(color, alpha), "bold",
               align="center", valign="middle")
        cx += 24
    left_end = cx - 6
    p = ui.media.current() if ui.media else None
    right = x + w - pad
    if p and p.playing:
        # equalizer + mini cover on the right, title in the middle
        bars = 5
        bw, gap = 2.6, 2.2
        ex = right - bars * (bw + gap) + gap
        color = ui.accent_for(p)
        for i, v in enumerate(ui.eq_levels(bars, p)):
            bh = 4 + v * (h * 0.42)
            D.fill_rounded(cr, ex + i * (bw + gap), cy - bh / 2, bw, bh, bw / 2, color, alpha)
        art_x = ex - 8 - 20
        if p.art:
            D.pixbuf_rounded(cr, p.art, art_x, cy - 10, 20, 20, 5, alpha)
        else:
            D.fill_rounded(cr, art_x, cy - 10, 20, 20, 5, T.card_hover, alpha)
            D.icon(cr, "audio-x-generic-symbolic", art_x + 4, cy - 6, 12, T.fg2, ui.S, alpha)
        tx = left_end + 6
        tw = art_x - 8 - tx
        if tw > 30:
            line = ui.lyric_or_title(p)
            D.text(cr, line, tx, cy, 11.5, T.fg, "medium", max_w=tw, valign="middle", align="center", alpha=alpha)
    else:
        now = dt.datetime.now()
        D.text(cr, now.strftime("%H:%M"), right, cy, 13, T.fg, "semibold", align="right", valign="middle", alpha=alpha)
        st = ui.sys_snapshot
        if st is not None and right - 40 - left_end > 50:
            D.text(cr, f"{st.cpu:.0f}%", right - 46, cy, 10.5, D.with_alpha(T.cpu, 0.9), "medium", align="right",
                   valign="middle", alpha=alpha)


# ====================================================================== header

PAGE_ICONS = {"usage": "emoji-objects-symbolic", "music": "audio-x-generic-symbolic",
              "monitor": "utilities-system-monitor-symbolic", "clipboard": "edit-paste-symbolic",
              "devices": "battery-good-symbolic"}


def draw_header(ui, cr, x, y, w):
    from .config import PAGE_NAMES
    tx = x
    for page in ui.config["pages"]:
        label = PAGE_NAMES[page]
        tw, _ = D.text_size(cr, label, 12, "medium")
        bw = tw + 38
        active = page == ui.page
        hov = ui.hover == ("tab", page)
        if active:
            D.fill_rounded(cr, tx, y, bw, 28, 14, (1, 1, 1, 0.13))
        elif hov:
            D.fill_rounded(cr, tx, y, bw, 28, 14, (1, 1, 1, 0.06))
        col = T.fg if active else T.fg2
        D.icon(cr, PAGE_ICONS[page], tx + 11, y + 7, 14, col, ui.S)
        D.text(cr, label, tx + 30, y + 14, 12, col, "medium", valign="middle")
        ui.hot(tx, y, bw, 28, ("tab", page), lambda p=page: ui.set_page(p))
        tx += bw + 4
    # right buttons
    bx = x + w - 28
    for key, ic, cb, tip in (("settings", "emblem-system-symbolic", ui.open_settings, "设置"),
                             ("refresh", "view-refresh-symbolic", ui.refresh_all, "刷新")):
        hov = ui.hover == ("btn", key)
        if hov:
            D.fill_rounded(cr, bx, y, 28, 28, 14, (1, 1, 1, 0.10))
        D.icon(cr, ic, bx + 7, y + 7, 14, T.fg if hov else T.fg2, ui.S)
        ui.hot(bx, y, 28, 28, ("btn", key), cb, tip)
        bx -= 32
    if ui.toast and time.time() < ui.toast[1]:
        D.text(cr, ui.toast[0], bx + 24, y + 14, 11.5, T.fg2, align="right", valign="middle")


# ====================================================================== AI usage

def draw_usage(ui, cr, x, y, w, h):
    balance = ui.usage_states.get("chatapi")
    if balance is not None:
        tx = x
        for view, label in (("chatapi", "ChatAPI 额度"), ("local", "本机用量")):
            width = D.text_size(cr, label, 12, "medium")[0] + 24
            key = ("usage_view", view)
            active = ui.usage_view == view
            if active or ui.hover == key:
                D.fill_rounded(cr, tx, y, width, 26, 8, T.card_hover if active else T.card)
            D.text(cr, label, tx + 12, y + 13, 12, T.fg if active else T.fg2, "medium", valign="middle")
            ui.hot(tx, y, width, 26, key, lambda v=view: ui.set_usage_view(v))
            tx += width + 4
        if ui.usage_view == "chatapi":
            if balance.updated:
                status = (balance.status + " · " if balance.status else "") + "更新于 " + _ago(balance.updated)
                D.text(cr, status, x + w - 4, y + 13, 11, T.yellow if balance.status else T.fg2,
                       align="right", valign="middle")
            _balance_table(ui, cr, x, y + 36, w, h - 36, balance)
            return
        y, h = y + 36, h - 36
    pids = ui.enabled_providers()
    if not pids:
        D.text(cr, "未启用任何工具，可在设置中开启 Claude / Codex", x + w / 2, y + h / 2, 13, T.fg3, align="center", valign="middle")
        return
    gap = 12
    cw = (w - gap * (len(pids) - 1)) / len(pids)
    for i, pid in enumerate(pids):
        _usage_card(ui, cr, pid, x + i * (cw + gap), y, cw, h)


def _balance_table(ui, cr, x, y, w, h, balance):
    D.fill_rounded(cr, x, y, w, h, 16, T.card)
    if not balance.pools:
        D.text(cr, balance.status or "暂无已启用的额度", x + w / 2, y + h / 2, 13, T.fg2,
               align="center", valign="middle")
        return
    left, right = x + 16, x + w - 16
    usage_x, usage_w = x + w * 0.32, w * 0.25
    reset_x = x + w * 0.61
    for label, pos in (("额度 / 生效时段", left), ("已用 / 上限", usage_x), ("下次重置 · 北京时间", reset_x)):
        D.text(cr, label, pos, y + 12, 11, T.fg2, "medium")
    D.text(cr, "剩余（美元）", right, y + 12, 11, T.fg2, "medium", align="right")
    top = y + 37
    row_h = 65
    height = h - 45
    overflow = max(0, row_h * len(balance.pools) - height)
    ui.balance_scroll = min(ui.balance_scroll, overflow)
    if overflow:
        ui.scroll_area(x, top, w, height, "balance", overflow)
    cr.save()
    cr.rectangle(x, top, w, height)
    cr.clip()
    for i, pool in enumerate(balance.pools):
        ry = top + i * row_h - ui.balance_scroll
        if ry + row_h <= top or ry >= top + height:
            continue
        color = T.purple if pool.client else T.codex
        D.fill_rounded(cr, left, ry, right - left, 1, 0, T.border)
        D.text(cr, pool.title, left, ry + 11, 14, T.fg, "semibold", max_w=usage_x - left - 12)
        scope = pool.client or "通用"
        D.text(cr, f"{scope} · {pool.hours}", left, ry + 36, 11.5, T.fg2, max_w=usage_x - left - 12)
        pct = pool.used / pool.limit if pool.limit else 0
        D.text(cr, f"${pool.used:,.2f} / ${pool.limit:,.2f}", usage_x, ry + 11, 12, T.fg2, max_w=usage_w)
        D.bar(cr, usage_x, ry + 41, usage_w - 40, 4, pct, color)
        D.text(cr, f"{pct:.0%}", usage_x + usage_w, ry + 43, 11, T.fg2, align="right", valign="middle")
        D.text(cr, pool.reset, reset_x, ry + 24, 12, T.fg2)
        D.text(cr, f"${max(0, pool.limit - pool.used):,.2f}", right, ry + 19, 17, color, "semibold", align="right")
    cr.restore()


def _usage_card(ui, cr, pid, x, y, w, h):
    st = ui.usage_states.get(pid)
    color = PROVIDER_COLORS[pid]
    D.fill_rounded(cr, x, y, w, h, 16, T.card)
    pad = 16
    ix, iw = x + pad, w - 2 * pad
    cy = y + 14
    # header
    D.ring(cr, ix + 9, cy + 10, 8, 2.6, (st.peak or 0) / 100 if st and st.peak is not None else 0,
           D.level_color(st.peak if st else None, color), track=D.with_alpha(color, 0.25))
    nx = ix + 26
    nw = D.text(cr, st.name if st else pid.title(), nx, cy + 10, 15, T.fg, "semibold", valign="middle")
    if st and st.plan:
        pw, _ = D.text_size(cr, st.plan, 10.5, "semibold")
        D.fill_rounded(cr, nx + nw + 8, cy + 2, pw + 14, 17, 8.5, D.with_alpha(color, 0.18))
        D.text(cr, st.plan, nx + nw + 15, cy + 10.5, 10.5, color, "semibold", valign="middle")
    if st:
        dot = {"online": T.green, "stale": T.yellow, "local": T.fg3, "error": T.red, "loading": T.fg3}.get(st.kind, T.fg3)
        status = st.status
        if st.kind in ("online", "stale") and st.quota_time:
            status += " · " + _ago(st.quota_time)
        sw = min(iw * 0.55, D.text_size(cr, status, 10.5)[0] + 2)
        D.text(cr, status, ix + iw - sw, cy + 10, 10.5, T.fg3, max_w=sw, valign="middle", align="right")
        D.fill_rounded(cr, ix + iw - sw - 11, cy + 7, 6, 6, 3, dot)
    cy += 34
    if st is None:
        D.text(cr, "读取中…", ix, cy, 12, T.fg3)
        return
    # quotas
    quotas = st.quotas[:3]
    if quotas:
        for q in quotas:
            pct = q.percent
            qc = D.level_color(pct, color)
            D.text(cr, q.label, ix, cy, 12, T.fg2, "medium")
            D.text(cr, f"{pct:.0f}%", ix + iw, cy - 1, 14, qc, "semibold", align="right")
            D.bar(cr, ix, cy + 21, iw, 6, pct / 100, qc)
            if q.reset_at:
                left = q.reset_at - time.time()
                when = dt.datetime.fromtimestamp(q.reset_at)
                D.text(cr, f"{D.fmt_duration(left)}后重置 · {when.strftime('%m-%d %H:%M')}", ix, cy + 31, 10.5, T.fg3)
            cy += 52
    else:
        D.fill_rounded(cr, ix, cy, iw, 40, 10, (1, 1, 1, 0.03))
        msg = "无在线额度，仅统计本机会话 Token" if st.kind in ("local", "loading") else st.status
        D.text(cr, msg, ix + 12, cy + 20, 11.5, T.fg3, max_w=iw - 24, valign="middle")
        cy += 52
    # divider
    D.fill_rounded(cr, ix, cy, iw, 1, 0.5, T.border)
    cy += 12
    # stats + 7 day chart
    half = iw * 0.27
    for i, (label, tot) in enumerate((("今日", st.today), ("近 7 天", st.week))):
        sx = ix + i * (half + 6)
        D.text(cr, label, sx, cy, 11, T.fg3)
        D.text(cr, D.fmt_tokens(tot.tokens), sx, cy + 15, 18, T.fg, "semibold")
        cost = D.fmt_cost(tot.cost) + ("+" if tot.unpriced else "")
        D.text(cr, cost, sx, cy + 39, 11.5, color, "medium")
    chx = ix + 2 * half + 18
    chw = ix + iw - chx
    _day_chart(ui, cr, chx, cy + 2, chw, 52, st.days, color)
    cy += 70
    # models
    models = st.models_week
    rows = int((y + h - 44 - cy - 18) // 20)
    if models and rows > 0:
        D.text(cr, "近 7 天模型", ix, cy, 11, T.fg3)
        D.text(cr, "Token", ix + iw - 70, cy, 11, T.fg3, align="right")
        D.text(cr, "估算费用", ix + iw, cy, 11, T.fg3, align="right")
        cy += 18
        top = max(m[1] for m in models) or 1
        for name, tok, cost in models[:rows]:
            D.fill_rounded(cr, ix - 4, cy - 1, max(8, (iw - 140) * tok / top), 17, 4, D.with_alpha(color, 0.13))
            D.text(cr, name, ix, cy, 11.5, T.fg2, max_w=iw - 150)
            D.text(cr, D.fmt_tokens(tok), ix + iw - 70, cy, 11.5, T.fg2, align="right", mono=False)
            D.text(cr, D.fmt_cost(cost), ix + iw, cy, 11.5, T.fg, align="right")
            cy += 20
    # today's token mix
    t = st.today
    if t.tokens and y + h - cy > 34:
        by = y + h - 30
        D.fill_rounded(cr, ix, by - 8, iw, 1, 0.5, T.border)
        parts = [("输入", t.input, T.fg2), ("输出", t.output, color), ("缓存", t.cache, T.fg3)]
        D.text(cr, "今日构成", ix, by, 10.5, T.fg3)
        px = ix + 58
        for label, v, c in parts:
            px += D.text(cr, f"{label} {D.fmt_tokens(v)}", px, by, 10.5, c) + 14


def _day_chart(ui, cr, x, y, w, h, days, color):
    n = len(days)
    top = max(days) or 1
    gap = 5
    bw = (w - gap * (n - 1)) / n
    today = dt.date.today()
    for i, v in enumerate(days):
        bx = x + i * (bw + gap)
        bh = max(2, (h - 14) * v / top) if v else 2
        c = color if i == n - 1 else D.with_alpha(color, 0.45)
        D.fill_rounded(cr, bx, y + h - 14 - bh, bw, bh, min(3, bw / 2), c)
        day = today - dt.timedelta(days=n - 1 - i)
        D.text(cr, "今" if i == n - 1 else "一二三四五六日"[day.weekday()], bx + bw / 2, y + h - 11, 9.5, T.fg3, align="center")
        key = ("day", i)
        ui.hot(bx, y, bw, h, key, None, f"{day.strftime('%m-%d')}  {D.fmt_tokens(v)} tokens")


def _ago(ts):
    s = time.time() - ts
    if s < 60:
        return "刚刚"
    if s < 3600:
        return f"{int(s // 60)} 分钟前"
    if s < 86400:
        return f"{int(s // 3600)} 小时前"
    return f"{int(s // 86400)} 天前"


# ====================================================================== music

def draw_music(ui, cr, x, y, w, h):
    p = ui.media.current() if ui.media else None
    if p is None:
        D.icon(cr, "audio-x-generic-symbolic", x + w / 2 - 20, y + h / 2 - 56, 40, T.fg3, ui.S)
        D.text(cr, "没有正在播放的媒体", x + w / 2, y + h / 2, 15, T.fg2, "medium", align="center", valign="middle")
        D.text(cr, "支持所有提供 MPRIS 的播放器：浏览器、Spotify、Rhythmbox、VLC、mpv(mpris 插件) 等",
               x + w / 2, y + h / 2 + 26, 11.5, T.fg3, align="center", valign="middle")
        return
    accent = ui.accent_for(p)
    # soft accent glow from the cover
    cs = 168
    cx, cy = x + 6, y + 4
    D.fill_rounded(cr, cx - 2, cy + 6, cs + 4, cs, 18, D.with_alpha(accent, 0.18))
    if p.art:
        D.pixbuf_rounded(cr, p.art, cx, cy, cs, cs, 16)
    else:
        D.fill_rounded(cr, cx, cy, cs, cs, 16, T.card_hover)
        D.icon(cr, "audio-x-generic-symbolic", cx + cs / 2 - 24, cy + cs / 2 - 24, 48, T.fg3, ui.S)
    rx = cx + cs + 24
    rw = x + w - rx
    # players
    players = sorted(ui.media.players.values(), key=lambda q: q.bus_name)
    chip_y = cy
    if len(players) > 1:
        px = rx
        for q in players:
            label = q.identity
            tw, _ = D.text_size(cr, label, 10.5, "medium")
            bw = tw + 22
            if px + bw > rx + rw:
                break
            active = q is p
            hov = ui.hover == ("player", q.bus_name)
            D.fill_rounded(cr, px, chip_y, bw, 20, 10, (1, 1, 1, 0.14 if active else 0.07 if hov else 0.04))
            if q.playing:
                D.fill_rounded(cr, px + 7, chip_y + 8, 4, 4, 2, T.green)
            D.text(cr, label, px + 14, chip_y + 10, 10.5, T.fg if active else T.fg2, "medium", valign="middle")

            def pick(name=q.bus_name):
                ui.media.pinned = name
                ui.media.selected = name
                ui.redraw()
            ui.hot(px, chip_y, bw, 20, ("player", q.bus_name), pick, "固定显示此播放器")
            px += bw + 6
        ty = cy + 32
    else:
        D.text(cr, p.identity, rx, chip_y + 2, 11, T.fg3, "medium")
        ty = cy + 24
    D.text(cr, p.title or "未知曲目", rx, ty, 21, T.fg, "semibold", max_w=rw)
    D.text(cr, p.artist or "", rx, ty + 32, 14, T.fg2, max_w=rw)
    if p.album:
        D.text(cr, p.album, rx, ty + 54, 12, T.fg3, max_w=rw)
    # equalizer
    bars = 36
    ew = rw
    eh = 34
    ey = cy + cs - eh
    gap = 3
    bw = (ew - gap * (bars - 1)) / bars
    for i, v in enumerate(ui.eq_levels(bars, p)):
        bh = 3 + v * (eh - 3)
        c = D.mix(accent, T.fg, i / bars * 0.35)
        D.fill_rounded(cr, rx + i * (bw + gap), ey + eh - bh, bw, bh, min(1.5, bw / 2), c, 0.85)
    # progress
    py = cy + cs + 22
    length = p.length
    pos = p.current_position()
    frac = pos / length if length > 0 else 0
    D.bar(cr, x + 6, py, w - 12, 5, frac, accent)
    if length > 0:
        knob = x + 6 + (w - 12) * frac
        if ui.hover == ("seek", 0):
            D.fill_rounded(cr, knob - 6, py - 3.5, 12, 12, 6, T.fg)

    def seek():
        if length > 0 and p.can.get("CanSeek", True):
            mx = ui.last_pointer[0]
            p.seek_to(max(0.0, min(1.0, (mx - x - 6) / (w - 12))) * length)
            ui.redraw()
    ui.hot(x + 6, py - 8, w - 12, 21, ("seek", 0), seek)
    D.text(cr, D.fmt_clock(pos), x + 6, py + 11, 10.5, T.fg3)
    D.text(cr, D.fmt_clock(length) if length else "--:--", x + w - 6, py + 11, 10.5, T.fg3, align="right")
    # controls
    by = py + 30
    mid = x + w / 2
    ctrls = [
        ("shuffle", "media-playlist-shuffle-symbolic", -150, 18, p.toggle_shuffle, p.shuffle is not None, bool(p.shuffle)),
        ("prev", "media-skip-backward-symbolic", -78, 22, p.previous, p.can.get("CanGoPrevious", True), False),
        ("play", "media-playback-pause-symbolic" if p.playing else "media-playback-start-symbolic", 0, 26, p.play_pause, True, False),
        ("next", "media-skip-forward-symbolic", 78, 22, p.next, p.can.get("CanGoNext", True), False),
        ("loop", "media-playlist-repeat-symbolic", 150, 18, p.cycle_loop, p.loop is not None, p.loop in ("Playlist", "Track")),
    ]
    for key, ic, dx, size, cb, enabled, on in ctrls:
        if not enabled:
            continue
        r = 26 if key == "play" else 19
        bx = mid + dx
        hov = ui.hover == ("ctl", key)
        if key == "play":
            D.fill_rounded(cr, bx - r, by + 22 - r, 2 * r, 2 * r, r, T.fg if not hov else D.mix(T.fg, accent, 0.25))
            D.icon(cr, ic, bx - size / 2, by + 22 - size / 2, size, T.bg, ui.S)
        else:
            if hov:
                D.fill_rounded(cr, bx - r, by + 22 - r, 2 * r, 2 * r, r, (1, 1, 1, 0.10))
            col = accent if on else (T.fg if key in ("prev", "next") else T.fg3)
            D.icon(cr, ic, bx - size / 2, by + 22 - size / 2, size, col, ui.S)
            if key == "loop" and p.loop == "Track":
                D.text(cr, "1", bx + 9, by + 22 + 3, 8.5, accent, "bold")

        def act(cb=cb):
            cb()
            ui.redraw_soon()
        ui.hot(bx - r, by + 22 - r, 2 * r, 2 * r, ("ctl", key), act)


# ====================================================================== system monitor

def draw_monitor(ui, cr, x, y, w, h):
    s = ui.sys_snapshot
    if s is None:
        D.text(cr, "读取中…", x + w / 2, y + h / 2, 13, T.fg3, align="center", valign="middle")
        return
    pw = 232
    gw = w - pw - 12
    cols, rows = 3, 2
    gap = 10
    tw = (gw - gap * (cols - 1)) / cols
    th = (h - gap * (rows - 1)) / rows
    tiles = [_tile_cpu, _tile_mem, _tile_gpu, _tile_net, _tile_disk, _tile_sys]
    for i, fn in enumerate(tiles):
        tx = x + (i % cols) * (tw + gap)
        ty = y + (i // cols) * (th + gap)
        D.fill_rounded(cr, tx, ty, tw, th, 14, T.card)
        fn(ui, cr, s, tx + 12, ty + 10, tw - 24, th - 20)
    _processes(ui, cr, s, x + gw + 12, y, pw, h)


def _tile_title(cr, label, value, x, y, w, color, sub=None):
    D.text(cr, label, x, y, 11, T.fg3, "medium")
    if value is not None:
        D.text(cr, value, x + w, y - 2, 15, color, "semibold", align="right")
    if sub:
        D.text(cr, sub, x, y + 15, 10, T.fg3, max_w=w * 0.6)


def _tile_cpu(ui, cr, s, x, y, w, h):
    _tile_title(cr, "CPU", f"{s.cpu:.0f}%", x, y, w, T.cpu,
                f"{s.cpu_freq / 1000:.1f} GHz" if s.cpu_freq else None)
    D.sparkline(cr, x, y + 34, w, h - 34 - 30, s.hist.get("cpu", []), T.cpu, vmax=100)
    cores = s.cores
    n = len(cores)
    if n:
        gap = 1.5 if n > 24 else 2
        bw = (w - gap * (n - 1)) / n
        by = y + h - 24
        for i, c in enumerate(cores):
            bh = max(1.5, 24 * c / 100)
            D.fill_rounded(cr, x + i * (bw + gap), by, bw, 24, min(1, bw / 2), T.track)
            D.fill_rounded(cr, x + i * (bw + gap), by + 24 - bh, bw, bh, min(1, bw / 2), D.level_color(c, T.cpu, 75, 95))
        ui.hot(x, by, w, 24, ("cores", 0), None, f"{n} 个逻辑核心 · 负载 {s.load[0]:.2f} {s.load[1]:.2f} {s.load[2]:.2f}")


def _tile_mem(ui, cr, s, x, y, w, h):
    _tile_title(cr, "内存", f"{s.mem_pct:.0f}%", x, y, w, T.mem,
                f"{D.fmt_bytes(s.mem_used)} / {D.fmt_bytes(s.mem_total)}")
    D.sparkline(cr, x, y + 34, w, h - 34 - 26, s.hist.get("mem", []), T.mem, vmax=100)
    D.bar(cr, x, y + h - 16, w, 6, s.mem_pct / 100, T.mem)
    if s.swap_total:
        D.text(cr, f"交换 {D.fmt_bytes(s.swap_used)} / {D.fmt_bytes(s.swap_total)}", x, y + h - 4, 9.5, T.fg3)


def _tile_gpu(ui, cr, s, x, y, w, h):
    g = s.gpu
    if not g:
        _tile_title(cr, "GPU", None, x, y, w, T.gpu)
        D.text(cr, "未检测到 nvidia-smi", x, y + 30, 11, T.fg3)
        return
    _tile_title(cr, "GPU", f"{(g.get('util') or 0):.0f}%", x, y, w, T.gpu, g.get("name"))
    D.sparkline(cr, x, y + 34, w, h - 34 - 40, s.hist.get("gpu", []), T.gpu, vmax=100)
    mu, mt = g.get("mem_used") or 0, g.get("mem_total") or 0
    if mt:
        D.text(cr, f"显存 {D.fmt_bytes(mu)} / {D.fmt_bytes(mt)}", x, y + h - 30, 10, T.fg2)
        D.bar(cr, x, y + h - 14, w, 5, mu / mt, D.with_alpha(T.gpu, 0.8))
    parts = []
    if g.get("temp") is not None:
        parts.append(f"{g['temp']:.0f}°C")
    if g.get("power") is not None:
        parts.append(f"{g['power']:.0f}W")
    if parts:
        D.text(cr, " · ".join(parts), x + w, y + h - 30, 10, T.fg2, align="right")


def _tile_net(ui, cr, s, x, y, w, h):
    D.text(cr, "网络", x, y, 11, T.fg3, "medium")
    D.text(cr, "↓ " + D.fmt_bytes(s.net_down, True), x + w, y - 2, 13, T.net_down, "semibold", align="right")
    D.text(cr, "↑ " + D.fmt_bytes(s.net_up, True), x + w, y + 16, 11.5, T.net_up, "medium", align="right")
    down, up = s.hist.get("down", []), s.hist.get("up", [])
    top = max(max(down or [1]), max(up or [1]), 1024)
    D.sparkline(cr, x, y + 40, w, h - 40, down, T.net_down, vmax=top)
    D.sparkline(cr, x, y + 40, w, h - 40, up, T.net_up, vmax=top, fill=False)


def _tile_disk(ui, cr, s, x, y, w, h):
    D.text(cr, "磁盘", x, y, 11, T.fg3, "medium")
    D.text(cr, f"读 {D.fmt_bytes(s.disk_read, True)}  写 {D.fmt_bytes(s.disk_write, True)}", x + w, y, 10, T.fg2, align="right")
    dy = y + 24
    for mount, used, total, pct in s.disks[: max(1, int((h - 24) // 30))]:
        D.text(cr, mount, x, dy, 10.5, T.fg2, max_w=w * 0.5)
        D.text(cr, f"{D.fmt_bytes(used)} / {D.fmt_bytes(total)}", x + w, dy, 10, T.fg3, align="right")
        D.bar(cr, x, dy + 16, w, 5, pct / 100, D.level_color(pct, T.disk, 85, 95))
        dy += 30
    if not s.disks:
        D.text(cr, "读取中…", x, dy, 11, T.fg3)


def _tile_sys(ui, cr, s, x, y, w, h):
    D.text(cr, "温度与负载", x, y, 11, T.fg3, "medium")
    rows = []
    if s.cpu_temp is not None:
        rows.append(("CPU", f"{s.cpu_temp:.0f}°C", D.level_color(s.cpu_temp, T.green, 80, 92), s.cpu_temp / 100))
    if s.gpu and s.gpu.get("temp") is not None:
        rows.append(("GPU", f"{s.gpu['temp']:.0f}°C", D.level_color(s.gpu["temp"], T.green, 80, 90), s.gpu["temp"] / 100))
    if s.nvme_temp is not None:
        rows.append(("SSD", f"{s.nvme_temp:.0f}°C", D.level_color(s.nvme_temp, T.green, 65, 75), s.nvme_temp / 100))
    dy = y + 22
    for label, val, col, frac in rows:
        D.text(cr, label, x, dy, 11, T.fg2)
        D.text(cr, val, x + w, dy, 11.5, col, "semibold", align="right")
        D.bar(cr, x + 36, dy + 6, w - 36 - 46, 4, frac, col)
        dy += 20
    up = int(s.uptime)
    D.text(cr, f"负载 {s.load[0]:.2f}  ·  运行 {up // 86400}天{up % 86400 // 3600}小时", x, y + h - 12, 10, T.fg3, max_w=w)


def _processes(ui, cr, s, x, y, w, h):
    D.fill_rounded(cr, x, y, w, h, 14, T.card)
    pad = 12
    D.text(cr, "占用最高的应用", x + pad, y + 10, 11, T.fg3, "medium")
    sort_mem = ui.proc_sort == "mem"
    for i, (key, label) in enumerate((("cpu", "CPU"), ("mem", "内存"))):
        bx = x + w - pad - 78 + i * 40
        active = ui.proc_sort == key
        D.fill_rounded(cr, bx, y + 8, 36, 18, 9, (1, 1, 1, 0.14 if active else 0.04))
        D.text(cr, label, bx + 18, y + 17, 10, T.fg if active else T.fg3, "medium", align="center", valign="middle")

        def setsort(k=key):
            ui.proc_sort = k
            ui.redraw()
        ui.hot(bx, y + 8, 36, 18, ("psort", key), setsort)
    rows = sorted(s.procs, key=(lambda r: -r[2]) if sort_mem else (lambda r: -r[1]))
    py = y + 36
    n = int((h - 40) // 22)
    top = max([r[2] if sort_mem else r[1] for r in rows[:1]] + [1e-9])
    for name, cpu, rss, pid, count in rows[:n]:
        val = rss if sort_mem else cpu
        D.fill_rounded(cr, x + pad - 4, py - 2, max(6, (w - 2 * pad + 8) * val / top), 19, 4,
                       D.with_alpha(T.mem if sort_mem else T.cpu, 0.12))
        label = name + (f" ({count})" if count > 1 else "")
        D.text(cr, label, x + pad, py, 11, T.fg2, max_w=w - 2 * pad - 92)
        D.text(cr, f"{cpu:.1f}%", x + w - pad - 52, py, 10.5, T.fg if not sort_mem else T.fg3, align="right")
        D.text(cr, D.fmt_bytes(rss), x + w - pad, py, 10.5, T.fg if sort_mem else T.fg3, align="right")
        py += 22


# ====================================================================== clipboard + file shelf

def draw_clipboard(ui, cr, x, y, w, h):
    cb = ui.clip
    lw = w * 0.60
    _clip_list(ui, cr, cb, x, y, lw, h)
    _shelf(ui, cr, cb, x + lw + 12, y, w - lw - 12, h)


def _small_button(ui, cr, label, x, y, key, cb):
    tw, _ = D.text_size(cr, label, 10.5)
    hov = ui.hover == key
    D.fill_rounded(cr, x - tw - 16, y, tw + 16, 20, 10, (1, 1, 1, 0.12 if hov else 0.05))
    D.text(cr, label, x - tw - 8, y + 10, 10.5, T.fg if hov else T.fg2, valign="middle")
    ui.hot(x - tw - 16, y, tw + 16, 20, key, cb)
    return tw + 16


def _clip_list(ui, cr, cb, x, y, w, h):
    D.fill_rounded(cr, x, y, w, h, 14, T.card)
    pad = 12
    D.text(cr, f"剪贴板  {len(cb.items)}", x + pad, y + 12, 11, T.fg3, "medium")
    if cb.items:
        _small_button(ui, cr, "清空", x + w - pad, y + 8, ("clipclear", 0), cb.clear)
    top = y + 36
    area_h = h - 40
    row_h = 46
    items = cb.items
    if not items:
        D.text(cr, "复制的文字和图片会出现在这里", x + w / 2, y + h / 2, 12, T.fg3, align="center", valign="middle")
        D.text(cr, "点击一条即可粘贴到当前窗口", x + w / 2, y + h / 2 + 20, 11, T.fg3, align="center", valign="middle")
        return
    max_scroll = max(0, len(items) * row_h - area_h)
    ui.clip_scroll = max(0, min(ui.clip_scroll, max_scroll))
    ui.scroll_area(x, top, w, area_h, "clip", max_scroll)
    cr.save()
    cr.rectangle(x, top, w, area_h)
    cr.clip()
    first = int(ui.clip_scroll // row_h)
    for idx in range(first, min(len(items), first + int(area_h // row_h) + 2)):
        c = items[idx]
        ry = top + idx * row_h - ui.clip_scroll
        rx, rw = x + 6, w - 12
        key = ("clip", c.id)
        hov = ui.hover and ui.hover[0] in ("clip", "clippin", "clipdel") and ui.hover[1] == c.id
        if hov:
            D.fill_rounded(cr, rx, ry + 2, rw, row_h - 4, 10, (1, 1, 1, 0.07))
        tx = rx + 10
        if c.kind == "image":
            pb = cb.thumb(c, int(36 * ui.S))
            if pb:
                D.pixbuf_rounded(cr, pb, tx, ry + 5, 36, 36, 6)
            tx += 46
            D.text(cr, "图片  " + c.text, tx, ry + row_h / 2, 12, T.fg2, valign="middle")
        elif c.kind == "color":
            col = parse_color(c.text)
            if col:
                D.fill_rounded(cr, tx, ry + 11, 24, 24, 6, col)
                D.rounded_rect(cr, tx, ry + 11, 24, 24, 6)
                D.set_color(cr, T.border)
                cr.set_line_width(1)
                cr.stroke()
            tx += 34
            D.text(cr, c.text.strip(), tx, ry + row_h / 2, 12.5, T.fg, "medium", valign="middle", mono=True)
        else:
            one = " ".join(c.text.split())
            color = {"link": T.teal, "code": T.fg}.get(c.kind, T.fg)
            if c.kind == "code":
                D.fill_rounded(cr, tx - 4, ry + 6, rw - 70 - (tx - rx) + 4, row_h - 12, 6, (1, 1, 1, 0.04))
                first_lines = "\n".join(c.text.strip("\n").splitlines()[:2])
                D.text(cr, first_lines, tx + 2, ry + 9, 10.5, T.fg2, max_w=rw - 80 - (tx - rx), mono=True, lines=2)
            else:
                mw = rw - 74 - (tx - rx)
                _, lh = D.layout(cr, one, 12, max_w=mw, lines=2).get_pixel_size()
                D.text(cr, one, tx, ry + (row_h - lh) / 2, 12, color, max_w=mw, lines=2)
        # right side: time / actions
        if hov:
            ax = rx + rw - 28
            for akey, ic, fn, tip in (("clipdel", "user-trash-symbolic", lambda c=c: cb.remove(c), "删除"),
                                      ("clippin", "starred-symbolic" if c.pinned else "non-starred-symbolic",
                                       lambda c=c: cb.toggle_pin(c), "收藏（退出时保留）")):
                ahov = ui.hover == (akey, c.id)
                if ahov:
                    D.fill_rounded(cr, ax, ry + row_h / 2 - 12, 24, 24, 12, (1, 1, 1, 0.12))
                D.icon(cr, ic, ax + 5, ry + row_h / 2 - 7, 14, T.yellow if akey == "clippin" and c.pinned else T.fg2, ui.S)
                ui.hot(ax, ry + row_h / 2 - 12, 24, 24, (akey, c.id), fn, tip)
                ax -= 28
        else:
            if c.pinned:
                D.icon(cr, "starred-symbolic", rx + rw - 22, ry + row_h / 2 - 6, 12, T.yellow, ui.S)
            D.text(cr, _ago_short(c.time), rx + rw - (30 if c.pinned else 10), ry + row_h / 2, 10, T.fg3, align="right", valign="middle")
        ui.hot(rx, max(ry, top), rw, min(row_h, top + area_h - max(ry, top)), key, lambda c=c: ui.paste_clip(c),
               "点击粘贴 · 右键仅复制" if ui.config["paste_on_click"] else "点击复制", right=lambda c=c: ui.copy_clip(c))
    cr.restore()
    if max_scroll > 0:
        frac = area_h / (len(items) * row_h)
        sh = max(20, area_h * frac)
        sy = top + (area_h - sh) * (ui.clip_scroll / max_scroll)
        D.fill_rounded(cr, x + w - 5, sy, 3, sh, 1.5, (1, 1, 1, 0.18))


def _ago_short(ts):
    s = time.time() - ts
    if s < 60:
        return "刚刚"
    if s < 3600:
        return f"{int(s // 60)}分钟前"
    if s < 86400:
        return f"{int(s // 3600)}小时前"
    return dt.datetime.fromtimestamp(ts).strftime("%m-%d")


def _shelf(ui, cr, cb, x, y, w, h):
    D.fill_rounded(cr, x, y, w, h, 14, T.card)
    pad = 12
    D.text(cr, f"文件中转  {len(cb.files)}", x + pad, y + 12, 11, T.fg3, "medium")
    if cb.files:
        _small_button(ui, cr, "清空", x + w - pad, y + 8, ("shelfclear", 0), cb.clear_files)
    top = y + 36
    if not cb.files:
        zx, zy, zw, zh = x + pad, top + 4, w - 2 * pad, h - 52
        cr.save()
        D.rounded_rect(cr, zx, zy, zw, zh, 12)
        cr.set_dash([5, 4])
        cr.set_line_width(1.2)
        D.set_color(cr, T.accent if ui.dnd_over else (1, 1, 1, 0.18))
        cr.stroke()
        cr.restore()
        D.icon(cr, "document-send-symbolic", zx + zw / 2 - 14, zy + zh / 2 - 36, 28, T.fg3, ui.S)
        D.text(cr, "把文件拖到这里暂存", zx + zw / 2, zy + zh / 2 + 6, 12, T.fg2, align="center", valign="middle")
        D.text(cr, "拖出或点击即可粘贴，只记录位置", zx + zw / 2, zy + zh / 2 + 26, 10.5, T.fg3, align="center", valign="middle")
        return
    row_h = 40
    area_h = h - 40
    max_scroll = max(0, len(cb.files) * row_h - area_h)
    ui.shelf_scroll = max(0, min(ui.shelf_scroll, max_scroll))
    ui.scroll_area(x, top, w, area_h, "shelf", max_scroll)
    cr.save()
    cr.rectangle(x, top, w, area_h)
    cr.clip()
    for idx, f in enumerate(cb.files):
        ry = top + idx * row_h - ui.shelf_scroll
        if ry + row_h < top or ry > top + area_h:
            continue
        rx, rw = x + 6, w - 12
        hov = ui.hover and ui.hover[0] in ("file", "filedel") and ui.hover[1] == f.path
        if hov:
            D.fill_rounded(cr, rx, ry + 2, rw, row_h - 4, 10, (1, 1, 1, 0.07))
        ic = "folder-symbolic" if f.is_dir else "text-x-generic-symbolic"
        D.icon(cr, ic, rx + 10, ry + row_h / 2 - 9, 18, T.fg2 if f.exists else T.red, ui.S)
        D.text(cr, f.name, rx + 38, ry + 5, 12, T.fg if f.exists else T.fg3, max_w=rw - 80)
        meta = ("文件夹" if f.is_dir else D.fmt_bytes(f.size)) if f.exists else "已移动或删除"
        D.text(cr, meta + "  ·  " + os.path.dirname(f.path).replace(os.path.expanduser("~"), "~"),
               rx + 38, ry + 22, 10, T.fg3, max_w=rw - 80)
        if hov:
            dhov = ui.hover == ("filedel", f.path)
            if dhov:
                D.fill_rounded(cr, rx + rw - 30, ry + row_h / 2 - 12, 24, 24, 12, (1, 1, 1, 0.12))
            D.icon(cr, "window-close-symbolic", rx + rw - 25, ry + row_h / 2 - 7, 14, T.fg2, ui.S)
            ui.hot(rx + rw - 30, ry + row_h / 2 - 12, 24, 24, ("filedel", f.path), lambda f=f: cb.remove_file(f), "移出中转站")
        ui.hot(rx, max(ry, top), rw, min(row_h, top + area_h - max(ry, top)), ("file", f.path),
               lambda f=f: ui.paste_files([f]), "点击粘贴 · 拖出到其他应用", drag=f)
    cr.restore()


# ====================================================================== devices

def draw_devices(ui, cr, x, y, w, h):
    devs = ui.devices.devices if ui.devices else []
    if not devs:
        D.icon(cr, "battery-good-symbolic", x + w / 2 - 20, y + h / 2 - 60, 40, T.fg3, ui.S)
        D.text(cr, "没有检测到带电量的设备", x + w / 2, y + h / 2, 15, T.fg2, "medium", align="center", valign="middle")
        D.text(cr, "通过 UPower 读取：蓝牙鼠标 / 键盘 / 耳机 / 手柄，罗技设备需 Solaar 或内核 hid-logitech 驱动",
               x + w / 2, y + h / 2 + 26, 11.5, T.fg3, align="center", valign="middle")
        return
    n = len(devs)
    per_row = min(n, 5)
    cw = min(150, (w - 12 * (per_row - 1)) / per_row)
    total = per_row * cw + 12 * (per_row - 1)
    sx = x + (w - total) / 2
    rows = math.ceil(n / per_row)
    ch = min(170, (h - 12 * (rows - 1)) / rows)
    sy = y + (h - (rows * ch + 12 * (rows - 1))) / 2
    warn, low = ui.config["battery_warn"], ui.config["battery_low"]
    for i, d in enumerate(devs):
        cx = sx + (i % per_row) * (cw + 12)
        cy = sy + (i // per_row) * (ch + 12)
        D.fill_rounded(cr, cx, cy, cw, ch, 16, T.card)
        col = T.red if d.percent <= low else T.orange if d.percent <= warn else T.green
        rr = min(38, ch * 0.24)
        rcx, rcy = cx + cw / 2, cy + 16 + rr + 4
        D.ring(cr, rcx, rcy, rr, 5, d.percent / 100, col)
        D.icon(cr, d.icon, rcx - 9, rcy - 18, 18, T.fg2, ui.S)
        D.text(cr, f"{d.percent:.0f}%", rcx, rcy + 9, 14, T.fg, "semibold", align="center", valign="middle")
        D.text(cr, d.model, cx + 10, rcy + rr + 14, 12, T.fg, "medium", max_w=cw - 20, align="center")
        state = "充电中" if d.charging else "已充满" if d.full else d.kind_name
        D.text(cr, state, cx + 10, rcy + rr + 32, 10.5, T.yellow if d.charging else T.fg3, max_w=cw - 20, align="center")


PAGES = {"usage": draw_usage, "music": draw_music, "monitor": draw_monitor,
         "clipboard": draw_clipboard, "devices": draw_devices}

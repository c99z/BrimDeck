"""Read the personal quota pools shown by chatapi.xiaoyu.ai."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from http.client import HTTPException
from pathlib import Path
import time
import urllib.error
import urllib.request

from .config import CONFIG_DIR

TOKEN_FILE = Path(CONFIG_DIR) / "chatapi.token"
BEIJING = timezone(timedelta(hours=8))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get(path, token=None):
    headers = {"Accept": "application/json", "User-Agent": "brimdeck-linux"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://chatapi.xiaoyu.ai" + path, headers=headers)
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
        payload = json.load(response)
    if payload.get("success") is not True:
        raise ValueError("request rejected")
    return payload["data"]


@dataclass(frozen=True)
class Pool:
    title: str
    client: str
    hours: str
    used: float
    limit: float
    reset: str


def _pool(item, units):
    sub = item["subscription"]
    clients = item.get("supported_clients") or []
    names = {"claude-code": "Claude Code", "codex": "Codex"}
    hours = []
    for period in item.get("active_periods") or []:
        start, end = period["start"], period["end"]
        hours.append(f"{start}–{end}" + ("（次日）" if end < start else ""))
    reset = sub.get("next_reset_time")
    return Pool(
        item["plan_title"], "、".join(names.get(c, c) for c in clients),
        "、".join(hours) or "全天",
        sub["amount_used"] / units, sub["amount_total"] / units,
        datetime.fromtimestamp(reset, BEIJING).strftime("%Y-%m-%d %H:%M") if reset else "—",
    )


@dataclass(frozen=True)
class Balance:
    pools: tuple = ()
    updated: float = 0
    status: str = "读取中…"


def read_balance(previous):
    try:
        token = TOKEN_FILE.read_text(encoding="ascii").strip()
        if not token:
            return Balance(status="请配置 ChatAPI 令牌")
        units = _get("/api/status")["quota_per_unit"]
        if units <= 0:
            raise ValueError("invalid quota unit")
        items = _get("/api/subscription/self", token)["subscriptions"]
        pools = [_pool(item, units) for item in (items or [])]
        pools.sort(key=lambda p: (bool(p.client), p.reset, p.title))
        return Balance(tuple(pools), time.time(), "")
    except FileNotFoundError:
        return None
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return Balance(status="令牌无效或无权读取额度")
        message = f"刷新失败（HTTP {exc.code}）"
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        message = "无法读取 ChatAPI 额度"
    return Balance(previous.pools, previous.updated, message) if previous else Balance(status=message)

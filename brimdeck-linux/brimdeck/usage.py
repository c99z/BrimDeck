"""AI coding tool usage: Claude Code and Codex.

Tokens and estimated cost come from the session logs these tools keep on this machine
(~/.claude/projects/**/*.jsonl and ~/.codex/sessions/**/*.jsonl), read incrementally.
Quotas come from each account's usage endpoint with the sign-in the tool already keeps
(the token is only used in memory, never written anywhere); Codex falls back to the
rate limits its latest session log recorded.
"""
import datetime as dt
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import chatapi
from .pricing import Pricing

HOME = os.path.expanduser("~")
DAYS = 7


def _claude_dirs():
    dirs = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    for base in ([env] if env else []) + [os.path.join(HOME, ".claude"), os.path.join(HOME, ".config", "claude")]:
        p = os.path.join(base, "projects")
        if os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    return dirs


def _claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")


def _codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")


def _iso(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class Quota:
    __slots__ = ("label", "percent", "reset_at", "window_min")

    def __init__(self, label, percent, reset_at=None, window_min=None):
        self.label, self.percent, self.reset_at, self.window_min = label, percent, reset_at, window_min


class Totals:
    __slots__ = ("tokens", "cost", "unpriced", "input", "output", "cache")

    def __init__(self):
        self.tokens = 0
        self.cost = 0.0
        self.unpriced = 0
        self.input = self.output = self.cache = 0


class ProviderState:
    def __init__(self, pid, name):
        self.id = pid
        self.name = name
        self.plan = ""
        self.status = "读取中…"
        self.kind = "loading"  # loading | online | local | stale | error
        self.quotas = []
        self.quota_time = None
        self.today = Totals()
        self.week = Totals()
        self.days = [0] * DAYS  # tokens, oldest first, last = today
        self.day_costs = [0.0] * DAYS
        self.models_today = []
        self.models_week = []
        self.scanned = None

    @property
    def peak(self):
        vals = [q.percent for q in self.quotas if q.percent is not None]
        return max(vals) if vals else None


class _File:
    __slots__ = ("size", "mtime", "offset", "entries", "model", "last_total", "quota", "quota_ts")

    def __init__(self):
        self.size = self.mtime = self.offset = 0
        self.entries = {}
        self.model = "unknown"
        self.last_total = None
        self.quota = None
        self.quota_ts = 0


class UsageService:
    def __init__(self, config, on_update):
        self.config = config
        self.on_update = on_update
        self.pricing = Pricing()
        self.files = {"claude": {}, "codex": {}}
        self.states = {"claude": ProviderState("claude", "Claude"), "codex": ProviderState("codex", "Codex")}
        self.states["chatapi"] = chatapi.Balance() if chatapi.TOKEN_FILE.exists() else None
        self._wake = threading.Event()
        self._stop = False
        self._next_quota = 0
        self._quota_backoff = {}
        self._force_quota = False
        self.thread = threading.Thread(target=self._run, name="usage", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop = True
        self._wake.set()

    def refresh(self, quotas=True):
        if quotas:
            self._force_quota = True
        self._wake.set()

    # ------------------------------------------------------------------ loop
    def _run(self):
        first = True
        while not self._stop:
            try:
                if first:
                    # Show quotas quickly, before the first (slower) log scan.
                    self.states["chatapi"] = chatapi.read_balance(self.states["chatapi"])
                    self._publish()
                    self._read_quotas()
                    self._publish()
                self.pricing.refresh()
                self._scan()
                now = time.time()
                if self._force_quota or now >= self._next_quota:
                    if not first or self._force_quota:
                        self.states["chatapi"] = chatapi.read_balance(self.states["chatapi"])
                        self._publish()
                    self._force_quota = False
                    self._read_quotas()
                    self._next_quota = now + max(60, int(self.config["quota_refresh_s"]))
                self._publish()
            except Exception as e:  # keep the service alive
                import traceback
                traceback.print_exc()
                print("brimdeck: usage refresh failed:", e)
            first = False
            self._wake.wait(max(15, int(self.config["usage_refresh_s"])))
            self._wake.clear()

    def _publish(self):
        self.on_update(self.states)

    # ------------------------------------------------------------------ logs
    def _cutoff(self):
        today = dt.datetime.combine(dt.date.today(), dt.time())
        return (today - dt.timedelta(days=DAYS)).timestamp()

    def _walk(self, roots, cutoff):
        for root in roots:
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    if n.endswith(".jsonl"):
                        p = os.path.join(dirpath, n)
                        try:
                            st = os.stat(p)
                        except OSError:
                            continue
                        if st.st_mtime >= cutoff:
                            yield p, st

    def _scan(self):
        cutoff = self._cutoff()
        prov = self.config["providers"]
        if prov.get("claude", True):
            self._scan_kind("claude", _claude_dirs(), cutoff, self._parse_claude, (b'"usage"',))
        if prov.get("codex", True):
            roots = [os.path.join(_codex_home(), "sessions"), os.path.join(_codex_home(), "archived_sessions")]
            self._scan_kind("codex", [r for r in roots if os.path.isdir(r)], cutoff, self._parse_codex,
                            (b"token_count", b"turn_context"))
        for pid in ("claude", "codex"):
            self._aggregate(pid, cutoff)

    def _scan_kind(self, kind, roots, cutoff, parser, needles):
        cache = self.files[kind]
        seen = set()
        for path, st in self._walk(roots, cutoff):
            seen.add(path)
            fc = cache.get(path)
            if fc is None or st.st_size < fc.offset:
                fc = cache[path] = _File()
            if st.st_size == fc.offset and st.st_mtime == fc.mtime:
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(fc.offset)
                    while True:
                        data = f.read(8 << 20)
                        if not data:
                            break
                        end = data.rfind(b"\n")
                        if end < 0:
                            if len(data) < (8 << 20):
                                break
                            f.seek(fc.offset)  # one line longer than a chunk: read it whole
                            data = f.readline()
                            end = len(data) - 1
                            if not data.endswith(b"\n"):
                                break
                        for line in data[: end + 1].split(b"\n"):
                            if line and any(n in line for n in needles):
                                parser(line, fc, cutoff)
                        fc.offset += end + 1
                        f.seek(fc.offset)
            except OSError:
                continue
            fc.size, fc.mtime = st.st_size, st.st_mtime
        for path in list(cache):
            if path not in seen:
                del cache[path]

    @staticmethod
    def _parse_claude(line, fc, cutoff):
        if b'"assistant"' not in line:
            return
        try:
            o = json.loads(line)
        except ValueError:
            return
        if o.get("type") != "assistant":
            return
        msg = o.get("message") or {}
        u = msg.get("usage")
        if not isinstance(u, dict):
            return
        ts = _iso(o.get("timestamp"))
        if ts is None or ts < cutoff:
            return
        model = msg.get("model") or "unknown"
        if model == "<synthetic>":
            return
        creation = _int(u.get("cache_creation_input_tokens"))
        hour = min(creation, _int((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens")))
        e = (ts, model, _int(u.get("input_tokens")), _int(u.get("cache_read_input_tokens")),
             creation - hour, hour, _int(u.get("output_tokens")))
        key = msg.get("id") or o.get("uuid") or f"{ts}:{sum(e[2:])}"
        old = fc.entries.get(key)
        if old is None or sum(e[2:]) >= sum(old[2:]):
            fc.entries[key] = e

    @staticmethod
    def _parse_codex(line, fc, cutoff):
        try:
            o = json.loads(line)
        except ValueError:
            return
        typ = o.get("type")
        p = o.get("payload") or {}
        if typ == "turn_context":
            fc.model = p.get("model") or fc.model
            return
        if typ != "event_msg" or p.get("type") != "token_count":
            return
        ts = _iso(o.get("timestamp"))
        if ts is None:
            return
        rl = p.get("rate_limits")
        if isinstance(rl, dict) and rl.get("limit_id") in (None, "", "codex") and ts >= fc.quota_ts:
            fc.quota, fc.quota_ts = rl, ts
        info = p.get("info") or {}
        total = info.get("total_token_usage")
        ident = json.dumps(total, sort_keys=True) if isinstance(total, dict) else ""
        if ident:
            if ident == fc.last_total:
                return
            fc.last_total = ident
        u = info.get("last_token_usage")
        if not isinstance(u, dict) or ts < cutoff:
            return
        inp = _int(u.get("input_tokens"))
        cached = min(inp, _int(u.get("cached_input_tokens")))
        write = _int(u.get("cache_write_input_tokens"))
        out = _int(u.get("output_tokens"))
        key = o.get("timestamp", "") + hashlib.sha1((ident + json.dumps(u, sort_keys=True)).encode()).hexdigest()[:16]
        fc.entries[key] = (ts, fc.model, inp - cached, cached, write, 0, out)

    def _aggregate(self, pid, cutoff):
        st = self.states[pid]
        merged = {}
        for fc in self.files[pid].values():
            for k, e in fc.entries.items():
                old = merged.get(k)
                if old is None or sum(e[2:]) > sum(old[2:]):
                    merged[k] = e
        today0 = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        week0 = (dt.datetime.combine(dt.date.today(), dt.time()) - dt.timedelta(days=DAYS - 1)).timestamp()
        today, week = Totals(), Totals()
        days, day_costs = [0] * DAYS, [0.0] * DAYS
        mt, mw = {}, {}
        for ts, model, inp, cr, w5, w1, out in merged.values():
            if ts < week0:
                continue
            tok = inp + cr + w5 + w1 + out
            if tok <= 0:
                continue
            cost = self.pricing.cost(model, inp, cr, w5, w1, out)
            before = 0 if ts >= today0 else int((today0 - ts) // 86400) + 1
            idx = max(0, DAYS - 1 - before)
            days[idx] += tok
            day_costs[idx] += cost or 0
            targets = [(week, mw)] + ([(today, mt)] if ts >= today0 else [])
            for tot, md in targets:
                tot.tokens += tok
                tot.input += inp
                tot.output += out
                tot.cache += cr + w5 + w1
                if cost is None:
                    tot.unpriced += 1
                else:
                    tot.cost += cost
                name = Pricing.normalize(model)
                m = md.setdefault(name, [0, 0.0])
                m[0] += tok
                m[1] += cost or 0
        st.today, st.week, st.days, st.day_costs = today, week, days, day_costs
        st.models_today = sorted(((k, v[0], v[1]) for k, v in mt.items()), key=lambda x: (-x[2], -x[1]))
        st.models_week = sorted(((k, v[0], v[1]) for k, v in mw.items()), key=lambda x: (-x[2], -x[1]))
        st.scanned = time.time()

    # ------------------------------------------------------------------ quotas
    def _read_quotas(self):
        prov = self.config["providers"]
        if prov.get("codex", True):
            self._read_codex()
        if prov.get("claude", True):
            self._read_claude()

    def _http_json(self, url, headers):
        req = urllib.request.Request(url, headers={"User-Agent": "brimdeck-linux", "Accept": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def _backoff(self, pid):
        return time.time() < self._quota_backoff.get(pid, 0)

    def _read_codex(self):
        st = self.states["codex"]
        err = None
        if self.config["codex_online"] and not self._backoff("codex"):
            try:
                with open(os.path.join(_codex_home(), "auth.json"), "r", encoding="utf-8") as f:
                    tokens = (json.load(f) or {}).get("tokens") or {}
                token = tokens.get("access_token") or ""
                if token:
                    headers = {"Authorization": "Bearer " + token}
                    if tokens.get("account_id"):
                        headers["ChatGPT-Account-Id"] = tokens["account_id"]
                    d = self._http_json("https://chatgpt.com/backend-api/wham/usage", headers)
                    quotas = self._codex_quotas(d.get("rate_limit") or {}, online=True)
                    if quotas:
                        st.quotas, st.quota_time = quotas, time.time()
                        st.plan = _plan_name(d.get("plan_type"))
                        st.kind, st.status = "online", "Codex 账户"
                        return
                    err = "账户接口未返回额度"
                else:
                    err = "未登录 Codex"
            except FileNotFoundError:
                err = "未找到 Codex 登录"
            except urllib.error.HTTPError as e:
                err = {401: "登录已失效", 403: "访问受限", 429: "请求频繁"}.get(e.code, f"接口错误 {e.code}")
                if e.code == 429:
                    self._quota_backoff["codex"] = time.time() + 900
            except Exception as e:
                err = "网络不可用"
                print("brimdeck: codex quota:", e)
        # Fallback: the latest rate limits Codex recorded in its session logs.
        best = None
        for fc in self.files["codex"].values():
            if fc.quota and (best is None or fc.quota_ts > best.quota_ts):
                best = fc
        if best is None:
            best = self._latest_codex_log_quota()
        if best is not None:
            quotas = self._codex_quotas(best.quota, online=False)
            if quotas:
                st.quotas, st.quota_time = quotas, best.quota_ts
                st.plan = st.plan or _plan_name(best.quota.get("plan_type"))
                st.kind = "stale"
                st.status = "本地会话记录" + (f"（{err}）" if err else "")
                return
        if st.kind != "online" or err:
            st.kind = "error" if err else "local"
            st.status = err or "暂无额度信息"

    def _latest_codex_log_quota(self):
        """Before the first scan: read just the newest session file's last rate limits."""
        newest = None
        for p, s in self._walk([os.path.join(_codex_home(), "sessions")], time.time() - 3 * 86400):
            if newest is None or s.st_mtime > newest[1]:
                newest = (p, s.st_mtime)
        if not newest:
            return None
        fc = _File()
        try:
            with open(newest[0], "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 400_000))
                for line in f.read().split(b"\n"):
                    if b"rate_limits" in line:
                        self._parse_codex(line, fc, 0)
        except OSError:
            return None
        return fc if fc.quota else None

    @staticmethod
    def _codex_quotas(limits, online):
        out = []
        now = time.time()
        for field in (("primary_window", "secondary_window") if online else ("primary", "secondary")):
            w = limits.get(field)
            if not isinstance(w, dict) or w.get("used_percent") is None:
                continue
            if online:
                minutes = int(w["limit_window_seconds"] / 60) if w.get("limit_window_seconds") else None
                reset = w.get("reset_at")
                if reset is None and w.get("reset_after_seconds") is not None:
                    reset = now + float(w["reset_after_seconds"])
            else:
                minutes = int(w["window_minutes"]) if w.get("window_minutes") else None
                reset = w.get("resets_at")
            out.append(Quota(_window_label(minutes, field), max(0.0, min(100.0, float(w["used_percent"]))),
                             float(reset) if reset else None, minutes))
        return out

    def _read_claude(self):
        st = self.states["claude"]
        cred = None
        try:
            with open(os.path.join(_claude_home(), ".credentials.json"), "r", encoding="utf-8") as f:
                cred = (json.load(f) or {}).get("claudeAiOauth") or None
        except (OSError, ValueError):
            cred = None
        if not cred or not cred.get("accessToken"):
            st.quotas = []
            st.kind = "local"
            st.status = "仅本地统计（未找到 Claude 订阅登录）"
            st.plan = st.plan or ""
            return
        st.plan = _claude_plan(cred.get("subscriptionType"), cred.get("rateLimitTier"))
        expires = cred.get("expiresAt")
        if expires and float(expires) / 1000 < time.time():
            st.kind, st.status = ("stale" if st.quotas else "error"), "登录已过期，打开 Claude Code 后会自动续期"
            return
        if not self.config["claude_online"] or self._backoff("claude"):
            return
        try:
            d = self._http_json("https://api.anthropic.com/api/oauth/usage",
                                {"Authorization": "Bearer " + cred["accessToken"], "anthropic-beta": "oauth-2025-04-20"})
            quotas = _claude_quotas(d)
            if quotas:
                st.quotas, st.quota_time = quotas, time.time()
                st.kind, st.status = "online", "Claude 账户"
            else:
                st.kind, st.status = "error", "账户接口未返回额度"
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self._quota_backoff["claude"] = time.time() + 900
            st.kind = "stale" if st.quotas else "error"
            st.status = {401: "登录已失效", 403: "访问受限", 429: "请求频繁，稍后重试"}.get(e.code, f"接口错误 {e.code}")
        except Exception as e:
            print("brimdeck: claude quota:", e)
            st.kind = "stale" if st.quotas else "error"
            st.status = "网络不可用"


def _window_label(minutes, field=""):
    if minutes == 300:
        return "5 小时额度"
    if minutes == 10080:
        return "每周额度"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440} 天额度"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60} 小时额度"
    if minutes:
        return f"{minutes} 分钟额度"
    return "主要额度" if field.startswith("primary") else "次要额度"


def _plan_name(p):
    if not p:
        return ""
    names = {"prolite": "Pro Lite", "pro": "Pro", "plus": "Plus", "team": "Team", "free": "Free",
             "enterprise": "Enterprise", "business": "Business", "edu": "Edu"}
    return names.get(str(p).lower(), str(p).replace("_", " ").title())


def _claude_plan(sub, tier):
    sub = (sub or "").lower()
    tier = (tier or "").lower()
    if "max" in sub or "max" in tier:
        for k in ("20x", "5x"):
            if k in tier:
                return f"Max {k}"
        return "Max"
    return {"pro": "Pro", "team": "Team", "enterprise": "Enterprise", "free": "Free"}.get(sub, sub.title())


def _claude_quotas(root):
    out = []

    def add(label, pct, reset, window):
        if pct is None or any(q.label == label for q in out):
            return
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return
        if pct < 0:
            return
        out.append(Quota(label, min(100.0, pct), _iso(reset) if isinstance(reset, str) else reset, window))

    for key, label, win in (("five_hour", "5 小时额度", 300), ("seven_day", "每周额度", 10080)):
        v = root.get(key) or {}
        add(label, v.get("utilization"), v.get("resets_at"), win)
    for lim in root.get("limits") or []:
        kind, group = lim.get("kind", ""), lim.get("group", "")
        scope = lim.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name")) or ((scope.get("surface") or {}).get("display_name")) or ""
        if kind == "session":
            add("5 小时额度", lim.get("percent"), lim.get("resets_at"), 300)
        elif kind == "weekly_all":
            add("每周额度", lim.get("percent"), lim.get("resets_at"), 10080)
        elif model:
            weekly = group == "weekly" or kind == "weekly_scoped"
            add(model + (" 每周" if weekly else ""), lim.get("percent"), lim.get("resets_at"), 10080 if weekly else None)
    for key, name in (("seven_day_opus", "Opus"), ("seven_day_sonnet", "Sonnet")):
        if not any(q.label.lower().startswith(name.lower()) for q in out):
            v = root.get(key) or {}
            add(name + " 每周", v.get("utilization"), v.get("resets_at"), 10080)
    return out

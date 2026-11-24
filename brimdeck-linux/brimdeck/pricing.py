"""Model prices (USD per token) from the OpenRouter catalog, cached for a day, with built-in fallbacks."""
import json
import os
import re
import time
import urllib.request

from .config import CACHE_DIR

SOURCE = "https://openrouter.ai/api/v1/models"
CACHE_FILE = os.path.join(CACHE_DIR, "model-prices.json")
MAX_AGE = 24 * 3600

# (input, output, cache_read, cache_write_5m) per token. Used before the catalog is downloaded or when offline.
BUILTIN = {
    "anthropic/claude-opus-5.5": (4e-6, 20e-6, 0.2e-6, 5e-6),
    "anthropic/claude-sonnet-5.5": (2e-6, 10e-6, 0.1e-6, 2.5e-6),
    "anthropic/claude-haiku-5.5": (0.1e-6, 0.5e-6, 0.01e-6, 0.125e-6),
    "anthropic/claude-fable-5.2": (8e-6, 40e-6, 0.2e-6, 10e-6),
    "anthropic/claude-opus-5": (5e-6, 25e-6, 0.5e-6, 6.25e-6),
    "openai/gpt-6.2-sol": (2e-6, 10e-6, 0.1e-6, 2.5e-6),
    "openai/gpt-6.2-astra": (8e-6, 40e-6, 0.4e-6, 10e-6),
    "openai/gpt-6.1-sol": (2e-6, 10e-6, 0.1e-6, 2.5e-6),
    "openai/gpt-6-sol": (2e-6, 10e-6, 0.2e-6, 2.5e-6),
    "openai/gpt-6-astra": (10e-6, 50e-6, 1e-6, 12.5e-6),
}


def _num(v):
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


class Pricing:
    def __init__(self):
        self.table = dict(BUILTIN)
        self.fetched = 0
        self._memo = {}
        self._load_cache()

    def _load_cache(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.table.update({k: tuple(v) for k, v in d["models"].items()})
            self.fetched = d.get("fetched", 0)
        except Exception:
            pass

    def refresh(self, force=False):
        """Downloads the catalog when the cache is older than a day. Returns whether prices changed."""
        if not force and time.time() - self.fetched < MAX_AGE:
            return False
        try:
            req = urllib.request.Request(SOURCE, headers={"User-Agent": "brimdeck-linux", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)["data"]
        except Exception as e:
            print("brimdeck: price catalog unavailable:", e)
            self.fetched = time.time() - MAX_AGE + 3600  # try again in an hour
            return False
        models = {}
        for m in data:
            mid = m.get("id", "")
            if mid.endswith((":batch", ":free")):
                continue
            p = m.get("pricing") or {}
            inp, out = _num(p.get("prompt")), _num(p.get("completion"))
            if inp is None or out is None:
                continue
            cr = _num(p.get("input_cache_read"))
            cw = _num(p.get("input_cache_write"))
            models[mid] = (inp, out, cr if cr is not None else inp, cw if cw is not None else inp)
        if not models:
            return False
        self.table = {**BUILTIN, **models}
        self._memo.clear()
        self.fetched = time.time()
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"fetched": self.fetched, "models": models}, f)
        except OSError:
            pass
        return True

    @staticmethod
    def normalize(model):
        """'anthropic/claude-opus-5-5@router/claude-opus-5-5' → 'claude-opus-5-5'; strips dates and [1m]."""
        m = (model or "").strip()
        if "/" in m:
            m = m.rsplit("/", 1)[-1]
        m = m.split("@", 1)[0]
        m = re.sub(r"\[.*?\]$", "", m)
        m = re.sub(r"-\d{8}$", "", m)
        return m.lower()

    def candidates(self, model):
        name = self.normalize(model)
        out = []
        if name.startswith("claude-"):
            m = re.match(r"^(claude-[a-z]+)-(\d+)-(\d+)$", name)
            if m:
                out.append(f"anthropic/{m.group(1)}-{m.group(2)}.{m.group(3)}")
            m = re.match(r"^(claude-[a-z]+)-(\d+)$", name)
            if m:
                out.append(f"anthropic/{name}")
            m = re.match(r"^claude-(\d+)-(\d+)-([a-z]+)$", name)  # claude-3-5-sonnet
            if m:
                out.append(f"anthropic/claude-{m.group(1)}.{m.group(2)}-{m.group(3)}")
            out.append("anthropic/" + name)
        elif name.startswith(("gpt-", "o1", "o3", "o4", "codex")):
            out.append("openai/" + name)
            base = re.sub(r"-(low|medium|high|xhigh|minimal)$", "", name)
            if base != name:
                out.append("openai/" + base)
        else:
            out.append(name)
        return out

    def price(self, model):
        if model in self._memo:
            return self._memo[model]
        found = None
        if model and model != "<synthetic>":
            for c in self.candidates(model):
                if c in self.table:
                    found = self.table[c]
                    break
            if found is None:
                name = self.normalize(model)
                for k, v in self.table.items():
                    if k.endswith("/" + name):
                        found = v
                        break
        self._memo[model] = found
        return found

    def cost(self, model, inp, cache_read, write5, write1h, out):
        p = self.price(model)
        if p is None:
            return None
        pin, pout, pcr, pcw = p
        return inp * pin + cache_read * pcr + write5 * pcw + write1h * pin * 2 + out * pout

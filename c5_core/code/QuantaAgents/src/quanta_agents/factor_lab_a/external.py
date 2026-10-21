"""Adapters for locally available external factor libraries (plugin candidates).

Sources:
- alpha101 / alpha191: auto-converted formula functions (BSD-2 origin, see the reference folder's
  THIRD_PARTY_NOTICE) that take a context object `ctx` with field access ctx('CLOSE') and operator
  methods (DELAY, RANK, SUM, MEAN, SMA, CORR, ...). We provide a pandas panel implementation.
- factor calendar (因子日历测试): numpy registries of daily standard / risk / additional factors.

Plugin ids: "alpha101:alpha_001", "alpha191:alpha_001", "calendar:<registry>:<name>".
INDNEUTRALIZE has no industry data here and is approximated by cross-sectional demeaning; every
alpha using it is tagged approx=True in its candidate metadata.
"""
from __future__ import annotations
import importlib.util
import inspect
import os
import sys
import numpy as np
import pandas as pd

ALPHA_REF_DIR = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/ext/alpha_formula_reference"
CALENDAR_DIR = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/ext/因子日历测试"
CALENDAR_MODULES = {"standard": "historical_standard_factors", "risk": "historical_risk_factors",
                    "additional": "historical_additional_factors"}


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------- alpha ctx
class AlphaContext:
    """Pandas implementation of the operator vocabulary used by the generated alpha101/191 code."""

    def __init__(self, panel):
        self.panel = panel
        self._cache = {}
        self.uses_indneutralize = False
        self.uses_unavailable = False

    # --- fields
    def __call__(self, name: str):
        n = name.upper()
        if n in self._cache:
            return self._cache[n]
        p = self.panel
        if n in ("CLOSE", "OPEN", "HIGH", "LOW", "VOLUME", "AMOUNT", "VWAP"):
            v = p[n.lower()]
        elif n in ("RETURNS", "RET"):
            v = p["ret"]
        elif n.startswith("ADV"):
            v = p["amount"].rolling(int(n[3:]), min_periods=int(n[3:])).mean()
        elif n == "CAP":
            v = p["float_market_cap"]
        elif n in ("BANCHMARKINDEXCLOSE", "BENCHMARKINDEXCLOSE"):
            v = (1 + p["mkt_ret"].fillna(0)).cumprod()
        elif n in ("BANCHMARKINDEXOPEN", "BENCHMARKINDEXOPEN"):
            v = (1 + p["mkt_ret"].fillna(0)).cumprod().shift(1)
        elif n == "TR":
            c1 = p["close"].shift(1)
            v = pd.concat([p["high"] - p["low"], (p["high"] - c1).abs(), (p["low"] - c1).abs()]).groupby(level=0).max()
            v = np.maximum(np.maximum(p["high"] - p["low"], (p["high"] - c1).abs()), (p["low"] - c1).abs())
        elif n == "HD":
            v = p["high"] - p["high"].shift(1)
        elif n == "LD":
            v = p["low"].shift(1) - p["low"]
        elif n == "DTM":
            o = p["open"]; v = ((o <= o.shift(1)) * 0).where(o <= o.shift(1), np.maximum(p["high"] - o, o - o.shift(1)))
        elif n == "DBM":
            o = p["open"]; v = ((o >= o.shift(1)) * 0).where(o >= o.shift(1), np.maximum(o - p["low"], o - o.shift(1)))
        elif n == "SEQUENCE":
            return _Sequence()
        else:
            self.uses_unavailable = True
            raise KeyError(f"field not available: {name}")
        v = v.astype(np.float64)
        self._cache[n] = v
        return v

    def _df(self, x):
        if isinstance(x, pd.DataFrame):
            return x
        if isinstance(x, np.ndarray) and x.ndim == 2:
            return pd.DataFrame(x, index=self.panel.dates, columns=self.panel.codes)
        like = self.panel["close"]
        return pd.DataFrame(float(x), index=like.index, columns=like.columns)

    @staticmethod
    def _w(n):
        n = int(round(float(n)))
        return max(n, 1)

    # --- elementwise
    def ABS(self, x): return self._df(x).abs()
    def LOG(self, x):
        x = self._df(x); return np.log(x.where(x > 0))
    def SIGN(self, x): return np.sign(self._df(x))
    def SIGNEDPOWER(self, x, p):
        x = self._df(x); p = float(p) if not isinstance(p, (pd.DataFrame, np.ndarray)) else self._df(p)
        return np.sign(x) * np.abs(x) ** p
    def MAX(self, a, b):
        if isinstance(b, (int, float)) and not isinstance(a, (int, float)):
            return self.TSMAX(a, b)
        return np.maximum(self._df(a), self._df(b))
    def MIN(self, a, b):
        if isinstance(b, (int, float)) and not isinstance(a, (int, float)):
            return self.TSMIN(a, b)
        return np.minimum(self._df(a), self._df(b))
    # --- cross section
    def RANK(self, x): return self._df(x).rank(axis=1, pct=True)
    def SCALE(self, x, a=1.0):
        x = self._df(x); return x.div(x.abs().sum(axis=1), axis=0) * a
    def INDNEUTRALIZE(self, x, group=None):
        self.uses_indneutralize = True
        x = self._df(x); return x.sub(x.mean(axis=1), axis=0)
    # --- time series
    def DELAY(self, x, n): return self._df(x).shift(self._w(n))
    def DELTA(self, x, n): return self._df(x) - self._df(x).shift(self._w(n))
    def SUM(self, x, n): n = self._w(n); return self._df(x).rolling(n, min_periods=n).sum()
    def MEAN(self, x, n): n = self._w(n); return self._df(x).rolling(n, min_periods=n).mean()
    MA = MEAN
    def STD(self, x, n): n = self._w(n); return self._df(x).rolling(n, min_periods=n).std(ddof=1)
    STDDEV = STD
    def TSMAX(self, x, n): n = self._w(n); return self._df(x).rolling(n, min_periods=n).max()
    def TSMIN(self, x, n): n = self._w(n); return self._df(x).rolling(n, min_periods=n).min()
    TS_MAX = TSMAX
    TS_MIN = TSMIN
    def TSRANK(self, x, n): n = self._w(n); return self._df(x).rolling(n, min_periods=n).rank(pct=True)
    TS_RANK = TSRANK
    def TS_ARGMAX(self, x, n):
        n = self._w(n); return self._df(x).rolling(n, min_periods=n).apply(lambda a: float(np.argmax(a)), raw=True)
    def TS_ARGMIN(self, x, n):
        n = self._w(n); return self._df(x).rolling(n, min_periods=n).apply(lambda a: float(np.argmin(a)), raw=True)
    def HIGHDAY(self, x, n):
        n = self._w(n); return (n - 1) - self.TS_ARGMAX(x, n)
    def LOWDAY(self, x, n):
        n = self._w(n); return (n - 1) - self.TS_ARGMIN(x, n)
    def CORR(self, x, y, n):
        n = self._w(n); return self._df(x).rolling(n, min_periods=n).corr(self._df(y))
    CORRELATION = CORR
    def COV(self, x, y, n):
        n = self._w(n); return self._df(x).rolling(n, min_periods=n).cov(self._df(y))
    COVARIANCE = COV
    def DECAYLINEAR(self, x, n):
        n = self._w(n); w = np.arange(1, n + 1, dtype=float); w /= w.sum()
        return self._df(x).rolling(n, min_periods=n).apply(lambda a: float(np.dot(a, w)), raw=True)
    DECAY_LINEAR = DECAYLINEAR
    def WMA(self, x, n):
        n = self._w(n); w = 0.9 ** np.arange(n - 1, -1, -1); w /= w.sum()
        return self._df(x).rolling(n, min_periods=n).apply(lambda a: float(np.dot(a, w)), raw=True)
    def SMA(self, x, n, m=1):
        n = self._w(n); m = float(m)
        return self._df(x).ewm(alpha=m / n, adjust=False, min_periods=n, ignore_na=False).mean()
    def COUNT(self, cond, n):
        n = self._w(n); c = self._df(cond).astype(float)
        return c.rolling(n, min_periods=n).sum()
    def SUMIF(self, x, n, cond):
        n = self._w(n); return (self._df(x) * self._df(cond).astype(float)).rolling(n, min_periods=n).sum()
    def PROD(self, x, n):
        n = self._w(n); x = self._df(x)
        return x.rolling(n, min_periods=n).apply(lambda a: float(np.prod(a)), raw=True)
    PRODUCT = PROD
    def REGBETA(self, y, x, n):
        n = self._w(n); y = self._df(y)
        if isinstance(x, _Sequence):
            t = np.arange(n, dtype=float); tc = t - t.mean(); denom = float((tc ** 2).sum())
            return y.rolling(n, min_periods=n).apply(lambda a: float(np.dot(a - a.mean(), tc) / denom), raw=True)
        x = self._df(x)
        return y.rolling(n, min_periods=n).cov(x) / x.rolling(n, min_periods=n).var(ddof=1)
    def REGBETA_FILTERED(self, *args, **kwargs):
        self.uses_unavailable = True
        raise NotImplementedError("REGBETA_FILTERED not supported")
    def REGRESI(self, y, x, n):
        n = self._w(n); y = self._df(y); x = self._df(x)
        beta = y.rolling(n, min_periods=n).cov(x) / x.rolling(n, min_periods=n).var(ddof=1)
        alpha = y.rolling(n, min_periods=n).mean() - beta * x.rolling(n, min_periods=n).mean()
        return y - alpha - beta * x


class _Sequence:
    """Marker for SEQUENCE(n) (time index regressor)."""


# ----------------------------------------------------------------------------- registries
_ALPHA_MODULES = {}
_CAL_MODULES = {}


def alpha_module(kind: str):
    if kind not in _ALPHA_MODULES:
        path = os.path.join(ALPHA_REF_DIR, f"{kind}_generated.py")
        _ALPHA_MODULES[kind] = _load_module(path, f"_ext_{kind}")
    return _ALPHA_MODULES[kind]


def alpha_names(kind: str) -> list[str]:
    mod = alpha_module(kind)
    return sorted(n for n in dir(mod) if n.startswith("alpha_") and callable(getattr(mod, n)))


def alpha_formula(kind: str, fn_name: str) -> str:
    """Original formula comment preceding the function definition."""
    path = os.path.join(ALPHA_REF_DIR, f"{kind}_generated.py")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"def {fn_name}("):
            j = i - 1
            while j >= 0 and not lines[j].startswith("#"):
                j -= 1
            return lines[j][1:].strip() if j >= 0 else ""
    return ""


def calendar_module(kind: str):
    if kind not in _CAL_MODULES:
        if CALENDAR_DIR not in sys.path:
            sys.path.insert(0, CALENDAR_DIR)
        path = os.path.join(CALENDAR_DIR, CALENDAR_MODULES[kind] + ".py")
        _CAL_MODULES[kind] = _load_module(path, CALENDAR_MODULES[kind])
    return _CAL_MODULES[kind]


def calendar_specs(kind: str) -> dict[str, object]:
    mod = calendar_module(kind)
    reg = mod.FACTOR_REGISTRY
    out = {}
    for spec in reg.values():
        out[spec.name] = spec
    return out


def list_plugins() -> list[dict]:
    items = []
    for kind in ("alpha101", "alpha191"):
        for fn in alpha_names(kind):
            items.append({"plugin": f"{kind}:{fn}", "name": f"{kind}_{fn[6:]}", "formula": alpha_formula(kind, fn), "source": kind})
    for kind in CALENDAR_MODULES:
        for name, spec in calendar_specs(kind).items():
            items.append({"plugin": f"calendar:{kind}:{name}", "name": f"cal_{kind}_{name}", "formula": getattr(spec, "note", ""), "source": "calendar"})
    return items


def _calendar_arrays(panel) -> dict[str, np.ndarray]:
    return {k: panel[k].to_numpy(np.float64) for k in ("close", "open", "high", "low", "volume", "amount", "total_shares", "float_shares")}


def compute_plugin(plugin: str, panel, membership: np.ndarray | None = None) -> tuple[pd.DataFrame, dict]:
    """Compute a plugin factor on the panel. Returns (frame, meta)."""
    kind, _, rest = plugin.partition(":")
    meta = {"plugin": plugin}
    if kind in ("alpha101", "alpha191"):
        mod = alpha_module(kind)
        ctx = AlphaContext(panel)
        fn = getattr(mod, rest)
        out = fn(ctx)
        frame = ctx._df(out)
        meta["approx_indneutralize"] = ctx.uses_indneutralize
        return frame.replace([np.inf, -np.inf], np.nan).astype(np.float64), meta
    if kind == "calendar":
        reg_kind, _, name = rest.partition(":")
        spec = calendar_specs(reg_kind)[name]
        fn = getattr(spec, "calculator", None) or getattr(spec, "function", None)
        arrays = _calendar_arrays(panel)
        params = inspect.signature(fn).parameters
        kwargs = {}
        if "membership" in params:
            kwargs["membership"] = membership
        if "reference" in params:
            kwargs["reference"] = (1 + panel["mkt_ret"].fillna(0)).cumprod().to_numpy(np.float64)
        if "params" in params:
            kwargs["params"] = dict(getattr(spec, "defaults", {}) or {})
        for extra in getattr(spec, "defaults", {}) or {}:
            if extra in params and extra not in kwargs:
                kwargs[extra] = spec.defaults[extra]
        out = fn(arrays, **kwargs) if "arrays" in params or len(params) >= 1 else fn()
        arr = np.asarray(out, dtype=np.float64)
        frame = pd.DataFrame(arr, index=panel.dates, columns=panel.codes)
        return frame.replace([np.inf, -np.inf], np.nan), meta
    raise KeyError(plugin)

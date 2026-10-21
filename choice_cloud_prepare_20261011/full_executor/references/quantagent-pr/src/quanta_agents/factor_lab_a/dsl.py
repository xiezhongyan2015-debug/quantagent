"""Safe, causal factor expression language over wide date x stock frames.

Superset of the legacy causal_factor_algebra_v6_1 (lag, pct_change, rolling_*, ema, ts_rank,
cs_rank, where, clip, abs, log, rolling_corr, rolling_residual) plus cross-sectional
z-score/demean/neutralise, delta, decay_linear, ts_zscore, rolling_cov/beta/skew, sign, sqrt,
power with literal exponent, elementwise min/max.

Every time-series operator only looks backwards; windows are literal integers 1..250.
Fields are resolved by the caller (Panel). Expressions are canonicalised via ast.unparse.
"""
from __future__ import annotations
import ast
import hashlib
import numpy as np
import pandas as pd

MAX_WINDOW = 250
MAX_NODES = 300
MAX_DEPTH = 30
from .minute_features import MINUTE_FIELDS, MINUTE_FIELD_DOC

SIGNAL_FIELDS = ("open", "high", "low", "close", "volume", "amount", "vwap", "prev_close", "turnover", "ret",
                 "float_shares", "total_shares", "float_market_cap", "total_market_cap", "log_cap", "log_total_cap",
                 "mkt_ret") + tuple(MINUTE_FIELDS)


def _win(w):
    if not isinstance(w, int) or isinstance(w, bool) or not (1 <= w <= MAX_WINDOW):
        raise ValueError(f"window must be literal int in 1..{MAX_WINDOW}, got {w!r}")
    return w


def _df(x, like):
    if isinstance(x, pd.DataFrame):
        return x
    return pd.DataFrame(float(x), index=like.index, columns=like.columns)


class Ops:
    """All operators; time-series ops are per-stock (column) trailing, cross-sectional ops per date (row)."""

    @staticmethod
    def abs(x): return x.abs()
    @staticmethod
    def sign(x): return np.sign(x)
    @staticmethod
    def sqrt(x): return np.sqrt(x.where(x >= 0))
    @staticmethod
    def log(x): return np.log(x.where(x > 0))
    @staticmethod
    def power(x, p):
        if not isinstance(p, (int, float)) or isinstance(p, bool):
            raise ValueError("power exponent must be a literal number")
        return np.sign(x) * np.abs(x) ** float(p) if p != int(p) else x ** int(p)
    @staticmethod
    def clip(x, lo, hi): return x.clip(float(lo), float(hi))
    @staticmethod
    def where(c, a, b):
        c = _df(c, a if isinstance(a, pd.DataFrame) else b)
        a = _df(a, c); b = _df(b, c)
        out = b.where(c <= 0, a)
        return out.where(c.notna())
    @staticmethod
    def emax(a, b): return _df(a, b).combine(_df(b, a), np.maximum) if isinstance(a, pd.DataFrame) or isinstance(b, pd.DataFrame) else max(a, b)
    @staticmethod
    def emin(a, b): return _df(a, b).combine(_df(b, a), np.minimum) if isinstance(a, pd.DataFrame) or isinstance(b, pd.DataFrame) else min(a, b)
    # time series
    @staticmethod
    def lag(x, n): return x.shift(_win(n))
    @staticmethod
    def delta(x, n): return x - x.shift(_win(n))
    @staticmethod
    def pct_change(x, n): return x / x.shift(_win(n)) - 1
    @staticmethod
    def rolling_mean(x, w): return x.rolling(_win(w), min_periods=w).mean()
    @staticmethod
    def rolling_sum(x, w): return x.rolling(_win(w), min_periods=w).sum()
    @staticmethod
    def rolling_std(x, w): return x.rolling(_win(w), min_periods=w).std(ddof=1)
    @staticmethod
    def rolling_max(x, w): return x.rolling(_win(w), min_periods=w).max()
    @staticmethod
    def rolling_min(x, w): return x.rolling(_win(w), min_periods=w).min()
    @staticmethod
    def rolling_median(x, w): return x.rolling(_win(w), min_periods=w).median()
    @staticmethod
    def rolling_skew(x, w): return x.rolling(_win(w), min_periods=w).skew()
    @staticmethod
    def rolling_kurt(x, w): return x.rolling(_win(w), min_periods=w).kurt()
    @staticmethod
    def ema(x, w): return x.ewm(span=_win(w), adjust=False, min_periods=w, ignore_na=False).mean()
    @staticmethod
    def ts_rank(x, w):
        w = _win(w)
        return x.rolling(w, min_periods=w).rank(pct=True)
    @staticmethod
    def ts_zscore(x, w):
        w = _win(w)
        r = x.rolling(w, min_periods=w)
        return (x - r.mean()) / r.std(ddof=1)
    @staticmethod
    def ts_argmax(x, w):
        w = _win(w)
        return x.rolling(w, min_periods=w).apply(lambda a: float(np.argmax(a)), raw=True)
    @staticmethod
    def ts_argmin(x, w):
        w = _win(w)
        return x.rolling(w, min_periods=w).apply(lambda a: float(np.argmin(a)), raw=True)
    @staticmethod
    def decay_linear(x, w):
        w = _win(w)
        wts = np.arange(1, w + 1, dtype=float); wts /= wts.sum()
        return x.rolling(w, min_periods=w).apply(lambda a: float(np.dot(a, wts)), raw=True)
    @staticmethod
    def rolling_corr(x, y, w):
        w = _win(w)
        return x.rolling(w, min_periods=w).corr(_df(y, x))
    @staticmethod
    def rolling_cov(x, y, w):
        w = _win(w)
        return x.rolling(w, min_periods=w).cov(_df(y, x))
    @staticmethod
    def rolling_beta(y, x, w):
        w = _win(w)
        x = _df(x, y)
        return y.rolling(w, min_periods=w).cov(x) / x.rolling(w, min_periods=w).var(ddof=1)
    @staticmethod
    def rolling_residual(y, x, w):
        """Current residual of trailing-window OLS y ~ 1 + x."""
        w = _win(w)
        x = _df(x, y)
        beta = y.rolling(w, min_periods=w).cov(x) / x.rolling(w, min_periods=w).var(ddof=1)
        alpha = y.rolling(w, min_periods=w).mean() - beta * x.rolling(w, min_periods=w).mean()
        return y - alpha - beta * x
    # cross section
    @staticmethod
    def cs_rank(x): return x.rank(axis=1, pct=True)
    @staticmethod
    def cs_demean(x): return x.sub(x.mean(axis=1), axis=0)
    @staticmethod
    def cs_zscore(x): return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1, ddof=0), axis=0)
    @staticmethod
    def cs_winsor(x, q=0.01):
        lo = x.quantile(float(q), axis=1); hi = x.quantile(1 - float(q), axis=1)
        return x.clip(lower=lo, upper=hi, axis=0)
    @staticmethod
    def cs_neutralize(y, x):
        """Cross-sectional OLS residual of y on x (per date), e.g. cs_neutralize(f, log_cap)."""
        x = _df(x, y)
        m = y.notna() & x.notna()
        yy = y.where(m); xx = x.where(m)
        xm = xx.sub(xx.mean(axis=1), axis=0); ym = yy.sub(yy.mean(axis=1), axis=0)
        beta = (xm * ym).sum(axis=1) / (xm * xm).sum(axis=1)
        return ym - xm.mul(beta, axis=0)
    @staticmethod
    def cs_scale(x): return x.div(x.abs().sum(axis=1), axis=0)
    @staticmethod
    def cs_rank_within(x, g, q):
        """Percentile of x inside q quantile groups of g per date (e.g. cs_rank_within(rev20, rolling_mean(turnover, 20), 5)):
        the rank-within-style-quintile form used by the competition book (A15 item 3)."""
        if not isinstance(q, int) or isinstance(q, bool) or not (2 <= q <= 50):
            raise ValueError("cs_rank_within: q must be a literal int in 2..50")
        g = _df(g, x)
        m = x.notna() & g.notna()
        gp = g.where(m).rank(axis=1, pct=True).to_numpy()
        grp = np.where(np.isfinite(gp), np.clip(np.ceil(np.nan_to_num(gp) * q), 1, q), 0).astype(int)
        out = pd.DataFrame(np.nan, index=x.index, columns=x.columns)
        for k in range(1, q + 1):
            mk = pd.DataFrame(grp == k, index=x.index, columns=x.columns)
            out = out.where(~mk, x.where(mk).rank(axis=1, pct=True))
        return out


FUNCTIONS = {name: getattr(Ops, name) for name in dir(Ops) if not name.startswith("_")}
WINDOW_FUNCTIONS = {"lag", "delay", "delta", "pct_change", "rolling_mean", "rolling_sum", "rolling_std", "rolling_var", "rolling_max",
                    "rolling_min", "rolling_median", "rolling_skew", "rolling_kurt", "ema", "ts_rank", "ts_zscore", "ts_argmax",
                    "ts_argmin", "decay_linear", "rolling_corr", "rolling_cov", "rolling_beta", "rolling_residual",
                    "ts_max", "ts_min", "ts_mean", "ts_std", "ts_sum"}
SIGN_VARYING = {"delta", "cs_neutralize", "cs_demean", "cs_zscore", "ts_zscore", "rolling_residual", "rolling_skew", "rolling_corr",
                "rolling_cov", "rolling_beta", "pct_change", "sign"}
FUNCTIONS["max"] = Ops.emax
FUNCTIONS["min"] = Ops.emin
FUNCTIONS["rolling_var"] = lambda x, w: x.rolling(_win(w), min_periods=w).var(ddof=1)
FUNCTIONS["ts_max"] = Ops.rolling_max
FUNCTIONS["ts_min"] = Ops.rolling_min
FUNCTIONS["ts_mean"] = Ops.rolling_mean
FUNCTIONS["ts_std"] = Ops.rolling_std
FUNCTIONS["ts_sum"] = Ops.rolling_sum
FUNCTIONS["delay"] = Ops.lag

FUNCTION_DOC = {
    "abs(x) sign(x) sqrt(x) log(x)": "elementwise; log/sqrt of non-positive -> NaN",
    "power(x, p)": "literal exponent; fractional p keeps sign",
    "clip(x, lo, hi) where(cond, a, b) max(a, b) min(a, b)": "elementwise; unknown cond -> NaN",
    "lag(x, n) delta(x, n) pct_change(x, n)": "per-stock backward shift / difference / return, n in 1..250",
    "rolling_mean/sum/std/var/max/min/median/skew/kurt(x, w)": "per-stock trailing window w (complete window required)",
    "ema(x, w) ts_rank(x, w) ts_zscore(x, w) ts_argmax(x, w) ts_argmin(x, w) decay_linear(x, w)": "trailing window transforms",
    "rolling_corr(x, y, w) rolling_cov(x, y, w) rolling_beta(y, x, w) rolling_residual(y, x, w)": "trailing pairwise stats; residual = current OLS residual",
    "cs_rank(x) cs_zscore(x) cs_demean(x) cs_winsor(x, q) cs_scale(x)": "per-date cross-sectional transforms over the whole panel",
    "cs_neutralize(y, x)": "per-date OLS residual of y on x, e.g. cs_neutralize(f, log_cap)",
    "cs_rank_within(x, g, q)": "per-date percentile of x inside q quantile groups of g (q literal int 2..50), e.g. cs_rank_within(f, rolling_mean(turnover, 20), 5)",
}
FIELD_DOC = {
    "open high low close vwap prev_close": "qfq-adjusted prices",
    "volume amount": "shares traded / CNY traded",
    "turnover": "volume / float_shares",
    "ret": "close / prev_close - 1",
    "float_shares total_shares float_market_cap total_market_cap": "shares & caps (CNY)",
    "log_cap log_total_cap": "log float / total market cap",
    "mkt_ret": "equal-weight eligible-universe daily return (same value across stocks)",
    **MINUTE_FIELD_DOC,
}


class Compiler:
    def __init__(self, fields: dict | None = None, resolver=None, strict: bool = True):
        self.fields = fields or {}
        self.resolver = resolver
        self.strict = strict  # False: skip the registration-time style rules (recomputing candidates registered before those rules)

    def canonical(self, expression: str) -> str:
        tree = ast.parse(expression.strip(), mode="eval")
        self._check(tree.body, 0)
        return ast.unparse(tree.body)

    @staticmethod
    def identity(canonical: str) -> str:
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def required_fields(self, expression: str) -> set[str]:
        tree = ast.parse(expression.strip(), mode="eval")
        return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in FUNCTIONS}

    def _check(self, node, depth):
        if depth > MAX_DEPTH:
            raise ValueError("expression too deep")
        if isinstance(node, ast.Name):
            if node.id in FUNCTIONS or node.id in SIGNAL_FIELDS or node.id in self.fields:
                return
            raise ValueError(f"unknown name: {node.id}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("only numeric literals allowed")
            return
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                raise ValueError("operator not allowed")
            if isinstance(node.op, ast.Pow) and not isinstance(node.right, ast.Constant):
                raise ValueError("** needs literal exponent")
            self._check(node.left, depth + 1); self._check(node.right, depth + 1); return
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not, ast.Invert)):
                raise ValueError("unary operator not allowed")
            self._check(node.operand, depth + 1); return
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                raise ValueError("chained comparison not allowed")
            self._check(node.left, depth + 1); self._check(node.comparators[0], depth + 1); return
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self._check(v, depth + 1)
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise ValueError(f"function not allowed: {ast.unparse(node.func)}")
            if node.keywords:
                raise ValueError("keyword arguments not allowed")
            if self.strict and node.func.id in ("log", "sqrt") and node.args and isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Name) and node.args[0].func.id in SIGN_VARYING:
                raise ValueError(f"{node.func.id}() must not be applied directly to {node.args[0].func.id}() (sign-varying; use sign(x)*log(1+abs(x)))")
            if node.func.id in WINDOW_FUNCTIONS:
                if not node.args:
                    raise ValueError(f"{node.func.id} needs a window")
                w = node.args[-1]
                if not (isinstance(w, ast.Constant) and isinstance(w.value, int) and not isinstance(w.value, bool) and 1 <= w.value <= MAX_WINDOW):
                    raise ValueError(f"{node.func.id}: window must be a literal int in 1..{MAX_WINDOW}")
            for a in node.args:
                self._check(a, depth + 1)
            return
        raise ValueError(f"syntax not allowed: {type(node).__name__}")

    def evaluate(self, expression: str, fields: dict | None = None):
        tree = ast.parse(expression.strip(), mode="eval")
        self._check(tree.body, 0)
        env = dict(self.fields)
        if fields:
            env.update(fields)
        n = sum(1 for _ in ast.walk(tree))
        if n > MAX_NODES:
            raise ValueError("expression too large")
        return self._eval(tree.body, env)

    def _get(self, name, env):
        if name in env:
            return env[name]
        if self.resolver is not None:
            val = self.resolver(name)
            env[name] = val
            return val
        raise KeyError(name)

    def _eval(self, node, env):
        if isinstance(node, ast.Name):
            return self._get(node.id, env)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            a = self._eval(node.left, env); b = self._eval(node.right, env)
            with np.errstate(all="ignore"):
                if isinstance(node.op, ast.Add): return a + b
                if isinstance(node.op, ast.Sub): return a - b
                if isinstance(node.op, ast.Mult): return a * b
                if isinstance(node.op, ast.Div):
                    if not isinstance(a, pd.DataFrame) and not isinstance(b, pd.DataFrame):
                        out = np.float64(a) / np.float64(b)
                        return float(out) if np.isfinite(out) else float("nan")
                    out = a / b
                    return out.replace([np.inf, -np.inf], np.nan)
                if isinstance(node.op, ast.Pow): return Ops.power(a, b)
        if isinstance(node, ast.UnaryOp):
            a = self._eval(node.operand, env)
            if isinstance(node.op, ast.USub): return -a
            if isinstance(node.op, ast.UAdd): return a
            if isinstance(node.op, (ast.Not, ast.Invert)):
                a = _df(a, a) if isinstance(a, pd.DataFrame) else a
                return (1 - (a != 0).astype(float)).where(a.notna()) if isinstance(a, pd.DataFrame) else float(not a)
        if isinstance(node, ast.Compare):
            a = self._eval(node.left, env); b = self._eval(node.comparators[0], env)
            op = node.ops[0]
            like = a if isinstance(a, pd.DataFrame) else b
            a = _df(a, like); b = _df(b, like)
            valid = a.notna() & b.notna()
            with np.errstate(all="ignore"):
                if isinstance(op, ast.Gt): r = a > b
                elif isinstance(op, ast.GtE): r = a >= b
                elif isinstance(op, ast.Lt): r = a < b
                elif isinstance(op, ast.LtE): r = a <= b
                elif isinstance(op, ast.Eq): r = a == b
                elif isinstance(op, ast.NotEq): r = a != b
                else: raise ValueError("comparison not allowed")
            return r.astype(float).where(valid)
        if isinstance(node, ast.BoolOp):
            vals = [self._eval(v, env) for v in node.values]
            like = next(v for v in vals if isinstance(v, pd.DataFrame))
            vals = [_df(v, like) for v in vals]
            out = vals[0]
            for v in vals[1:]:
                if isinstance(node.op, ast.And):
                    out = ((out != 0) & (v != 0)).astype(float).where(out.notna() & v.notna())
                else:
                    out = ((out != 0) | (v != 0)).astype(float).where(out.notna() & v.notna())
            return out
        if isinstance(node, ast.Call):
            fn = FUNCTIONS[node.func.id]
            args = [self._eval(a, env) for a in node.args]
            with np.errstate(all="ignore"):
                out = fn(*args)
            if isinstance(out, pd.DataFrame):
                out = out.replace([np.inf, -np.inf], np.nan)
            return out
        raise ValueError("unsupported node")


def dsl_reference() -> str:
    lines = ["FIELDS:"] + [f"  {k}: {v}" for k, v in FIELD_DOC.items()] + ["FUNCTIONS:"] + [f"  {k}: {v}" for k, v in FUNCTION_DOC.items()]
    lines += ["OPERATORS: + - * / ** (literal exponent), comparisons (> >= < <= == !=) -> 1/0/NaN, and/or/not",
              f"LIMITS: windows literal ints 1..{MAX_WINDOW}; no assignments, subscripts, attributes, imports; time-series ops are strictly backward-looking",
              "Legacy style also accepted: 0 / 0 for NaN, where(abs(x) > 1e-12, a / x, 0 / 0)."]
    return "\n".join(lines)

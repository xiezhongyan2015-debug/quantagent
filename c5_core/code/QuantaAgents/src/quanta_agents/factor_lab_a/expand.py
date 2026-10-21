"""Programmatic factor transformation / re-expression engine ("大海捞金" layer).

For a seed factor (DSL expression or plugin), generate variants:
  - window neighbourhood (DSL only): each integer window literal scaled by 0.5x / 2x (snapped to the allowed grid)
  - transformations (any seed): size-neutralisation, turnover/vol neutralisation, EMA smoothing, ts-zscore,
    signed log compression, cross-sectional rank, 20-day reversal residual
Variants are screened on the TRAINING years only (2016-2018): worst training-year RankIC, |t|, coverage and
numeric novelty against the pool + already screened variants. Survivors are registered as candidates
(source "expand", parent = seed id) for the normal full evaluation.
"""
from __future__ import annotations
import re
import numpy as np
import pandas as pd
from .evaluate import rank_ic

WINDOW_GRID = [2, 3, 5, 8, 10, 15, 20, 30, 40, 60, 90, 120, 180, 250]

TRANSFORMS = {
    "neut_cap": ("cs_neutralize({x}, log_cap)", "市值中性化"),
    "neut_turn": ("cs_neutralize({x}, cs_rank(rolling_mean(turnover, 20)))", "换手中性化"),
    "neut_vol": ("cs_neutralize({x}, cs_rank(rolling_std(ret, 20)))", "波动中性化"),
    "ema5": ("ema({x}, 5)", "5日指数平滑"),
    "ema10": ("ema({x}, 10)", "10日指数平滑"),
    "tsz60": ("ts_zscore({x}, 60)", "60日时序标准化"),
    "slog": ("sign({x}) * log(1 + abs({x}))", "符号对数压缩"),
    "rank": ("cs_rank({x})", "截面排名"),
    "resid_rev20": ("cs_neutralize({x}, close / lag(close, 20) - 1)", "剥离20日反转"),
    "delta5": ("delta({x}, 5)", "5日变化"),
    "tsrank60": ("ts_rank({x}, 60)", "60日时序排名"),
}


def _snap(w: int, factor: float) -> int:
    target = w * factor
    return min(WINDOW_GRID, key=lambda g: abs(g - target))


def window_variants(expression: str) -> list[tuple[str, str]]:
    """Return (variant_expression, tag) with each integer literal window scaled up/down."""
    out = []
    ints = [(m.start(), m.end(), int(m.group())) for m in re.finditer(r"(?<![\w.])(\d+)(?![\w.])", expression)]
    ints = [(s, e, v) for s, e, v in ints if 2 <= v <= 250]
    if not ints:
        return out
    for factor, tag in ((0.5, "w0.5"), (2.0, "w2")):
        parts = []; last = 0
        for s, e, v in ints:
            parts.append(expression[last:s]); parts.append(str(_snap(v, factor))); last = e
        parts.append(expression[last:])
        v = "".join(parts)
        if v != expression:
            out.append((v, tag))
    if len(ints) >= 2:  # also scale only the largest window
        s, e, v = max(ints, key=lambda t: t[2])
        for factor, tag in ((0.5, "wmax0.5"), (2.0, "wmax2")):
            v2 = expression[:s] + str(_snap(v, factor)) + expression[e:]
            if v2 != expression:
                out.append((v2, tag))
    return out


def transform_variants(expression: str, transforms=None) -> list[tuple[str, str]]:
    out = []
    for key in (transforms or TRANSFORMS):
        tmpl, _ = TRANSFORMS[key]
        out.append((tmpl.format(x=f"({expression})"), key))
    return out


def apply_transform_frames(frame: pd.DataFrame, chain: list[str], panel) -> pd.DataFrame:
    """Apply a transform chain to an already computed frame (used for plugin seeds)."""
    from .dsl import Compiler
    env = {"__x__": frame}
    comp = Compiler(resolver=lambda n: env[n] if n in env else panel[n])
    expr = "__x__"
    for key in chain:
        expr = TRANSFORMS[key][0].format(x=f"({expr})")
    comp.fields = {"__x__": frame}
    import ast as _ast
    # allow the placeholder name
    from . import dsl as _dsl
    if "__x__" not in _dsl.SIGNAL_FIELDS:
        _dsl.SIGNAL_FIELDS = tuple(_dsl.SIGNAL_FIELDS) + ("__x__",)
    return comp.evaluate(expr)


class TrainScreen:
    """Cheap screen on training years with numeric novelty check."""

    def __init__(self, label: pd.DataFrame, mask: pd.DataFrame, min_stocks=100, sample_step=10):
        self.label = label; self.mask = mask; self.min_stocks = min_stocks
        self.sample_rows = np.arange(len(label))[::sample_step]
        self.sigs: dict[str, np.ndarray] = {}
        self._ucount = mask.sum(axis=1).to_numpy()

    def add_signature(self, key: str, frame: pd.DataFrame):
        self.sigs[key] = frame.iloc[self.sample_rows].where(self.mask.iloc[self.sample_rows]).rank(axis=1, pct=True).to_numpy(np.float32)

    def novelty(self, frame: pd.DataFrame) -> tuple[float, str | None]:
        sig = frame.iloc[self.sample_rows].where(self.mask.iloc[self.sample_rows]).rank(axis=1, pct=True).to_numpy(np.float32)
        best, arg = 0.0, None
        for k, other in self.sigs.items():
            m = np.isfinite(sig) & np.isfinite(other)
            n = m.sum(axis=1)
            if n.sum() < 1000:
                continue
            a = np.where(m, sig, 0); b = np.where(m, other, 0)
            with np.errstate(all="ignore"):
                am = a.sum(axis=1) / np.maximum(n, 1); bm = b.sum(axis=1) / np.maximum(n, 1)
                ac = np.where(m, a - am[:, None], 0); bc = np.where(m, b - bm[:, None], 0)
                r = (ac * bc).sum(axis=1) / np.sqrt((ac ** 2).sum(axis=1) * (bc ** 2).sum(axis=1))
            c = float(np.nanmean(np.abs(r)))
            if c > best:
                best, arg = c, k
        return best, arg

    def stats(self, frame: pd.DataFrame) -> dict:
        ric, n = rank_ic(frame, self.label, self.mask, self.min_stocks)
        s = ric.dropna()
        if len(s) < 200 or s.std() == 0:
            return {"ok": False, "reason": "insufficient_days"}
        sign = int(np.sign(s.mean()))
        annual = {int(y): float(s[s.index.year == y].mean()) * sign for y in sorted(set(s.index.year))}
        return {"ok": True, "train_mean": float(s.mean()) * sign, "train_t": float(s.mean() / s.std(ddof=1) * np.sqrt(len(s))),
                "sign": sign, "annual": annual, "worst": min(annual.values()),
                "coverage": float(np.median(n.to_numpy() / np.maximum(self._ucount, 1)))}

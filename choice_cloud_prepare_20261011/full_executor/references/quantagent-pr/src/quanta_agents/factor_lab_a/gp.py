"""Lightweight genetic-programming search over the DSL (non-LLM control arm / raw-material generator).

Fitness uses TRAINING years only (2016-2018): |t-stat of daily RankIC| with a penalty on size and
on redundancy against a given set of signatures. Winners are handed to the normal full evaluation.
"""
from __future__ import annotations
import random
import numpy as np
import pandas as pd
from .dsl import Compiler
from .evaluate import rank_ic


def _row_corr(a, b, m):
    n = m.sum(axis=1)
    with np.errstate(all="ignore"):
        am = a.sum(axis=1) / np.maximum(n, 1); bm = b.sum(axis=1) / np.maximum(n, 1)
        ac = np.where(m, a - am[:, None], 0); bc = np.where(m, b - bm[:, None], 0)
        r = (ac * bc).sum(axis=1) / np.sqrt((ac ** 2).sum(axis=1) * (bc ** 2).sum(axis=1))
    return r, n

TERMINALS = ["close", "open", "high", "low", "volume", "amount", "turnover", "ret", "vwap", "log_cap", "prev_close"]
WINDOWS = [3, 5, 10, 20, 40, 60, 120]
UNARY = ["abs", "log", "sign", "cs_rank", "cs_zscore"]
TS1 = ["lag", "delta", "pct_change", "rolling_mean", "rolling_std", "rolling_sum", "rolling_max", "rolling_min",
       "ema", "ts_rank", "ts_zscore", "rolling_skew"]
TS2 = ["rolling_corr", "rolling_cov", "rolling_residual", "rolling_beta"]
BINARY = ["+", "-", "*", "/"]


def random_expr(rng: random.Random, depth=0, max_depth=3) -> str:
    if depth >= max_depth or (depth > 0 and rng.random() < 0.25):
        return rng.choice(TERMINALS)
    r = rng.random()
    if r < 0.35:
        return f"{rng.choice(TS1)}({random_expr(rng, depth + 1, max_depth)}, {rng.choice(WINDOWS)})"
    if r < 0.50:
        return f"{rng.choice(TS2)}({random_expr(rng, depth + 1, max_depth)}, {random_expr(rng, depth + 1, max_depth)}, {rng.choice(WINDOWS)})"
    if r < 0.65:
        return f"{rng.choice(UNARY)}({random_expr(rng, depth + 1, max_depth)})"
    if r < 0.72:
        return f"cs_neutralize({random_expr(rng, depth + 1, max_depth)}, log_cap)"
    return f"({random_expr(rng, depth + 1, max_depth)} {rng.choice(BINARY)} {random_expr(rng, depth + 1, max_depth)})"


def mutate(rng: random.Random, expr: str, compiler: Compiler) -> str:
    import ast
    tree = ast.parse(expr, mode="eval")
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Call, ast.BinOp, ast.Name))]
    if not nodes:
        return random_expr(rng)
    target = rng.choice(nodes)
    new = ast.parse(random_expr(rng, depth=1), mode="eval").body
    if isinstance(target, ast.Call) and rng.random() < 0.5 and target.args and isinstance(target.args[-1], ast.Constant):
        target.args[-1] = ast.Constant(rng.choice(WINDOWS))
        return ast.unparse(tree.body)
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if value is target:
                setattr(parent, field, new)
                return ast.unparse(tree.body)
            if isinstance(value, list):
                for i, v in enumerate(value):
                    if v is target:
                        value[i] = new
                        return ast.unparse(tree.body)
    return ast.unparse(tree.body)


def crossover(rng: random.Random, a: str, b: str) -> str:
    import ast
    ta = ast.parse(a, mode="eval"); tb = ast.parse(b, mode="eval")
    donors = [n for n in ast.walk(tb) if isinstance(n, (ast.Call, ast.BinOp))]
    hosts = [n for n in ast.walk(ta) if isinstance(n, (ast.Call, ast.BinOp, ast.Name))]
    if not donors or not hosts:
        return a
    donor = rng.choice(donors); host = rng.choice(hosts)
    for parent in ast.walk(ta):
        for field, value in ast.iter_fields(parent):
            if value is host:
                setattr(parent, field, donor); return ast.unparse(ta.body)
            if isinstance(value, list):
                for i, v in enumerate(value):
                    if v is host:
                        value[i] = donor; return ast.unparse(ta.body)
    return a


class GPSearch:
    def __init__(self, compiler: Compiler, train_fields: dict, label: pd.DataFrame, mask: pd.DataFrame,
                 pool_signatures: dict[str, pd.DataFrame] | None = None, seed=0, min_stocks=100):
        self.compiler = compiler
        self.fields = train_fields
        self.label = label
        self.mask = mask
        self.rng = random.Random(seed)
        self.min_stocks = min_stocks
        self.cache: dict[str, dict] = {}
        self.pool_sigs = pool_signatures or {}
        self.sigs: dict[str, np.ndarray] = {}
        self._ucount = self.mask.sum(axis=1).to_numpy()

    def fitness(self, expr: str) -> dict:
        try:
            canon = self.compiler.canonical(expr)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if canon in self.cache:
            return self.cache[canon]
        try:
            f = self.compiler.evaluate(canon, self.fields)
            if not isinstance(f, pd.DataFrame):
                raise ValueError("constant expression")
            ric, n = rank_ic(f, self.label, self.mask, self.min_stocks)
            s = ric.dropna()
            if len(s) < 200 or s.std() == 0:
                out = {"ok": False, "error": "insufficient days"}
            else:
                t = float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))
                sign = int(np.sign(s.mean()))
                annual = [float(s[s.index.year == y].mean()) * sign for y in sorted(set(s.index.year))]
                worst = min(annual) if annual else 0.0
                cover = float((n >= self.min_stocks).mean())
                stock_cover = float(np.median(n.to_numpy() / np.maximum(self._ucount, 1)))
                size_pen = 0.02 * max(0, len(canon) - 80)
                sig = f.iloc[::10].where(self.mask.iloc[::10]).rank(axis=1, pct=True).to_numpy(np.float32)
                dup = None
                for k, other in self.sigs.items():
                    m = np.isfinite(sig) & np.isfinite(other)
                    if m.sum() < 1000:
                        continue
                    r, _ = _row_corr(np.where(m, sig, 0), np.where(m, other, 0), m)
                    if np.nanmean(np.abs(r)) > 0.95:
                        dup = k
                        break
                out = {"ok": True, "canonical": canon, "t": t, "mean_ric": float(s.mean()), "coverage": cover,
                       "stock_coverage": stock_cover, "worst_annual": worst, "duplicate_of": dup,
                       "fitness": (worst * 100.0) * min(1.0, cover / 0.8) * (1.0 if stock_cover >= 0.9 else 0.2) - size_pen - (50 if dup else 0),
                       "sign": sign}
                if dup is None:
                    self.sigs[canon] = sig
        except Exception as exc:
            out = {"ok": False, "error": str(exc)[:120]}
        self.cache[canon] = out
        return out

    def run(self, population=120, generations=4, elite=20, seeds: list[str] | None = None, log=print) -> list[dict]:
        pop = list(seeds or [])
        while len(pop) < population:
            pop.append(random_expr(self.rng))
        scored = []
        for gen in range(generations):
            scored = []
            for e in pop:
                r = self.fitness(e)
                if r.get("ok"):
                    scored.append(r)
            scored = sorted({r["canonical"]: r for r in scored}.values(), key=lambda r: -r["fitness"])
            log(f"gen {gen}: evaluated {len(pop)} ok {len(scored)} best fitness {scored[0]['fitness']:.2f} "
                f"({scored[0]['canonical'][:80]})" if scored else f"gen {gen}: nothing valid")
            if gen == generations - 1:
                break
            parents = [r["canonical"] for r in scored[:elite]] or [random_expr(self.rng)]
            pop = list(parents)
            while len(pop) < population:
                if self.rng.random() < 0.6:
                    pop.append(mutate(self.rng, self.rng.choice(parents), self.compiler))
                elif self.rng.random() < 0.8:
                    pop.append(crossover(self.rng, self.rng.choice(parents), self.rng.choice(parents)))
                else:
                    pop.append(random_expr(self.rng))
        return scored

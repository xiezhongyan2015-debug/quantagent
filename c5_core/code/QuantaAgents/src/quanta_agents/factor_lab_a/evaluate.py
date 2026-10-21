"""Single-factor evaluation: RankIC / ICIR / quantile spread / decay / exposures / redundancy.

Vectorised: labels are ranked once within the eligible universe and cached; the factor is ranked
once and reused for every horizon, the quantile table and the turnover diagnostic.
Direction is fixed on the training years (default 2016-2018) and applied to every later year.
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

TRAIN_YEARS = (2016, 2017, 2018)
TARGET_YEARS = (2019, 2020, 2021, 2022, 2023, 2024)
ALL_YEARS = TRAIN_YEARS + TARGET_YEARS


def _row_corr_np(x: np.ndarray, y: np.ndarray):
    """Row-wise Pearson over common finite cells of two 2-D float arrays."""
    m = np.isfinite(x) & np.isfinite(y)
    n = m.sum(axis=1)
    xz = np.where(m, x, 0.0); yz = np.where(m, y, 0.0)
    with np.errstate(all="ignore"):
        nn = np.maximum(n, 1)
        xm = xz.sum(axis=1) / nn; ym = yz.sum(axis=1) / nn
        xc = np.where(m, x - xm[:, None], 0.0); yc = np.where(m, y - ym[:, None], 0.0)
        r = (xc * yc).sum(axis=1) / np.sqrt((xc ** 2).sum(axis=1) * (yc ** 2).sum(axis=1))
    return r, n


def row_corr(a: pd.DataFrame, b: pd.DataFrame):
    r, n = _row_corr_np(a.to_numpy(np.float64), b.to_numpy(np.float64))
    return pd.Series(r, index=a.index), pd.Series(n, index=a.index)


def rank_rows(a: np.ndarray) -> np.ndarray:
    """Average-tie ranks along axis 1 ignoring NaN (pandas semantics)."""
    return pd.DataFrame(a).rank(axis=1).to_numpy(np.float32)


def rank_ic(factor: pd.DataFrame, label: pd.DataFrame, mask: pd.DataFrame, min_stocks=100):
    m = mask & factor.notna() & label.notna()
    f = factor.where(m).rank(axis=1); l = label.where(m).rank(axis=1)
    r, n = row_corr(f, l)
    return r.where(n >= min_stocks), n


def pearson_ic(factor: pd.DataFrame, label: pd.DataFrame, mask: pd.DataFrame, min_stocks=100, winsor=0.01):
    m = mask & factor.notna() & label.notna()
    f = factor.where(m)
    lo = f.quantile(winsor, axis=1); hi = f.quantile(1 - winsor, axis=1)
    f = f.clip(lower=lo, upper=hi, axis=0)
    f = f.sub(f.mean(axis=1), axis=0).div(f.std(axis=1, ddof=0), axis=0)
    r, n = row_corr(f, label.where(m))
    return r.where(n >= min_stocks), n


def annual_table(series: pd.Series, years=ALL_YEARS) -> dict:
    out = {}
    yrs = series.index.year
    for y in years:
        s = series[yrs == y].dropna()
        if len(s) == 0:
            out[y] = {"mean": None, "std": None, "icir": None, "days": 0}
            continue
        sd = float(s.std(ddof=1)) if len(s) > 1 else float("nan")
        out[y] = {"mean": float(s.mean()), "std": sd, "days": int(len(s)),
                  "icir": float(s.mean() / sd * np.sqrt(len(s))) if sd and sd > 0 else None,
                  "t": float(s.mean() / sd * np.sqrt(len(s))) if sd and sd > 0 else None}
    return out


def quantile_returns(factor: pd.DataFrame, label: pd.DataFrame, mask: pd.DataFrame, q=10):
    m = mask & factor.notna() & label.notna()
    r = factor.where(m).rank(axis=1, pct=True)
    return _quantiles_from_pct(r.to_numpy(np.float32), label.where(m).to_numpy(np.float64), factor.index, q)


def _quantiles_from_pct(pct: np.ndarray, lab: np.ndarray, index, q=10) -> pd.DataFrame:
    valid = np.isfinite(pct) & np.isfinite(lab)
    bucket = np.clip(np.ceil(np.nan_to_num(pct) * q), 1, q).astype(int) - 1
    n_dates = pct.shape[0]
    rows = np.repeat(np.arange(n_dates), pct.shape[1]).reshape(pct.shape)
    sums = np.zeros((n_dates, q)); cnts = np.zeros((n_dates, q))
    np.add.at(sums, (rows[valid], bucket[valid]), lab[valid])
    np.add.at(cnts, (rows[valid], bucket[valid]), 1.0)
    with np.errstate(all="ignore"):
        frame = pd.DataFrame(sums / np.where(cnts > 0, cnts, np.nan), index=index, columns=range(1, q + 1))
        frame["universe"] = np.where(valid, lab, 0).sum(axis=1) / np.maximum(valid.sum(axis=1), 1)
    frame.loc[valid.sum(axis=1) == 0, "universe"] = np.nan
    return frame


def top_turnover_from_pct(pct: np.ndarray, index, q=10) -> pd.Series:
    top = pct > 1 - 1 / q
    prev = np.vstack([np.zeros((1, top.shape[1]), bool), top[:-1]])
    new = (top & ~prev).sum(axis=1); cnt = top.sum(axis=1)
    with np.errstate(all="ignore"):
        s = pd.Series(new / np.where(cnt > 0, cnt, np.nan), index=index)
    return s.iloc[1:]


def top_turnover(factor: pd.DataFrame, mask: pd.DataFrame, q=10) -> pd.Series:
    r = factor.where(mask & factor.notna()).rank(axis=1, pct=True)
    return top_turnover_from_pct(r.to_numpy(np.float32), factor.index, q)


def style_exposure(factor: pd.DataFrame, styles: dict[str, pd.DataFrame], mask: pd.DataFrame, step=5) -> dict:
    out = {}
    idx = factor.index[::step]
    f = factor.loc[idx].where(mask.loc[idx]).rank(axis=1)
    for name, s in styles.items():
        r, n = row_corr(f, s.loc[idx].where(mask.loc[idx]).rank(axis=1))
        out[name] = float(r.where(n >= 100).mean())
    return out


class Evaluator:
    def __init__(self, panel, universe="all_a", label="label_5", min_stocks=100, train_years=TRAIN_YEARS,
                 target_years=TARGET_YEARS, sample_step=10, decay_labels=("label_1", "label_10", "label_20")):
        self.panel = panel
        self.universe = universe
        self.label_name = label
        self.min_stocks = min_stocks
        self.train_years = tuple(train_years)
        self.target_years = tuple(target_years)
        self.mask = panel.mask(universe) & (panel["eval_ok"] > 0)
        self.label = panel[label]
        self.dates = panel.dates
        self.years = np.array(self.dates.year)
        self.sample_idx = panel.dates[::sample_step]
        self.sample_pos = np.arange(len(self.dates))[::sample_step]
        self.style_pos = np.arange(len(self.dates))[::5]
        self.decay_labels = [l for l in decay_labels if panel.has(l)]
        self._styles = None
        self._mask_np = self.mask.to_numpy(bool)
        self._universe_count = self._mask_np.sum(axis=1)
        self._label_rank: dict[str, np.ndarray] = {}
        self._label_val: dict[str, np.ndarray] = {}

    def _label_arrays(self, name):
        if name not in self._label_rank:
            lab = self.panel[name].where(self.mask)
            self._label_val[name] = lab.to_numpy(np.float64)
            self._label_rank[name] = lab.rank(axis=1).to_numpy(np.float32)
        return self._label_rank[name], self._label_val[name]

    @property
    def styles(self):
        if self._styles is None:
            p = self.panel
            idx = self.dates[self.style_pos]
            m = self.mask.loc[idx]
            raw = {"size": p["log_cap"], "turnover20": p["turnover"].rolling(20).mean(),
                   "vol20": p["ret"].rolling(20).std(), "mom20": p["close"].pct_change(20, fill_method=None)}
            self._styles = {k: v.loc[idx].where(m).rank(axis=1).to_numpy(np.float32) for k, v in raw.items()}
        return self._styles

    def signature(self, factor: pd.DataFrame) -> pd.DataFrame:
        """Sampled-date percentile ranks used for cheap redundancy checks."""
        return factor.loc[self.sample_idx].where(self.mask.loc[self.sample_idx]).rank(axis=1, pct=True).astype(np.float32)

    def _series(self, r, n):
        return pd.Series(np.where(n >= self.min_stocks, r, np.nan), index=self.dates), pd.Series(n, index=self.dates)

    def evaluate(self, factor: pd.DataFrame, full=True) -> dict:
        fmask = self._mask_np & np.isfinite(factor.to_numpy(np.float64))
        fv = np.where(fmask, factor.to_numpy(np.float64), np.nan)
        frank = pd.DataFrame(fv).rank(axis=1).to_numpy(np.float32)
        cnt = fmask.sum(axis=1)
        pct = frank / np.where(cnt > 0, cnt, np.nan)[:, None]
        lrank, lval = self._label_arrays(self.label_name)
        r, n = _row_corr_np(frank, lrank)
        ric, n = self._series(r, n)
        # Pearson on winsorised z-scores (diagnostic)
        with np.errstate(all="ignore"):
            lo = np.nanpercentile(fv, 1, axis=1, keepdims=True); hi = np.nanpercentile(fv, 99, axis=1, keepdims=True)
            fw = np.clip(fv, lo, hi)
            fw = (fw - np.nanmean(fw, axis=1, keepdims=True)) / np.nanstd(fw, axis=1, keepdims=True)
        rp, np_ = _row_corr_np(fw, lval)
        pic, _ = self._series(rp, np_)
        yrs = self.years
        train = ric[np.isin(yrs, self.train_years)].dropna()
        direction, t_train = 0, None
        if len(train) > 20 and train.std() > 0:
            t_train = float(train.mean() / train.std(ddof=1) * np.sqrt(len(train)))
            direction = int(np.sign(train.mean()))
        sric = ric * direction if direction else ric * np.nan
        spic = pic * direction if direction else pic * np.nan
        annual_rank = annual_table(sric); annual_pear = annual_table(spic); annual_raw_rank = annual_table(ric)
        tgt_ok = [annual_rank[y]["mean"] for y in self.target_years if annual_rank[y]["mean"] is not None]
        pear_ok = [annual_pear[y]["mean"] for y in self.target_years if annual_pear[y]["mean"] is not None]
        in_target = np.isin(yrs, self.target_years)
        res = {"direction": direction, "train_t": t_train,
               "train_mean_rank_ic": float(train.mean()) if len(train) else None,
               "annual_rank_ic": {str(k): v for k, v in annual_rank.items()},
               "annual_pearson_ic": {str(k): v for k, v in annual_pear.items()},
               "annual_raw_rank_ic": {str(k): v for k, v in annual_raw_rank.items()},
               "target_mean_rank_ic": float(np.mean(tgt_ok)) if tgt_ok else None,
               "target_worst_rank_ic": float(np.min(tgt_ok)) if tgt_ok else None,
               "target_mean_pearson_ic": float(np.mean(pear_ok)) if pear_ok else None,
               "target_worst_pearson_ic": float(np.min(pear_ok)) if pear_ok else None,
               "target_icir": None,
               "coverage": {str(y): float((n[yrs == y] >= self.min_stocks).mean()) for y in ALL_YEARS},
               "valid_days_target": int(sric[in_target].notna().sum()),
               "median_stocks": float(np.median(n[in_target])),
               "stock_coverage": float(np.median(cnt[in_target] / np.maximum(self._universe_count[in_target], 1)))}
        s = sric[in_target].dropna()
        if len(s) > 1 and s.std() > 0:
            res["target_icir"] = float(s.mean() / s.std(ddof=1) * np.sqrt(252))
        if not full or not direction:
            return res
        dpct = pct if direction > 0 else 1 - pct + 1 / np.where(cnt > 0, cnt, np.nan)[:, None]
        qr = _quantiles_from_pct(dpct, lval, self.dates)
        tq = qr[in_target]
        res["quantile_mean_target"] = [float(tq[k].mean()) for k in range(1, 11)]
        res["long_short_target"] = float((tq[10] - tq[1]).mean())
        res["top_excess_target"] = float((tq[10] - tq["universe"]).mean())
        res["bottom_excess_target"] = float((tq[1] - tq["universe"]).mean())
        mono = np.corrcoef(np.arange(10), res["quantile_mean_target"])[0, 1]
        res["quantile_monotonicity"] = float(mono) if np.isfinite(mono) else None
        decay = {"label_5": res["target_mean_rank_ic"]}
        for lab in self.decay_labels:
            lr, _ = self._label_arrays(lab)
            r2, n2 = _row_corr_np(frank, lr)
            s2, _ = self._series(r2 * direction, n2)
            decay[lab] = float(s2[in_target].mean())
        res["decay_rank_ic"] = decay
        tt = top_turnover_from_pct(np.nan_to_num(dpct, nan=0.0), self.dates)
        res["top_decile_turnover"] = float(tt[np.isin(tt.index.year, self.target_years)].mean())
        fs = frank[self.style_pos] * direction
        exp = {}
        for k, srank in self.styles.items():
            r3, n3 = _row_corr_np(fs, srank)
            exp[k] = float(np.nanmean(np.where(n3 >= 100, r3, np.nan)))
        res["style_exposure"] = exp
        return res

    def daily_ic(self, factor: pd.DataFrame, direction: int) -> pd.Series:
        ric, _ = rank_ic(factor, self.label, self.mask, self.min_stocks)
        return ric * direction


def signature_corr(a, b) -> float:
    """Mean |row correlation| over sampled dates with >= 100 common stocks. Accepts DataFrames or numpy arrays
    (float16 arrays are upcast per call so the caller can keep the pool signatures compact)."""
    x = a.to_numpy(np.float64) if isinstance(a, pd.DataFrame) else np.asarray(a, dtype=np.float32)
    y = b.to_numpy(np.float64) if isinstance(b, pd.DataFrame) else np.asarray(b, dtype=np.float32)
    r, n = _row_corr_np(x, y)
    r = np.where(n >= 100, r, np.nan)
    return float(np.nanmean(np.abs(r))) if np.isfinite(r).any() else float("nan")


def classify_failure(res: dict, thresholds) -> str:
    if res.get("direction", 0) == 0:
        return "no_direction"
    if (res.get("stock_coverage") or 1.0) < thresholds.get("min_stock_coverage", 0.9):
        return "low_coverage"
    tm = res.get("target_mean_rank_ic") or 0
    tw = res.get("target_worst_rank_ic")
    exp = res.get("style_exposure") or {}
    strong = [k for k, v in exp.items() if v is not None and abs(v) > 0.6]
    tag = ("+style:" + ",".join(strong)) if strong else ""
    if tm >= thresholds.get("single_factor_mean", 0.05):
        if tw is not None and tw > thresholds.get("single_factor_worst_strong", 0.05):
            return "pass_worst" + tag
        return "pass_mean" + tag
    if tm < 0.01:
        return "no_signal" + tag
    if tw is not None and tw < 0 and tm >= thresholds.get("single_factor_mean", 0.05) * 0.6:
        return "unstable_years" + tag
    return "weak_signal" + tag

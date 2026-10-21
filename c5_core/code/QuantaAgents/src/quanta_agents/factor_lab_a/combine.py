"""Combination models over the admitted pool: equal-rank, ridge, LightGBM; rolling annual retrain.

Rows = (date, stock) eligible cells; features = pre-ranked per-date percentiles of direction-applied factors
(float16 arrays, typically numpy memmaps from pool_ranks/);
label = per-date demeaned 5-day return (raw) or its per-date rank (rank target).
Only labels that have fully exited before the test year are used for training (year-end purge).
"""
from __future__ import annotations
import os
import time
import numpy as np
import pandas as pd
from .evaluate import rank_ic, pearson_ic, annual_table, TARGET_YEARS


class LazyRanks:
    """Row-sliceable view of a float16 rank array on disk without holding a persistent memmap
    (hundreds of persistent memmaps exhaust Windows address/commit resources)."""

    def __init__(self, path: str):
        self.path = path

    def __getitem__(self, sel):
        # plain read (no file mapping): ~30MB per member, sliced immediately
        with open(self.path, "rb") as fh:
            arr = np.load(fh)
        return np.array(arr[sel])


def _long(ranks: dict, label: pd.DataFrame, mask: pd.DataFrame, dates, pos: np.ndarray, feature_ids, day_step=1, min_feature_share=0.8, dtype=np.float32):
    """Long table from pre-ranked (float16, date x stock) arrays; rows selected by panel row positions.
    No row copies: returns full X (NaN allowed, dtype float32 for training / float16 for test), targets with
    invalid rows zeroed, and a keep mask."""
    sel = pos[::day_step]
    idx = dates[sel]
    m = mask.loc[idx] & label.loc[idx].notna()
    mv = m.to_numpy(bool)
    n_dates, n_codes = m.shape
    X = np.empty((n_dates * n_codes, len(feature_ids)), dtype)
    finite = np.zeros(n_dates * n_codes, np.int32)
    for j, fid in enumerate(feature_ids):
        col = np.asarray(ranks[fid][sel], dtype=np.float32)
        col = np.where(mv, col, np.nan).ravel()
        X[:, j] = col
        finite += np.isfinite(col)
        del col
    lab = label.loc[idx].where(m)
    y_raw = lab.sub(lab.mean(axis=1), axis=0).to_numpy(np.float32).ravel()
    y_rank = (lab.rank(axis=1, pct=True) - 0.5).to_numpy(np.float32).ravel()
    keep = np.isfinite(y_raw)
    if len(feature_ids):
        keep &= finite >= min_feature_share * len(feature_ids)
    y_raw = np.where(keep, y_raw, 0.0).astype(np.float32)
    y_rank = np.where(keep, y_rank, 0.0).astype(np.float32)
    date_id = np.repeat(np.arange(n_dates), n_codes)
    return X, y_raw, y_rank, date_id, idx, n_codes, keep


def _predict_chunks(booster, X, chunk=200_000):
    """Predict a (possibly float16) matrix in row chunks so the float32 copy never exceeds ~0.5GB."""
    out = np.empty(len(X), np.float32)
    for s in range(0, len(X), chunk):
        out[s:s + chunk] = booster.predict(np.asarray(X[s:s + chunk], dtype=np.float32))
    return out


def _fit_predict(model: str, box: list, ytr, wtr, Xte, seed=0, params=None, n_seeds=1):
    """box = [Xtr]; the training matrix is released (box[0] = None) as soon as the model no longer needs it
    (LightGBM: after the binned Dataset is constructed), so peak memory ~1.25x Xtr instead of 2x+.
    wtr: boolean keep mask for training rows (used as sample weight 0/1; no row copies for lgbm)."""
    if model == "equal":
        box[0] = None
        return np.nanmean(np.asarray(Xte, dtype=np.float32), axis=1)
    if model == "ridge":
        from sklearn.linear_model import Ridge
        lam = (params or {}).get("lambda", 1.0)
        Xtr = box[0]; box[0] = None
        Xk = np.nan_to_num(Xtr[wtr], nan=0.5); yk = ytr[wtr]
        del Xtr
        m = Ridge(alpha=lam * len(Xk) / 1000.0).fit(Xk, yk)
        del Xk
        return m.predict(np.nan_to_num(np.asarray(Xte, dtype=np.float32), nan=0.5))
    if model == "lgbm":
        import lightgbm as lgb
        p = {"objective": "regression", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500,
             "feature_fraction": 0.7, "bagging_fraction": 0.7, "bagging_freq": 1, "lambda_l2": 10.0,
             "verbose": -1, "num_threads": 24, "seed": seed}
        p.update(params or {})
        rounds = p.pop("num_rounds", 400)
        ds = lgb.Dataset(box[0], ytr, weight=wtr.astype(np.float32), free_raw_data=True,
                         params={"max_bin": p.get("max_bin", 255), "min_data_in_leaf": p.get("min_data_in_leaf", 500), "verbose": -1})
        ds.construct()
        box[0] = None
        preds = []
        for k in range(max(1, n_seeds)):
            pk = dict(p, seed=seed + k)
            booster = lgb.train(pk, ds, num_boost_round=rounds)
            preds.append(_predict_chunks(booster, Xte))
            del booster
        del ds
        return np.mean(preds, axis=0) if len(preds) > 1 else preds[0]
    raise ValueError(model)


def rolling_combination(panel, evaluator, ranks: dict, feature_ids: list[str], model="lgbm",
                        target="raw", update="expanding", train_years=(2016, 2017, 2018), target_years=TARGET_YEARS,
                        day_step=2, params=None, seed=0, context_fields=(), n_seeds=1) -> dict:
    """context_fields: raw panel fields (e.g. log_cap) appended as ranked features so the model can learn
    conditional effects; n_seeds > 1 averages predictions of independently seeded fits (lgbm only)."""
    label = evaluator.label
    mask = evaluator.mask
    dates = panel.dates
    years = np.array([d.year for d in dates])
    out_daily = []
    coef_info = {}
    ranks = dict(ranks)
    feature_ids = list(feature_ids)
    for cf in context_fields:
        key = f"ctx:{cf}"
        ranks[key] = panel[cf].where(mask).rank(axis=1, pct=True).to_numpy(np.float16)
        feature_ids.append(key)
    all_pos = np.arange(len(dates))
    for Y in target_years:
        if update == "fixed":
            tr_years = list(train_years)
        elif update == "rolling3y":
            tr_years = [Y - 3, Y - 2, Y - 1]
        else:
            tr_years = list(range(train_years[0], Y))
        tr_mask = np.isin(years, tr_years)
        te_mask = years == Y
        Xtr, ytr_raw, ytr_rank, _, _, _, ktr = _long(ranks, label, mask, dates, all_pos[tr_mask], feature_ids, day_step)
        ytr = ytr_raw if target == "raw" else ytr_rank
        del ytr_raw, ytr_rank
        Xte, _, _, date_id, te_idx, n_codes, keep = _long(ranks, label, mask, dates, all_pos[te_mask], feature_ids, 1, dtype=np.float16)
        if ktr.sum() < 1000 or keep.sum() == 0:
            del Xtr, Xte
            continue
        box = [Xtr]; del Xtr
        pred = _fit_predict(model, box, ytr, ktr, Xte, seed=seed, params=params, n_seeds=n_seeds)
        del box
        full = np.where(keep, pred, np.nan).astype(np.float32)
        del Xte, pred
        pred_df = pd.DataFrame(full.reshape(len(te_idx), n_codes), index=te_idx, columns=panel.codes)
        out_daily.append(pred_df)
        coef_info[str(Y)] = {"train_rows": int(ktr.sum()), "test_rows": int(keep.sum()), "train_years": tr_years}
    if not out_daily:
        return {"status": "unavailable"}, None
    prediction = pd.concat(out_daily).reindex(dates)
    return _finish(prediction, panel, evaluator, feature_ids, model, target, update, target_years, coef_info, params, day_step, context_fields, n_seeds)


def _finish(prediction, panel, evaluator, feature_ids, model, target, update, target_years, coef_info, params, day_step, context_fields=(), n_seeds=1):
    label = evaluator.label; mask = evaluator.mask; dates = panel.dates
    ric, n = rank_ic(prediction, label, mask, evaluator.min_stocks)
    pic, _ = pearson_ic(prediction, label, mask, evaluator.min_stocks)
    ar = annual_table(ric, target_years); ap = annual_table(pic, target_years)
    from .evaluate import _quantiles_from_pct, top_turnover_from_pct
    pm = mask & prediction.notna() & label.notna()
    pct = prediction.where(pm).rank(axis=1, pct=True).to_numpy(np.float32)
    qr = _quantiles_from_pct(pct, label.where(pm).to_numpy(np.float64), dates)
    in_t = np.isin(qr.index.year, target_years)
    tq = qr[in_t]
    trade = {"top_decile_excess_5d": float((tq[10] - tq["universe"]).mean()),
             "bottom_decile_excess_5d": float((tq[1] - tq["universe"]).mean()),
             "long_short_5d": float((tq[10] - tq[1]).mean()),
             "quantile_mean_5d": [float(tq[k].mean()) for k in range(1, 11)],
             "top_decile_daily_turnover": float(top_turnover_from_pct(np.nan_to_num(pct, nan=0.0), dates)[in_t[1:]].mean()),
             "annual_top_excess_5d": {str(y): float((tq[10] - tq["universe"])[tq.index.year == y].mean()) for y in target_years}}
    tgt = [ar[y]["mean"] for y in target_years if ar[y]["mean"] is not None]
    pea = [ap[y]["mean"] for y in target_years if ap[y]["mean"] is not None]
    s = ric[np.isin(ric.index.year, target_years)].dropna()
    covered = [int(y) for y in target_years if ar[y]["mean"] is not None]
    return {"status": "ok" if len(covered) == len(target_years) else "partial", "years_covered": covered,
            "missing_years": [int(y) for y in target_years if int(y) not in covered],
            "model": model, "target": target, "update": update, "features": list(feature_ids),
            "n_features": len(feature_ids), "annual_rank_ic": {str(k): v for k, v in ar.items()},
            "annual_pearson_ic": {str(k): v for k, v in ap.items()},
            "target_mean_rank_ic": float(np.mean(tgt)) if tgt else None, "target_worst_rank_ic": float(np.min(tgt)) if tgt else None,
            "target_mean_pearson_ic": float(np.mean(pea)) if pea else None,
            "target_worst_pearson_ic": float(np.min(pea)) if pea else None,
            "target_icir": float(s.mean() / s.std(ddof=1) * np.sqrt(252)) if len(s) > 1 else None,
            "fits": coef_info, "params": params or {}, "day_step": day_step, "context_fields": list(context_fields), "n_seeds": n_seeds,
            "trading": trade,
            "nan_policy": "rows kept if label finite and >=80% features finite; LightGBM sees NaN natively, linear models fill 0.5"}, prediction


# ----------------------------------------------------------------------------- low-memory path (one pass over the feature files, binned dataset + subsets)
try:
    import lightgbm as _lgb
    _SeqBase = _lgb.Sequence
except Exception:  # pragma: no cover - lightgbm missing
    _SeqBase = object


class MemmapSequence(_SeqBase):
    """LightGBM Sequence over a row-major float16 memmap (rows contiguous, so random-row sampling is cheap)."""
    batch_size = 16384

    def __init__(self, mm):
        self.mm = mm

    def __len__(self):
        return int(self.mm.shape[0])

    def __getitem__(self, idx):
        import numbers
        if isinstance(idx, numbers.Integral):
            return np.asarray(self.mm[idx], dtype=np.float64)  # LightGBM samples rows as double
        if isinstance(idx, slice):
            return np.asarray(self.mm[idx], dtype=np.float64)
        return np.asarray(self.mm[np.asarray(idx)], dtype=np.float64)


def _smooth_objective(n_codes: int, keep: np.ndarray, lam: float):
    """Custom LightGBM objective: weighted squared error plus lam * sum over consecutive sampled dates of the same stock
    of (p_t - p_next)^2. Rows are date-major with n_codes rows per date, so the next sampled date's row is i + n_codes.
    Returns fobj(preds, dataset) -> (grad, hess)."""
    n = len(keep)
    i = np.arange(n - n_codes)
    pair = keep[i] & keep[i + n_codes]
    a = i[pair]; b = a + n_codes
    deg = np.bincount(a, minlength=n) + np.bincount(b, minlength=n)

    def fobj(preds, dataset):
        y = dataset.get_label(); w = dataset.get_weight()
        w = np.ones_like(preds) if w is None else np.asarray(w)
        grad = w * (preds - y)
        hess = w.copy()
        if lam > 0:
            d = preds[a] - preds[b]
            grad += lam * (np.bincount(a, weights=d, minlength=n) - np.bincount(b, weights=d, minlength=n))
            hess += lam * deg
        return grad, hess
    return fobj


def rolling_combination_lowmem(panel, evaluator, ranks: dict, feature_ids: list, target="rank", train_years=(2016, 2017, 2018),
                               target_years=TARGET_YEARS, day_step=4, params=None, seed=0, n_seeds=1, workdir=None, log=print,
                               label_override=None, min_feature_share=0.8, smooth_lambda=0.0, row_weight=None):
    """Same model as rolling_combination(model='lgbm', update='expanding') with peak memory ~ binned data (1 byte/cell)
    instead of a float32 copy of the training matrix. One pass over the member rank files writes a row-major float16
    memmap of the sampled training rows (all years before the last target year) and one of the test rows; LightGBM
    bins the training memmap once through the Sequence interface; every target year trains on the row prefix
    (years before it) via Dataset.subset and predicts its own test slice in chunks. label_override: alternative
    training label panel (e.g. a multi-horizon blend); evaluation always uses the evaluator's label."""
    import lightgbm as lgb
    import tempfile, shutil
    label_tr = label_override if label_override is not None else evaluator.label
    mask = evaluator.mask; dates = panel.dates; codes = panel.codes
    assert dates.is_monotonic_increasing, "panel dates must be sorted for the prefix logic"
    # test rows: every eligible name (the evaluator's mask additionally blanks the last six signal dates of each
    # year for the label purge; a prediction is still needed there so the portfolio does not go flat)
    mask_te = panel.mask(evaluator.universe)
    years = np.array([d.year for d in dates]); n_codes = len(codes)
    feature_ids = list(feature_ids); nf = len(feature_ids)
    last_train_year = max(target_years) - 1
    tr_pos = np.arange(len(dates))[np.isin(years, list(range(train_years[0], last_train_year + 1)))][::day_step]
    te_pos = np.arange(len(dates))[np.isin(years, list(target_years))]
    tr_years = years[tr_pos]; te_years = years[te_pos]
    n_tr = len(tr_pos) * n_codes; n_te = len(te_pos) * n_codes
    workdir = workdir or tempfile.mkdtemp(prefix="combo_lowmem_")
    os.makedirs(workdir, exist_ok=True)
    Xtr = np.lib.format.open_memmap(os.path.join(workdir, "xtr.npy"), mode="w+", dtype=np.float16, shape=(n_tr, nf))
    Xte = np.lib.format.open_memmap(os.path.join(workdir, "xte.npy"), mode="w+", dtype=np.float16, shape=(n_te, nf))
    mtr = (mask & label_tr.notna()).iloc[tr_pos].to_numpy(bool); mte = mask_te.iloc[te_pos].to_numpy(bool)
    fin_tr = np.zeros(n_tr, np.int32); fin_te = np.zeros(n_te, np.int32)
    t0 = time.time()
    BLOCK = 32  # features per write block: 64-byte runs per row instead of 2-byte ones
    for j0 in range(0, nf, BLOCK):
        ids_blk = feature_ids[j0:j0 + BLOCK]
        blk_tr = np.empty((n_tr, len(ids_blk)), np.float16); blk_te = np.empty((n_te, len(ids_blk)), np.float16)
        for jj, fid in enumerate(ids_blk):
            with open(ranks[fid].path, "rb") as fh:
                arr = np.load(fh)
            a = np.where(mtr, arr[tr_pos], np.nan).astype(np.float16).ravel(); blk_tr[:, jj] = a; fin_tr += np.isfinite(a)
            b = np.where(mte, arr[te_pos], np.nan).astype(np.float16).ravel(); blk_te[:, jj] = b; fin_te += np.isfinite(b)
            del arr, a, b
        Xtr[:, j0:j0 + len(ids_blk)] = blk_tr; Xte[:, j0:j0 + len(ids_blk)] = blk_te
        del blk_tr, blk_te
        if (j0 + len(ids_blk)) % 128 < BLOCK or j0 + BLOCK >= nf:
            log(f"  packed {j0 + len(ids_blk)}/{nf} features ({time.time() - t0:.0f}s)")
    Xtr.flush(); Xte.flush(); del Xtr, Xte
    lab = label_tr.iloc[tr_pos].where(mask.iloc[tr_pos])
    if target == "raw":
        y = lab.sub(lab.mean(axis=1), axis=0).to_numpy(np.float32).ravel()
    elif target == "binary":   # 0/1 event label (e.g. crash within the horizon); pair with params objective='binary'
        y = lab.to_numpy(np.float32).ravel()
    else:
        y = (lab.rank(axis=1, pct=True) - 0.5).to_numpy(np.float32).ravel()
    keep_tr = np.isfinite(y) & (fin_tr >= min_feature_share * nf)
    y = np.where(keep_tr, y, 0.0).astype(np.float32)
    # optional per-row training weights (date x code frame on the panel grid; A17 item 2: universe / top-heavy weighting)
    w_tr = keep_tr.astype(np.float32)
    if row_weight is not None:
        rw = row_weight.iloc[tr_pos].to_numpy(np.float32).ravel()
        w_tr = np.where(keep_tr & np.isfinite(rw), rw, 0.0).astype(np.float32)
    keep_te = mte.ravel() & (fin_te >= min_feature_share * nf)
    p = {"objective": "regression", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500, "feature_fraction": 0.7,
         "bagging_fraction": 0.7, "bagging_freq": 1, "lambda_l2": 10.0, "verbose": -1, "num_threads": 24, "seed": seed}
    p.update(params or {}); rounds = p.pop("num_rounds", 400)
    ds_params = {"max_bin": p.get("max_bin", 255), "min_data_in_leaf": p.get("min_data_in_leaf", 500), "bin_construct_sample_cnt": 50000, "verbose": -1}
    out_daily = []; coef_info = {}
    try:
        mm_tr = np.load(os.path.join(workdir, "xtr.npy"), mmap_mode="r")
        row_year_tr = np.repeat(tr_years, n_codes)
        # bin boundaries are learned on the first training window only (walk-forward: no test-year feature values
        # influence the binning); every later year reuses those mappers through `reference`
        n_ref = int((row_year_tr <= max(train_years)).sum())
        ds_ref = lgb.Dataset([MemmapSequence(mm_tr[:n_ref])], label=y[:n_ref], weight=w_tr[:n_ref], params=ds_params, free_raw_data=True)
        ds_ref.construct()
        seq = MemmapSequence(mm_tr)
        ds_all = lgb.Dataset([seq], label=y, weight=w_tr, reference=ds_ref, params=ds_params, free_raw_data=True)
        ds_all.construct()
        del ds_ref
        log(f"  binned dataset {n_tr} rows x {nf} features, bins from the first {n_ref} rows ({time.time() - t0:.0f}s)")
        Xte_mm = np.load(os.path.join(workdir, "xte.npy"), mmap_mode="r")
        row_year_te = np.repeat(te_years, n_codes)
        for Y in target_years:
            n_prefix = int((row_year_tr < Y).sum())
            if keep_tr[:n_prefix].sum() < 1000:
                log(f"  year {Y}: skipped (fewer than 1000 training rows)"); continue
            te_rows = np.where(row_year_te == Y)[0]
            if len(te_rows) == 0:
                continue
            lo, hi = int(te_rows[0]), int(te_rows[-1]) + 1
            keep = keep_te[lo:hi]
            if not keep.any():
                log(f"  year {Y}: skipped (no usable test rows)"); continue
            ds = ds_all if n_prefix == n_tr else ds_all.subset(np.arange(n_prefix, dtype=np.int32).tolist())
            preds = []
            pk = dict(p)
            if smooth_lambda > 0:
                pk["objective"] = _smooth_objective(n_codes, keep_tr[:n_prefix], smooth_lambda)
            for k in range(max(1, n_seeds)):
                booster = lgb.train(dict(pk, seed=seed + k), ds, num_boost_round=rounds)
                preds.append(_predict_chunks(booster, Xte_mm[lo:hi]))
                del booster
            pred = np.mean(preds, axis=0) if len(preds) > 1 else preds[0]
            full = np.where(keep, pred, np.nan).astype(np.float32)
            te_idx = dates[te_pos[(te_years == Y)]]
            out_daily.append(pd.DataFrame(full.reshape(len(te_idx), n_codes), index=te_idx, columns=codes))
            coef_info[str(Y)] = {"train_rows": int(keep_tr[:n_prefix].sum()), "test_rows": int(keep.sum()), "train_years": sorted(set(int(v) for v in row_year_tr[:n_prefix]))}
            log(f"  year {Y}: trained on {coef_info[str(Y)]['train_rows']} rows ({time.time() - t0:.0f}s)")
            if ds is not ds_all:
                del ds
        del ds_all, seq, Xte_mm, mm_tr
    finally:
        for fn in ("xtr.npy", "xte.npy"):
            try:
                os.remove(os.path.join(workdir, fn))
            except OSError as exc:
                log(f"  could not remove {fn}: {exc}")
        try:
            os.rmdir(workdir)
        except OSError:
            pass
    if not out_daily:
        return {"status": "unavailable"}, None
    prediction = pd.concat(out_daily).reindex(dates)
    res, prediction = _finish(prediction, panel, evaluator, feature_ids, "lgbm", target, "expanding", target_years, coef_info, params, day_step, (), n_seeds)
    res["path"] = "lowmem"; res["bins"] = "walk-forward: bin boundaries from the first training window only; test rows on the eligibility mask (no year-end purge)"
    res["smooth_lambda"] = smooth_lambda; res["label_override"] = label_override is not None
    return res, prediction

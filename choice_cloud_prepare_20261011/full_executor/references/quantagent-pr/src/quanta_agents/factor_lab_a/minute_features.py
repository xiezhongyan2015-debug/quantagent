"""Intraday (1-minute) bars aggregated to daily fields: the second information source of the A layer.

Source: the qfq minute parquet library (one file per stock, columns datetime/open/high/low/close/volume/amount/
float_shares/total_shares/qfq_ratio). Every regular session has 240 bars (09:31-11:30, 13:01-15:00); the 09:31
bar carries the opening auction. Days without exactly 240 bars get NaN features (counted in the manifest).

Per stock-day, with r_i = close_i / close_{i-1} - 1 (r_1 against the day open), v_i volume, a_i amount (CNY):

  im_ret_open30 / im_ret_close30 / im_ret_mid   first 30 min, last 30 min, middle session returns
  im_ret_am / im_ret_pm / im_ret_last1           morning, afternoon, last-minute returns
  im_vshare_open30 / im_vshare_close30 / im_vshare_last5 / im_vshare_am   volume shares of those windows
  im_rv                                          realised variance (sum r^2)
  im_updown                                      log(up semivariance / down semivariance)
  im_rskew / im_rkurt                            realised skewness / excess kurtosis of r_i
  im_maxret / im_minret                          largest / smallest one-minute return
  im_bigamt_share                                amount share of the top-10% amount minutes (large-order proxy)
  im_big_ret                                     mean r in top-20% amount minutes minus mean r in bottom-20%
  im_big_vwap_dev                                vwap of top-20% amount minutes / day vwap - 1
  im_pv_corr / im_pv_corr_lag                    corr(r_i, v_i) and corr(r_i, v_{i-1})
  im_vol_centroid / im_vol_herf / im_vol_cv      volume time-centroid (0..1), 240*sum(share^2), std/mean of volume
  im_zero_share / im_pos_share                   fraction of zero-return / positive-return minutes
  im_amihud                                      log(mean |r| / mean amount)
  im_path_eff                                    |day return| / sum |r_i|
  im_close_vwap                                  close / day vwap - 1 (qfq basis)
  im_mdd                                         intraday max drawdown of close from its running high
  im_hl_time                                     (minute of day high - minute of day low) / 239

Policy (feature version 2): a day is NaN in every field when it has no volume, fewer than 60 priced minutes or a
flat price path; the three large-order fields are NaN when fewer than 60 minutes traded or the top/bottom amount
groups overlap (otherwise they degenerate into constants that proxy illiquidity). Known structural break: the
closing call auction (14:57-15:00, folded into the last bar) started on 2018-08-20, so the last-5/last-30-minute
volume shares, the last-minute return and the volume-shape fields have a different regime before and after.

CLI:
  python -m quanta_agents.factor_lab_a.minute_features build --out F:/A_Layer_Research/minute_daily --workers 8
  python -m quanta_agents.factor_lab_a.minute_features assemble --daily F:/A_Layer_Research/minute_daily --panel-dir F:/A_Layer_Research/panel
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

SOURCE_DEFAULT = r"D:/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data"
PATTERN = re.compile(r"^(?:sh(?:600|601|603|605|688|689)|sz(?:000|001|002|003|300|301|302))\d{3}$")
BARS = 240

MINUTE_FIELDS = ("im_ret_open30", "im_ret_close30", "im_ret_mid", "im_ret_am", "im_ret_pm", "im_ret_last1",
                 "im_vshare_open30", "im_vshare_close30", "im_vshare_last5", "im_vshare_am",
                 "im_rv", "im_updown", "im_rskew", "im_rkurt", "im_maxret", "im_minret",
                 "im_bigamt_share", "im_big_ret", "im_big_vwap_dev", "im_pv_corr", "im_pv_corr_lag",
                 "im_vol_centroid", "im_vol_herf", "im_vol_cv", "im_zero_share", "im_pos_share", "im_amihud", "im_path_eff",
                 "im_close_vwap", "im_mdd", "im_hl_time")

MINUTE_FIELD_DOC = {
    "im_ret_open30 im_ret_close30 im_ret_mid im_ret_am im_ret_pm im_ret_last1": "intraday session returns (first/last 30 min, middle, morning, afternoon, last minute)",
    "im_vshare_open30 im_vshare_close30 im_vshare_last5 im_vshare_am": "volume share of the first 30 / last 30 / last 5 minutes / morning",
    "im_rv im_updown im_rskew im_rkurt im_maxret im_minret": "realised variance, log up/down semivariance ratio, realised skew/kurt, extreme 1-min returns",
    "im_bigamt_share im_big_ret im_big_vwap_dev": "large-order proxies from minute amount: top-10% amount share, big-minus-small minute return, big-minute vwap deviation",
    "im_pv_corr im_pv_corr_lag": "intraday corr of minute return with (lagged) minute volume",
    "im_vol_centroid im_vol_herf im_vol_cv": "volume time-centroid (0..1), concentration (240*sum share^2), coefficient of variation",
    "im_zero_share im_pos_share im_amihud im_path_eff": "share of zero / positive minutes, log intraday Amihud, path efficiency",
    "im_close_vwap im_mdd im_hl_time": "close vs day vwap, intraday max drawdown, timing of high minus low (fraction of day)",
}


def _row_corr(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    m = np.isfinite(x) & np.isfinite(y)
    n = m.sum(axis=1)
    xz = np.where(m, x, 0.0); yz = np.where(m, y, 0.0)
    with np.errstate(all="ignore"):
        nn = np.maximum(n, 1)
        xm = xz.sum(axis=1) / nn; ym = yz.sum(axis=1) / nn
        xc = np.where(m, x - xm[:, None], 0.0); yc = np.where(m, y - ym[:, None], 0.0)
        r = (xc * yc).sum(axis=1) / np.sqrt((xc ** 2).sum(axis=1) * (yc ** 2).sum(axis=1))
    return np.where(n >= 30, r, np.nan)


def day_features(op, hi, lo, cl, vo, am, qfq) -> dict[str, np.ndarray]:
    """All inputs are (n_days, 240) float64 arrays; qfq is (n_days,)."""
    with np.errstate(all="ignore"):
        cl = np.where(cl > 0, cl, np.nan); op = np.where(op > 0, op, np.nan)
        hi = np.where(hi > 0, hi, np.nan); lo = np.where(lo > 0, lo, np.nan)
        qfq = np.where(qfq > 0, qfq, np.nan)
        prev = np.concatenate([op[:, :1], cl[:, :-1]], axis=1)
        r = cl / np.where(prev > 0, prev, np.nan) - 1.0
        vs = vo.sum(axis=1); as_ = am.sum(axis=1)
        n = np.isfinite(r).sum(axis=1)
        # a day is unusable when it has no volume, no open, fewer than 60 priced minutes, or a completely flat
        # price path (limit-locked all day): every field is NaN on such days so all 31 columns share one sample
        flat = np.nanstd(np.where(np.isfinite(r), r, np.nan), axis=1) == 0
        bad = ~(vs > 0) | ~np.isfinite(op[:, 0]) | (n < 60) | flat
        f = {}
        f["im_ret_open30"] = cl[:, 29] / op[:, 0] - 1
        f["im_ret_close30"] = cl[:, 239] / cl[:, 209] - 1
        f["im_ret_mid"] = cl[:, 209] / cl[:, 29] - 1
        f["im_ret_am"] = cl[:, 119] / op[:, 0] - 1
        f["im_ret_pm"] = cl[:, 239] / cl[:, 119] - 1
        f["im_ret_last1"] = r[:, 239]
        vsd = np.where(vs > 0, vs, np.nan)
        f["im_vshare_open30"] = vo[:, :30].sum(axis=1) / vsd
        f["im_vshare_close30"] = vo[:, 210:].sum(axis=1) / vsd
        f["im_vshare_last5"] = vo[:, 235:].sum(axis=1) / vsd
        f["im_vshare_am"] = vo[:, :120].sum(axis=1) / vsd
        r2 = r ** 2
        f["im_rv"] = np.nansum(r2, axis=1)
        up = np.nansum(np.where(r > 0, r2, 0.0), axis=1); dn = np.nansum(np.where(r < 0, r2, 0.0), axis=1)
        f["im_updown"] = np.where((up > 0) & (dn > 0), np.log(up / np.where(dn > 0, dn, np.nan)), np.nan)
        mu = np.nanmean(r, axis=1); c = r - mu[:, None]
        m2 = np.nanmean(c ** 2, axis=1); m3 = np.nanmean(c ** 3, axis=1); m4 = np.nanmean(c ** 4, axis=1)
        okm = (m2 > 0) & (n >= 30)
        f["im_rskew"] = np.where(okm, m3 / m2 ** 1.5, np.nan)
        f["im_rkurt"] = np.where(okm, m4 / m2 ** 2 - 3.0, np.nan)
        f["im_maxret"] = np.nanmax(r, axis=1); f["im_minret"] = np.nanmin(r, axis=1)
        # large-order proxies: only minutes with trades take part; if fewer than 60 traded minutes, or the top/bottom
        # amount groups overlap (ties at zero), the day is degenerate and the three fields are NaN instead of the
        # constants (1, 0, 0) that would otherwise turn them into an illiquidity proxy
        amn = np.where(am > 0, am, np.nan)
        n_amt = np.isfinite(amn).sum(axis=1)
        q90 = np.nanpercentile(amn, 90, axis=1); q80 = np.nanpercentile(amn, 80, axis=1); q20 = np.nanpercentile(amn, 20, axis=1)
        top10 = amn >= q90[:, None]; top20 = amn >= q80[:, None]; bot20 = amn <= q20[:, None]
        degenerate = (n_amt < 60) | (top20.sum(axis=1) > 96) | (top20 & bot20).any(axis=1)
        asd = np.where(as_ > 0, as_, np.nan)
        f["im_bigamt_share"] = np.where(degenerate, np.nan, np.where(top10, am, 0.0).sum(axis=1) / asd)
        f["im_big_ret"] = np.where(degenerate, np.nan, np.nanmean(np.where(top20, r, np.nan), axis=1) - np.nanmean(np.where(bot20, r, np.nan), axis=1))
        vo_top = np.where(top20, vo, 0.0).sum(axis=1)
        vw_top = np.where(top20, am, 0.0).sum(axis=1) / np.where(vo_top > 0, vo_top, np.nan)
        vw_all = as_ / vsd
        f["im_big_vwap_dev"] = np.where(degenerate, np.nan, vw_top / vw_all - 1)
        f["im_pv_corr"] = _row_corr(r, vo)
        f["im_pv_corr_lag"] = _row_corr(r[:, 1:], vo[:, :-1])
        pos = np.arange(BARS, dtype=float)
        f["im_vol_centroid"] = (vo * pos[None, :]).sum(axis=1) / vsd / (BARS - 1)
        share = vo / vsd[:, None]
        f["im_vol_herf"] = BARS * (share ** 2).sum(axis=1)
        f["im_vol_cv"] = vo.std(axis=1) / np.where(vo.mean(axis=1) > 0, vo.mean(axis=1), np.nan)
        f["im_zero_share"] = np.nanmean(np.where(np.isfinite(r), (r == 0).astype(float), np.nan), axis=1)
        f["im_pos_share"] = np.nanmean(np.where(np.isfinite(r), (r > 0).astype(float), np.nan), axis=1)
        absr = np.abs(r)
        amihud = np.nanmean(absr, axis=1) / np.where(np.nanmean(amn, axis=1) > 0, np.nanmean(amn, axis=1), np.nan)
        f["im_amihud"] = np.where(amihud > 0, np.log(amihud), np.nan)
        f["im_path_eff"] = np.abs(cl[:, 239] / op[:, 0] - 1) / np.where(np.nansum(absr, axis=1) > 0, np.nansum(absr, axis=1), np.nan)
        f["im_close_vwap"] = cl[:, 239] / (vw_all * qfq) - 1
        runmax = np.maximum.accumulate(np.where(np.isfinite(cl), cl, -np.inf), axis=1)
        f["im_mdd"] = np.nanmin(cl / np.where(runmax > 0, runmax, np.nan) - 1, axis=1)
        hl = (np.nanargmax(np.where(np.isfinite(hi), hi, -np.inf), axis=1) - np.nanargmin(np.where(np.isfinite(lo), lo, np.inf), axis=1)) / (BARS - 1)
        f["im_hl_time"] = np.where(np.isfinite(hi).any(axis=1) & np.isfinite(lo).any(axis=1), hl, np.nan)
        for k in f:
            v = np.where(bad, np.nan, f[k])
            f[k] = np.where(np.isfinite(v), v, np.nan).astype(np.float32)
    return f


FEATURE_VERSION = 2  # bump whenever day_features changes; files written by an older version are rebuilt


def aggregate_stock(path: str, out_dir: str, start: str, force: bool = False) -> tuple[str, int, int, str]:
    code = os.path.basename(path)[:-8]
    if not PATTERN.match(code):
        return code, 0, 0, "skipped"
    out = os.path.join(out_dir, f"{code.upper()}.parquet")
    if os.path.exists(out) and not force:
        try:
            meta = pq.read_schema(out).metadata or {}
            if meta.get(b"feature_version") == str(FEATURE_VERSION).encode():
                return code, -1, 0, "exists"
        except Exception:  # noqa: BLE001
            pass
    try:
        t = pq.read_table(path, columns=["datetime", "open", "high", "low", "close", "volume", "amount", "qfq_ratio"],
                          filters=[("datetime", ">=", pd.Timestamp(start))]).to_pandas()
        if t.empty:
            return code, 0, 0, "empty"
        day = t["datetime"].dt.normalize().to_numpy()
        days, first, counts = np.unique(day, return_index=True, return_counts=True)
        ok = counts == BARS
        irregular = int((~ok).sum())
        rows = np.concatenate([np.arange(s, s + BARS) for s, k in zip(first[ok], counts[ok])]) if ok.any() else np.array([], int)
        n = int(ok.sum())
        if n == 0:
            return code, 0, irregular, "no regular days"
        if (day[rows].reshape(n, BARS) != days[ok][:, None]).any():
            return code, 0, irregular, "error: bars of one day are not contiguous/sorted in the file"
        def mat(col):
            return t[col].to_numpy(np.float64)[rows].reshape(n, BARS)
        qfq = t["qfq_ratio"].to_numpy(np.float64)[first[ok]]
        qfq_last = t["qfq_ratio"].to_numpy(np.float64)[first[ok] + BARS - 1]
        if not np.allclose(qfq, qfq_last, rtol=1e-6, equal_nan=True):
            return code, 0, irregular, "error: qfq_ratio varies within a day"
        f = day_features(mat("open"), mat("high"), mat("low"), mat("close"), mat("volume"), mat("amount"), qfq)
        d = pd.DataFrame(f); d.insert(0, "date", pd.DatetimeIndex(days[ok]))
        if irregular:
            extra = pd.DataFrame({"date": pd.DatetimeIndex(days[~ok])})
            d = pd.concat([d, extra], ignore_index=True).sort_values("date")
        d["code"] = code.upper()
        import pyarrow as pa
        table = pa.Table.from_pandas(d, preserve_index=False)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), b"feature_version": str(FEATURE_VERSION).encode()})
        pq.write_table(table, out)
        return code, n, irregular, "ok"
    except Exception as exc:  # noqa: BLE001
        return code, 0, 0, f"error: {exc}"[:200]


def build(source: str, out_dir: str, start="2015-01-01", workers=8, log=print, force=False):
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(os.path.join(source, f) for f in os.listdir(source) if f.endswith(".parquet"))
    log(f"{len(files)} minute files -> {out_dir} (feature version {FEATURE_VERSION}, force={force})")
    done = rows = irregular = errors = 0; t0 = time.time(); errs = []; skipped = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(aggregate_stock, f, out_dir, start, force) for f in files]
        for fut in as_completed(futs):
            code, n, irr, status = fut.result()
            done += 1
            if status.startswith("error"):
                errors += 1; errs.append({"code": code, "error": status})
            elif n == -1:
                skipped += 1
            elif n > 0:
                rows += n; irregular += irr
            elif status == "no regular days":
                irregular += irr
            if done % 250 == 0:
                log(f"{done}/{len(files)} days={rows} irregular={irregular} errors={errors} ({time.time() - t0:.0f}s)")
    manifest = {"source": source, "start": start, "files": len(files), "days": rows, "irregular_days": irregular, "errors": errs, "skipped_existing": skipped,
                "feature_version": FEATURE_VERSION, "fields": list(MINUTE_FIELDS), "elapsed_s": round(time.time() - t0), "built": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    log(f"finished {done} files days={rows} irregular={irregular} errors={errors} in {time.time() - t0:.0f}s")
    return manifest


def assemble(daily_dir: str, panel_dir: str, log=print):
    """Write one wide float32 parquet per field into panel_dir, aligned to the panel's dates x codes."""
    with open(os.path.join(panel_dir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    dates = pd.DatetimeIndex(meta["dates"]); codes = list(meta["codes"])
    cidx = {c: i for i, c in enumerate(codes)}
    didx = pd.Series(np.arange(len(dates)), index=dates)
    arrays = {k: np.full((len(dates), len(codes)), np.nan, np.float32) for k in MINUTE_FIELDS}
    files = sorted(glob.glob(os.path.join(daily_dir, "*.parquet")))
    n = 0; t0 = time.time()
    for f in files:
        code = os.path.basename(f)[:-8]
        j = cidx.get(code)
        if j is None:
            continue
        t = pd.read_parquet(f)
        t["date"] = pd.to_datetime(t["date"])
        t = t[t["date"].isin(didx.index)]
        if t.empty:
            continue
        ri = didx.loc[t["date"]].to_numpy()
        for k in MINUTE_FIELDS:
            arrays[k][ri, j] = t[k].to_numpy(np.float32) if k in t.columns else np.nan
        n += 1
        if n % 1000 == 0:
            log(f"assembled {n} stocks ({time.time() - t0:.0f}s)")
    cov = {}
    for k, a in arrays.items():
        pd.DataFrame(a, index=dates, columns=codes).to_parquet(os.path.join(panel_dir, f"{k}.parquet"))
        cov[k] = float(np.isfinite(a).mean())
    meta["fields"] = sorted(set(meta.get("fields", [])) | set(MINUTE_FIELDS))
    meta["minute_fields"] = {"source": daily_dir, "fields": list(MINUTE_FIELDS), "assembled": time.strftime("%Y-%m-%dT%H:%M:%S"), "cell_coverage": cov}
    tmp = os.path.join(panel_dir, "meta.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    os.replace(tmp, os.path.join(panel_dir, "meta.json"))
    log(f"assembled {n} stocks into {panel_dir}; cell coverage " + ", ".join(f"{k[3:]}={v:.2f}" for k, v in list(cov.items())[:6]) + " ...")
    return cov


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build"); s.add_argument("--source", default=SOURCE_DEFAULT); s.add_argument("--out", required=True); s.add_argument("--start", default="2015-01-01"); s.add_argument("--workers", type=int, default=8); s.add_argument("--force", action="store_true")
    s = sub.add_parser("assemble"); s.add_argument("--daily", required=True); s.add_argument("--panel-dir", required=True)
    s = sub.add_parser("test"); s.add_argument("--source", default=SOURCE_DEFAULT); s.add_argument("--code", default="sh600000"); s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)
    if a.cmd == "build":
        build(a.source, a.out, a.start, a.workers, log, force=a.force)
    elif a.cmd == "assemble":
        assemble(a.daily, a.panel_dir, log)
    else:
        os.makedirs(a.out, exist_ok=True)
        t0 = time.time(); print(aggregate_stock(os.path.join(a.source, f"{a.code}.parquet"), a.out, "2015-01-01"), f"{time.time() - t0:.2f}s")
        d = pd.read_parquet(os.path.join(a.out, f"{a.code.upper()}.parquet"))
        print(d.describe().T.round(4).to_string())


if __name__ == "__main__":
    main()

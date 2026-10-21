"""Build the auxiliary arrays for the aligned-objective GRU experiments (run locally, upload the npz).

For every 15:00 row of the frozen hourly panel this stores, on the panel's own stock axis:
  vol, turn, logcap, beta60  -- the production neutralisation exposures (risk_context.npz, same file the
                                cluster inference used, sha 5b2065...);
  bw                         -- PIT CSI500 official weight (Tushare index_weight month-end file), taken from the
                                latest month-end at least LAG_DAYS calendar days before the signal date,
                                renormalised over that day's members; NaN for members without a published weight;
  y4ema, y4ema_end_ns        -- sum_{j=0..9} 0.8^j * y4(15:00 of day d+j); NaN unless all ten terms are finite.
                                End time = label end of the tenth term (used to purge rows at split boundaries).
No feature or label of the panel is modified; the training code only reads these arrays.

Usage: python -I gru_aligned_20261019/build_aux.py  (from the project root)
"""
from pathlib import Path
import hashlib, json, sys
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'gru_research/artifacts/dataset'
RISK = ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz'
RISK_SHA = '5b2065310e2cfdb8277f17db891cbb132a0c786c4f736f721e88f3d541d0ccc5'
WEIGHTS = ROOT / 'outputs/csi500_index_weight_tushare_official_monthly.csv'
OUT = Path(__file__).resolve().parent / 'aux' / 'aligned_aux_20261019.npz'
LAG_DAYS = 10
EMA_DECAY, EMA_TERMS = 0.8, 10
TZ = 'Asia/Shanghai'


def sha(p):
    with open(p, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    assert sha(RISK) == RISK_SHA, 'risk_context.npz differs from the file used by the cluster inference'
    meta = json.loads((DATASET / 'meta.json').read_text())
    times = np.load(DATASET / 'times.npy')
    members = np.load(DATASET / 'members.npy', mmap_mode='r')
    labels = np.load(DATASET / 'labels.npy', mmap_mode='r')
    label_end = np.load(DATASET / 'label_end_times.npy')
    symbols = json.loads((DATASET / 'symbols.json').read_text())
    clock = pd.to_datetime(times, utc=True).tz_convert(TZ)
    t15 = np.flatnonzero(clock.hour == 15)
    assert len(t15) * 4 == len(times) and np.all(np.diff(t15) == 4), 'every day must have four rows ending at 15:00'
    dates = clock[t15].strftime('%Y-%m-%d').to_numpy()
    q = np.load(RISK, allow_pickle=True)  # project-generated file, sha pinned above
    assert np.array_equal(q['times'], times)
    expo = {k: np.asarray(q[k][t15], dtype=np.float32) for k in ('volatility', 'turnover', 'log_cap', 'beta60')}

    w = pd.read_csv(WEIGHTS, dtype={'con_code': str})
    w['trade_date'] = pd.to_datetime(w['trade_date'].astype(str), format='mixed')
    w = w[w.index_code == '000905.SH']
    pos = {s: i for i, s in enumerate(symbols)}
    w = w[w.con_code.isin(pos)]
    month_ends = np.array(sorted(w.trade_date.unique()), dtype='datetime64[D]')
    by_date = {np.datetime64(d, 'D'): g for d, g in w.groupby('trade_date')}
    bw = np.full((len(t15), len(symbols)), np.nan, dtype=np.float32)
    used = []
    for r, d in enumerate(pd.to_datetime(dates)):
        ok = month_ends[month_ends + np.timedelta64(LAG_DAYS, 'D') < np.datetime64(d)]
        if not len(ok):
            used.append(None); continue
        g = by_date[ok[-1]]; used.append(str(pd.Timestamp(ok[-1]).date()))
        row = np.full(len(symbols), np.nan); row[[pos[c] for c in g.con_code]] = g.weight.to_numpy(float)
        m = np.asarray(members[t15[r]])
        row[~m] = np.nan
        s = np.nansum(row)
        if s > 0:
            bw[r] = (row / s).astype(np.float32)

    y4 = np.asarray(labels[t15, :, 3], dtype=np.float64)
    end4 = label_end[t15, 3]
    n = len(t15)
    y4ema = np.full_like(y4, np.nan); y4ema_end = np.full(n, np.iinfo(np.int64).max, dtype=np.int64)
    coef = EMA_DECAY ** np.arange(EMA_TERMS)
    for r in range(n - EMA_TERMS + 1):
        block = y4[r:r + EMA_TERMS]
        ok = np.isfinite(block).all(axis=0)
        y4ema[r, ok] = (coef[:, None] * block[:, ok]).sum(axis=0)
        y4ema_end[r] = end4[r + EMA_TERMS - 1]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, t15=t15.astype(np.int64), times15=times[t15], dates15=dates.astype('U10'),
                        vol=expo['volatility'], turn=expo['turnover'], logcap=expo['log_cap'], beta60=expo['beta60'],
                        bw=bw, y4ema=y4ema.astype(np.float32), y4ema_end_ns=y4ema_end,
                        symbols=np.array(symbols, dtype='U12'))
    mem15 = np.asarray(members[t15])
    report = {'schema': 'gru_aligned_aux_v1', 'file': OUT.name, 'sha256': sha(OUT), 'dataset_fingerprint_meta': meta.get('data_fingerprint'),
              'times_sha256': sha(DATASET / 'times.npy'), 'symbols_sha256': sha(DATASET / 'symbols.json'), 'risk_context_sha256': RISK_SHA,
              'weights_file_sha256': sha(WEIGHTS), 'weight_lag_calendar_days': LAG_DAYS, 'ema_decay': EMA_DECAY, 'ema_terms': EMA_TERMS,
              'rows15': int(n), 'first_date': dates[0], 'last_date': dates[-1],
              'member_coverage': {k: float(np.isfinite(v[mem15]).mean()) for k, v in [('vol', expo['volatility']), ('turn', expo['turnover']),
                                  ('logcap', expo['log_cap']), ('beta60', expo['beta60']), ('bw', bw), ('y4ema', y4ema)]},
              'bw_first_month_end_used': next((u for u in used if u), None), 'rows_without_weights': int(sum(u is None for u in used))}
    (OUT.parent / 'aux_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.exit(main())

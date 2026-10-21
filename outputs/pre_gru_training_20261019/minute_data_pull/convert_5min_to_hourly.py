"""DRAFT: baostock 5-minute bars -> GRU hourly rows/features aligned with gru_research/minute_data.py.

Input  : data/5min/<bs code>.parquet  (time = bar END, 09:35..11:30, 13:05..15:00, 48 bars/day; unadjusted;
         volume in shares, includes the opening call auction in the 09:35 bar and the closing auction in 15:00)
         data/adj_factor/<bs code>.parquet (baostock query_adjust_factor; backAdjustFactor effective from dividOperateDate)
Output : data/hourly_5m/<SYMBOL>.parquet with the same columns as minute_data.add_features_and_labels output
         (date, slot, signal_time UTC ns, hour_complete, 24 HOUR_FEATURES, window_*/execution_*/y1..y4) plus
         `bar_count` (instead of minute_count) and `source='baostock_5min'`.

Mapping to the 1-minute definition (1-min source is START-labelled 09:30..11:29, 13:00..14:59; 60 bars per hour):
  slot 0 = 1-min 09:30..10:29  <-> 5-min bars ending 09:35..10:30  (12 bars), signal 10:30
  slot 1 = 1-min 10:30..11:29  <-> 5-min bars ending 10:35..11:30, signal 11:30
  slot 2 = 1-min 13:00..13:59  <-> 5-min bars ending 13:05..14:00, signal 14:00
  slot 3 = 1-min 14:00..14:59  <-> 5-min bars ending 14:05..15:00, signal 15:00
Feature status under 5-minute bars (see README section 4):
  identical by construction : 14 hourly-OHLCV features (h_return_1 .. h_return_20) and h_last15_volume_share (= last 3 bars)
  same formula, different scale/noise (re-standardise, do NOT mix with 1-min-trained models):
      h_realized_volatility, h_minute_return_skew (12 pts), h_minute_return_max/min, h_up_semivariance_share,
      h_return_logvolume_corr (12 pts), h_path_efficiency, h_upminute_volume_share
  near-degenerate            : h_zero_volume_share (zero-volume 5-min bars are rare -> mostly 0)
  execution window (5 x 1-min opens at 09:31/10:31/13:01/14:01) has no 5-min equivalent:
      approximated by the VWAP (amount/volume) of the bar ending 09:40 / 10:35 / 13:05 / 14:05
      (slot 0 uses the 09:40 bar to keep the opening auction out of the execution price) -> labels y1..y4 change slightly.
Usage: python convert_5min_to_hourly.py [--codes sh.600000,...] [--limit N]
"""
import argparse, glob, os, sys
import numpy as np
import pandas as pd

ROOT = '/Users/leslie/Downloads/中证500量化60min'
sys.path.insert(0, os.path.join(ROOT, 'gru_research'))
import minute_data as md  # noqa: E402  (reuses HOUR_FEATURES, add_features_and_labels, calendar helper)

HERE = os.path.dirname(os.path.abspath(__file__))
IN5, INADJ, OUT = (os.path.join(HERE, 'data', d) for d in ('5min', 'adj_factor', 'hourly_5m'))
TZ = 'Asia/Shanghai'
# 5-min bar END minute-of-day per slot (12 bars each)
SLOT_ENDS = [np.arange(575, 631, 5), np.arange(635, 691, 5), np.arange(785, 841, 5), np.arange(845, 901, 5)]
EXEC_BAR_END = [580, 635, 785, 845]   # 09:40, 10:35, 13:05, 14:05
SIGNAL_MINUTES = md.SIGNAL_MINUTES


def symbol_of(bs_code):
    return f'{bs_code[3:]}.{bs_code[:2].upper()}'


def back_factor(times, adj):
    """backAdjustFactor in effect on each bar date (factor applies from dividOperateDate onward)."""
    if adj is None or adj.empty:
        return np.ones(len(times))
    a = adj.copy()
    a['d'] = pd.to_datetime(a['dividOperateDate'])
    a['f'] = pd.to_numeric(a['backAdjustFactor'], errors='coerce')
    a = a.dropna(subset=['f']).sort_values('d')
    days = pd.to_datetime(times.dt.date)
    idx = np.searchsorted(a['d'].values, days.values, side='right') - 1
    f = np.where(idx >= 0, a['f'].values[np.clip(idx, 0, None)], 1.0)
    return f


def hourly_rows(bars, adj, symbol):
    bars = bars.sort_values('time').drop_duplicates('time')
    if bars.empty:
        return pd.DataFrame()
    bars['factor'] = back_factor(bars['time'], adj)
    bars['date'] = bars['time'].dt.strftime('%Y-%m-%d')
    bars['mod'] = bars['time'].dt.hour * 60 + bars['time'].dt.minute
    rows = []
    for day, g in bars.groupby('date', sort=True):
        midnight = pd.Timestamp(day, tz=TZ).tz_convert('UTC').value
        raw = g[['open', 'high', 'low', 'close']].to_numpy(float)
        fac = g['factor'].to_numpy(float)
        adjp = raw * fac[:, None]
        vol, amt, mod = g['volume'].to_numpy(float), g['amount'].to_numpy(float), g['mod'].to_numpy()
        valid = (np.isfinite(raw).all(1) & (raw > 0).all(1) & np.isfinite(vol) & (vol >= 0)
                 & (raw[:, 1] >= raw[:, [0, 2, 3]].max(1) - 1e-8) & (raw[:, 2] <= raw[:, [0, 1, 3]].min(1) + 1e-8))
        for slot in range(4):
            exp = SLOT_ENDS[slot]
            pos = np.flatnonzero(np.isin(mod, exp))
            complete = len(pos) == 12 and np.array_equal(mod[pos], exp) and valid[pos].all() and vol[pos].sum() > 0
            row = {'date': day, 'symbol': symbol, 'slot': slot, 'signal_time': int(midnight + SIGNAL_MINUTES[slot] * 60 * 10**9),
                   'hour_complete': bool(complete), 'bar_count': len(pos)}
            row.update({k: np.nan for k in md.HOUR_FEATURES[14:]})
            row.update({k: np.nan for k in ['open', 'high', 'low', 'close', 'volume', 'raw_open', 'raw_high', 'raw_low',
                                             'raw_close', 'adjustment_factor']})
            if complete:
                o, h, l, c = adjp[pos[0], 0], adjp[pos, 1].max(), adjp[pos, 2].min(), adjp[pos[-1], 3]
                v = vol[pos]
                r = np.log(adjp[pos, 3] / np.r_[o, adjp[pos[:-1], 3]])
                sr, std, logv = float(np.square(r).sum()), r.std(), np.log1p(v)
                corr = float(np.corrcoef(r, logv)[0, 1]) if std > 1e-12 and logv.std() > 1e-12 else np.nan
                path = float(np.abs(np.diff(np.r_[o, adjp[pos, 3]])).sum())
                stats = [np.sqrt(sr), float(np.mean(((r - r.mean()) / std) ** 3)) if std > 1e-12 else np.nan,
                         r.max(), r.min(), md._ratio(np.square(r[r > 0]).sum(), sr), corr,
                         md._ratio(v[-3:].sum(), v.sum()),          # last 15 minutes = last 3 five-minute bars
                         float(np.mean(v == 0)), md._ratio(abs(c - o), path), md._ratio(v[r > 0].sum(), v.sum())]
                row.update(dict(zip(md.HOUR_FEATURES[14:], stats)))
                row.update({'open': o, 'high': h, 'low': l, 'close': c, 'volume': v.sum(), 'raw_open': raw[pos[0], 0],
                            'raw_high': raw[pos, 1].max(), 'raw_low': raw[pos, 2].min(), 'raw_close': raw[pos[-1], 3],
                            'adjustment_factor': fac[pos[-1]]})
            e = np.flatnonzero(mod == EXEC_BAR_END[slot])
            ok = len(e) == 1 and valid[e].all() and vol[e].sum() > 0 and amt[e].sum() > 0
            vwap_raw = float(amt[e].sum() / vol[e].sum()) if ok else np.nan
            row.update({'window_start_time': int(midnight + (EXEC_BAR_END[slot] - 5) * 60 * 10**9),
                        'window_end_time': int(midnight + EXEC_BAR_END[slot] * 60 * 10**9),
                        'window_complete': bool(ok), 'window_price': vwap_raw * fac[e[0]] if ok else np.nan,
                        'window_raw_price': vwap_raw, 'window_volume': float(vol[e].sum()) if ok else np.nan,
                        'window_tradable': bool(ok), 'window_all_flat': bool(ok and raw[e[0], 1] == raw[e[0], 2])})
            rows.append(row)
    return pd.DataFrame(rows)


def calendar():
    d = pd.read_parquet(os.path.join(ROOT, 'c5_core/data/A_Layer_Live/daily_source/sh600000.parquet'), columns=['date'])
    return sorted(pd.to_datetime(d['date']).dt.strftime('%Y-%m-%d'))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--codes', default=''); ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    files = sorted(glob.glob(os.path.join(IN5, '*.parquet')))
    if a.codes:
        files = [f for f in files if os.path.basename(f)[:-8] in a.codes.split(',')]
    if a.limit:
        files = files[:a.limit]
    cal = calendar()
    for f in files:
        code = os.path.basename(f)[:-8]
        bars = pd.read_parquet(f)
        pa_ = os.path.join(INADJ, f'{code}.parquet')
        adj = pd.read_parquet(pa_) if os.path.exists(pa_) else None
        hr = hourly_rows(bars, adj, symbol_of(code))
        if hr.empty:
            continue
        # expand to the true trading grid between first and last bar day so suspensions break the 80-bar window
        dates = [d for d in cal if hr.date.min() <= d <= hr.date.max()]
        hr = hr[hr.date.isin(dates)]
        try:
            feat = md.add_features_and_labels(hr, dates)
        except ValueError as e:   # next_known_open_time needs the iFinD calendar after the last day
            print(code, 'skip labels:', e); continue
        feat['source'] = 'baostock_5min'
        feat.to_parquet(os.path.join(OUT, f'{symbol_of(code)}.parquet'), index=False)
        print(code, len(feat), 'hour_complete', round(float(feat.hour_complete.mean()), 3))


if __name__ == '__main__':
    main()

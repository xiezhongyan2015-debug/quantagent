"""Extend the frozen hourly panel from public 1-minute bars (Sina) using the frozen aggregation code.

For every frozen symbol: Sina end-labelled 1-minute bars -> start-labelled minutes (SZ closing-auction minutes 14:57/14:58
synthesised as zero-volume bars at the previous close) -> qfq columns from the Tencent daily qfq/raw ratio of that day ->
gru_research.minute_data.aggregate_stock_day (unchanged frozen code) -> hourly rows. Adjusted prices are bridged onto the
frozen per-symbol adjustment basis with one constant per symbol (frozen factor / Tencent ratio on a common day). Overlap
sessions (2026-09-22, 09-23) are compared hour by hour with the frozen rows and reported, never overwritten.
Outputs (outputs/gru_live_extend_20261012/hourly/): new_hourly_rows.parquet (all symbols, dates >= FIRST_NEW),
overlap_check.csv, summary.json.
"""
import json, re, sys, os, io
from pathlib import Path
import numpy as np, pandas as pd, pyarrow as pa, pyarrow.parquet as pq
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from gru_research.minute_data import aggregate_stock_day, MINUTE_OFFSETS, TZ
ART = ROOT / 'gru_research/artifacts'; DATASET = ART / 'dataset'
SINA = Path(os.environ['EXT_SINA']) if os.environ.get('EXT_SINA') else ROOT / 'gru_research/trading_data/future_minute_probe/sina_20261012/raw'
TENCENT = Path(os.environ['EXT_TENCENT']) if os.environ.get('EXT_TENCENT') else ROOT / 'gru_research/trading_data/future_minute_probe/tencent_daily_20261012/raw'
OUT = ROOT / 'outputs/gru_live_extend_20261012/hourly'; OUT.mkdir(parents=True, exist_ok=True)
FROZEN_LAST = '2026-09-23'; OVERLAP = ['2026-09-22', '2026-09-23']
NS_MIN = 60 * 10**9


def read_frozen(sym, dates=None):
    """All partition files of one symbol's frozen hourly rows (DuckDB wrote several data_*.parquet per symbol)."""
    files = sorted((ART / f'minute_raw_by_symbol/symbol={sym}').glob('*.parquet'))
    if not files: return None
    f = pd.concat([pd.read_parquet(x) for x in files], ignore_index=True)
    return f[f.date.isin(dates)] if dates is not None else f


STORE = Path(os.environ['EXT_MINUTE_STORE']) if os.environ.get('EXT_MINUTE_STORE') else ROOT / 'gru_research/trading_data/minute_store'


def sina_minutes(sym):
    """Today's Sina window (~8 sessions) merged into the persistent per-symbol minute store, so sessions never drop out
    once the public window rolls forward. Store rows are keyed by bar start time; the newest fetch wins on overlap."""
    p = SINA / f'{sym}.json'; arr = []
    if p.exists():
        m = re.search(r'\((\[.*\]|null)\)', p.read_text(encoding='utf-8'), re.S); arr = json.loads(m.group(1)) or []
    df = pd.DataFrame(arr)
    if len(df):
        df['end'] = pd.to_datetime(df.day).dt.tz_localize(TZ)
        for c in ['open', 'high', 'low', 'close', 'volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
        df['start'] = df.end - pd.Timedelta(minutes=1); df = df[['start', 'open', 'high', 'low', 'close', 'volume']]
    STORE.mkdir(parents=True, exist_ok=True); sp = STORE / f'{sym}.parquet'
    if sp.exists():
        old = pd.read_parquet(sp); old['start'] = pd.to_datetime(old.start, utc=True).dt.tz_convert(TZ)
        df = pd.concat([old[~old.start.isin(df.start)] if len(df) else old, df], ignore_index=True) if len(df) else old
    if not len(df): return None
    df = df.sort_values('start').drop_duplicates('start', keep='last'); df = df[df.start >= pd.Timestamp(OVERLAP[0], tz=TZ)]
    df.to_parquet(sp, index=False)
    df['date'] = df.start.dt.strftime('%Y-%m-%d'); df['end'] = df.start + pd.Timedelta(minutes=1)
    return df


def tencent_ratio(sym):
    """Per-date qfq/raw close ratio from Tencent daily bars, plus raw daily volume (lots) for cross-checks."""
    out = {}
    try:
        raw = json.load(open(TENCENT / f'{sym}_raw.json', encoding='utf-8')); adj = json.load(open(TENCENT / f'{sym}_qfq.json', encoding='utf-8'))
    except FileNotFoundError: return out
    k = list(raw['data'])[0]; r = {x[0]: x for x in raw['data'][k].get('day', [])}; a = {x[0]: x for x in adj['data'][k].get('qfqday', adj['data'][k].get('day', []))}
    for d in r:
        if d in a and float(r[d][2]) > 0: out[d] = dict(ratio=float(a[d][2]) / float(r[d][2]), raw_close=float(r[d][2]), volume_lots=float(r[d][5]))
    return out


def minute_table(sym, day, bars, ratio, fetched_ns):
    """Build the frozen MINUTE_REQUIRED parquet bytes for one symbol-day (start-labelled, qfq basis)."""
    midnight = pd.Timestamp(day, tz=TZ)
    grid = {int(o): None for o in MINUTE_OFFSETS}
    for row in bars.itertuples():
        off = int((row.start - midnight).total_seconds() // 60)
        if off in grid: grid[off] = row
    rows = []; prev_close = None
    for off in MINUTE_OFFSETS:
        r = grid[int(off)]
        if r is None:
            # Sina omits minutes without trades (and the 14:57-14:59 closing-call minutes); the frozen archive carries them as
            # zero-volume bars at the previous close (h_zero_volume_share is a frozen feature). Synthesise after the first bar.
            if prev_close is not None: rows.append((off, prev_close, prev_close, prev_close, prev_close, 0.0, True))
            continue
        rows.append((off, r.open, r.high, r.low, r.close, r.volume, False)); prev_close = r.close
    if not rows: return None, 0
    f = pd.DataFrame(rows, columns=['off', 'open', 'high', 'low', 'close', 'volume', 'synth'])
    ts = midnight + pd.to_timedelta(f.off, unit='m')
    t = pa.table({'股票代码': pa.array([sym[:6]] * len(f)), 'datetime': pa.array(ts.dt.tz_localize(None).dt.strftime('%Y-%m-%d %H:%M:%S').to_numpy()),
                  'datetime_nano': pa.array(ts.dt.tz_convert('UTC').astype('int64').to_numpy(), pa.int64()),
                  'open': pa.array(f.open.to_numpy(float)), 'high': pa.array(f.high.to_numpy(float)), 'low': pa.array(f.low.to_numpy(float)), 'close': pa.array(f.close.to_numpy(float)),
                  'volume': pa.array(f.volume.to_numpy(float)), 'qfq_ratio': pa.array(np.full(len(f), ratio)),
                  'qfq_open': pa.array(f.open.to_numpy(float) * ratio), 'qfq_high': pa.array(f.high.to_numpy(float) * ratio), 'qfq_low': pa.array(f.low.to_numpy(float) * ratio), 'qfq_close': pa.array(f.close.to_numpy(float) * ratio),
                  'available_at_ns': pa.array(np.full(len(f), fetched_ns, dtype=np.int64), pa.int64())})
    buf = io.BytesIO(); pq.write_table(t, buf); return buf.getvalue(), int(f.synth.sum())


def main(first_new='2026-09-24'):
    symbols = json.loads((DATASET / 'symbols.json').read_text(encoding='utf-8'))
    fetch_log = {j['symbol']: j for j in map(json.loads, open(SINA.parent / 'fetch_log.jsonl'))}
    new_rows = []; overlap_rows = []; checks = []; summary = dict(symbols=len(symbols), with_sina=0, with_tencent=0, bridged=0, bridge_from_overlap=0, no_bridge=0, synth_minutes=0, audit_errors=0)
    for u, sym in enumerate(symbols):
        if not (SINA / f'{sym}.json').exists(): continue
        df = sina_minutes(sym)
        if df is None: continue
        summary['with_sina'] += 1
        ratios = tencent_ratio(sym)
        if ratios: summary['with_tencent'] += 1
        frozen = read_frozen(sym)
        if frozen is None: summary['no_frozen_rows'] = summary.get('no_frozen_rows', 0) + 1; frozen = pd.DataFrame(columns=['date', 'slot', 'hour_complete', 'adjustment_factor', 'close', 'raw_close', 'volume', 'h_realized_volatility', 'h_minute_return_skew', 'h_last15_volume_share', 'h_zero_volume_share', 'window_raw_price'])
        fetched_ns = int(pd.Timestamp(fetch_log[sym]['fetched_at_utc']).value) if sym in fetch_log else int(pd.Timestamp.utcnow().value)
        rows = []
        for day, bars in df.groupby('date'):
            if day < OVERLAP[0]: continue
            if day in ratios: ratio = ratios[day]['ratio']
            else:   # Tencent publishes the current session's daily bar late in the afternoon: carry the latest earlier ratio (corporate actions on that day are caught on the next run)
                earlier = [d for d in ratios if d < day]
                if not earlier: continue
                ratio = ratios[max(earlier)]['ratio']; summary['ratio_carried_symbol_days'] = summary.get('ratio_carried_symbol_days', 0) + 1
            table, synth = minute_table(sym, day, bars, ratio, fetched_ns)
            if table is None: continue
            summary['synth_minutes'] += synth
            out, audit = aggregate_stock_day((day, sym, table))
            if audit['error']: summary['audit_errors'] += 1; checks.append(dict(symbol=sym, date=day, kind='audit_error', detail=audit['error'])); continue
            for r in out: r['symbol'] = sym; r['sina_minute_count_day'] = len(bars); r['synth_minutes'] = synth
            rows.extend(out)
        if not rows: continue
        new = pd.DataFrame(rows)
        # ---- bridge adjusted basis: frozen factor / Tencent ratio on the latest common day with a frozen complete hour
        k = np.nan
        fr = frozen[frozen.hour_complete & frozen.date.isin(OVERLAP)]
        common = sorted(set(fr.date) & set(new[new.hour_complete].date))
        if common:
            d = common[-1]; ff = fr[fr.date == d].adjustment_factor.iloc[-1]; k = float(ff / ratios[d]['ratio']); summary['bridge_from_overlap'] += 1
        else:
            last = frozen[frozen.hour_complete]
            if len(last) and last.date.iloc[-1] in ratios: d = last.date.iloc[-1]; k = float(last.adjustment_factor.iloc[-1] / ratios[d]['ratio'])
        if np.isfinite(k):
            for c in ['open', 'high', 'low', 'close', 'window_price']: new[c] = new[c] * k
            new['adjustment_factor'] = new['adjustment_factor'] * k; new['bridge_k'] = k; summary['bridged'] += 1
        else: new['bridge_k'] = np.nan; summary['no_bridge'] += 1
        # ---- overlap comparison with frozen hours (raw OHLCV, adjusted close, microstructure stats)
        for day in OVERLAP:
            a = new[(new.date == day)].set_index('slot'); b = frozen[frozen.date == day].set_index('slot')
            for slot in range(4):
                if slot not in a.index or slot not in b.index: continue
                x, y = a.loc[slot], b.loc[slot]
                checks.append(dict(symbol=sym, date=day, slot=slot, kind='overlap', frozen_complete=bool(y.hour_complete), new_complete=bool(x.hour_complete),
                                   raw_close_new=x.raw_close, raw_close_frozen=y.raw_close, vol_new=x.volume, vol_frozen=y.volume,
                                   vol_ratio=(x.volume / y.volume) if (np.isfinite(y.volume) and y.volume > 0) else np.nan,
                                   adj_close_rel=(x.close / y.close - 1) if np.isfinite(y.close) and y.close > 0 else np.nan,
                                   rv_new=x.h_realized_volatility, rv_frozen=y.h_realized_volatility, skew_new=x.h_minute_return_skew, skew_frozen=y.h_minute_return_skew,
                                   last15_new=x.h_last15_volume_share, last15_frozen=y.h_last15_volume_share, zero_new=x.h_zero_volume_share, zero_frozen=y.h_zero_volume_share,
                                   exec_raw_new=x.window_raw_price, exec_raw_frozen=y.window_raw_price))
        new_rows.append(new[new.date >= first_new].assign(symbol=sym)); overlap_rows.append(new[new.date.isin(OVERLAP)].assign(symbol=sym))
        if u % 200 == 0: print('progress', u, len(new_rows), flush=True)
    allnew = pd.concat(new_rows, ignore_index=True) if new_rows else pd.DataFrame()
    allnew.to_parquet(OUT / 'new_hourly_rows.parquet', index=False)
    (pd.concat(overlap_rows, ignore_index=True) if overlap_rows else pd.DataFrame()).to_parquet(OUT / 'rebuilt_overlap_rows.parquet', index=False)
    chk = pd.DataFrame(checks); chk.to_csv(OUT / 'overlap_check.csv', index=False)
    ov = chk[chk.kind == 'overlap'] if len(chk) else chk
    if len(ov):
        both = ov[ov.frozen_complete & ov.new_complete]
        summary.update(overlap_hours=int(len(ov)), overlap_both_complete=int(len(both)),
                       raw_close_equal=float((np.isclose(both.raw_close_new, both.raw_close_frozen, atol=1e-6)).mean()),
                       vol_ratio_median=float(both.vol_ratio.median()), vol_ratio_p05=float(both.vol_ratio.quantile(.05)), vol_ratio_p95=float(both.vol_ratio.quantile(.95)),
                       adj_close_rel_maxabs=float(both.adj_close_rel.abs().max()),
                       rv_rel_err_median=float(((both.rv_new - both.rv_frozen).abs() / both.rv_frozen.abs()).median()),
                       last15_abs_err_median=float((both.last15_new - both.last15_frozen).abs().median()),
                       frozen_complete_new_incomplete=int((ov.frozen_complete & ~ov.new_complete).sum()), new_complete_frozen_incomplete=int((~ov.frozen_complete & ov.new_complete).sum()))
    summary.update(new_rows=int(len(allnew)), new_dates=sorted(allnew.date.unique().tolist()) if len(allnew) else [], new_complete_share=float(allnew.hour_complete.mean()) if len(allnew) else None)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8'); print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()

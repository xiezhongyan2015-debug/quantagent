"""Extend the frozen slow (daily) feature panel to new sessions with public daily data, reusing the frozen formulas.

Daily rows for current CSI500 members on sessions after the iFinD panel end (2026-09-24):
  prices  : Tencent qfq daily OHLC rescaled onto each symbol's iFinD 后复权 series (one constant per symbol, anchored on the
            last iFinD session); volume = Tencent lots x 100; amount = sum(minute close x minute volume) from Sina 1-minute
            bars; turnoverRatio = volume / float shares x 100 with float shares from today's Tencent quote snapshot.
  features: gru_research.slow_features.daily_state (DAILY16), the 64 frozen factor formulas via the legacy compiler,
            pit_join for FUND16 + AGES2 + d_log_total_cap (financial/market PIT files unchanged, carried forward).
Validation: overlap sessions (09-22..09-24) compare the rebuilt daily rows with iFinD, and the rebuilt 2026-09-23 slow row
with the frozen slow_raw_by_day. Outputs in outputs/gru_live_extend_20261012/daily/.
"""
import json, re, sys, os
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from gru_research import slow_features as SF
ART = ROOT / 'gru_research/artifacts'; DATASET = ART / 'dataset'
TENCENT = Path(os.environ['EXT_TENCENT']) if os.environ.get('EXT_TENCENT') else ROOT / 'gru_research/trading_data/future_minute_probe/tencent_daily_20261012/raw'
SINA = Path(os.environ['EXT_SINA']) if os.environ.get('EXT_SINA') else ROOT / 'gru_research/trading_data/future_minute_probe/sina_20261012/raw'
QT = (Path(os.environ['EXT_QT']) if os.environ.get('EXT_QT') else ROOT / 'gru_research/trading_data/future_minute_probe/tencent_qt_20261012') / 'qt_snapshot.json'
OUT = ROOT / 'outputs/gru_live_extend_20261012/daily'; OUT.mkdir(parents=True, exist_ok=True)
IFIND_END = '2026-09-24'
CAL = json.loads((ROOT / '实盘日更/config/calendar_2026.json').read_text(encoding='utf-8'))
NEW_DATES = None   # resolved in main(): exchange sessions after IFIND_END that exist in the Tencent daily download


def tencent_daily(sym):
    try: raw = json.load(open(TENCENT / f'{sym}_raw.json', encoding='utf-8')); adj = json.load(open(TENCENT / f'{sym}_qfq.json', encoding='utf-8'))
    except FileNotFoundError: return None
    k = list(raw['data'])[0]; r = pd.DataFrame([x[:6] for x in raw['data'][k].get('day', [])], columns=['date', 'open', 'close', 'high', 'low', 'lots'])
    a = pd.DataFrame([x[:6] for x in adj['data'][k].get('qfqday', adj['data'][k].get('day', []))], columns=['date', 'qopen', 'qclose', 'qhigh', 'qlow', 'qlots'])
    if r.empty or a.empty: return None
    d = r.merge(a, on='date'); 
    for c in d.columns[1:]: d[c] = pd.to_numeric(d[c], errors='coerce')
    return d


def sina_amount(sym):
    p = SINA / f'{sym}.json'
    if not p.exists(): return {}
    m = re.search(r'\((\[.*\]|null)\)', p.read_text(encoding='utf-8'), re.S); arr = json.loads(m.group(1)) or []
    if not arr: return {}
    df = pd.DataFrame(arr); df['date'] = pd.to_datetime(df.day).dt.strftime('%Y-%m-%d')
    for c in ['close', 'volume']: df[c] = pd.to_numeric(df[c], errors='coerce')
    full = df.groupby('date').size(); amt = (df.close * df.volume).groupby(df.date).sum()
    return {d: float(amt[d]) for d in amt.index if full[d] >= 230}   # only sessions with (almost) complete minute coverage


def main():
    global NEW_DATES
    symbols = json.loads((DATASET / 'symbols.json').read_text(encoding='utf-8'))
    probe = tencent_daily('000905.SH'); NEW_DATES = sorted(d for d in probe.date if d > IFIND_END and d in CAL)
    # intraday shadow runs (2026-10-15): a session that already has hourly rows but no Tencent daily bar yet (today, before
    # 16:00) still gets a slow row; every slow feature is shift(1), so that row is computed from data through the previous
    # session, exactly as the frozen formulas define it.  In the 16:30 run this adds nothing.
    hourly_path = ROOT / 'outputs/gru_live_extend_20261012/hourly/new_hourly_rows.parquet'
    if hourly_path.exists():
        hd = sorted(set(pd.read_parquet(hourly_path, columns=['date']).date.unique()) & set(CAL)); extra = [d for d in hd if d > IFIND_END and d not in NEW_DATES]
        if extra: NEW_DATES = sorted(set(NEW_DATES) | set(extra)); print('sessions without a daily bar yet (slow row from the previous session):', extra)
    print('new sessions', NEW_DATES)
    members_latest = pd.read_parquet(ART / 'minute_memberships.parquet'); last_mem_day = members_latest.date.max()
    members = sorted(members_latest[members_latest.date == last_mem_day].symbol)
    qt = {r['symbol']: r for r in json.load(open(QT, encoding='utf-8'))['rows']}
    daily, audit = SF.read_daily(history_start='2025-01-01', end=IFIND_END, symbols=None)
    daily['date_s'] = daily.date.dt.strftime('%Y-%m-%d')
    new_rows = []; overlap = []; summary = dict(members=len(members), tencent_missing=0, sina_amount_missing=0, qt_missing=0)
    for sym in members:
        td = tencent_daily(sym)
        if td is None: summary['tencent_missing'] += 1; continue
        hist = daily[daily.symbol == sym].sort_values('date')
        if hist.empty: continue
        anchor = hist.iloc[-1]; h = anchor.date_s
        row_h = td[td.date == h]
        if row_h.empty or not np.isfinite(row_h.qclose.iloc[0]) or row_h.qclose.iloc[0] <= 0: summary['anchor_missing'] = summary.get('anchor_missing', 0) + 1; continue
        scale = float(anchor.close / row_h.qclose.iloc[0])
        amounts = sina_amount(sym)
        q = qt.get(sym)
        float_shares = float(q['float_cap_yi']) * 1e8 / float(q['last']) if q and float(q.get('last') or 0) > 0 and float(q.get('float_cap_yi') or 0) > 0 else np.nan
        if not np.isfinite(float_shares): summary['qt_missing'] += 1
        unit = 1.0 if sym.startswith('688') else 100.0   # Tencent reports STAR-market daily volume in shares, others in lots (verified on the iFinD overlap)
        for r in td.itertuples():
            vol = r.lots * unit; amt = amounts.get(r.date, np.nan)
            rec = dict(thscode=sym, date=r.date, open=r.qopen * scale, high=r.qhigh * scale, low=r.qlow * scale, close=r.qclose * scale, volume=vol, amount=amt,
                       turnoverRatio=vol / float_shares * 100 if np.isfinite(float_shares) else np.nan)
            if r.date in NEW_DATES:
                if not np.isfinite(amt): summary['sina_amount_missing'] += 1
                new_rows.append(rec)
            elif r.date >= '2026-09-22' and r.date <= IFIND_END:
                ref = hist[hist.date_s == r.date]
                if len(ref):
                    ref = ref.iloc[0]
                    overlap.append(dict(symbol=sym, date=r.date, close_rel=rec['close'] / ref.close - 1, open_rel=rec['open'] / ref.open - 1, vol_ratio=vol / ref.volume if ref.volume > 0 else np.nan,
                                        amount_ratio=amt / ref.amount if np.isfinite(amt) and ref.amount > 0 else np.nan, turnover_ratio=rec['turnoverRatio'] / ref.turnoverRatio if np.isfinite(rec['turnoverRatio']) and ref.turnoverRatio > 0 else np.nan))
    new = pd.DataFrame(new_rows); ov = pd.DataFrame(overlap)
    new.to_parquet(OUT / 'new_daily_rows.parquet', index=False); ov.to_csv(OUT / 'daily_overlap_check.csv', index=False)
    if len(ov):
        summary.update(overlap_rows=int(len(ov)), close_rel_maxabs=float(ov.close_rel.abs().max()), close_rel_p99=float(ov.close_rel.abs().quantile(.99)), open_rel_p99=float(ov.open_rel.abs().quantile(.99)),
                       vol_ratio_median=float(ov.vol_ratio.median()), vol_ratio_p05=float(ov.vol_ratio.quantile(.05)), vol_ratio_p95=float(ov.vol_ratio.quantile(.95)),
                       amount_ratio_median=float(ov.amount_ratio.median()), amount_ratio_p05=float(ov.amount_ratio.quantile(.05)), amount_ratio_p95=float(ov.amount_ratio.quantile(.95)),
                       turnover_ratio_median=float(ov.turnover_ratio.median()), turnover_ratio_p05=float(ov.turnover_ratio.quantile(.05)), turnover_ratio_p95=float(ov.turnover_ratio.quantile(.95)))
    # ---- slow features on the extended panel
    ext = pd.concat([daily.drop(columns=['date_s']), new.rename(columns={'thscode': 'symbol'}).assign(date=pd.to_datetime(new.date)).assign(turnover=lambda f: f.turnoverRatio / 100.0)], ignore_index=True)
    assert not ext.duplicated(['date', 'symbol']).any()
    all_calendar = pd.DatetimeIndex(sorted(set(p.name[:10] for p in (SF.DAILY_ROOT / 'memberships').glob('*.json.gz')) | set(NEW_DATES)))
    calendar = all_calendar[(all_calendar >= '2025-01-01')]
    fields = SF.wide_fields(ext, calendar, symbols)
    target_dates = ['2026-09-23', '2026-09-24'] + NEW_DATES
    member_days = pd.DataFrame([(d, s) for d in target_dates for s in members], columns=['date', 'symbol'])
    financial = pd.read_parquet(SF.FUND_ROOT / 'financial_verified_19_fields.parquet'); market = pd.read_parquet(SF.FUND_ROOT / 'weekly_market_and_industry.parquet')
    raw = SF.pit_join(member_days, financial[financial.thscode.isin(members)], market[market.thscode.isin(members)], all_calendar)
    for name, values in SF.daily_state(fields).items(): raw[name] = SF.at_members(values, raw)
    raw['d_log_total_cap'] = np.log(raw.total_market_cap_cny.where(raw.total_market_cap_cny > 0))
    selection = json.loads((ART / 'slow_factor_selection.json').read_text(encoding='utf-8')); compiler = SF.load_legacy_compiler(); names = []
    for definition in selection['selected']:
        name = f"factor_{definition['id']}"; value = compiler.evaluate(definition['canonical'], fields=fields).shift(1); raw[name] = SF.at_members(value, raw).astype(np.float32); names.append(name)
    manifest = json.loads((ART / 'slow_manifest.json').read_text(encoding='utf-8')); slow_names = list(manifest['daily16']) + list(manifest['factors']) + list(manifest['fund16']) + list(manifest['ages2'])
    assert names == list(manifest['factors'])
    raw[slow_names] = raw[slow_names].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    raw['date'] = pd.to_datetime(raw.date).dt.strftime('%Y-%m-%d')
    raw[['date', 'symbol'] + slow_names].to_parquet(OUT / 'slow_new_rows.parquet', index=False)
    # ---- validate the rebuilt 2026-09-23 row against the frozen slow_raw_by_day (same inputs -> should agree)
    frozen = np.load(DATASET / 'slow_raw_by_day.npy', mmap_mode='r'); times = np.load(DATASET / 'times.npy'); days = pd.to_datetime(times[3::4], utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
    di = int(np.flatnonzero(days == '2026-09-23')[0]); idx = {s: i for i, s in enumerate(symbols)}
    chk = raw[raw.date == '2026-09-23']; a = chk[slow_names].to_numpy(float); b = np.stack([frozen[di, idx[s], :] for s in chk.symbol]).astype(float)
    both = np.isfinite(a) & np.isfinite(b); rel = np.abs(a - b) / np.maximum(np.abs(b), 1e-9)
    per_feature = {n: dict(finite_both=int(both[:, j].sum()), only_new=int((np.isfinite(a[:, j]) & ~np.isfinite(b[:, j])).sum()), only_frozen=int((~np.isfinite(a[:, j]) & np.isfinite(b[:, j])).sum()),
                            rel_err_median=float(np.nanmedian(np.where(both[:, j], rel[:, j], np.nan))) if both[:, j].any() else None, rel_err_max=float(np.nanmax(np.where(both[:, j], rel[:, j], np.nan))) if both[:, j].any() else None) for j, n in enumerate(slow_names)}
    summary.update(validate_0923=dict(rows=int(len(chk)), features_exact_1e6=int(sum(1 for v in per_feature.values() if v['rel_err_max'] is not None and v['rel_err_max'] < 1e-6)), features_total=len(slow_names),
                                      worst=sorted([(n, v['rel_err_max']) for n, v in per_feature.items() if v['rel_err_max'] is not None], key=lambda x: -x[1])[:8]),
                   new_slow_rows=int((raw.date.isin(NEW_DATES)).sum()), new_slow_finite_share=float(np.isfinite(raw.loc[raw.date.isin(NEW_DATES), slow_names].to_numpy(float)).mean()))
    (OUT / 'validate_0923_per_feature.json').write_text(json.dumps(per_feature, indent=1), encoding='utf-8'); (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding='utf-8'); print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()

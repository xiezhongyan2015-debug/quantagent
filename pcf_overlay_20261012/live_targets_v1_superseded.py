"""Daily target generator for the live account (PCF skeleton, optional c5 veto, optional signal tilt).

Inputs (all must be known before the execution session opens):
  --pcf        official 563030 PCF XML for trading day T (the console convention: executed on T+1 open)
  --c5         optional CSV with columns code,state (teammate's daily target file or the c5 export); codes like 600000.SH
  --signal     optional CSV with columns code,z (already neutralised z-scores; e.g. GRU 15:00 or blend); missing -> 0
  --holdings   optional JSON account snapshot {"positions": {"SH600000": shares, ...}, "nav": ...} for turnover precheck
  --mode       P0 | V5 | V5_tilt | tilt      (V5 = remove c5 excluded/vetoed names, renormalise)
  --k          tilt strength (default 0.5), z clipped to [-2, 2]
  --invested   target stock weight of NAV (default 0.98)
Outputs: <out>/target_<exec_date>.json (console external_file format), target_<exec_date>.csv, precheck_<exec_date>.json
Never places orders. Never reads prices after the signal date.
"""
import argparse, json, hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def parse_pcf(path):
    root = ET.fromstring(Path(path).read_bytes())
    meta = {x.tag: x.text for x in root if x.tag != 'ComponentList'}
    rows = []
    for x in root.findall('./ComponentList/Component'):
        r = {a.tag: a.text for a in x}
        rows.append(dict(code6=r['InstrumentID'].zfill(6), name=r.get('InstrumentName'), quantity=float(r['Quantity']),
                         flag=str(r['SubstitutionFlag']), sub_cash=float(r.get('SubstitutionCashAmount', 0) or 0)))
    df = pd.DataFrame(rows)
    df['code'] = df.code6 + np.where(df.code6.str.startswith(('5', '6')), '.SH', '.SZ')
    df['console_code'] = np.where(df.code6.str.startswith(('5', '6')), 'SH', 'SZ') + df.code6
    return df, dict(trading_day=meta['TradingDay'], pre_trading_day=meta['PreTradingDay'], nav_per_cu=float(meta['NAVperCU']),
                    nav=float(meta['NAV']), cu=float(meta['CreationRedemptionUnit']), est_cash=float(meta['EstimatedCashComponent']))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pcf', required=True); ap.add_argument('--prices', required=True, help='CSV code,close : closes of the PCF valuation day (PreTradingDay)')
    ap.add_argument('--c5'); ap.add_argument('--signal'); ap.add_argument('--holdings'); ap.add_argument('--mode', default='P0', choices=['P0', 'V5', 'V5_tilt', 'tilt'])
    ap.add_argument('--k', type=float, default=.5); ap.add_argument('--invested', type=float, default=.98)
    ap.add_argument('--exec-date', required=True); ap.add_argument('--signal-date', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--strategy-id', default='pcf_overlay_v4'); ap.add_argument('--min-weight', type=float, default=0.0005)
    a = ap.parse_args()
    if pd.Timestamp(a.signal_date) >= pd.Timestamp(a.exec_date): raise SystemExit('signal date must precede execution date')
    pcf, meta = parse_pcf(a.pcf)
    px = pd.read_csv(a.prices, dtype={'code': str}).set_index('code').close.astype(float)
    pcf = pcf[pcf.flag.isin(['1', '3'])].copy()                       # mandatory-cash names (2/4) are never bought
    pcf['close'] = pcf.code.map(px)
    missing = pcf[pcf.close.isna() | (pcf.close <= 0)]
    if len(missing): raise SystemExit('missing valuation prices for: ' + ','.join(missing.code))
    pcf['w'] = pcf.quantity * pcf.close / meta['nav_per_cu']
    w = pcf.set_index('code').w
    if w.sum() > a.invested: w = w / w.sum() * a.invested
    removed = pd.Series(dtype=float); info = {}
    if a.mode in ('V5', 'V5_tilt'):
        if not a.c5: raise SystemExit('V5 needs --c5')
        c5 = pd.read_csv(a.c5, dtype=str)
        bad = set(c5[c5.state.isin(['excluded', 'vetoed'])].code)
        removed = w[w.index.isin(bad)]; tot = w.sum(); w = w[~w.index.isin(bad)]; w = w / w.sum() * tot
        info['c5_states_seen'] = int(len(c5)); info['removed_names'] = int(len(removed)); info['removed_weight'] = float(removed.sum())
    if a.mode in ('tilt', 'V5_tilt'):
        if not a.signal: raise SystemExit('tilt needs --signal')
        sig = pd.read_csv(a.signal, dtype={'code': str}).set_index('code').z.astype(float)
        z = sig.reindex(w.index).fillna(0.0).clip(-2, 2)
        tot = w.sum(); w = w * (1 + a.k * z); w = w / w.sum() * tot
        info['signal_coverage'] = float(sig.reindex(w.index).notna().mean())
    w = w[w >= a.min_weight]
    w = w / w.sum() * min(a.invested, float(w.sum()) if w.sum() > a.invested else a.invested) if w.sum() > a.invested else w
    targets = [dict(code=('SH' if c.endswith('.SH') else 'SZ') + c[:6], weight=round(float(v), 6)) for c, v in w.sort_values(ascending=False).items()]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    payload = dict(strategy_id=a.strategy_id, execution_date=a.exec_date, signal_date=a.signal_date, mode='weights', targets=targets,
                   generated_from=dict(pcf=str(a.pcf), pcf_sha256=sha(a.pcf), pcf_trading_day=meta['trading_day'], pcf_valuation_day=meta['pre_trading_day'],
                                       c5=a.c5 and str(a.c5), c5_sha256=a.c5 and sha(a.c5), signal=a.signal and str(a.signal), signal_sha256=a.signal and sha(a.signal),
                                       mode=a.mode, k=a.k, invested=a.invested))
    (out / f'target_{a.exec_date}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    pd.DataFrame(targets).to_csv(out / f'target_{a.exec_date}.csv', index=False)
    pre = dict(execution_date=a.exec_date, signal_date=a.signal_date, mode=a.mode, names=len(targets), stock_weight=float(w.sum()),
               max_single=float(w.max()), max_single_code=str(w.idxmax()), weight_below_1pct_names=int((w < .01).sum()), **info)
    if a.holdings:
        h = json.loads(Path(a.holdings).read_text(encoding='utf-8-sig')); pos = h.get('positions', {}); nav = float(h.get('nav', 0) or 0)
        if nav > 0 and 'prices_now' in h:
            cur = {c: float(s) * float(h['prices_now'].get(c, 0)) / nav for c, s in pos.items()}
            tgt = {t['code']: t['weight'] for t in targets}
            codes = set(cur) | set(tgt); turn = sum(abs(tgt.get(c, 0) - cur.get(c, 0)) for c in codes)
            pre['planned_two_way_turnover'] = float(turn); pre['names_to_sell_out'] = int(sum(1 for c in cur if c not in tgt and cur[c] > 0))
    (out / f'precheck_{a.exec_date}.json').write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(pre, ensure_ascii=False))


if __name__ == '__main__':
    main()

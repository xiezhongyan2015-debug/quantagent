"""Q1: live executor 10-12..10-20 -- which target gaps were blocked by the 3% directional price guard.
Inputs (all local, read-only): raw/ledgers_redacted_*.json (redacted ledger extract from the cloud executor, no account ids),
raw/sina1m/*.json (Sina 1-minute, bar label = minute END time), raw/tencent_daily/*.json (raw daily [date,open,close,high,low,vol]).
Outputs: live_gap_minutes.csv (every gap stock x day), live_blocked.csv (blocked subset), live_daily_summary.csv."""
import json, glob, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; RAW = HERE / 'raw'
LEDGER = sorted(RAW.glob('ledgers_redacted_*.json'))[-1]
G = 0.03
def dot(c): return c[2:] + '.' + c[:2]
def daily(sym):
    rows = json.loads((RAW / 'tencent_daily' / f'{sym}.json').read_text())
    f = pd.DataFrame([r[:6] for r in rows], columns=['date', 'open', 'close', 'high', 'low', 'vol']).set_index('date')
    return f.astype(float)
IDX = daily('000905.SH')
def minutes(sym):
    p = RAW / 'sina1m' / f'{sym}.json'
    if not p.exists(): return None
    f = pd.DataFrame(json.loads(p.read_text()))
    if f.empty: return None
    f['t'] = pd.to_datetime(f.day); f[['open', 'high', 'low', 'close']] = f[['open', 'high', 'low', 'close']].astype(float)
    f['date'] = f.t.dt.strftime('%Y-%m-%d'); return f
led = json.load(open(LEDGER)); days = sorted(led['days'])
alldays = list(IDX.index)
def fwd(d, sym, k):
    """close(D) -> close(D+k) excess over CSI500 index; NaN if not yet available."""
    try:
        dd = daily(sym); i = alldays.index(d)
        if i + k >= len(alldays): return np.nan
        e = alldays[i + k]
        if e not in dd.index or d not in dd.index: return np.nan
        return dd.loc[e, 'close'] / dd.loc[d, 'close'] - IDX.loc[e, 'close'] / IDX.loc[d, 'close']
    except Exception: return np.nan
rows = []
for day in days:
    r = led['days'][day]; nav = float(r['nav']); base = r.get('baseline_positions') or {}; pos = r['positions'] or {}
    tr = pd.Timestamp(r['transition_at']).tz_convert('Asia/Shanghai').tz_localize(None)
    end = pd.Timestamp(r['checked_at']).tz_convert('Asia/Shanghai').tz_localize(None)
    pilot = {a['code'] for a in r['attempts'] if a['phase'] != 'formal'}
    for code, t in r['targets'].items():
        q0 = base.get(code, 0); gap = t['quantity'] - q0
        if gap == 0: continue
        sym = dot(code); ref = float(t['reference_price']); side = 1 if gap > 0 else 2
        att = [a for a in r['attempts'] if a['code'] == code and a['phase'] == 'formal']
        filled = sum((a['filled'] or 0) for a in att)
        fp = [(float(a['price']), a['filled']) for a in att if a['filled']]
        fill_dev = (sum(x * q for x, q in fp) / sum(q for _, q in fp) / float(t['reference_price']) - 1) if fp else np.nan
        first = pd.Timestamp(att[0]['submitted_at']).tz_convert('Asia/Shanghai').tz_localize(None) if att else pd.NaT
        final_gap = t['quantity'] - pos.get(code, 0)
        dd = daily(sym); o = dd.loc[day] if day in dd.index else None
        m = minutes(sym); md = m[m.date == day] if m is not None else None
        adv = (lambda p: p / ref - 1) if side == 1 else (lambda p: 1 - p / ref)   # adverse deviation (positive = worse for us)
        rec = dict(day=day, code=code, side='buy' if side == 1 else 'sell', new_or_exit=('new' if q0 == 0 else 'exit' if t['quantity'] == 0 else 'adjust'),
                   gap_shares=gap, ref=ref, gap_cny=abs(gap) * ref, gap_pct_nav=abs(gap) * ref / nav * 100,
                   open=o.open if o is not None else np.nan, open_dev=(o.open / ref - 1) if o is not None else np.nan,
                   day_high_dev=(o.high / ref - 1) if o is not None else np.nan, day_low_dev=(o.low / ref - 1) if o is not None else np.nan,
                   close_dev=(o.close / ref - 1) if o is not None else np.nan,
                   attempts=len(att), filled_shares=filled, fill_price_dev=fill_dev, zero_fill_cancels=sum(1 for a in att if a['state'] == '8' and not a['filled']), first_order=first.strftime('%H:%M:%S') if att else '',
                   final_gap_shares=final_gap, final_gap_cny=abs(final_gap) * ref, final_gap_pct_nav=abs(final_gap) * ref / nav * 100,
                   final_deferred=r['deferred'].get(code, ''), transition=tr.strftime('%H:%M:%S'), ledger_end=end.strftime('%H:%M:%S'), pilot=code in pilot)
        if md is not None and len(md):
            # Minutes from the formal transition until min(ledger end, 14:50): adverse deviation by minute close.
            w = md[(md.t > tr) & (md.t <= min(end, pd.Timestamp(day + ' 14:50:00')))]
            a = adv(w.close.to_numpy()); blocked = a > G + 1e-12
            # until first fill (or ledger end if never filled)
            if att and filled: stop = first
            else: stop = end
            w2 = w[w.t <= stop]; a2 = adv(w2.close.to_numpy())
            rec.update(minutes_in_window=len(w), minutes_blocked=int(blocked.sum()), share_blocked=float(blocked.mean()) if len(w) else np.nan,
                       blocked_before_first_order=int((a2 > G + 1e-12).sum()),
                       adv_at_transition=float(adv(md[md.t <= tr + pd.Timedelta(minutes=1)].close.iloc[-1])) if (md.t <= tr + pd.Timedelta(minutes=1)).any() else np.nan,
                       adv_max_window=float(a.max()) if len(a) else np.nan, adv_min_window=float(a.min()) if len(a) else np.nan,
                       intraday_max_dev=float(md.high.max() / ref - 1), intraday_min_dev=float(md.low.min() / ref - 1),
                       adv_at_1450=float(adv(md[md.t <= pd.Timestamp(day + ' 14:50:00')].close.iloc[-1])) if (md.t <= pd.Timestamp(day + ' 14:50:00')).any() else np.nan,
                       last_minute=str(md.t.iloc[-1].time()))
        for k in (1, 3, 5): rec[f'fwd{k}_excess'] = fwd(day, sym, k)
        rec['day0_open_to_close_excess'] = ((o.close / o.open) - IDX.loc[day, 'close'] / IDX.loc[day, 'open']) if (o is not None and day in IDX.index) else np.nan
        rows.append(rec)
A = pd.DataFrame(rows); A.to_csv(HERE / 'live_gap_minutes.csv', index=False, float_format='%.6g')
crit = (A.final_deferred.str.contains('3%')) | (A.adv_at_transition.fillna(0) > G) | (A.blocked_before_first_order.fillna(0) >= 5)
blk = A[crit].copy()
blk['blocked_kind'] = np.where(blk.final_gap_shares != 0, '收盘仍未完成', '一度被挡后完成')
sign = np.where(blk.side == 'buy', -1, 1)   # unfinished buy misses +ret; unfinished sell keeps exposure
for k in (1, 3, 5): blk[f'residual_impact{k}_cny'] = sign * blk.final_gap_cny * blk[f'fwd{k}_excess']
blk['signed_fwd1'] = np.where(blk.side == 'buy', 1, -1) * blk.fwd1_excess
blk.to_csv(HERE / 'live_blocked.csv', index=False, float_format='%.6g')
S = A.groupby('day').agg(gap_names=('code', 'size'), gap_cny=('gap_cny', 'sum'), final_gap_names=('final_gap_shares', lambda s: int((s != 0).sum())),
                         final_gap_cny=('final_gap_cny', 'sum')).reset_index()
S = S.merge(blk.groupby('day').agg(blocked_names=('code', 'size'), blocked_cny=('gap_cny', 'sum'),
                                   blocked_unfinished_cny=('final_gap_cny', 'sum')).reset_index(), on='day', how='left')
S.to_csv(HERE / 'live_daily_summary.csv', index=False, float_format='%.6g')
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
print(S)
cols = ['day', 'code', 'side', 'new_or_exit', 'gap_shares', 'gap_cny', 'gap_pct_nav', 'open_dev', 'intraday_max_dev', 'intraday_min_dev', 'adv_at_transition', 'minutes_blocked', 'minutes_in_window',
        'first_order', 'filled_shares', 'fill_price_dev', 'adv_at_1450', 'final_gap_shares', 'final_gap_cny', 'final_deferred', 'fwd1_excess', 'fwd3_excess', 'fwd5_excess', 'ledger_end', 'last_minute']
print(blk[cols].to_string())

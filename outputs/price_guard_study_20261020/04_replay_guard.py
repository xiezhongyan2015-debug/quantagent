"""Q2/Q3: replay the live construction (skeleton v5 = K1+T1+BA, 2025-01-03..2026-09-17, 415 execution days) with the executor's
directional price guard and with the candidate rule changes.

Inputs (read-only):
  targets_v5.npz                     daily target weights + execution inputs, written by 03_dump_targets.py (exact copy of replay_v5.py)
  pcf_overlay_20261012 daily_prices  raw open/high/low/close, isST
  gru_research/artifacts/minute_raw_by_symbol  hourly raw OHLC (4 bars/day: 09:30, 10:30, 13:00, 14:00), CSI500 members only
  raw/csi500_index_daily_2024_2026.json        Tencent CSI500 index daily (open, close)

Execution model (same as construction_replay.simulate except the fill rule):
  * desired units, 10k CNY deadband and floor release are copied verbatim from construction_replay.simulate;
  * baseline replay fills every trade at the adjusted OPEN of the execution day;
  * here each trade first checks the rule at the open; if blocked, it scans the four hourly bars and fills at the first bar whose
    range re-enters the allowed band, at the band boundary (or at the bar open if the bar opens inside the band); if never, the
    trade is skipped for the day (next day re-targets from actual holdings, like the live executor);
  * buys cannot fill at a limit-up price, sells cannot fill at a limit-down price (one-word boards stay unfilled under every rule);
  * names without hourly bars (non-CSI500 members) use the day's raw high/low as one bar (timing unknown; flagged).
Fill price in adjusted terms = adjusted open x raw fill / raw open. Cost 2 bp per side as in the replay.

Rules:  G_buy6 buys 6% / sells 3% | G_buyB15 buys clip(1.5 sigma20,3%,6%) / sells 3% | G_buyC5 buys 3%->5% from 14:00 / sells 3% |
        NG no guard | A3 3% (live) | A5, A6 static 5%/6% (sensitivity) | B10, B15 clip(m*sigma20, 3%, 6%) |
        C5 3% until 14:00 then 5% | D_open reference = today's open, 3% | E unguarded new buys and full exits |
        F_sell sells unguarded, buys 3% | F_buy buys unguarded, sells 3%
Outputs: replay_daily_<rule>.csv, replay_events_A3.csv, replay_rule_summary.csv, replay_rule_by_year.csv, replay_blocked_buckets.csv
Usage (project root): .pcf-research-venv/bin/python -I outputs/price_guard_study_20261020/04_replay_guard.py [open|h1]
  timing 'open' = first check and fill at the open (replay convention); 'h1' = first check at the 10:30 price (live-like late start), re-entry scan 10:30-15:00.
"""
from pathlib import Path
import json, sys, time
import numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore', category=RuntimeWarning)
import pyarrow.dataset as ds

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from pcf_overlay_20261012.data import OUT

CAPITAL = 10_000_000.; FEE = .0002; DEADBAND = 10_000.
INF = 9.99
t0 = time.time(); log = lambda *a: print(*a, round(time.time() - t0, 1), 's', flush=True)

Z = np.load(HERE / 'targets_v5.npz', allow_pickle=False)
dates = [str(x) for x in Z['dates']]; symbols = [str(x) for x in Z['symbols']]; all_days = [str(x) for x in Z['all_days']]
jpos = Z['jpos']; signal_marks = Z['signal_marks']; execp = Z['execp']; fills = Z['fills']; closes = Z['closes']
mem_sig = Z['mem_sig']; mem_exec = Z['mem_exec']; rawclose = Z['rawclose']; closeadj = Z['closeadj']; opadj = Z['opadj']
T_, U = Z['W_K1_T1_BA'].shape; lookup = {s: i for i, s in enumerate(symbols)}

prices = pd.read_parquet(OUT / 'daily_prices.parquet', columns=['date', 'symbol', 'open', 'high', 'low', 'close', 'isST'])
prices = prices[prices.date.isin(set(all_days))]
def wide(f): return prices.pivot(index='date', columns='symbol', values=f).reindex(index=all_days, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
rawopen = wide('open'); rawhigh = wide('high'); rawlow = wide('low'); isST = wide('isST'); rawclose2 = wide('close')
assert np.allclose(np.nan_to_num(rawclose2), np.nan_to_num(rawclose)), 'raw close mismatch with replay'
log('daily')

# hourly bars for execution days
hb = ds.dataset(ROOT / 'gru_research/artifacts/minute_raw_by_symbol', format='parquet', partitioning='hive').to_table(
    columns=['symbol', 'date', 'slot', 'raw_open', 'raw_high', 'raw_low', 'raw_close'], filter=ds.field('date') >= '2024-12-01').to_pandas()
hb = hb[hb.date.isin(set(dates)) & hb.symbol.isin(lookup.keys())]
tix = {d: k for k, d in enumerate(dates)}
HB = np.full((T_, U, 4, 4), np.nan)
HB[hb.date.map(tix).to_numpy(), hb.symbol.map(lookup).to_numpy(), hb.slot.to_numpy().astype(int)] = hb[['raw_open', 'raw_high', 'raw_low', 'raw_close']].to_numpy(float)
log('hourly', len(hb))

idx = pd.DataFrame(json.load(open(HERE / 'raw/csi500_index_daily_2024_2026.json')), columns=['date', 'open', 'close', 'high', 'low', 'vol']).set_index('date').astype(float)
idx_open = idx.open.reindex(all_days).to_numpy(); idx_close = idx.close.reindex(all_days).to_numpy()

ret = np.full_like(closeadj, np.nan); ret[1:] = closeadj[1:] / closeadj[:-1] - 1
def sigma20(j):
    return np.nanstd(ret[j - 20:j], axis=0, ddof=1)   # through the signal day j-1

limit_pct = np.array([.2 if s[:3] in ('300', '301', '688', '689') else .1 for s in symbols])

def r2(x): return np.floor(x * 100 + 0.5) / 100

def fill_rule(side, ref, op, p0, bars, s0, lim, g_slots, ref_open=False):
    """side +1 buy / -1 sell. First check at price p0 (the open, or the 10:30 price in timing 'h1'); if blocked, scan bars s0..3 and fill
    at the first bar that re-enters the band. g_slots[k] = allowed adverse deviation in slot k (g at the first check = g_slots[0]).
    Returns (filled, raw price, where) where = 'first' | 'bar<k>' | ''."""
    R = op if ref_open else ref
    if side > 0:
        b0 = R * (1 + g_slots[0]) + 1e-9
        if p0 <= b0 and p0 < lim - 1e-9: return True, p0, 'first'
        for k in range(s0, len(bars)):
            o, h, l, c = bars[k]
            if not np.isfinite(l): continue
            B = R * (1 + g_slots[k]) + 1e-9
            if l <= B and l < lim - 1e-9:
                p = o if (k > s0 or s0 > 0) and o <= B and o < lim - 1e-9 else min(B, lim - 0.01)
                return True, max(p, l), f'bar{k}'
        return False, np.nan, ''
    else:
        b0 = R * (1 - g_slots[0]) - 1e-9
        if p0 >= b0 and p0 > lim + 1e-9: return True, p0, 'first'
        for k in range(s0, len(bars)):
            o, h, l, c = bars[k]
            if not np.isfinite(h): continue
            B = R * (1 - g_slots[k]) - 1e-9
            if h >= B and h > lim + 1e-9:
                p = o if (k > s0 or s0 > 0) and o >= B and o > lim + 1e-9 else max(B, lim + 0.01)
                return True, min(p, h), f'bar{k}'
        return False, np.nan, ''

RULES = ['NG', 'A3', 'A5', 'A6', 'B10', 'B15', 'C5', 'D_open', 'E', 'F_sell', 'F_buy', 'G_buy6', 'G_buyB15', 'G_buyC5']

def guard_for(rule, side, kind, sig, nbars):
    """Per-slot allowed adverse deviation for one order."""
    if rule == 'NG': g = INF
    elif rule == 'A3': g = .03
    elif rule == 'A5': g = .05
    elif rule == 'A6': g = .06
    elif rule in ('B10', 'B15'):
        m = 1.0 if rule == 'B10' else 1.5; g = float(np.clip(m * sig, .03, .06)) if np.isfinite(sig) else .03
    elif rule == 'C5':
        return [.03, .03, .03, .05] if nbars == 4 else [.03]   # daily-bar fallback: no timing -> stays at 3%
    elif rule == 'D_open': g = .03
    elif rule == 'E': g = INF if kind in ('new', 'exit') else .03
    elif rule == 'F_sell': g = INF if side < 0 else .03
    elif rule == 'F_buy': g = INF if side > 0 else .03
    elif rule == 'G_buy6': g = .06 if side > 0 else .03                       # asymmetric: buys 6%, sells 3%
    elif rule == 'G_buyB15': g = (float(np.clip(1.5 * sig, .03, .06)) if np.isfinite(sig) else .03) if side > 0 else .03
    elif rule == 'G_buyC5':                                                    # buys 3% -> 5% from 14:00; sells 3%
        if side > 0 and nbars == 4: return [.03, .03, .03, .05]
        g = .03
    return [g] * nbars

def run(rule, W, keep_events=False, timing='open'):
    units = np.zeros(U); marks = np.zeros(U); cash = CAPITAL; rows = []; events = []
    for t, day in enumerate(dates):
        j = int(jpos[t]); sig = signal_marks[t]; valid = np.isfinite(sig) & (sig > 0); marks[valid] = sig[valid]; nav = cash + units @ marks
        wfull = W[t]; desired = np.divide(wfull * nav, marks, out=np.zeros(U), where=marks > 0); desired[~valid] = units[~valid]
        # ---- deadband + floor release: verbatim from construction_replay.simulate (legacy=False)
        keep = (units > 1e-10) & (desired > 1e-10) & (np.abs(desired - units) * marks < DEADBAND)
        mem = mem_sig[t]; val = lambda q, m=None: float((q * marks)[m].sum() if m is not None else (q * marks).sum())
        full = val(desired) / nav; full_m = val(desired, mem) / nav
        upper = nav * (full + .005); fs = max(.905, full - .005); fm = max(.805, full_m - .005)
        for _ in range(int(keep.sum())):
            book = np.where(keep, units, desired); over = val(book) - upper; short = nav * fs - val(book); short_m = nav * fm - val(book, mem)
            if over > 0: cand = keep & (units > desired); gap = (units - desired) * marks
            elif short > 0: cand = keep & (units < desired); gap = (desired - units) * marks
            elif short_m > 0: cand = keep & (units < desired) & mem; gap = (desired - units) * marks
            else: break
            if not cand.any(): break
            i = int(np.argmax(np.where(cand, gap, -np.inf))); keep[i] = False
        delta = np.where(keep, 0., desired - units)
        px = execp[t]; can = fills[t] & np.isfinite(px) & (px > 0)
        fpx = np.where(can, px, marks).astype(float)
        sg = sigma20(j)
        intents = np.flatnonzero(can & (np.abs(delta) * marks > 1e-6))
        st = dict(intent_cny=0., blocked_open_cny=0., unfilled_cny=0., late_cny=0., chase_cny=0., fill_adv_x_cny=0., beyond3_cny=0., beyond5_cny=0., beyond7_cny=0., fallback_cny=0.)
        for i in intents:
            side = 1 if delta[i] > 0 else -1; amt = abs(delta[i]) * marks[i]
            kind = 'new' if (side > 0 and units[i] <= 1e-10) else 'exit' if (side < 0 and desired[i] <= 1e-10) else 'adjust'
            ref = rawclose[j - 1, i]; op = rawopen[j, i]
            if not (np.isfinite(ref) and ref > 0 and np.isfinite(op) and op > 0):
                ok, fr, where = True, op, 'first'; nb = 0
            else:
                bars = HB[t, i]
                if np.isfinite(bars[:, 2]).all(): nb = 4
                else:
                    bars = np.array([[op, rawhigh[j, i], rawlow[j, i], rawclose[j, i]]]); nb = 1; st['fallback_cny'] += amt
                L = .05 if isST[j, i] == 1 else limit_pct[i]
                lim = r2(ref * (1 + L)) if side > 0 else r2(ref * (1 - L))
                g = guard_for(rule, side, kind, sg[i], nb)
                if timing == 'h1' and nb == 4: p0, s0 = bars[0, 3], 1      # first look at ~10:30 (end of the first hour)
                else: p0, s0 = op, 0
                ok, fr, where = fill_rule(side, ref, op, p0, bars, s0, lim, g, ref_open=(rule == 'D_open'))
            st['intent_cny'] += amt
            adv_open = side * (op / ref - 1) if (np.isfinite(ref) and ref > 0) else np.nan
            if where != 'first': st['blocked_open_cny'] += amt
            if not ok:
                delta[i] = 0.; st['unfilled_cny'] += amt
            else:
                fpx[i] = px[i] * fr / op if (np.isfinite(op) and op > 0) else px[i]
                adv = side * (fr / ref - 1) if (np.isfinite(ref) and ref > 0) else 0.
                st['fill_adv_x_cny'] += adv * amt
                if where != 'first': st['late_cny'] += amt
                if adv > .03 + 1e-9: st['beyond3_cny'] += amt; st['chase_cny'] += (adv - .03) * amt
                if adv > .05 + 1e-9: st['beyond5_cny'] += amt
                if adv > .07 + 1e-9: st['beyond7_cny'] += amt
            if keep_events and np.isfinite(adv_open):
                e = dict(date=day, symbol=symbols[i], side='buy' if side > 0 else 'sell', kind=kind, amount_cny=amt, weight_nav=amt / nav,
                         open_adv=adv_open, first_adv=side * (p0 / ref - 1) if nb == 4 else adv_open, sigma20=sg[i], hourly=nb == 4, filled=ok, where=where, fill_adv=side * (fr / ref - 1) if ok else np.nan)
                dec_adj = opadj[j, i] * (p0 / op) if (nb == 4 and np.isfinite(op) and op > 0) else opadj[j, i]   # price at the first check
                e['d0_x'] = closeadj[j, i] / dec_adj - idx_close[j] / idx_open[j]          # decision -> same-day close (index from its open; approx in h1)
                for k in (1, 3, 5):
                    jj = j + k
                    e[f'fwd{k}c_x'] = (closeadj[jj, i] / closeadj[j, i] - idx_close[jj] / idx_close[j]) if jj < len(all_days) and np.isfinite(closeadj[jj, i]) else np.nan
                events.append(e)
        sell = np.where(can, np.maximum(-delta, 0), 0)
        proceeds = sell * fpx; cash += float((proceeds * (1 - FEE)).sum()); units -= sell
        buy = np.where(can, np.maximum(delta, 0), 0); cost = float((buy * fpx).sum() * (1 + FEE))
        if cost > cash: buy *= max(cash, 0) / cost
        paid = buy * fpx; cash -= float(paid.sum() * (1 + FEE)); units += buy
        observed = np.isfinite(closes[t]) & (closes[t] > 0); marks[observed] = closes[t, observed]
        equity = units * marks; nav2 = cash + equity.sum()
        rows.append(dict(date=day, nav=nav2 / CAPITAL, turnover=float((paid.sum() + proceeds.sum()) / nav2), cash_weight=cash / nav2,
                         stock_weight=equity.sum() / nav2, member_nav_weight_exec=float(equity[mem_exec[t]].sum() / nav2),
                         target_gap_l1=float(np.abs(equity / nav2 - wfull).sum() / 2), **{k: v / nav for k, v in st.items()}))
    return pd.DataFrame(rows), pd.DataFrame(events)

def boot_ci(d, B=4000, block=20, seed=7):
    rng = np.random.default_rng(seed); n = len(d); out = np.empty(B)
    for b in range(B):
        starts = rng.integers(0, n, size=int(np.ceil(n / block)))
        ix = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        out[b] = d[ix].mean()
    return np.percentile(out, [2.5, 97.5])

if __name__ == '__main__':
    W = Z['W_K1_T1_BA']; TIMING = sys.argv[1] if len(sys.argv) > 1 else 'open'; SUF = '' if TIMING == 'open' else '_' + TIMING
    (HERE / 'replay_daily').mkdir(exist_ok=True); res = {}; ev = {}
    for rule in RULES:
        d, e = run(rule, W, keep_events=rule in ('A3', 'NG'), timing=TIMING)
        d.to_csv(HERE / 'replay_daily' / f'replay_daily_{rule}{SUF}.csv', index=False, float_format='%.8g'); res[rule] = d
        if len(e): ev[rule] = e
        log(TIMING, rule, round(d.nav.iloc[-1], 5))
    ref = pd.read_csv(HERE / 'replay_v5_rerun/arm_K1_T1_BA_daily.csv')
    log('NG vs original replay (open fills, no limit check) final NAV', round(res['NG'].nav.iloc[-1], 5), round(ref.nav.iloc[-1], 5))
    ev['A3'].to_csv(HERE / f'replay_events_A3{SUF}.csv', index=False, float_format='%.6g')
    AF = 244.
    rows = []; yrows = []
    base = res['A3']; rb = base.nav.pct_change().fillna(base.nav.iloc[0] - 1).to_numpy()
    for rule, d in res.items():
        r = d.nav.pct_change().fillna(d.nav.iloc[0] - 1).to_numpy(); diff = r - rb
        lo, hi = boot_ci(diff[1:])   # skip the initial build day
        rows.append(dict(rule=rule, final_nav=d.nav.iloc[-1], ann_ret=(d.nav.iloc[-1]) ** (AF / len(d)) - 1,
                         ann_vs_A3=diff[1:].mean() * AF, ci_lo=lo * AF, ci_hi=hi * AF, te_vs_A3=diff[1:].std() * np.sqrt(AF),
                         turnover_daily=d.turnover.iloc[1:].mean(), intent_pct_nav=d.intent_cny.iloc[1:].mean(),
                         blocked_open_pct_nav=d.blocked_open_cny.iloc[1:].mean(), unfilled_pct_nav=d.unfilled_cny.iloc[1:].mean(),
                         unfilled_share_of_intent=d.unfilled_cny.iloc[1:].sum() / d.intent_cny.iloc[1:].sum(),
                         late_fill_share=d.late_cny.iloc[1:].sum() / d.intent_cny.iloc[1:].sum(),
                         fill_adv_bp=1e4 * d.fill_adv_x_cny.iloc[1:].sum() / (d.intent_cny.iloc[1:].sum() - d.unfilled_cny.iloc[1:].sum()),
                         beyond3_pct_nav_per_day=d.beyond3_cny.iloc[1:].mean(), beyond5_pct_nav_per_day=d.beyond5_cny.iloc[1:].mean(),
                         beyond7_pct_nav_per_day=d.beyond7_cny.iloc[1:].mean(), chase_bp_nav_per_year=1e4 * d.chase_cny.iloc[1:].mean() * AF,
                         days_with_unfilled=int((d.unfilled_cny.iloc[1:] > 0).sum()), mean_target_gap_l1=d.target_gap_l1.iloc[1:].mean(),
                         min_member_weight=d.member_nav_weight_exec.min(), fallback_share=d.fallback_cny.iloc[1:].sum() / d.intent_cny.iloc[1:].sum()))
        for y in ('2025', '2026'):
            m = d.date.str.startswith(y).to_numpy(); m[0] = False
            yrows.append(dict(rule=rule, year=y, days=int(m.sum()), ann_vs_A3=diff[m].mean() * AF, unfilled_pct_nav=d.unfilled_cny[m].mean()))
    S = pd.DataFrame(rows); S.to_csv(HERE / f'replay_rule_summary{SUF}.csv', index=False, float_format='%.6g')
    Y = pd.DataFrame(yrows).pivot(index='rule', columns='year', values='ann_vs_A3'); Y.to_csv(HERE / f'replay_rule_by_year{SUF}.csv', float_format='%.6g')
    # ---- event-level view of the live rule
    e = ev['A3']; e = e[e.date != dates[0]].copy()
    e['blocked_open'] = e['where'] != 'first'; e['sgn'] = np.where(e.side == 'buy', 1, -1)
    e['signed_d0'] = e.sgn * e.d0_x
    for k in (1, 3, 5): e[f'signed_fwd{k}'] = e.sgn * e[f'fwd{k}c_x']
    e['bucket'] = pd.cut(e.first_adv, [-1, .03, .04, .05, .06, .08, 1], labels=['<=3%', '3-4%', '4-5%', '5-6%', '6-8%', '>8%'])
    g = e.groupby(['bucket', 'side'], observed=True)
    Bk = g.agg(n=('amount_cny', 'size'), amount_pct_nav_per_day=('weight_nav', lambda s: s.sum() / (len(dates) - 1)), filled_rate=('filled', 'mean'),
               late_fill_rate=('where', lambda s: (s.str.startswith('bar')).mean()), d0_signed=('signed_d0', 'mean'),
               fwd1_signed=('signed_fwd1', 'mean'), fwd3_signed=('signed_fwd3', 'mean'), fwd5_signed=('signed_fwd5', 'mean'),
               fwd5_signed_se=('signed_fwd5', lambda x: x.std() / np.sqrt(x.notna().sum()))).reset_index()
    Bk.to_csv(HERE / f'replay_blocked_buckets{SUF}.csv', index=False, float_format='%.6g')
    K = e[e.blocked_open].groupby(['kind', 'side']).agg(n=('amount_cny', 'size'), amount_pct_nav_per_day=('weight_nav', lambda s: s.sum() / (len(dates) - 1)),
               filled_rate=('filled', 'mean'), d0_signed=('signed_d0', 'mean'), fwd1_signed=('signed_fwd1', 'mean'), fwd3_signed=('signed_fwd3', 'mean'), fwd5_signed=('signed_fwd5', 'mean')).reset_index()
    K.to_csv(HERE / f'replay_blocked_by_kind{SUF}.csv', index=False, float_format='%.6g')
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    print(S.round(5).to_string()); print(Y.round(5)); print(Bk.round(4).to_string()); print(K.round(4).to_string())
    print('blocked at open share of intent:', e[e.blocked_open].weight_nav.sum() / e.weight_nav.sum(), 'never filled among blocked:', 1 - e[e.blocked_open].filled.mean(),
          'blocked days', e[e.blocked_open].date.nunique(), 'of', len(dates) - 1, 'hourly share', e.hourly.mean())

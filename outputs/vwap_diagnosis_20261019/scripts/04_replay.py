"""Part 2: holding-period replays under the frozen PROTOCOL.json.

Step 1 rebuilds the pilot_v1 account base locally and reproduces P0 and
VWAP_NULL_head4 with the extracted original engine (calibration).  Step 2 runs
the declared usages with a generalized engine that is first checked to equal
the original bit-for-bit on daily runs.
"""
import ast
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
import common as C

PROTOCOL = C.OUT / 'PROTOCOL.json'
protocol = json.loads(PROTOCOL.read_text())
for rel, h in protocol['inputs_sha256'].items():
    assert C.sha(C.ROOT / rel) == h, 'input changed since freeze: ' + rel
ENGINE = C.SNAP / 'HOME/gru_minute_vwap_20261014/account_reference_inputs_v1/candidate_replay.py'
PARAMS = {'capital': 10_000_000.0, 'fee_per_side': 0.0002, 'k': 0.5, 'min_trade_notional_cny': 1000.0,
          'first_signal_date': '2024-06-03', 'last_execution_date': '2026-09-17'}
STATS = {'annual_sessions': 252, 'block_days': 20, 'replicates': 2000, 'seed': 20261011, 'exclude_initial_build': True}
src = ENGINE.read_text()
orig = {'np': np, 'pd': pd, 'PARAMETERS': PARAMS, 'STATISTICS': STATS}
for node in ast.parse(src).body:
    if isinstance(node, ast.FunctionDef) and node.name in ('tilt_target', 'account', 'paired_summary'):
        exec(ast.get_source_segment(src, node), orig)

# ---------- base rebuild (798 PCF axis, 416 execution rows) ----------
p0 = np.load(C.PILOT / 'P0_plans.npz', allow_pickle=False)
dates, sdates, symbols = p0['dates'], p0['signal_dates'], p0['symbols']
reference = p0['target']
pq = pd.read_parquet(C.PARQUET, columns=['date', 'symbol', 'open_adjusted', 'close_adjusted', 'volume', 'amount', 'factor', 'isST'])
pq['amount'] = pd.to_numeric(pq['amount'], errors='coerce')
pq['vwap'] = np.where((pq.volume > 0) & (pq.amount > 0), pq.amount / pq.volume * pq.factor, np.nan)
cal = pd.Index(sorted(pq.date.unique()))
sym = pd.Index(symbols)


def panel(col):
    a = np.full((len(cal), len(sym)), np.nan)
    r = cal.get_indexer(pq.date); c = sym.get_indexer(pq.symbol); ok = c >= 0
    a[r[ok], c[ok]] = pq[col].to_numpy(float)[ok]
    return a


O, Cl, V, AMT, ST, VOL = (panel(c) for c in ('open_adjusted', 'close_adjusted', 'vwap', 'amount', 'isST', 'volume'))
ei = cal.get_indexer(dates.astype(str)); si = cal.get_indexer(sdates.astype(str))
assert (ei >= 0).all() and (si >= 0).all() and np.all(ei == si + 1)
prev_open, marks, prev_st = O[si], Cl[si], ST[si]
prev_amt = np.where(np.isfinite(AMT[si]), AMT[si], VOL[si])   # amendment 2: volume proxy where amount is absent
fill = (np.isfinite(prev_open) & (prev_open > 0) & np.isfinite(marks) & (marks > 0) &
        np.isfinite(prev_amt) & (prev_amt > 0) & (prev_st != 1))
vwap_exec = np.where(np.isfinite(V[ei]) & (V[ei] > 0), V[ei], O[ei])   # amendment 1: open fallback
base = {'dates': dates, 'signal_dates': sdates, 'symbols': symbols, 'reference': reference,
        'signal_marks': marks, 'closes': Cl[ei], 'fill': fill,
        'membership_at_execution_close': np.zeros(reference.shape, bool)}
EXEC = {'open': O[ei], 'vwap': vwap_exec}
vwap_cov = float(np.nansum(np.where(np.isfinite(V[ei]) & (V[ei] > 0), reference, 0)) / np.nansum(reference))

# ---------- scores on the account axis ----------
scores, axes = C.scores_cached()
srow = pd.Index(axes['dates']).get_indexer(sdates.astype(str)); assert (srow >= 0).all()
col = pd.Index(axes['symbols']).get_indexer(symbols); cov = col >= 0


def to_account(full):
    out = np.full(reference.shape, np.nan)
    out[:, cov] = full[np.ix_(srow, col[cov])]
    return out


def tilt(ref, z):
    return orig['tilt_target'](ref, z)


# ---------- generalized engine ----------
def book(targets, trade, capital, fee, exec_price):
    """Original account() arithmetic; orders only on rows where trade[t] is True."""
    units = np.zeros(len(symbols)); mk = np.zeros_like(units); cash = capital
    nav_out, traded_out, fee_out = [], [], []
    for t in range(len(dates)):
        signal = marks[t]; valid = np.isfinite(signal) & (signal > 0); mk[valid] = signal[valid]
        traded = 0.; fees = 0.
        if trade[t]:
            signal_nav = cash + units @ mk
            target = targets[t]
            desired = units.copy() if not np.isfinite(target).all() else np.divide(target * signal_nav, mk, out=np.zeros_like(units), where=mk > 0)
            desired[~valid] = units[~valid]
            delta = desired - units
            delta[np.abs(delta) * mk < PARAMS['min_trade_notional_cny']] = 0
            price = exec_price[t]
            can = fill[t] & np.isfinite(price) & (price > 0)
            price = np.where(can, price, mk)
            sell = np.where(can, np.maximum(-delta, 0), 0)
            proceeds = sell * price; cash += float((proceeds * (1 - fee)).sum()); units -= sell
            buy = np.where(can, np.maximum(delta, 0), 0)
            cost = float((buy * price).sum() * (1 + fee))
            if cost > cash: buy *= max(cash, 0) / cost
            paid = buy * price; cash -= float(paid.sum() * (1 + fee)); units += buy
            assert cash > -1e-6 and not np.any(units < -1e-10)
            traded = float(paid.sum() + proceeds.sum()); fees = traded * fee
        observed = np.isfinite(base['closes'][t]) & (base['closes'][t] > 0)
        mk[observed] = base['closes'][t, observed]
        nav_out.append(cash + float((units * mk).sum())); traded_out.append(traded); fee_out.append(fees)
    return np.array(nav_out), np.array(traded_out), np.array(fee_out)


def account_df(targets, fee, exec_name, sleeves=1):
    T = len(dates); navs = np.zeros(T); traded = np.zeros(T); fees = np.zeros(T)
    for j in range(sleeves):
        trade = np.array([t == 0 or (t >= j and (t - j) % sleeves == 0) for t in range(T)])
        n, tr, f = book(targets, trade, PARAMS['capital'] / sleeves, fee, EXEC[exec_name])
        navs += n; traded += tr; fees += f
    return pd.DataFrame({'date': dates.astype(str), 'nav': navs / PARAMS['capital'], 'nav_cny': navs,
                         'two_way_turnover_close_nav': traded / navs, 'fee_cny': fees})


# ---------- calibration ----------
calib = {'vwap_available_reference_weight_share': vwap_cov}
PARAMS['fee_per_side'] = 0.0002
arr_p0 = dict(base, execution_prices=EXEC['open'], scores=np.full(reference.shape, np.nan), valuation_dates=sdates,
              signal_time_ns=np.zeros(len(dates), np.int64), benchmark=np.ones(len(dates)))
d_p0, _, _ = orig['account'](arr_p0, 'P0')
sealed = to_account(C.load_signal('VWAP_NULL', 'head4')['scores'])
d_v4, _, _ = orig['account'](dict(arr_p0, scores=sealed), 'GRU_k05')
pil_p0 = pd.read_csv(C.PILOT / 'P0_daily.csv'); pil_v4 = pd.read_csv(C.PILOT / 'VWAP_NULL_head4_daily.csv')
calib['P0_max_abs_nav_diff'] = float(np.abs(d_p0.nav.to_numpy() - pil_p0.nav.to_numpy()).max())
calib['VWAP_NULL_head4_max_abs_nav_diff'] = float(np.abs(d_v4.nav.to_numpy() - pil_v4.nav.to_numpy()).max())
mine = orig['paired_summary'](d_p0, d_v4); theirs = orig['paired_summary'](pil_p0, pil_v4)
pilot_summary = json.loads((C.PILOT / 'SUMMARY.json').read_text())['metrics_by_window']['continuous2025_2026']['VWAP_NULL_head4_vs_P0']
calib['rebuilt'] = {'rel_cagr': mine['annualized_relative_cagr'], 'ci': mine['bootstrap']['relative_cagr_95pct'],
                    'turnover': mine['candidate_mean_two_way_turnover_close_nav_excluding_build']}
calib['pilot_daily_recomputed'] = {'rel_cagr': theirs['annualized_relative_cagr'], 'ci': theirs['bootstrap']['relative_cagr_95pct']}
calib['pilot_SUMMARY'] = {'rel_cagr': pilot_summary['annualized_relative_cagr'], 'ci': pilot_summary['bootstrap']['relative_cagr_95pct']}
calib['delta_rel_cagr_pp'] = 100 * (mine['annualized_relative_cagr'] - pilot_summary['annualized_relative_cagr'])
calib['delta_ci_pp'] = [100 * (a - b) for a, b in zip(mine['bootstrap']['relative_cagr_95pct'], pilot_summary['bootstrap']['relative_cagr_95pct'])]
calib['accepted'] = bool(max(calib['P0_max_abs_nav_diff'], calib['VWAP_NULL_head4_max_abs_nav_diff']) < 1e-6 or
                         (abs(calib['delta_rel_cagr_pp']) < .10 and max(map(abs, calib['delta_ci_pp'])) < .20))
# generalized engine == original on daily runs
g_p0 = account_df(reference, .0002, 'open'); g_v4 = account_df(np.asarray([tilt(r, s) for r, s in zip(reference, sealed)]), .0002, 'open')
calib['general_engine_equals_original'] = {'P0': float(np.abs(g_p0.nav - d_p0.nav).max()), 'VWAP_NULL_head4': float(np.abs(g_v4.nav - d_v4.nav).max())}
C.write_json(C.RESULTS / 'part2_calibration.json', calib)
print(json.dumps(calib, indent=1), flush=True)
assert calib['general_engine_equals_original']['VWAP_NULL_head4'] < 1e-9
if '--calibrate-only' in sys.argv:
    sys.exit(0)

# ---------- declared runs ----------
N_OF = {'head3': 5, 'head4': 20}


def targets_for(arm, head, usage):
    d = scores[(arm, head)]
    if usage.startswith('a_') or usage.startswith('b_'):
        z = to_account(d[usage.split('_', 1)[1]])
        return np.asarray([tilt(r, s) for r, s in zip(reference, z)]), 1
    if usage == 'c1_tranche':
        z = to_account(d['ema1']); return np.asarray([tilt(r, s) for r, s in zip(reference, z)]), N_OF[head]
    if usage == 'c1_tranche_ema0.2':
        z = to_account(d['ema0.2']); return np.asarray([tilt(r, s) for r, s in zip(reference, z)]), N_OF[head]
    if usage == 'c2_boxcar':
        n = N_OF[head]; full = d['ema1']
        mult_full = np.where(np.isfinite(full), 1 + .5 * np.clip(full, -2, 2), 1.)
        out = []
        for t, r in enumerate(srow):
            mf = mult_full[r - n + 1:r + 1].mean(axis=0)
            m = np.ones(len(symbols)); m[cov] = mf[col[cov]]
            x = reference[t] * m; out.append(x / x.sum() * reference[t].sum())
        return np.asarray(out), 1
    raise KeyError(usage)


USAGES = ('a_ema0.2', 'b_ema0.1', 'b_ema0.05', 'c1_tranche', 'c1_tranche_ema0.2', 'c2_boxcar')
runs = {}
for fee in (.0002, .0004):
    for ex in ('open', 'vwap'):
        runs[('P0', '-', 'daily', ex, fee)] = account_df(reference, fee, ex)
        for n in (5, 20):
            runs[('P0', '-', f'tranche{n}', ex, fee)] = account_df(reference, fee, ex, sleeves=n)
        for arm in C.ARMS:
            for head in ('head3', 'head4'):
                for usage in USAGES:
                    tg, sl = targets_for(arm, head, usage)
                    runs[(arm, head, usage, ex, fee)] = account_df(tg, fee, ex, sleeves=sl)
        print('runs done', fee, ex, flush=True)

store = {'|'.join(map(str, k)): v.nav.to_numpy() for k, v in runs.items()}
store.update({'turnover|' + '|'.join(map(str, k)): v.two_way_turnover_close_nav.to_numpy() for k, v in runs.items()})
np.savez_compressed(C.RESULTS / 'part2_daily_navs.npz', dates=dates.astype(str), **store)

years = pd.to_datetime(dates).year.to_numpy()
WINDOWS = {'continuous2025_2026': np.arange(len(dates)), '2025': np.flatnonzero(years == 2025),
           '2026': np.r_[np.flatnonzero(years == 2025)[-1], np.flatnonzero(years == 2026)]}


def compare(bk, ck, label):
    out = []
    for w, rows in WINDOWS.items():
        b = runs[bk].iloc[rows].reset_index(drop=True); c = runs[ck].iloc[rows].reset_index(drop=True)
        m = orig['paired_summary'](b, c)
        out.append(dict(comparison=label, window=w, candidate='|'.join(map(str, ck)), base='|'.join(map(str, bk)),
                        rel_cagr=m['annualized_relative_cagr'], ci_lo=m['bootstrap']['relative_cagr_95pct'][0],
                        ci_hi=m['bootstrap']['relative_cagr_95pct'][1], ir=m['information_ratio_vs_P0'],
                        te=m['tracking_error_relative_daily_vs_P0'],
                        cand_turnover=m['candidate_mean_two_way_turnover_close_nav_excluding_build'],
                        base_turnover=m['base_mean_two_way_turnover_close_nav_excluding_build'],
                        cand_cagr=m['candidate_cagr_after_build'], base_cagr=m['base_cagr_after_build']))
    return out


rows = []
for (arm, head, usage, ex, fee) in [k for k in runs if k[0] != 'P0']:
    ck = (arm, head, usage, ex, fee)
    rows += compare(('P0', '-', 'daily', ex, fee), ck, 'vs_P0')
    if usage.startswith('c1'):
        rows += compare(('P0', '-', f'tranche{N_OF[head]}', ex, fee), ck, 'vs_P0_tranche')
    if arm.startswith('VWAP'):
        suffix = arm.split('_')[1]
        rows += compare(('SHORT_' + suffix, head, usage, ex, fee), ck, 'vs_SHORT_same_usage')
        rows += compare(('SHORT_' + suffix, 'head4', 'a_ema0.2', 'open', fee), ck, 'vs_SHORT_head4_current')
for n in (5, 20):
    for ex in ('open', 'vwap'):
        for fee in (.0002, .0004):
            rows += compare(('P0', '-', 'daily', ex, fee), ('P0', '-', f'tranche{n}', ex, fee), 'P0_tranche_vs_P0')
for fee in (.0002, .0004):
    rows += compare(('P0', '-', 'daily', 'open', fee), ('P0', '-', 'daily', 'vwap', fee), 'P0_vwap_vs_open')
res = pd.DataFrame(rows)
res.to_csv(C.RESULTS / 'part2_comparisons.csv', index=False)
print('comparisons', len(res))

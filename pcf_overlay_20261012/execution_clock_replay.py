"""P0 (pure PCF replication) under three execution clocks: 09:30 open, first-hour midpoint proxy, 10:31-10:35 tape.
Answers the cloud-prep item 3: how much does a 09:35-start executor lose relative to the open-fill replay."""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0, '/Users/leslie/Downloads/中证500量化60min')
from pcf_overlay_20261012.replay import account, summary, relative_windows, CAPITAL, FEE, DATA
from pcf_overlay_20261012.data import OUT, ROOT, END, symbol
DEST = OUT / 'execution_clock_20261011'; DEST.mkdir(exist_ok=True)
pc = pd.read_parquet(OUT/'pcf_components.parquet'); meta = pd.read_csv(OUT/'pcf_metadata.csv', dtype={'date': str, 'valuation_date': str}).set_index('date')
prices = pd.read_parquet(OUT/'daily_prices.parquet'); symbols = json.loads((OUT/'price_symbols.json').read_text()); U = len(symbols)
historic = pd.read_parquet(ROOT/'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
recent = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
for f in (historic, recent): f['date'] = pd.to_datetime(f.date).dt.strftime('%Y-%m-%d')
benchmark = pd.concat([historic, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
pricebench = pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv').set_index('date').close
calendar = sorted(set(benchmark.index) & set(pricebench.index) & set(prices.date)); all_days = [d for d in calendar if '2023-03-01' <= d <= END]
def wide(field): return prices.pivot(index='date', columns='symbol', values=field).reindex(index=all_days, columns=symbols).to_numpy()
rawclose = wide('close'); op = wide('open_adjusted'); close = wide('close_adjusted'); factor = wide('factor'); volume = wide('volume'); valid = wide('valid') == 1; isST = wide('isST')
can = valid & (volume > 0) & (isST != 1); lookup = {s: i for i, s in enumerate(symbols)}; daypos = {d: i for i, d in enumerate(all_days)}
times = np.load(DATA/'times.npy'); clock = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
model_symbols = json.loads((DATA/'symbols.json').read_text()); mapping = np.array([lookup[s] for s in model_symbols])
frozen_member = np.load(DATA/'members.npy', mmap_mode='r')
execution = np.load(DATA/'execution_raw_prices.npy', mmap_mode='r'); efill = np.load(DATA/'execution_complete.npy', mmap_mode='r') & np.load(DATA/'execution_tradable.npy', mmap_mode='r') & ~np.load(DATA/'execution_all_flat.npy', mmap_mode='r')
hours = np.load(DATA/'hour_ohlcv.npy', mmap_mode='r'); by = {h: {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == h)} for h in (10, 15)}
pcgroups = {d: g[g.stock_replicable] for d, g in pc.groupby('date')}
coverage = []; dates = []; refs = []; marks = []; fills = []; members = []; closes = []; px_open = []; px_mid = []; px_tape = []; fill_tape = []
for j, day in enumerate(all_days):
    if j == 0: continue
    prior = all_days[j-1]
    if prior not in pcgroups or prior < '2023-03-13' or day not in by[10] or prior not in by[15]: continue
    g = pcgroups[prior]; m = meta.loc[prior]; vday = m.valuation_date
    ids = np.array([lookup[symbol(c)] for c in g.code]); prev = rawclose[daypos[vday], ids]; missing = ~np.isfinite(prev) | (prev <= 0)
    reference = np.zeros(U); values = g.quantity.to_numpy() * prev / float(m.nav_per_cu)
    if missing.any(): reference[:] = np.nan
    else:
        reference[ids] = values
        if reference.sum() > .98: reference *= .98 / reference.sum()
    cur = by[15][prior]; member = np.zeros(U, bool); member[mapping] = frozen_member[cur]
    c10 = by[10][day]; p10 = np.full(U, np.nan); p10[mapping] = execution[c10] * factor[j, mapping]
    f10 = np.zeros(U, bool); f10[mapping] = efill[c10]; f10 &= np.isfinite(p10) & (p10 > 0)
    c10close = np.full(U, np.nan); c10close[mapping] = hours[c10, :, 8] * factor[j, mapping]
    mid = np.where(np.isfinite(c10close), 0.5 * (op[j] + c10close), op[j]); p10 = np.where(f10, p10, op[j]); f10 = can[j]; cov = float(np.isfinite(c10close[ids]).mean()); coverage.append(cov)
    dates.append(day); refs.append(reference); marks.append(close[j-1]); fills.append(can[j]); members.append(member); closes.append(close[j])
    px_open.append(op[j]); px_mid.append(mid); px_tape.append(p10); fill_tape.append(f10)
A = lambda x: np.asarray(x); refs = A(refs); b = benchmark.loc[dates].to_numpy(); pb = pricebench.loc[dates].to_numpy()
arms = {'open_0930': (A(px_open), A(fills)), 'mid_first_hour': (A(px_mid), A(fills) & np.isfinite(A(px_mid))), 'tape_1031_1035': (A(px_tape), A(fill_tape))}
res = {}; rows = []
for arm, (px, fl) in arms.items():
    d, tr = account(refs, A(marks), px, fl, dates, A(closes), A(members)); d.to_csv(DEST/f'P0_{arm}_daily.csv', index=False)
    r = summary(d, b, pb); r['arm'] = arm; rows.append(r); res[arm] = d.nav.to_numpy(); print(arm, round(r['annualized_relative_excess'], 4), round(r['average_turnover'], 4), round(r['max_fill_blocked_weight'], 4), flush=True)
pd.DataFrame(rows).to_csv(DEST/'scorecard.csv', index=False)
out = []
for arm in ['mid_first_hour', 'tape_1031_1035']:
    x = res[arm]; y = res['open_0930']; rx = x[1:]/x[:-1]; ry = y[1:]/y[:-1]; path = rx/ry; n = len(path)
    ann = float(np.prod(path) ** (252/n) - 1); rng = np.random.default_rng(20261011)
    ix = (rng.integers(0, n, size=(2000, int(np.ceil(n/20)), 1)) + np.arange(20)) % n; ix = ix.reshape(2000, -1)[:, :n]
    lo, hi = np.quantile(np.prod(path[ix], axis=1) ** (252/n) - 1, [.025, .975]); diff = rx - ry
    for seg, start in [('full', dates[0]), ('2025-01..', '2025-01-02'), ('2026-01..', '2026-01-02')]:
        sel = np.array([d_ >= start for d_ in dates[1:]]); p = path[sel]; nn = len(p)
        out.append(dict(arm=arm, versus='open_0930', segment=seg, days=nn, ann_rel=float(np.prod(p) ** (252/nn) - 1), ir=float(diff[sel].mean()/diff[sel].std(ddof=1)*np.sqrt(252))))
    out[-3].update(lo95=float(lo), hi95=float(hi))
t = pd.DataFrame(out); print(t.round(4).to_string(index=False)); t.to_csv(DEST/'clock_comparison.csv', index=False)
json.dump(dict(question='P0 replication cost of executing after the open instead of at the 09:30 auction', arms=list(arms), window=[dates[0], dates[-1]], capital=CAPITAL, fee=FEE, note='mid_first_hour = 0.5*(open + 10:30 raw close) adjusted; tape = 10:31-10:35 execution tape; names without hourly tape fall back to the daily open so only timing differs; no 09:35 tape exists', tape_coverage_mean=float(np.mean(coverage)), tape_coverage_min=float(np.min(coverage))), open(DEST/'protocol.json', 'w'), indent=2)
print('CLOCK_DONE')

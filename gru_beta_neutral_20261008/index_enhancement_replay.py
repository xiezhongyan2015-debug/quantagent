"""Index-weight skeleton + GRU veto / tilt replay on frozen 14:00 scores.

Construction (per 14:00 signal, executed 14:01-14:05 same day, fee 0.0002 per side all-in):
  index        : hold current CSI500 members at market-cap reference weights (control).
  veto10/veto20: reference weights, exclude members whose smoothed score percentile falls
                 below q_out; re-admit above q_in (hysteresis 10/25 and 20/35).
  tilt         : w ∝ reference × (1 + k·clip(z, -2, 2)), z = cross-sectional z of 5-signal EMA score.
  veto10_tilt  : both.
  *_neutral    : same, with score first residualised on [volatility, turnover, log cap, beta60].
No future fills, labels or prices enter target construction. Frozen panel unchanged.
"""
from pathlib import Path
import argparse, importlib.util, json
import numpy as np
import pandas as pd
from .attribution import ROOT, DATA, OUT, sha
from .replay import risk_weights, holding_metrics, neutralize, rank

SNAP = OUT/'source_snapshot/outputs/gru_market_attention_20261007'
FUSION = OUT/'lightgbm_fusion_paired/frozen_fusion_scores'
LGBM = ROOT/'outputs/gru_lightgbm_20261009'
DEST = ROOT/'outputs/gru_index_enhancement_replay_20261010'
SEEDS = [11, 22, 33, 44, 55]
METHODS = {
    'index':             dict(veto=None, tilt=0.0, neutral=False),
    'veto10':            dict(veto=(.10, .25), tilt=0.0, neutral=False),
    'veto20':            dict(veto=(.20, .35), tilt=0.0, neutral=False),
    'tilt':              dict(veto=None, tilt=0.5, neutral=False),
    'veto10_tilt':       dict(veto=(.10, .25), tilt=0.5, neutral=False),
    'veto10_neutral':    dict(veto=(.10, .25), tilt=0.0, neutral=True),
    'veto20_neutral':    dict(veto=(.20, .35), tilt=0.0, neutral=True),
    'tilt_neutral':      dict(veto=None, tilt=0.5, neutral=True),
    'tilt1_neutral':     dict(veto=None, tilt=1.0, neutral=True),
    'veto10_tilt_neutral': dict(veto=(.10, .25), tilt=0.5, neutral=True),
    'tilt1_neutral_sn':    dict(veto=None, tilt=1.0, neutral=True, sector_neutral=True),
    'tilt1_neutral_sn_band': dict(veto=None, tilt=1.0, neutral=True, sector_neutral=True, band=0.3),
    'veto20_neutral_sn':   dict(veto=(.20, .35), tilt=0.0, neutral=True, sector_neutral=True),
    'veto20_tilt1_neutral_sn': dict(veto=(.20, .35), tilt=1.0, neutral=True, sector_neutral=True),
    # alpha-level stop: when the trailing 10-session return relative to the benchmark (known at signal
    # time, i.e. through the previous close) is below -2.2%, collapse the tilt/veto to the pure skeleton
    # until it recovers above -1.1% (registration-card thresholds: -1.1% notify, -2.2% stop risk orders).
    'tilt1_neutral_cap50':  dict(veto=None, tilt=1.0, neutral=True, active_cap=0.005),
    'tilt075_neutral_cap50': dict(veto=None, tilt=0.75, neutral=True, active_cap=0.005),
    'tilt_neutral_cap50':   dict(veto=None, tilt=0.5, neutral=True, active_cap=0.005),
    'tilt1_neutral_cap30':  dict(veto=None, tilt=1.0, neutral=True, active_cap=0.003),
    'tilt075_neutral':      dict(veto=None, tilt=0.75, neutral=True),
    'tilt1_neutral_stop':  dict(veto=None, tilt=1.0, neutral=True, alpha_stop=(-.022, -.011)),
    'tilt_neutral_stop':   dict(veto=None, tilt=0.5, neutral=True, alpha_stop=(-.022, -.011)),
}
# Index deletions are announced publicly about two weeks before the effective date. The frozen panel
# stores no prices after a name leaves the index, so a position that is still held on the effective
# day can never be sold again at a real price. The replay therefore sells a leaving name on its last
# member day (announcement proxy: one-day lookahead of the membership mask only, never of prices).
EXIT_ANNOUNCEMENT_PROXY = True
EXTERNAL_SIGNALS = [
    ('return_head_A', ROOT/'outputs/gru_return_head_20261009_stageA_repair1/signals/scores.npz', 'scores'),
    ('return_head_B', ROOT/'outputs/gru_return_shared_20261009_stageB_repair1/signals/scores.npz', 'scores'),
    ('rank_B0', ROOT/'outputs/gru_return_head_20261009_stageA_repair1/signals/rank_replay.npz', 'rank_ensemble'),
]
import os
USE_OFFICIAL_WEIGHTS = os.environ.get('GRU_OFFICIAL_WEIGHTS', '1') == '1'
DEST = Path(os.environ.get('GRU_INDEX_DEST', DEST))
SLOT = int(os.environ.get('GRU_SIGNAL_SLOT', '14'))   # 14: 14:00 signal, 14:01-14:05 fill; 15: 15:00 signal, next-day 09:31-09:36 fill
EMA_ALPHA = 0.2          # five-signal half-life-ish smoothing of the daily 14:00 score
MIN_ORDER_CNY = 100      # fractional diagnostic ledger; index basket has many sub-1000 CNY lines


class IndexTargets:
    def __init__(self, params, risk, membership, signal_tradable):
        self.p, self.risk, self.membership, self.tradable = params, risk, membership, signal_tradable
        self.event = 0
        self.ema = None
        self.excluded = None
        self.diagnostics = []
        self.benchmark_prev = None     # benchmark NAV through the previous close, aligned to events
        self.nav_history = []
        self.stopped = False

    def smoothed(self, score, expo, eligible):
        s = neutralize(score, expo) if self.p['neutral'] else score.astype(float)
        z = np.full(len(s), np.nan)
        ok = eligible & np.isfinite(s)
        if ok.sum() >= 100:
            z[ok] = (s[ok]-s[ok].mean())/s[ok].std()
        if self.ema is None:
            self.ema = z.copy()
        else:
            prev = self.ema
            new = np.where(np.isfinite(z) & np.isfinite(prev), EMA_ALPHA*z+(1-EMA_ALPHA)*prev, z)
            self.ema = np.where(np.isfinite(new), new, np.nan)
        out = self.ema.copy()
        okz = np.isfinite(out) & eligible
        if okz.sum() >= 100:
            out[okz] = (out[okz]-out[okz].mean())/out[okz].std()
        return out

    def __call__(self, score, units, marks, nav, buffer=0, n=100):
        j = self.event
        self.event += 1
        expo = self.risk['exposure'][j]
        member = self.membership[j].copy()
        if EXIT_ANNOUNCEMENT_PROXY and j+1 < len(self.membership):
            member &= self.membership[j+1]       # sell announced deletions on their last member day
        reference = self.risk['reference'][j].astype(float)
        if self.risk.get('official') is not None:
            off = self.risk['official'][j]
            if np.isfinite(off).any() and np.nansum(off) > 0.5:
                reference = np.where(np.isfinite(off), off, 0.0)
        priced = np.isfinite(marks) & (marks > 0)
        tradable = self.tradable[j]
        held = units > 1e-10
        frozen = held & ~tradable
        if self.excluded is None:
            self.excluded = np.zeros(len(units), bool)
        eligible = member & priced & tradable & np.isfinite(score)
        if self.p['neutral']:
            eligible &= np.isfinite(expo).all(axis=1)
        z = self.smoothed(score, expo, eligible)
        tilt_k = self.p['tilt']; veto = self.p['veto']
        if self.p.get('alpha_stop') and self.benchmark_prev is not None:
            # nav at this signal (marked at signal prices) versus benchmark through the previous close
            self.nav_history.append(nav)
            if len(self.nav_history) > 10 and j >= 10:
                rel = (self.nav_history[-1]/self.nav_history[-11])/(self.benchmark_prev[j]/self.benchmark_prev[j-10])-1
                stop, resume = self.p['alpha_stop']
                if self.stopped and rel > resume: self.stopped = False
                elif not self.stopped and rel < stop: self.stopped = True
            if self.stopped:
                tilt_k, veto = 0.0, None
        # veto with hysteresis on percentile of the smoothed score among eligible members
        if veto is not None:
            q_out, q_in = veto
            ids = np.flatnonzero(eligible & np.isfinite(z))
            pct = np.full(len(units), np.nan)
            pct[ids] = rank(z[ids])
            newly = eligible & np.isfinite(pct) & (pct < q_out)
            readmit = self.excluded & np.isfinite(pct) & (pct > q_in)
            self.excluded = (self.excluded | newly) & ~readmit & member
        weights = np.zeros(len(units))
        weights[frozen] = units[frozen]*marks[frozen]/nav
        budget = max(.98-weights.sum(), 0)
        base = np.where(member & priced & ~frozen, reference, 0.0)
        if tilt_k > 0:
            factor = np.where(np.isfinite(z), 1+tilt_k*np.clip(z, -2, 2), 1.0)
            base = base*factor
        if veto is not None:
            base[self.excluded] = 0.0
        base[~tradable] = 0.0            # cannot trade into a suspended name; held ones are frozen above
        if self.p.get('sector_neutral'):
            # rescale within each industry so sector weights equal the (tradable) reference sector weights
            ind = self.risk['industry'][j]
            ref_tr = np.where(member & priced & ~frozen & tradable, reference, 0.0)
            for k in np.unique(ind[base > 0]):
                sel = ind == k
                target, have = ref_tr[sel].sum(), base[sel].sum()
                if have > 0 and target > 0:
                    base[sel] *= target/have
        if self.p.get('band', 0) > 0:
            # trade band: keep current weight where |target-current| is below band × reference weight
            current = np.where(priced, units*marks/nav, 0.0)
            small = np.abs(base/ max(base.sum(), 1e-12) * budget - current) < self.p['band']*np.where(reference > 0, reference, 1e-9)
            keep = small & (current > 0) & member & tradable & ~frozen
            base[keep] = current[keep] * base.sum()/budget if budget > 0 else base[keep]
        total = base.sum()
        if total <= 0:
            return units.copy(), np.flatnonzero(held)
        base = base/total*budget
        cap = self.p.get('active_cap', 0)
        if cap > 0:
            # active-weight cap: |w_i - reference_i| <= cap (as fraction of NAV), applied to the invested budget
            ref_scaled = np.where(member & priced & ~frozen & tradable, reference, 0.0)
            ref_scaled = ref_scaled/max(ref_scaled.sum(), 1e-12)*budget
            for _ in range(3):
                base = np.clip(base, np.maximum(ref_scaled-cap, 0.0), ref_scaled+cap)
                base = base/max(base.sum(), 1e-12)*budget
        base = np.minimum(base, .05)     # single-name cap; mass dropped stays as cash (rare)
        weights += base
        desired = np.zeros(len(units))
        np.divide(weights*nav, marks, out=desired, where=priced)
        delta = desired-units
        delta[frozen] = 0
        delta[np.abs(delta)*marks < MIN_ORDER_CNY] = 0
        self.diagnostics.append(dict(event=j, excluded=int(self.excluded.sum()) if veto else 0, stopped=bool(self.stopped),
                                     names=int((weights > 0).sum()), active_abs=float(np.abs(base-np.where(member, reference*budget, 0)).sum()/2)))
        return units+delta, np.flatnonzero(weights > 0)


def window_stats(nav, bnav, length=50):
    rel = (nav/np.r_[1, nav[:-1]])/(bnav/np.r_[1, bnav[:-1]])
    roll = pd.Series(rel).rolling(length).apply(np.prod, raw=True).dropna().to_numpy()-1
    nonover = roll[::length] if len(roll) else roll
    out = {f'w{length}_n_overlapping': int(len(roll)), f'w{length}_n_nonoverlapping': int(len(nonover))}
    if len(roll):
        out.update({f'w{length}_mean': float(roll.mean()), f'w{length}_p10': float(np.quantile(roll, .1)),
                    f'w{length}_p50': float(np.median(roll)), f'w{length}_p90': float(np.quantile(roll, .9)),
                    f'w{length}_win_rate': float((roll > 0).mean()), f'w{length}_worst': float(roll.min()),
                    f'w{length}_nonoverlapping_win_rate': float((nonover > 0).mean()) if len(nonover) else np.nan})
    return out


def block_ci(difference, seed=808):
    a = np.asarray(difference, float); rng = np.random.default_rng(seed); n = len(a)
    observed = float(np.mean(a.sum(axis=0))*252/n)
    starts = rng.integers(0, n, size=(2000, int(np.ceil(n/20))))
    positions = (starts[:, :, None]+np.arange(20)) % n
    positions = positions.reshape(2000, -1)[:, :n]
    samples = a[positions].mean(axis=(1, 2))*252
    lo, hi = np.quantile(samples, [.025, .975])
    return dict(annualized_mean_daily_return_difference=observed, lower95=float(lo), upper95=float(hi),
                interval_crosses_zero=bool(lo <= 0 <= hi))


def load_models(split, times, full):
    """Return dict name -> (14:00 score matrix), plus the shared 14:00 index vector."""
    models, reference = {}, None
    def add(name, path):
        nonlocal reference
        a = np.load(path); indices = a['indices']
        assert np.array_equal(a['times'], times[indices])
        keep = full[indices].hour == SLOT; ix = indices[keep]
        if reference is None: reference = ix
        else: assert np.array_equal(reference, ix)
        s = a['scores'][keep, :, 3].astype(float)
        models[name] = s
        return (s-np.nanmean(s, axis=1, keepdims=True))/np.nanstd(s, axis=1, keepdims=True)
    for variant in ['control', 'market_attention']:
        z = [add(f'{variant}_seed{seed}', SNAP/f'{variant}_seed{seed}'/(split+'_scores.npz')) for seed in SEEDS]
        models[variant+'_ensemble'] = np.mean(z, axis=0)
    z = [add(f'fusion_seed{seed}', FUSION/f'{split}_seed{seed}.npz') for seed in SEEDS]
    models['fusion_ensemble'] = np.mean(z, axis=0)
    z = [add(f'lightgbm_seed{seed}', LGBM/f'seed{seed}'/(split+'_scores.npz')) for seed in SEEDS]
    models['lightgbm_ensemble'] = np.mean(z, axis=0)
    for name in [k for k in models if k.startswith(('fusion_seed', 'lightgbm_seed', 'market_attention_seed'))]:
        models.pop(name)   # only control seeds are kept for paired seed noise
    # external 14:00 signals (teammate return-head experiments), aligned by signal time
    symbols = json.load(open(DATA/'symbols.json'))
    for name, path, key in EXTERNAL_SIGNALS:
        path = Path(path)
        if not path.exists(): continue
        z = np.load(path, allow_pickle=True)
        assert list(z['symbols']) == symbols, name
        pos = {int(t): i for i, t in enumerate(z['signal_time_ns'])}
        arr = z[key]
        if arr.ndim == 3: arr = np.nanmean(arr, axis=0)
        rows = [pos.get(int(times[i])) for i in reference]
        out = np.full((len(reference), len(symbols)), np.nan)
        for r, src in enumerate(rows):
            if src is not None: out[r] = arr[src]
        models[name] = out
    return models, reference


OFFICIAL_WEIGHTS = Path('/private/tmp/claude-501/-Users-leslie-Downloads---500--60min/8f0cdd66-88cc-4e16-a075-9237fdeb39f0/scratchpad/csi500_index_weight_tushare.parquet')


def official_weights(ix, times, full, allhour, symbols):
    """Official CSI500 weights (tushare index_weight month-end snapshots), drifted by price to each
    14:00 signal date using the previous 15:00 close, renormalised. NaN rows where no snapshot <= t-1."""
    if not OFFICIAL_WEIGHTS.exists():
        return None, {}
    w = pd.read_parquet(OFFICIAL_WEIGHTS)
    w['trade_date'] = pd.to_datetime(w.trade_date)
    pos = {s: k for k, s in enumerate(symbols)}
    snaps = {}
    for d, g in w.groupby('trade_date'):
        vec = np.full(len(symbols), np.nan)
        for code, weight in zip(g.con_code, g.weight):
            if code in pos: vec[pos[code]] = weight/100
        snaps[d] = vec
    dates15 = {str(full[i].date()): i for i in range(3, len(times), 4)}
    out = np.full((len(ix), len(symbols)), np.nan); used = {}
    snap_dates = sorted(snaps)
    for j, i in enumerate(ix):
        d = full[i].date()
        prior = [s for s in snap_dates if s.date() < d]
        if not prior: continue
        s = prior[-1]
        base = snaps[s]
        row15 = dates15.get(str(s.date()))
        prev_day_rows = [k for k in range(i-1, -1, -1) if full[k].hour == 15]
        if row15 is None or not prev_day_rows: continue
        p0 = np.asarray(allhour[row15, :, 3], float); p1 = np.asarray(allhour[prev_day_rows[0], :, 3], float)
        drift = np.where(np.isfinite(p0) & np.isfinite(p1) & (p0 > 0), p1/p0, 1.0)
        vec = base*drift
        vec = vec/np.nansum(vec)
        out[j] = vec; used[str(d)] = str(s.date())
    return out, used


WALKFORWARD = os.environ.get('GRU_WALKFORWARD', '0') == '1'
WF_ROOT = ROOT/'outputs/gru_walkforward_20261010'


def load_models_wf(year, times, full):
    """Walk-forward test scores for test year `year` (model trained through year-2, validated on year-1)."""
    models, reference = {}, None
    z = []
    for seed in SEEDS:
        a = np.load(WF_ROOT/f'T{year}'/f'control_seed{seed}'/'test_scores.npz'); indices = a['indices']
        assert np.array_equal(a['times'], times[indices])
        keep = full[indices].hour == SLOT; ix = indices[keep]
        if reference is None: reference = ix
        else: assert np.array_equal(reference, ix)
        s = a['scores'][keep, :, 3].astype(float); models[f'wf_seed{seed}'] = s
        z.append((s-np.nanmean(s, axis=1, keepdims=True))/np.nanstd(s, axis=1, keepdims=True))
    models['wf_ensemble'] = np.mean(z, axis=0)
    if year in (2025, 2026):   # frozen single-window control for comparison on the same sections
        frozen, ref2 = load_models({2025: 'test_2025', 2026: 'recent_2026'}[year], times, full)
        common = np.isin(ref2, reference); assert np.array_equal(ref2[common], reference)
        models['control_ensemble'] = frozen['control_ensemble'][common]
    return models, reference


def main(methods, models_wanted):
    DEST.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location('frozen_ledger', ROOT/'gru_market_attention_20261007/ledger.py')
    ledger = importlib.util.module_from_spec(spec); spec.loader.exec_module(ledger)
    checks = ledger.tests()
    ledger.CAPITAL = 2_000_000
    ledger.PROTOCOL['minimum_signal_order_cny'] = MIN_ORDER_CNY
    load = lambda n: np.load(DATA/(n+'.npy'), mmap_mode='r')
    times, execution = load('times'), load('earliest_execution_times')
    full = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    allhour = load('hour_ohlcv')
    closes = {str(full[i].date()): np.asarray(allhour[i, :, 3], float) for i in range(3, len(times), 4)}
    q = np.load(OUT/'risk_context.npz'); assert np.array_equal(q['times'], times)
    bench = pd.read_csv(OUT/'benchmark_sources/H00905_daily.csv').set_index('date').close
    pricebench = pd.read_csv(OUT/'benchmark_sources/000905_daily.csv').set_index('date').close
    records, daily = [], {}
    year_splits = [(y, None) for y in range(2021, 2027)] if WALKFORWARD else [(2025, 'test_2025'), (2026, 'recent_2026')]
    for year, split in year_splits:
        models, ix = load_models_wf(year, times, full) if WALKFORWARD else load_models(split, times, full)
        exec_days = np.array([str(t.date()) for t in pd.to_datetime(execution[ix], utc=True).tz_convert('Asia/Shanghai')])
        keep_days = np.isin(exec_days, pricebench.index.to_numpy())   # drop signals whose execution day has no benchmark close yet
        ix = ix[keep_days]; models = {k: v[keep_days] for k, v in models.items()}
        membership = np.asarray(load('members')[ix])
        risk = risk_weights(ix, q, membership)
        symbols = json.load(open(DATA/'symbols.json'))
        official, used = official_weights(ix, times, full, allhour, symbols)
        if official is not None and USE_OFFICIAL_WEIGHTS:
            # official weights only for names that are current members in the frozen panel; members that the
            # month-end snapshot does not yet contain (new entrants between snapshot and rebalance) get the
            # cap-weight reference scaled by the median official/cap ratio of names that have both.
            official = np.where(membership, official, np.nan)
            ref = risk['reference']
            for j in range(len(ix)):
                have = membership[j] & np.isfinite(official[j]) & (ref[j] > 0)
                miss = membership[j] & ~np.isfinite(official[j]) & (ref[j] > 0)
                if have.sum() >= 100 and miss.any():
                    official[j, miss] = ref[j, miss]*np.median(official[j, have]/ref[j, have])
            risk['official'] = official
            covered = np.isfinite(official).any(axis=1)
            print(f'{year}: official index weights available on {int(covered.sum())}/{len(ix)} signal days', flush=True)
        else:
            risk['official'] = None
        signal = np.asarray(allhour[ix, :, 3], float)
        prices = np.asarray(load('execution_prices')[ix], float)
        raw = load('execution_raw_prices')[ix]
        fill = load('execution_complete')[ix] & load('execution_tradable')[ix] & ~load('execution_all_flat')[ix] & np.isfinite(raw) & (raw > 0)
        tradable = np.asarray(load('hour_complete')[ix]) & np.isfinite(signal) & (signal > 0) & (allhour[ix, :, 4] > 0)
        days = [str(t.date()) for t in pd.to_datetime(execution[ix], utc=True).tz_convert('Asia/Shanghai')]
        assert np.all(execution[ix] > times[ix])
        pnav = pricebench.loc[days].to_numpy()/pricebench.loc[pricebench.index < days[0]].iloc[-1]
        tr_available = all(d in bench.index for d in days) and (bench.index < days[0]).any()
        bnav = bench.loc[days].to_numpy()/bench.loc[bench.index < days[0]].iloc[-1] if tr_available else pnav
        if not tr_available: print(f'{year}: total-return benchmark unavailable, using price index 000905 (dividend gap not credited)', flush=True)
        for method in methods:
            folder = DEST/method; folder.mkdir(exist_ok=True)
            for model, score in models.items():
                if model not in models_wanted: continue
                if method == 'index' and model != ('wf_ensemble' if WALKFORWARD else 'control_ensemble'): continue   # score-free control
                for cost in (['gross', 'net'] if model.endswith('ensemble') else ['net']):
                    targets = IndexTargets(METHODS[method], risk, membership, tradable); ledger.targets = targets
                    targets.benchmark_prev = np.r_[1.0, bnav[:-1]]
                    d, e, tr = ledger.simulate(prices, fill, score, signal, days, closes, 0, 0, cost == 'gross', True, 100, membership)
                    holding_metrics(d, tr, risk, signal, prices, closes, days)
                    d['benchmark_nav'] = bnav; d['price_benchmark_nav'] = pnav
                    tag = f'{year}_{model}_{method}_{cost}'
                    d.to_csv(folder/(tag+'_daily.csv'), index=False); e.to_csv(folder/(tag+'_events.csv'), index=False)
                    pd.DataFrame(targets.diagnostics).to_csv(folder/(tag+'_targets.csv'), index=False)
                    if cost == 'net': daily[(year, method, model)] = d
                    rec = dict(method=method, year=year, model=model, cost_case=cost, **ledger.metrics(d, bnav))
                    rec['total_return_index_excess_pp'] = float((d.nav.iloc[-1]-bnav[-1])*100); rec['benchmark_is_total_return'] = bool(tr_available)
                    rec['price_index_excess_pp'] = float((d.nav.iloc[-1]-pnav[-1])*100)
                    rec['max_industry_deviation_proxy'] = float(d.max_industry_deviation_proxy.max())
                    after = d.after_build_grace
                    rec['beta_pass'] = .9 <= rec['beta'] <= 1.1
                    rec['industry_proxy_pass'] = bool((d.loc[after, 'max_industry_deviation_proxy'] <= .030001).all())
                    rec['stock92_pass'] = bool((d.loc[after, 'cash_weight'] <= .080001).all())
                    rec['member85_pass'] = bool((d.loc[after, 'member_nav_weight'] >= .849999).all())
                    rec['single5_pass'] = bool((d.loc[after, 'max_stock_nav_weight'] <= .050001).all())
                    rec['all_exposure_gates_pass'] = all(rec[k] for k in ['beta_pass', 'industry_proxy_pass', 'stock92_pass', 'member85_pass', 'single5_pass'])
                    nav = d.nav.to_numpy()
                    # Day 1 is a cash-to-basket build day (benchmark measured from the prior-year close, the
                    # basket bought at 14:01); report the excess after the build separately.
                    rec['post_build_excess_pp'] = float(((nav[-1]/nav[0])/(bnav[-1]/bnav[0])-1)*100)
                    rec['build_day_active_pp'] = float(((nav[0])/(bnav[0])-1)*100)
                    rel = (nav/np.r_[1, nav[:-1]])/(bnav/np.r_[1, bnav[:-1]])
                    rec['worst20d_relative_return'] = float((pd.Series(rel).rolling(20).apply(np.prod, raw=True)-1).min())
                    rec.update(window_stats(nav, bnav, 50)); rec.update(window_stats(nav, bnav, 10))
                    rec['mean_excluded'] = float(np.mean([t['excluded'] for t in targets.diagnostics]))
                    rec['mean_names'] = float(np.mean([t['names'] for t in targets.diagnostics]))
                    rec['mean_active_share'] = float(np.mean([t['active_abs'] for t in targets.diagnostics]))
                    records.append(rec)
            pd.DataFrame(records).to_csv(DEST/'scorecard_running.csv', index=False)
            print('COMPLETED', year, method, flush=True)
    s = pd.DataFrame(records); s.to_csv(DEST/'scorecard.csv', index=False)
    # paired seed comparison versus the score-free index control (control seeds only)
    boot = []
    for year in (range(2021, 2027) if WALKFORWARD else [2025, 2026]):
        base = daily.get((year, 'index', 'wf_ensemble' if WALKFORWARD else 'control_ensemble'))
        if base is None: continue
        b = base.nav.to_numpy(); br = b/np.r_[1, b[:-1]]
        for method in methods:
            if method == 'index': continue
            diffs = []
            for seed in SEEDS:
                d = daily.get((year, method, f'wf_seed{seed}' if WALKFORWARD else f'control_seed{seed}'))
                if d is None: break
                a = d.nav.to_numpy(); diffs.append(a/np.r_[1, a[:-1]]-br)
            if len(diffs) == 5:
                boot.append(dict(year=year, method=method, versus='index', **block_ci(np.stack(diffs, axis=1))))
            for model in ['control_ensemble', 'market_attention_ensemble', 'fusion_ensemble', 'lightgbm_ensemble', 'wf_ensemble']:
                d = daily.get((year, method, model))
                if d is None: continue
                a = d.nav.to_numpy(); diff = (a/np.r_[1, a[:-1]]-br)[:, None].repeat(5, axis=1)
                boot.append(dict(year=year, method=method, versus='index', model=model, **block_ci(diff)))
    pd.DataFrame(boot).to_csv(DEST/'block_bootstrap_vs_index.csv', index=False)
    receipt = dict(status='complete', methods=methods, cases=len(s), code_sha256=sha(__file__), ledger_tests=checks,
                   fee_per_side=.0002, minimum_order_cny=MIN_ORDER_CNY, ema_alpha=EMA_ALPHA, initial_capital=2_000_000,
                   benchmark='official H00905 total return, with price-index comparison',
                   reference_weights='total market cap of current members (weekly iFinD), not official free-float index weights',
                   exposed_historical_tests=True, frozen_input_panel_unchanged=True,
                   all_exposure_gate_pass_cases=int(s.all_exposure_gates_pass.sum()))
    (DEST/'completion.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    cols = ['method', 'year', 'model', 'cost_case', 'net_return', 'total_return_index_excess_pp', 'beta', 'tracking_error',
            'information_ratio', 'daily_two_sided_turnover', 'total_fees_cny', 'max_industry_deviation_proxy', 'worst20d_relative_return',
            'w50_mean', 'w50_p10', 'w50_win_rate', 'all_exposure_gates_pass']
    print(s[s.cost_case == 'net'][cols].round(4).to_string(index=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--methods', nargs='+', default=list(METHODS))
    ap.add_argument('--models', nargs='+', default=['control_ensemble', 'market_attention_ensemble', 'fusion_ensemble', 'lightgbm_ensemble']+[f'control_seed{s}' for s in SEEDS])
    a = ap.parse_args(); main(a.methods, a.models)

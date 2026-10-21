"""Matched frozen-score portfolio experiments; only prior risk information."""
from pathlib import Path
import argparse, importlib.util, json, os
import numpy as np
import pandas as pd
from .attribution import ROOT, DATA, OUT, sha

SNAP = OUT/'source_snapshot/outputs/gru_market_attention_20261007'
SEEDS = [11, 22, 33, 44, 55]

def rank(x):
    return pd.Series(x).rank(method='average', pct=True).to_numpy()

def neutralize(score, exposure):
    out = np.full_like(score, np.nan, dtype=float)
    ok = np.isfinite(score) & np.isfinite(exposure).all(axis=1)
    if ok.sum() < 100:
        return out
    x = exposure[ok].copy()
    x[:, 0] = rank(x[:, 0])
    x[:, 1] = rank(x[:, 1])
    scale = x.std(axis=0)
    x = (x-x.mean(axis=0))/np.where(scale > 1e-12, scale, 1)
    x = np.c_[np.ones(len(x)), x]
    out[ok] = score[ok] - x @ np.linalg.lstsq(x, score[ok], rcond=None)[0]
    assert np.max(np.abs(x.T @ out[ok])) < 1e-7
    return out

def choose(score, units, eligible, buffer, n=100):
    ids = np.flatnonzero(eligible & np.isfinite(score))
    order = ids[np.argsort(-score[ids], kind='stable')]
    if not buffer:
        return order[:n]
    keep = [int(u) for u in order[:int(np.ceil(.5*len(order)))] if units[u] > 1e-10][:n]
    have = set(keep)
    return np.array(keep + [int(u) for u in order[:int(np.ceil(.2*len(order)))] if u not in have][:n-len(keep)], int)

class Targets:
    def __init__(self, method, risk, membership, signal_tradable):
        self.method, self.risk, self.membership = method, risk, membership
        self.signal_tradable = signal_tradable
        self.event = 0
        self.diagnostics = []

    def __call__(self, score, units, marks, nav, buffer=0, n=100):
        j = self.event
        self.event += 1
        expo = self.risk['exposure'][j]
        ind = self.risk['industry'][j]
        member = self.membership[j]
        reference = self.risk['reference'][j]
        priced = np.isfinite(marks) & (marks > 0)
        tradable = self.signal_tradable[j]
        eligible = member & priced & np.isfinite(score)
        if self.method != 'baseline':
            eligible &= tradable & np.isfinite(expo).all(axis=1)
        frozen = (units > 1e-10) & ~tradable if self.method != 'baseline' else np.zeros(len(units), bool)
        weights = np.zeros(len(units))
        weights[frozen] = units[frozen]*marks[frozen]/nav
        budget = max(.98-weights.sum(), 0)
        objective = neutralize(score, expo) if self.method == 'neutral' else score
        if self.method in ['baseline', 'neutral']:
            selected = choose(objective, units, eligible, buffer, n)
            if not len(selected):
                return units.copy(), selected
            weights[selected] += min(budget/len(selected), .05 if self.method != 'baseline' else .09)
        elif self.method == 'bucket':
            ids = np.flatnonzero(eligible)
            bins = np.array_split(ids[np.argsort(expo[ids, 0], kind='stable')], 5)
            selections = []
            for group in bins:
                mask = np.zeros(len(units), bool)
                mask[group] = True
                selected = choose(score, units, mask, buffer, max(1, int(np.ceil(.2*len(group)))))
                if len(selected):
                    weights[selected] += budget/5/len(selected)
                    selections.extend(selected)
            selected = np.asarray(selections, int)
        else:
            from scipy.optimize import linprog
            selected = choose(score, units, eligible, buffer, n)
            candidate = np.zeros(len(units), bool)
            candidate[selected] = True
            outcome = None
            broadened = False
            sectors = np.unique(ind[member | frozen])
            beta = expo[:, 3]
            # Missing risk is never assigned a manufactured beta. Frozen risk can make the LP infeasible.
            if np.any(frozen & ~np.isfinite(beta)):
                self.diagnostics.append(dict(event=j, status='frozen_risk_missing', broadened=False))
                return units.copy(), np.flatnonzero(units > 1e-10)
            beta = np.nan_to_num(beta)
            a, b = [beta, -beta], [1.05, -.95]
            for k in sectors:
                sector = (ind == k).astype(float)
                target = float(reference[ind == k].sum())
                a.extend([sector, -sector])
                b.extend([target+.03, -(target-.03)])
            a.append(-member.astype(float)); b.append(-.85)
            objective = np.nan_to_num(score, nan=0)
            for broad in [False, True]:
                active = eligible if broad else candidate
                ids = np.flatnonzero(active | frozen)
                bounds = [(float(weights[u]), float(weights[u])) if frozen[u] else (0, .05) for u in ids]
                outcome = linprog(-objective[ids], A_ub=np.asarray(a)[:, ids], b_ub=b,
                                  A_eq=np.ones((1, len(ids))), b_eq=[.98], bounds=bounds, method='highs',
                                  options={'threads': 1})
                if outcome.success:
                    broadened = broad
                    break
            if not outcome.success:
                self.diagnostics.append(dict(event=j, status='infeasible_hold', broadened=True, solver_status=int(outcome.status)))
                return units.copy(), np.flatnonzero(units > 1e-10)
            weights[:] = 0
            weights[ids] = outcome.x
            selected = ids[outcome.x > 1e-10]
            assert .95-1e-7 <= weights@beta <= 1.05+1e-7
            assert weights.max() <= .05+1e-7 or np.any(frozen & (weights > .05))
            assert weights[member].sum() >= .85-1e-7
            for k in sectors:
                assert abs(weights[ind == k].sum()-reference[ind == k].sum()) <= .03+1e-7
            self.diagnostics.append(dict(event=j, status='optimal', broadened=broadened,
                                         target_beta=float(weights@beta), selected_count=len(selected)))
        desired = np.zeros(len(units))
        np.divide(weights*nav, marks, out=desired, where=priced)
        delta = desired-units
        if self.method != 'constraint':
            delta[np.abs(delta)*marks < 1000] = 0
        # The LP uses no minimum order filter; a filter could silently break exact constraints.
        return units+delta, selected

def risk_weights(indices, q, members):
    cap = q['log_cap'][indices].astype(float)
    reference = np.where(members & np.isfinite(cap), np.exp(cap-np.nanmax(cap, axis=1, keepdims=True)), 0)
    reference /= reference.sum(axis=1, keepdims=True)
    exposure = np.stack([q['volatility'][indices], q['turnover'][indices], cap, q['beta60'][indices]], axis=2).astype(float)
    return dict(exposure=exposure, industry=q['industry'][indices], reference=reference)

def holding_metrics(d, trades, risk, signals, prices, closes, days):
    units = np.zeros(signals.shape[1])
    marks = np.zeros_like(units)
    grouped = {}
    for j, u, delta, bought, sold, price, fee in trades:
        grouped.setdefault(j, []).append((u, bought-sold))
    beta, deviations, maxweights, unknowns = [], [], [], []
    for j, day in enumerate(days):
        for price in [signals[j], prices[j]]:
            ok = np.isfinite(price) & (price > 0)
            marks[ok] = price[ok]
        for u, change in grouped.get(j, []):
            units[u] += change
        close = closes[day]
        ok = np.isfinite(close) & (close > 0)
        marks[ok] = close[ok]
        weights = units*marks/(d.nav.iloc[j]*2_000_000)
        b = risk['exposure'][j, :, 3]
        unknown = weights[~np.isfinite(b)].sum()
        beta.append(float(weights@np.nan_to_num(b)) if unknown < 1e-6 else np.nan)
        unknowns.append(float(unknown))
        ind = risk['industry'][j]
        deviations.append(max(abs(weights[ind == k].sum()-risk['reference'][j, ind == k].sum()) for k in np.unique(ind)))
        maxweights.append(weights.max())
    d['estimated_beta_at_signal_information'] = beta
    d['unknown_beta_weight'] = unknowns
    d['max_industry_deviation_proxy'] = deviations
    d['max_stock_nav_weight'] = maxweights

def test():
    rng = np.random.default_rng(99)
    exposure = rng.normal(size=(500, 4))
    score = exposure@np.array([.5, -.7, .2, 1.1])+rng.normal(size=500)
    residual = neutralize(score, exposure)
    assert np.isfinite(residual).all()
    # Execution prices and future labels are intentionally absent from target inputs.
    member = np.ones((1, 500), bool)
    risk = dict(exposure=exposure[None], industry=np.tile(np.arange(5), 100)[None], reference=np.ones((1, 500))/500)
    target = Targets('bucket', risk, member, member)
    units, chosen = target(score, np.zeros(500), np.ones(500)*10, 2_000_000)
    assert np.isclose((units*10).sum(), .98*2_000_000)
    order = np.argsort(exposure[:, 0])
    assert all(np.isclose((units[group]*10).sum(), .98*2_000_000/5) for group in np.array_split(order, 5))
    return ['score residual orthogonal to specified regression design', 'five volatility bins each20% of invested capital', 'target API excludes future fills and labels']

def main(methods):
    checks = test()
    spec = importlib.util.spec_from_file_location('frozen_ledger', ROOT/'gru_market_attention_20261007/ledger.py')
    ledger = importlib.util.module_from_spec(spec); spec.loader.exec_module(ledger)
    checks += ledger.tests()
    ledger.CAPITAL = 2_000_000
    load = lambda n: np.load(DATA/(n+'.npy'), mmap_mode='r')
    times, execution = load('times'), load('earliest_execution_times')
    full = pd.to_datetime(times, utc=True).tz_convert('Asia/Shanghai')
    closes = {str(full[i].date()): np.asarray(load('hour_ohlcv')[i, :, 3], float) for i in range(3, len(times), 4)}
    q = np.load(OUT/'risk_context.npz')
    assert np.array_equal(q['times'], times)
    bench = pd.read_csv(OUT/'benchmark_sources/H00905_daily.csv').set_index('date').close
    pricebench = pd.read_csv(OUT/'benchmark_sources/000905_daily.csv').set_index('date').close
    summaries, slotrows, parity, pairs = [], [], [], []
    labels = load('labels')
    for year, split in [(2025, 'test_2025'), (2026, 'recent_2026')]:
        models, reference = {}, None
        for variant in ['control', 'market_attention']:
            z = []
            for seed in SEEDS:
                path = SNAP/f'{variant}_seed{seed}'/(split+'_scores.npz')
                a = np.load(path)
                full_scores = a['scores']
                indices = a['indices']
                assert np.array_equal(a['times'], times[indices])
                keep = full[indices].hour == 14
                ix = indices[keep]
                if reference is None: reference = ix
                else: assert np.array_equal(reference, ix)
                score = full_scores[keep, :, 3]
                models[f'{variant}_seed{seed}'] = score
                z.append((score-np.nanmean(score, axis=1, keepdims=True))/np.nanstd(score, axis=1, keepdims=True))
                # Keep raw-label RankIC by slot, including transformed score evaluation.
                for slot in [10, 11, 14, 15]:
                    select = full[indices].hour == slot
                    selected_ix = indices[select]
                    slot_scores = full_scores[select, :, 3]
                    rs = risk_weights(selected_ix, q, np.asarray(load('members')[selected_ix]))
                    for method in ['baseline', 'neutral']:
                        values = []
                        for j, t in enumerate(selected_ix):
                            s = slot_scores[j]
                            if method == 'neutral': s = neutralize(s, rs['exposure'][j])
                            y = labels[t, :, 3]; ok = np.isfinite(s)&np.isfinite(y)
                            if ok.sum() >= 100: values.append(np.corrcoef(rank(s[ok]), rank(y[ok]))[0, 1])
                        slotrows.append(dict(year=year, variant=variant, seed=seed, method=method, slot=slot, rank_ic=float(np.mean(values))))
            models[variant+'_ensemble'] = np.mean(z, axis=0)
        ix = reference
        membership = np.asarray(load('members')[ix])
        risk = risk_weights(ix, q, membership)
        signal = np.asarray(load('hour_ohlcv')[ix, :, 3], float)
        prices = np.asarray(load('execution_prices')[ix], float)
        raw = load('execution_raw_prices')[ix]
        fill = load('execution_complete')[ix]&load('execution_tradable')[ix]&~load('execution_all_flat')[ix]&np.isfinite(raw)&(raw>0)
        tradable = np.asarray(load('hour_complete')[ix]) & np.isfinite(signal) & (signal>0) & (load('hour_ohlcv')[ix, :, 4]>0)
        days = [str(t.date()) for t in pd.to_datetime(execution[ix], utc=True).tz_convert('Asia/Shanghai')]
        assert np.all(execution[ix] > times[ix])
        bnav = bench.loc[days].to_numpy()/bench.loc[bench.index<days[0]].iloc[-1]
        pnav = pricebench.loc[days].to_numpy()/pricebench.loc[pricebench.index<days[0]].iloc[-1]
        for method in methods:
            folder = OUT/('replay_'+method); folder.mkdir(exist_ok=True)
            for model, score in models.items():
                for strategy, buffer in [('Top100', 0), ('Buffer20_50', 1)]:
                    for cost in (['gross', 'net'] if model.endswith('ensemble') else ['net']):
                        targets = Targets(method, risk, membership, tradable); ledger.targets = targets
                        d, e, tr = ledger.simulate(prices, fill, score, signal, days, closes, 0, buffer, cost=='gross', True, 100, membership)
                        holding_metrics(d, tr, risk, signal, prices, closes, days)
                        d['benchmark_nav'] = bnav; d['price_benchmark_nav'] = pnav
                        tag = f'{year}_{model}_{strategy}_{cost}_slip0'
                        if method == 'baseline':
                            old = pd.read_csv(ROOT/'训练回测证据与复核/evidence/replay14_fee2_allin'/(tag+'_daily.csv'))
                            error = float(np.max(np.abs(d.nav.to_numpy()-old.nav.to_numpy())))
                            assert error < 1e-10, (tag, error)
                            parity.append(dict(case=tag, maximum_nav_error=error))
                        d.to_csv(folder/(tag+'_daily.csv'), index=False)
                        e.to_csv(folder/(tag+'_events.csv'), index=False)
                        record = dict(method=method, year=year, model=model, strategy=strategy, cost_case=cost, **ledger.metrics(d, bnav))
                        record['price_index_excess_pp'] = float((d.nav.iloc[-1]-pnav[-1])*100)
                        record['total_return_index_excess_pp'] = float((d.nav.iloc[-1]-bnav[-1])*100)
                        record['max_industry_deviation_proxy'] = float(d.max_industry_deviation_proxy.max())
                        record['beta_pass'] = .9 <= record['beta'] <= 1.1
                        after = d.after_build_grace
                        record['industry_proxy_pass'] = bool((d.loc[after,'max_industry_deviation_proxy'] <= .030001).all())
                        record['stock92_pass'] = bool((d.loc[after,'cash_weight'] <= .080001).all())
                        record['member85_pass'] = bool((d.loc[after,'member_nav_weight'] >= .849999).all())
                        record['single5_pass'] = bool((d.loc[after,'max_stock_nav_weight'] <= .050001).all())
                        record['constraint_fail_days'] = sum(t['status'] != 'optimal' for t in targets.diagnostics)
                        record['expanded_candidate_days'] = sum(t.get('broadened',False) for t in targets.diagnostics)
                        record['estimated_beta_max_unknown_weight'] = float(d.unknown_beta_weight.max())
                        relative = d.nav.to_numpy()/np.r_[1,d.nav.to_numpy()[:-1]]/(bnav/np.r_[1,bnav[:-1]])
                        record['worst20d_relative_return'] = float((pd.Series(relative).rolling(20).apply(np.prod, raw=True)-1).min())
                        summaries.append(record)
                        if targets.diagnostics:
                            pd.DataFrame(targets.diagnostics).to_csv(folder/(tag+'_optimizer.csv'), index=False)
            pd.DataFrame(summaries).to_csv(OUT/'scorecard_running.csv', index=False)
            print('COMPLETED', year, method, flush=True)
    s = pd.DataFrame(summaries)
    s.to_csv(OUT/('scorecard_'+ '_'.join(methods)+'.csv'), index=False)
    pd.DataFrame(slotrows).to_csv(OUT/'rank_ic_by_slot.csv', index=False)
    pd.DataFrame(parity).to_csv(OUT/'baseline_parity.csv', index=False)
    for method in methods:
        if method == 'baseline': continue
        for year in [2025, 2026]:
            for strategy in ['Top100', 'Buffer20_50']:
                for seed in SEEDS:
                    row = {'method':method, 'year':year, 'strategy':strategy, 'seed':seed}
                    for variant in ['control', 'market_attention']:
                        select = s[(s.method==method)&(s.year==year)&(s.strategy==strategy)&(s.model==f'{variant}_seed{seed}')]
                        if len(select): row[variant+'_net_return'] = float(select.iloc[0].net_return)
                    row['attention_minus_control_pp'] = (row['market_attention_net_return']-row['control_net_return'])*100
                    pairs.append(row)
    pd.DataFrame(pairs).to_csv(OUT/('seed_pairs_'+'_'.join(methods)+'.csv'), index=False)
    receipt = {'status':'complete', 'methods':methods, 'cases':len(s), 'code_sha256':sha(__file__), 'tests':checks,
               'fee_per_side':.0002, 'initial_capital':2_000_000, 'benchmark':'official H00905 total return, with price-index comparison',
               'industry_neutrality_officially_certified':False, 'exposed_historical_tests':True,
               'constraint_no_relaxation':'If fixed Top/Buffer pool infeasible, expand to all currently scored risk-valid constituents. If still infeasible, hold existing positions; count failure. No beta or industry bounds relaxed.',
               'suspension_proxy':'Current complete hour, positive observed signal close and positive current-hour volume; no future fill used for selection. Held unavailable stocks frozen. Not authoritative suspension-status feed.',
               'minimum_orders':'Baseline/neutral/bucket retain1000 CNY filter; exact LP constraints use no minimum filter. All are fractional adjusted-unit diagnostic ledgers.',
               'gate':'Realized regression beta in[.9,1.1]; industry proxy maximum<=.03 after3sessions; stock>=.92; membership>=.85; single<=.05. Constraints evaluated after realized fills, not only planned orders.'}
    (OUT/('completion_'+'_'.join(methods)+'.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(s[(s.cost_case=='net') & s.model.str.endswith('ensemble')][['method','year','model','strategy','net_return','total_return_index_excess_pp','beta','max_industry_deviation_proxy','beta_pass']].to_string(index=False))

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--methods', nargs='+', default=['baseline','neutral','bucket','constraint']); a=ap.parse_args()
    main(a.methods)

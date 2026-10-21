"""Read-only audit of the existing overlay; never rerun or overwrite its account.

Writes evidence in a separate audit directory. Counterexamples use synthetic
inputs only and make no claim about corrected historical strategy performance.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import shutil
from unittest.mock import patch

import numpy as np
import pandas as pd
from . import optimizer_overlay as O
from .data import ROOT, OUT, write, sha

DEST = OUT / 'optimizer_overlay_audit_20261011'
SOURCE = OUT / 'optimizer_overlay'


def risk_prefix_checks():
    prices = pd.read_parquet(OUT / 'daily_prices.parquet')
    symbols = json.loads((OUT / 'price_symbols.json').read_text())
    history = pd.read_parquet(ROOT / 'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/benchmarks/H00905_daily.parquet')[['date', 'close']]
    recent = pd.read_csv(ROOT / 'outputs/gru_beta_neutral_20261008/benchmark_sources/H00905_daily.csv')[['date', 'close']]
    for frame in (history, recent):
        frame['date'] = pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d')
    benchmark = pd.concat([history, recent]).drop_duplicates('date', keep='last').set_index('date').close.astype(float)
    pb = pd.read_csv(ROOT / 'outputs/gru_beta_neutral_20261008/benchmark_sources/000905_daily.csv')
    dates = sorted(d for d in set(benchmark.index) & set(pb.date) & set(prices.date) if '2023-03-01' <= d <= O.END)
    def wide(field):
        return prices.pivot(index='date', columns='symbol', values=field).reindex(index=dates, columns=symbols).apply(pd.to_numeric, errors='coerce').to_numpy(float)
    close, amount = wide('close_adjusted'), wide('amount')
    ret = np.full_like(close, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1
    b = benchmark.loc[dates].to_numpy()
    br = np.r_[np.nan, b[1:] / b[:-1] - 1]
    q = np.load(ROOT / 'outputs/gru_beta_neutral_20261008/risk_context.npz')
    clock = pd.to_datetime(q['times'], utc=True).tz_convert('Asia/Shanghai')
    by15 = {str(clock[i].date()): i for i in np.flatnonzero(clock.hour == 15)}
    lookup = {s: i for i, s in enumerate(symbols)}
    mapping = np.array([lookup[s] for s in json.loads((O.ROOT / 'gru_research/artifacts/dataset/symbols.json').read_text())])
    industry = np.full(close.shape, -1, int)
    logcap = np.full(close.shape, np.nan)
    for j, day in enumerate(dates):
        if day in by15:
            industry[j, mapping] = q['industry'][by15[day]]
            logcap[j, mapping] = q['log_cap'][by15[day]]
    cuts = ['2023-08-24', '2023-12-29', '2024-12-31']
    full = O.RiskModel(dates, symbols, ret, amount, logcap, industry, br)
    snapshots = {}
    prev = None
    for j, day in enumerate(dates):
        if prev is not None:
            full.update(j, prev)
        prev = full.exposures(j)
        if day in cuts:
            F, d = full.covariance()
            snapshots[day] = (prev.copy(), F.copy(), d.copy())
    checks = []
    for day in cuts:
        end = dates.index(day) + 1
        prefix = O.RiskModel(dates[:end], symbols, ret[:end], amount[:end], logcap[:end], industry[:end], br[:end])
        prev = None
        for j in range(end):
            if prev is not None:
                prefix.update(j, prev)
            prev = prefix.exposures(j)
        F, d = prefix.covariance()
        X0, F0, d0 = snapshots[day]
        equal_schema = full.inds == prefix.inds
        checks.append(dict(cutoff=day, equal_industry_schema=equal_schema,
            exposure_max_abs=float(np.max(np.abs(prev-X0))) if equal_schema else None,
            factor_covariance_max_abs=float(np.max(np.abs(F-F0))) if equal_schema else None,
            specific_variance_max_abs=float(np.max(np.abs(d-d0))),
            passed=bool(equal_schema and np.array_equal(prev, X0) and np.array_equal(F, F0) and np.array_equal(d, d0))))
    # Future-only schema mutation: a structural future read, not a demonstrated
    # return advantage in the existing dataset (whose schemas above agree).
    changed = industry.copy()
    changed[-1, 0] = 999999
    future_schema = O.RiskModel(dates, symbols, ret, amount, logcap, changed, br)
    schema_test = dict(original_K=full.K, future_mutated_K=future_schema.K,
        past_design_shape_changed=full.exposures(0).shape != future_schema.exposures(0).shape,
        actual_dataset_prefix_checks_passed=all(x['passed'] for x in checks))
    stats = prices.groupby('source').agg(rows=('valid', 'size'), invalid=('valid', lambda x: int((~x).sum())),
                                        missing_ST=('isST', lambda x: int(x.isna().sum()))).to_dict('index')
    return checks, schema_test, stats


def counterexamples():
    n = 10
    b = np.full(n, .098)
    current = np.zeros(n)
    X = np.zeros((n, 6))
    F = np.eye(6)*1e-6
    d = np.full(n, .0004)
    S = np.ones((n, 1))
    alpha = np.linspace(-.04, .04, n)
    p = O.ARMS['O1'].copy()
    with patch.object(O, 'IC_2024', .065):
        w1, _ = O.solve(alpha, b, current, X, F, d, S, p, 1.13, .98)
    with patch.object(O, 'IC_2024', .040):
        w2, _ = O.solve(alpha, b, current, X, F, d, S, p, 1.13, .98)
    assert w1 is not None and w2 is not None
    delta = float(np.max(np.abs(w1-w2)))
    assert delta > 1e-6, 'Calibration should affect the synthetic target'
    # Force solve failure: the actual caller submits the heuristic tilt instead
    # of STOP, and that tilt can exceed the optimizer's frozen name cap.
    ref = np.array([[.49, .49]])
    z = np.array([[-2., 2.]])
    zeros = np.zeros((2, 6))
    with patch.object(O, 'solve', return_value=(None, {'status': 'infeasible_or_failed'})):
        daily, diag = O.simulate('O1', ['2025-01-02'], ref, z, np.array([[10.,10.]]),
            np.array([[10.,10.]]), np.ones((1,2),bool), np.array([[10.,10.]]), ['A','B'], None,
            {0:zeros}, {0:(np.eye(6)*1e-6,np.full(2,.0004))}, {0:np.ones((2,1))},
            [np.array([0,1])], np.ones((1,2),bool))
    assert bool(diag.fallback_tilt.iloc[0])
    return dict(IC_parameter_target_sensitivity=dict(max_target_weight_change=delta, synthetic_only=True),
        forced_solver_failure=dict(fallback_submitted=bool(diag.fallback_tilt.iloc[0]),
            actual_max_stock_NAV_weight=float(daily.max_stock_nav_weight.iloc[0]),
            optimizer_name_cap=.50, cap_violated=bool(daily.max_stock_nav_weight.iloc[0]>.50), synthetic_only=True))


def audit_semesters():
    """Attribute daily relative wealth changes to their ending calendar half."""
    base = pd.read_csv(SOURCE/'P0_daily.csv')
    date = pd.to_datetime(base.date)
    groups = (date.dt.year.astype(str)+'H'+np.where(date.dt.month<=6,'1','2')).to_numpy()
    records = []
    for arm in ('P1','O1','O2'):
        d = pd.read_csv(SOURCE/f'{arm}_daily.csv')
        assert np.array_equal(d.date.to_numpy(),base.date.to_numpy())
        wealth = (d.nav/base.nav).to_numpy()
        growth = wealth[1:]/wealth[:-1]
        for half in sorted(set(groups)):
            take = groups[1:]==half
            records.append(dict(arm=arm, half=half, return_periods=int(take.sum()),
                relative_wealth_vs_P0=float(np.prod(growth[take])-1)))
    pd.DataFrame(records).to_csv(DEST/'recomputed_calendar_half_years.csv',index=False)
    path = DEST/'audit_evidence.json'
    if path.exists():
        evidence = json.loads(path.read_text())
        evidence['recomputed_calendar_half_years'] = records
        evidence['audit_code_sha256'] = sha(__file__)
        evidence['report_O2_eight_positive_calendar_halves_reproduced'] = False
        assert all(sha(ROOT/x['path'])==x['sha256'] for x in evidence['originals'])
        write(path,evidence)
    return records


def main():
    DEST.mkdir(exist_ok=True)
    snapshot = DEST/'original_snapshot'
    snapshot.mkdir(exist_ok=True)
    paths = [ROOT/'pcf_overlay_20261012/optimizer_overlay.py', SOURCE/'protocol.json', SOURCE/'scorecard.csv',
             SOURCE/'yearly_scorecard.csv', SOURCE/'paired_comparisons.csv', SOURCE/'优化器叠加结果_2026-10-11.md',
             OUT/'optimizer_overlay_run.log']
    paths += [SOURCE/f'{arm}_daily.csv' for arm in ('P0','P1','O1','O2')]
    paths += [SOURCE/f'{arm}_optimizer.csv' for arm in ('O1','O2')]
    originals = []
    for path in paths:
        target = snapshot/path.name
        if target.exists():
            if sha(target) != sha(path):
                raise RuntimeError('Original audit snapshot differs; create a new audit version')
        else:
            shutil.copy2(path, target)
        originals.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path)))
    diagnostics = []
    for arm, limit in [('O1',.15),('O2',.10)]:
        d = pd.read_csv(SOURCE/f'{arm}_optimizer.csv')
        active = d[d.status!='warmup_hold_P0']
        diagnostics.append(dict(arm=arm, active_days=len(active),
            uses_2024_IC_before_2025_days=int((active.day<'2025-01-01').sum()),
            target_turnover_above_15_days=int((active.turnover_planned>.150001).sum()),
            maximum_target_turnover=float(active.turnover_planned.max()),
            active_share_below_cap_days=int((active.active_share<limit-1e-6).sum()),
            inaccurate_solutions=int((active.status=='optimal_inaccurate').sum()),
            fallback_days=int(active.get('fallback_tilt',pd.Series(False,index=active.index)).fillna(False).astype(bool).sum())))
    model_boundaries=[]
    for year in range(2023,2027):
        for seed in (11,22,33,44,55):
            run=ROOT/f'outputs/gru_walkforward_20261010/T{year}/control_seed{seed}'
            wf=json.loads((run/'wf.json').read_text()); result=json.loads((run/'result.json').read_text())
            export=json.loads((run/'export.json').read_text())
            w=wf['window']
            model_boundaries.append(dict(year=year,seed=seed,window=w,
                chronological=w['train_end']<=w['val_start']<w['val_end']<=w['test_start'],
                checkpoint_matches=sha(run/'best.pt')==result['best_checkpoint_sha256']==export['checkpoint_sha256'],
                wrapper_matches=wf['wrapper_source_sha256']==sha(ROOT/'gru_walkforward_20261010/experiment_wf.py'),
                base_source_matches=wf['base_experiment_sha256']==sha(ROOT/'gru_market_attention_20261007/experiment.py'),
                prediction_eligibility_uses_labels=export['prediction_eligibility_uses_labels'],
                historical_tests_already_exposed=export['historical_tests_already_exposed']))
    metrics=[]
    base=pd.read_csv(SOURCE/'P0_daily.csv')
    for arm in ('P1','O1','O2'):
        d=pd.read_csv(SOURCE/f'{arm}_daily.csv'); selected=d.date>='2023-08-25'
        nav=d.nav[selected].to_numpy(); p0=base.nav[selected].to_numpy()
        active=nav[1:]/nav[:-1]-p0[1:]/p0[:-1]
        wealth=nav/p0; windows=wealth[50:]/wealth[:-50]-1
        metrics.append(dict(arm=arm, paired_start='2023-08-25', return_periods=len(active),
            tracking_error_vs_P0=float(active.std(ddof=1)*np.sqrt(252)),
            information_ratio_vs_P0=float(active.mean()/active.std(ddof=1)*np.sqrt(252)),
            annualized_relative_wealth_vs_P0=float((wealth[-1]/wealth[0])**(252/len(active))-1),
            relative_P0_50day_p10=float(np.quantile(windows,.1)), relative_P0_50day_worst=float(windows.min()),
            relative_P0_50day_win_rate=float((windows>0).mean())))
    prefix, schema, prices = risk_prefix_checks()
    inputs = [OUT/'pcf_components.parquet', OUT/'pcf_metadata.csv', OUT/'daily_prices.parquet', OUT/'price_symbols.json',
              ROOT/'outputs/gru_beta_neutral_20261008/risk_context.npz', ROOT/'pcf_overlay_20261012/replay.py',
              ROOT/'pcf_overlay_20261012/data.py', ROOT/'gru_market_attention_20261007/context_manifest.json',
              ROOT/'gru_research/artifacts/dataset/slow_factor_selection.json']
    input_fingerprints = [dict(path=str(path.relative_to(ROOT)), sha256=sha(path)) for path in inputs]
    evidence=dict(status='audit_failed_clean_out_of_sample_claim',
        audited_at=datetime.now(ZoneInfo('Asia/Singapore')).isoformat(), originals=originals,
        input_fingerprints=input_fingerprints, audit_code_sha256=sha(__file__),
        original_files_modified=False, full_strategy_rerun=False, corrected_performance_claimed=False,
        diagnostics=diagnostics, model_boundaries=model_boundaries,
        risk_prefix_checks=prefix, future_schema_counterexample=schema,
        synthetic_counterexamples=counterexamples(), source_price_coverage=prices,
        independently_recomputed_pair_metrics=metrics,
        bootstrap_estimand='annualized mean arithmetic daily return difference; not annualized compounded relative wealth',
        limitations=['No exhaustive raw-feature PIT replay or remote training rerun',
                     'PCF historical publication/revision timestamps unverified',
                     'No original per-order target ledger exported; actual daily compliance cannot be fully reconstructed',
                     'No timestamped pre-run calibration source or full discarded-run archive supplied',
                     'No corrected strategy return inferred from synthetic counterexamples'])
    write(DEST/'audit_evidence.json',evidence)
    audit_semesters()
    pd.DataFrame(metrics).to_csv(DEST/'recomputed_pair_metrics.csv',index=False)
    assert all(sha(ROOT/x['path'])==x['sha256'] for x in originals)
    print(json.dumps(dict(audit=str(DEST.relative_to(ROOT)), model_boundaries_checked=len(model_boundaries),
        risk_prefix_checks=prefix, diagnostics=diagnostics, synthetic=evidence['synthetic_counterexamples']),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

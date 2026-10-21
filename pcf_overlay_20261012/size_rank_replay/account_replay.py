"""Paired PCF accounts P0 / OLD_RANK / SIZE_RANK on the rebuilt paired_v1 base, behind a calibration gate.

Run: .pcf-research-venv/bin/python -I pcf_overlay_20261012/size_rank_replay/account_replay.py \
        --snapshot <snapshot dir> --out outputs/pcf_overlay_20261012/size_rank_replay_v1 --protocol-sha <sha256 of PROTOCOL.json>

The engine (tilt_target/account/paired_summary) and the score mapping (validate_signal_pack/map_account_scores) are the
snapshot's own source files, SHA-checked and AST-selected exactly as paired_v1 account.py does; nothing in the snapshot is
imported or run as a script.  SIZE_RANK is only run after P0/OLD_RANK reproduce paired_v1 (PROTOCOL.json calibration_gate).
"""
from pathlib import Path
import argparse, ast, copy, hashlib, json
import numpy as np
import pandas as pd

ENGINE_SHA = 'd4e3b4addfc9ace3f96ad56e50ddeb7139f7aa109d4efd9e08064be53056ae4b'
FORWARD_SHA = '8e93138dab88a10f00aec0923643c68d8ad06da5a4494457940fe8d673a45f19'
BASE_SHA = '9004e56f85f278e9479ae69a4decc6b9bc93c4e076a778a6554d309733c57637'
SIGNAL_SHA = {'OLD_RANK': 'e402d5a3c48ad45bd3d4c5e8c4fde068f5a9d37933a12295519f035e5ee63b1f',
              'SIZE_RANK': '91e4d25aaaf3732c436142701de6de0aa638b02733d5661525338794d8ef2c93'}
PAIRED_SUMMARY_SHA = '08c09aaeca2a031d45058138bb2becc41d80f48b6f6d1558ce69c9f0d0c0b098'
ARRAY_KEYS = {'dates', 'signal_dates', 'valuation_dates', 'symbols', 'signal_time_ns', 'reference', 'scores',
              'signal_marks', 'execution_prices', 'closes', 'fill', 'membership_at_execution_close', 'benchmark'}
SIGNAL_KEYS = {'scores', 'dates', 'symbols', 'signal_time_ns', 'membership', 'roles'}
PARAMETERS = {'capital': 10000000., 'fee_per_side': .0002, 'k': .5, 'min_trade_notional_cny': 1000.,
              'first_signal_date': '2024-12-31', 'last_execution_date': '2026-09-17'}
STATISTICS = {'annual_sessions': 252, 'block_days': 20, 'replicates': 2000, 'seed': 20261011, 'exclude_initial_build': True}
PAIRS = [('P0', 'OLD_RANK'), ('P0', 'SIZE_RANK'), ('OLD_RANK', 'SIZE_RANK')]


def require(ok, message):
    if not ok: raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''): h.update(block)
    return h.hexdigest()


def selected(path, expected, names, ns):
    require(sha(path) == expected, 'SHA mismatch: ' + str(path)); tree = ast.parse(Path(path).read_text(encoding='utf-8')); nodes = []
    for name in names:
        m = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
        require(len(m) == 1 and not any(isinstance(n, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)) for n in ast.walk(m[0])), 'bad pure function ' + name)
        nodes.append(copy.deepcopy(m[0]))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), ns)
    return ns


def window_metrics(kernel, accounts, pairs):
    """Same windows/renaming as paired_v1 account.py window_metrics, for the given pairs."""
    first = accounts['P0']; years = pd.to_datetime(first.date).dt.year.to_numpy()
    primary = np.flatnonzero(years == 2025); carry = np.flatnonzero(years == 2026)
    require(len(first) == 416 and len(primary) == 243 and len(carry) == 173, 'Continuous account window differs')
    result = {}
    for scope, rows in (('primary2025', primary), ('carry2026', np.r_[primary[-1], carry]), ('continuous2025_2026', np.arange(len(first)))):
        comparisons = {}
        for base, candidate in pairs:
            b = accounts[base].iloc[rows].reset_index(drop=True); c = accounts[candidate].iloc[rows].reset_index(drop=True)
            m = kernel['paired_summary'](b, c)
            for key in list(m):
                if '_vs_P0' in key: m[key.replace('_vs_P0', '_vs_base')] = m.pop(key)
            m.update(base_arm=base, candidate_arm=candidate)
            if scope == 'carry2026':
                for key in list(m):
                    if '_including_build' in key: m.pop(key)
                    elif '_excluding_build' in key: m[key.replace('_excluding_build', '_excluding_anchor')] = m.pop(key)
                m['bootstrap']['exclude_initial_build'] = False; m['bootstrap']['exclude_anchor_row'] = True
            comparisons[candidate + '_vs_' + base] = m
        result[scope] = comparisons
    return result


def table(metrics):
    rows = []
    for scope, comps in metrics.items():
        for name, m in comps.items():
            to = 'excluding_anchor' if scope == 'carry2026' else 'excluding_build'
            rows.append({'window': scope, 'comparison': name, 'days': m['days'],
                         'annualized_relative_cagr_pct': 100 * m['annualized_relative_cagr'],
                         'ci95_low_pct': 100 * m['bootstrap']['relative_cagr_95pct'][0], 'ci95_high_pct': 100 * m['bootstrap']['relative_cagr_95pct'][1],
                         'arith_active_ci95_low_pct': 100 * m['bootstrap']['arithmetic_active_mean_annualized_95pct'][0],
                         'arith_active_ci95_high_pct': 100 * m['bootstrap']['arithmetic_active_mean_annualized_95pct'][1],
                         'information_ratio': m['information_ratio_vs_base'], 'tracking_error_pct': 100 * m['tracking_error_arithmetic_active_vs_base'],
                         'relative_max_drawdown_pct': 100 * m['relative_wealth_max_drawdown'],
                         'candidate_net_return_pct': 100 * m['candidate_net_return_after_build'], 'base_net_return_pct': 100 * m['base_net_return_after_build'],
                         'candidate_turnover_two_way_daily_pct': 100 * m[f'candidate_mean_two_way_turnover_close_nav_{to}'],
                         'base_turnover_two_way_daily_pct': 100 * m[f'base_mean_two_way_turnover_close_nav_{to}'],
                         'candidate_fees_cny': m[f'candidate_fees_{to}_cny']})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--snapshot', type=Path, required=True); ap.add_argument('--out', type=Path, required=True); ap.add_argument('--protocol-sha', required=True)
    a = ap.parse_args(); snap = a.snapshot.resolve(); out = a.out.resolve()
    require(sha(out / 'PROTOCOL.json') == a.protocol_sha, 'PROTOCOL.json differs from the frozen hash')
    result_dir = out / 'account'; require(not result_dir.exists(), 'Preserve existing result attempt'); result_dir.mkdir()
    ref = snap / 'HOME/gru_minute_vwap_20261014'; paired = snap / 'DATA/gru_rank_minute_20261015/accounts/paired_v1'
    kernel = selected(ref / 'account_reference_inputs_v1/candidate_replay.py', ENGINE_SHA, ['array_digest', 'tilt_target', 'account', 'paired_summary'],
                      {'np': np, 'pd': pd, 'hashlib': hashlib, 'json': json, 'PARAMETERS': PARAMETERS.copy(), 'STATISTICS': STATISTICS.copy()})
    forward = selected(ref / 'inference.py', FORWARD_SHA, ['validate_signal_pack', 'map_account_scores'],
                       {'np': np, 'pd': pd, 'require': require, 'ACCOUNT_FIELDS': ARRAY_KEYS, 'SIGNAL_FIELDS': SIGNAL_KEYS,
                        'ARMS': ('OLD_RANK', 'SIZE_RANK'), 'SEEDS': (66, 77, 88, 99, 111), 'CHANNELS': ('head4',)})
    require(sha(out / 'inputs/base_rebuilt.npz') == BASE_SHA, 'Rebuilt base differs from the frozen hash')
    with np.load(out / 'inputs/base_rebuilt.npz', allow_pickle=False) as z: base = {k: z[k] for k in z.files}
    require(set(base) == ARRAY_KEYS, 'base fields')
    for v in base.values(): v.flags.writeable = False
    signals = {}
    for arm, h in SIGNAL_SHA.items():
        p = snap / f'HOME/gru_teammate_features_20261014_scratch_v2/inference/{arm}_ensemble_signals.npz'; require(sha(p) == h, 'signal SHA ' + arm)
        with np.load(p, allow_pickle=False) as z: signals[arm] = {k: z[k] for k in z.files}
    for key in ('dates', 'symbols', 'signal_time_ns', 'membership', 'roles'):
        require(np.array_equal(signals['OLD_RANK'][key], signals['SIZE_RANK'][key]), 'paired signal axes differ: ' + key)
    require(np.array_equal(np.isfinite(signals['OLD_RANK']['scores']), np.isfinite(signals['SIZE_RANK']['scores'])), 'paired finite bitmap differs')
    arms = {'P0': dict(base)}
    for arm in signals: arms[arm] = forward['map_account_scores'](base, signals[arm])
    nonscore = {k: kernel['array_digest'](base[k]) for k in ARRAY_KEYS - {'scores'}}
    for name, arr in arms.items(): require(all(kernel['array_digest'](arr[k]) == h for k, h in nonscore.items()), 'non-score field changed ' + name)

    # ---- calibration gate: P0 and OLD_RANK against paired_v1
    require(sha(paired / 'SUMMARY.json') == PAIRED_SUMMARY_SHA, 'paired_v1 SUMMARY differs')
    frozen = json.load(open(paired / 'SUMMARY.json'))['metrics_by_window']
    accounts, ledgers = {}, {}
    for name in ('P0', 'OLD_RANK'):
        daily, trades, plans = kernel['account'](arms[name], 'P0' if name == 'P0' else 'GRU_k05'); accounts[name] = daily; ledgers[name] = (trades, plans)
    gate = {'windows': {}, 'nav': {}, 'targets': {}}
    with np.load(paired / 'OLD_RANK_plans.npz', allow_pickle=False) as z:
        mine = np.asarray([p['target'] for p in ledgers['OLD_RANK'][1]]); gate['targets']['OLD_RANK_max_abs_diff'] = float(np.abs(mine - z['target']).max())
        gate['targets']['OLD_RANK_order_units_max_abs_diff'] = float(np.abs(np.asarray([p['order_units'] for p in ledgers['OLD_RANK'][1]]) - z['order_units']).max())
    for name in ('P0', 'OLD_RANK'):
        theirs = pd.read_csv(paired / f'{name}_daily.csv'); require(np.array_equal(theirs.date.to_numpy(), accounts[name].date.to_numpy()), 'date axis')
        gate['nav'][name] = {'max_rel_nav_dev': float(np.abs(accounts[name].nav.to_numpy() / theirs.nav.to_numpy() - 1).max()),
                             'final_nav_mine': float(accounts[name].nav.iloc[-1]), 'final_nav_paired_v1': float(theirs.nav.iloc[-1]),
                             'turnover_mean_mine_pct': 100 * float(accounts[name].two_way_turnover_close_nav.iloc[1:].mean()),
                             'turnover_mean_paired_v1_pct': 100 * float(theirs.two_way_turnover_close_nav.iloc[1:].mean()),
                             'trades_mine': len(ledgers[name][0]), 'trades_paired_v1': int(sum(1 for _ in open(paired / f'{name}_trades.csv')) - 1)}
    calib = window_metrics(kernel, accounts, [('P0', 'OLD_RANK')])
    for scope in calib:
        m, f = calib[scope]['OLD_RANK_vs_P0'], frozen[scope]['OLD_RANK_vs_P0']
        gate['windows'][scope] = {'mine_pct': 100 * m['annualized_relative_cagr'], 'paired_v1_pct': 100 * f['annualized_relative_cagr'],
                                  'delta_pp': 100 * (m['annualized_relative_cagr'] - f['annualized_relative_cagr']),
                                  'ci_mine_pct': [100 * x for x in m['bootstrap']['relative_cagr_95pct']], 'ci_paired_v1_pct': [100 * x for x in f['bootstrap']['relative_cagr_95pct']],
                                  'ir_mine': m['information_ratio_vs_base'], 'ir_paired_v1': f.get('information_ratio_vs_base')}
    gate['pass'] = bool(gate['targets']['OLD_RANK_max_abs_diff'] <= 1e-12 and all(abs(w['delta_pp']) <= .02 for w in gate['windows'].values())
                        and all(v['max_rel_nav_dev'] <= 1e-4 for v in gate['nav'].values()))
    (result_dir / 'CALIBRATION.json').write_text(json.dumps(gate, ensure_ascii=False, indent=2))
    print(json.dumps(gate, ensure_ascii=False, indent=1), flush=True)
    if not gate['pass']:
        print('CALIBRATION FAILED: SIZE_RANK not run'); return

    daily, trades, plans = kernel['account'](arms['SIZE_RANK'], 'GRU_k05'); accounts['SIZE_RANK'] = daily
    for name, d in accounts.items(): d.to_csv(result_dir / f'{name}_daily.csv', index=False)
    metrics = window_metrics(kernel, accounts, PAIRS)
    (result_dir / 'METRICS.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=1))
    tab = table(metrics); tab.to_csv(result_dir / 'results_table.csv', index=False)
    coverage = {n: float(np.mean(np.sum(np.where(np.isfinite(arms[n]['scores']), base['reference'], 0), axis=1) / base['reference'].sum(axis=1))) for n in signals}
    corr = []
    for t in range(len(base['dates'])):
        x, y = arms['OLD_RANK']['scores'][t], arms['SIZE_RANK']['scores'][t]; ok = np.isfinite(x) & np.isfinite(y) & (base['reference'][t] > 0)
        corr.append(pd.Series(x[ok]).rank().corr(pd.Series(y[ok]).rank()))
    (result_dir / 'SIGNAL_DIAGNOSTICS.json').write_text(json.dumps({'PCF_weight_finite_score_mean': coverage,
        'rank_corr_OLD_vs_SIZE_on_PCF_names_mean': float(np.nanmean(corr)), 'rank_corr_min': float(np.nanmin(corr))}, indent=1))
    pd.set_option('display.width', 250); print(tab.round(3).to_string(index=False))


if __name__ == '__main__':
    main()

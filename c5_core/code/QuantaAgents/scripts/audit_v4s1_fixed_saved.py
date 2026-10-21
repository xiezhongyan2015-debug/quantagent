"""Read-only independent fixed-arm account audit; no strategy execution.

Only saved exposed development ledgers and their original 2017-2021 source
archive are read. No model gateway, registration or worker entry is called.
The audit result is new; all original experiment bytes must remain unchanged.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.saved_execution import SavedRawResearch
from audit_raw_cashflows_v3 import cash_action_audit


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode('utf-8')).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def inventory(root):
    return {str(p.relative_to(root)).replace('\\', '/'): {
        'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
        for p in root.rglob('*') if p.is_file()}


def metrics(body, calendar):
    assert body['calendar'] == [d['date'] for d in body['daily']] == calendar
    assert len(calendar) == 244 and calendar[0] == '2019-01-02' and calendar[-1] == '2019-12-31'
    with localcontext() as ctx:
        ctx.prec = 40
        initial = Decimal(body['initial_cash'])
        assert initial == Decimal('1000000') == Decimal(body['final_snapshot']['external_cash_flow'])
        nav = [Decimal(d['simulated_net_asset_value']) for d in body['daily']]
        previous = [initial] + nav[:-1]
        returns = [end / begin - 1 for begin, end in zip(previous, nav)]
        mean = sum(returns) / len(returns)
        deviation = (sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)).sqrt()
        rf_daily = Decimal('1.02') ** (Decimal(1) / 252) - 1
        high, drawdown = initial, Decimal(0)
        for amount in nav:
            high = max(high, amount)
            drawdown = max(drawdown, 1 - amount / high)
        return {'initial_cash': str(initial), 'final_nav': str(nav[-1]),
            'return_on_full_initial_cash': str(nav[-1] / initial - 1),
            'maximum_drawdown': str(drawdown), 'daily_return_count': len(returns),
            'daily_return_hash': digest([str(r) for r in returns]),
            'sharpe_rf0': str(mean / deviation * Decimal(252).sqrt()) if deviation else None,
            'sharpe_rf2': str((mean - rf_daily) / deviation * Decimal(252).sqrt()) if deviation else None,
            'filled_trade_count': sum(t['quantity'] > 0 for t in body['trades']),
            'exit_fill_batches': sum(t['quantity'] > 0 and t['side'] == 'sell' for t in body['trades'])}


def main(trial):
    assert trial in {'f1', 'f2', 'f3'}, 'Only this frozen study fixed-arm cohort is admitted.'
    root = ROOT / 'experiment_traces/v4s1' / trial
    output = ROOT / 'docs/research/meta_framework_v4_handoff' / f'v4s1_{trial}_account_audit_001.json'
    assert not output.exists(), 'Append-only audit: never replace a previous output.'
    process = read(root / 'process_runs/1/receipt.json')
    assert process['metrics']['active_processes'] == 0, 'Audit only after original process completion.'
    before = inventory(root)
    stage = read(root / 'plan.json')
    control = read(root / 'control/plan.json')
    producer = read(root / 'producer_receipt.json')
    result = producer['result']
    batch = root / 'control/arms/fixed/batches/frozen'
    registration = read(batch / 'registration.json')
    observation = read(root / 'control/arms/fixed/observations/001.json')
    case = control['case']
    assert stage['tasks']['producer']['case'] == case
    assert digest(case) == stage['tasks']['producer']['case_hash'] == control['case_hash']
    assert case['research_class'] == 'real_saved_development'
    assert case['decision_fixture']['codes'] == ['sh600004']
    assert case['decision_fixture']['calendar'][-1] == '2019-12-31'
    assert all('2017' <= day[:4] <= '2021' for day in case['decision_fixture']['calendar'])
    assert result == observation and digest(registration) == result['registration_hash']
    assert producer['actual_model_calls'] == result['actual_project_model_calls'] == 0
    assert producer['model_final'] is False and producer['formal_target_success'] is False
    assert [r['candidate_id'] for r in result['candidates']] == [f'c{i:03}' for i in range(1, 9)]
    assert [r['candidate_id'] for r in registration['candidates']] == [f'c{i:03}' for i in range(1, 9)]
    assert result['reserved_candidates'] == 8 and result['started_candidates'] <= 8
    rows, assertions = [], []
    reviews = {r['candidate_id']: r for r in result['development_selection']['candidate_reviews']}
    for declared, published in zip(registration['candidates'], result['candidates']):
        cid = declared['candidate_id']; folder = batch / 'candidates' / cid
        assert declared['program_hash'] == digest(declared['program']) == reviews[cid]['program_hash']
        if published['status'] == 'completed':
            assert declared['program_hash'] == published['program_hash']
        assert declared['parameters'] == published['parameters']
        if published['status'] in {'not_started', 'invalid'}:
            assert not (folder / 'intent.json').exists()
            rows.append({'candidate_id': cid, 'parameters': declared['parameters'],
                'status': published['status'], 'raw_status_saved_only': None, 'reused': False,
                'program_hash': declared['program_hash'], 'metrics': None,
                'eligible_on_completed_account': False, 'full_capital_cashflow_and_nav_reconciled': False,
                'original_disposition': published, 'formal_success_denominator_contribution': 0})
            continue
        raw_root = folder / 'workbench/raw_children/program'
        raw_plan = read(raw_root / 'plan.json')
        assert raw_plan['calendar'] == case['decision_fixture']['calendar']
        assert raw_plan['codes'] == case['decision_fixture']['codes']
        assert raw_plan['initial_cash'] == case['initial_cash']
        assert raw_plan['identity']['strategy_hash'] == declared['program_hash']
        assert not (folder / 'disposition.json').exists(), 'This cohort has no reused candidate slot.'
        reconciled = SavedRawResearch(raw_root).reconcile_saved_only(expected_plan_sha256=raw_plan['plan_sha256'])
        record = {'candidate_id': cid, 'parameters': declared['parameters'], 'status': published['status'],
            'raw_status_saved_only': reconciled['status'], 'reused': False,
            'program_hash': declared['program_hash'], 'executable_program_hash': declared['executable_program_hash'],
            'process_exit': read(folder / 'process_exit.json'), 'raw_records': reconciled['records'],
            'raw_record_head': reconciled['record_head_sha256'], 'metrics': None,
            'formal_success_denominator_contribution': 0}
        if published['status'] == 'completed':
            receipt = read(folder / 'receipt.json')
            saved = read(folder / 'worker_result.json')
            artifact = saved['artifact']
            assert digest(saved) == receipt['worker_result_hash'] == published['worker_result_hash']
            assert digest(artifact) == saved['artifact_hash'] == receipt['artifact_hash'] == published['artifact_hash']
            assert artifact['program'] == declared['program']
            assert reconciled == artifact['raw'] and reconciled['status'] == 'completed_mechanical'
            body = reconciled['result']
            cash = cash_action_audit(body, case)
            calculated = metrics(body, case['decision_fixture']['calendar'])
            for name, value in calculated.items():
                original = reviews[cid]['metrics'][name]
                if name in {'daily_return_hash', 'daily_return_count', 'filled_trade_count', 'exit_fill_batches'}:
                    assert value == original, (cid, name, value, original)
                else:
                    assert abs(Decimal(value) - Decimal(original)) < Decimal('1e-30'), (cid, name)
            assert Decimal(cash['net_return']) == Decimal(calculated['return_on_full_initial_cash'])
            assert abs(Decimal(cash['maximum_drawdown']) - Decimal(calculated['maximum_drawdown'])) < Decimal('1e-25')
            assert Decimal(published['account']['final_nav']) == Decimal(calculated['final_nav'])
            assert Decimal(published['account']['fees_on_recorded_trades']) == Decimal(cash['fees']['total'])
            assert not any(cash['positions_including_closed'].values())
            eligible = calculated['filled_trade_count'] > 0 and calculated['sharpe_rf2'] is not None and Decimal(calculated['sharpe_rf2']) > 0
            assert reviews[cid]['eligible'] == eligible
            record.update(metrics=calculated, eligible_on_completed_account=eligible,
                full_capital_cashflow_and_nav_reconciled=True,
                cashflow={k:v for k,v in cash.items() if k != 'daily'})
        else:
            assert published['status'] == 'unknown' and not (folder / 'worker_result.json').exists()
            assert not (folder / 'receipt.json').exists()
            assert reconciled['status'] == 'interrupted_saved_only' and reconciled['result'] is None
            partial = reconciled['partial']
            record.update(eligible_on_completed_account=False, full_capital_cashflow_and_nav_reconciled=False,
                saved_prefix={'valued_sessions': len(partial['progress']['daily']),
                    'valuation_complete_through': reconciled['valuation_complete_through'],
                    'last_started_date': reconciled['last_started_date'],
                    'pending_event_intent': reconciled['pending_event_intent'] is not None,
                    'journal_entries': len(partial['journal']),
                    'full_account_metrics_withheld': True})
        rows.append(record)
    selection = result['development_selection']
    assert selection['policy'] == control['development_selection']
    assert selection['policy']['require_all_candidates_settled'] is True
    all_settled = all(r['status'] in {'completed', 'reused', 'failed', 'invalid'} for r in rows)
    assert selection['all_candidates_settled'] == all_settled
    eligible = [r for r in rows if r['eligible_on_completed_account']]
    best = max(eligible, key=lambda r: Decimal(r['metrics']['sharpe_rf2'])) if eligible else None
    selected = best if all_settled else None
    assert selection['outcome'] == ('development_candidate' if selected else 'abstain')
    assert selection['selected_candidate_id'] == (selected['candidate_id'] if selected else None)
    if selected:
        assert selection['selected_program_hash'] == selected['program_hash']
        assert selection['reason'] == 'highest_eligible_rf2_original_order_tie'
    else:
        assert selection['selected_metrics'] is None
        assert selection['reason'] == ('no_eligible_development_candidate' if all_settled else 'unsettled_or_unstarted_frozen_candidate')
    assert process['intent_hash'] == digest(read(root / 'process_runs/1/intent.json'))
    assert process['dispatch_hash'] == digest(read(root / 'process_runs/1/dispatch.json'))
    assert process['exit_code'] == 0 and process['metrics']['active_processes'] == 0
    registry = ROOT / 'experiment_traces/meta_framework_v3/study_registry.sqlite3'
    with sqlite3.connect(registry.as_uri() + '?mode=ro', uri=True) as db:
        model_calls, model_tokens = db.execute(
            'SELECT count(*),COALESCE(sum(known_tokens),0) FROM calls WHERE study_id=? AND trial_id=?', ('v4s1', trial)).fetchone()
        stored = db.execute('SELECT receipt FROM producer_runs WHERE study_id=? AND trial_id=?', ('v4s1', trial)).fetchone()
    assert model_calls == model_tokens == 0 and json.loads(stored[0]) == producer
    after = inventory(root)
    assert before == after, 'An original experiment artifact changed during read-only audit.'
    completed_count = sum(r['full_capital_cashflow_and_nav_reconciled'] for r in rows)
    summary = {'kind': 'v4s1_fixed_independent_saved_account_audit_v1', 'trial_id': trial,
        'observed_at': datetime.now(timezone.utc).isoformat(), 'root': str(root),
        'original_artifacts_unchanged': True, 'original_file_inventory': before,
        'audit_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'cashflow_audit_script_sha256': hashlib.sha256((ROOT / 'scripts/audit_raw_cashflows_v3.py').read_bytes()).hexdigest(),
        'candidate_slots': 8, 'started_candidates': result['started_candidates'],
        'status_counts': dict(Counter(r['status'] for r in rows)), 'reused_candidates': 0,
        'complete_account_count': completed_count,
        'nominal_initial_cash_per_candidate': '1000000.00', 'frozen_calendar_sessions': 244,
        'metric_definition': {'annualization': 252, 'sample_ddof': 1, 'annual_rf2': '0.02',
            'rf_daily': '1.02**(1/252)-1', 'inactive_cash_days_included': True,
            'return_denominator': 'complete 1000000 CNY initial capital', 'drawdown_includes_initial_capital': True,
            'exit_definition': 'each positive-quantity sell fill batch, including reductions; not flat-position round trips'},
        'candidate_rows': rows, 'selection_verified': True, 'selection': selection,
        'process_receipt': process, 'known_model_calls': model_calls, 'known_model_tokens': model_tokens,
        'formal_success_denominator_contribution': 0, 'formal_target_success': False,
        'new_model_calls': 0, 'new_strategy_executions': 0, 'old_records_modified': False,
        'findings': [f'{completed_count} full 244-session accounts reconcile; all remaining original dispositions are retained.',
            'All eight original slots remain in the result. Zero reused or replaced candidates.',
            'Frozen all-candidates-settled selection rule independently verified.',
            'Outer process exit zero records a settled producer report, not eight completed accounts.'],
        'limitations': ['One previously exposed 2019 stock/calendar; no independent market sample or formal financial contribution.',
            'Mechanical funded cash/share consistency is verified; historical arrival, real fills, market capacity and statutory/account-specific tax classification remain uncertified.',
            'Any interrupted account has no full-return, full-Sharpe or full-drawdown claim. No prefix replaces its full denominator.',
            'OS I/O transfer counters are not physical disk bytes and overlap tool measurements; audit and outer-controller work are outside trial cost.']}
    with output.open('x', encoding='utf-8') as stream:
        stream.write(serial(summary))
    print(serial({'output': str(output), 'status_counts': summary['status_counts'],
        'selection': selection['outcome'], 'selected_candidate_id': selection['selected_candidate_id'],
        'rows': [{k:r.get(k) for k in ('candidate_id','parameters','status','metrics','saved_prefix')} for r in rows],
        'formal_success_denominator_contribution': 0, 'original_artifacts_unchanged': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', choices=['f1', 'f2', 'f3'], required=True)
    main(parser.parse_args().trial)

"""Read-only saved-account audit for v4c1 and its explicitly bound child.

Freeze this script's metric policy before reading candidate results. Saved
ledger-event replay is permitted; strategy, market-source and model execution
are absent. Running work remains incomplete and can never pass this audit.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.ledger import digest, need, serial
from quanta_agents.meta_v3.kernel import module
from quanta_agents.meta_v3 import saved_execution

SCOPE = ROOT / 'experiment_traces/v4c1'
POLICY = {
    'version': 'v4_saved_full_cash_audit_v1', 'scope': 'experiment_traces/v4c1',
    'nav': 'Every frozen trading day in order, including cash days; initial prior NAV is the full frozen initial cash.',
    'daily_return': 'NAV[t]/NAV[t-1]-1; no removed first day or inactive days; no capital rescaling.',
    'annualization': 252, 'standard_deviation_ddof': 1,
    'primary_rf_annual': '0.02', 'primary_rf_daily': '(1.02)^(1/252)-1',
    'sensitivity_rf_annual': '0', 'sharpe': 'sqrt(252)*(mean(daily_return)-daily_rf)/sample_std',
    'zero_volatility': 'undefined; never a numeric or formal pass',
    'maximum_drawdown': 'max(1-NAV[t]/max(initial_cash,NAV[0..t]))',
    'exit_batches': 'Distinct committed positive-quantity sell_fill event IDs; reductions are included and labelled.',
    'flat_share_exits': 'Positive total shares to zero at a committed sell event, including active pending bonus shares; dividend rights can survive.',
    'numeric_minimum_days': 252, 'numeric_minimum_exit_batches': 30,
    'numeric_maximum_drawdown': '0.25', 'numeric_sharpe_strictly_greater_than': '1',
    'no_trade': 'Cannot pass numeric screening even if another scalar appears favorable.',
    'formal': 'Always false here: 2019 is exposed development, not independent OOS or certified costs/capacity.',
    'scope_comparison': 'Parent and child statistics remain separate; information sets differ. Keep every candidate and failure.',
}


def read(path, maximum=128 * 1024**2):
    path = Path(path)
    need(path.is_file() and path.stat().st_size <= maximum, 'missing or oversized audit input: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(serial(value))


def number(value):
    need(type(value) in (str, int, float, Decimal) and not isinstance(value, bool), 'numeric saved amount required')
    value = Decimal(str(value))
    need(value.is_finite(), 'nonfinite saved amount')
    return value


def nav_metrics(initial_cash, navs, *, trade_count, exit_batches, flat_share_exits):
    """Independent Decimal arithmetic on a supplied complete NAV sequence."""
    initial = number(initial_cash)
    need(initial > 0 and navs, 'positive full initial cash and complete NAV sequence required')
    need(all(type(n) is int and n >= 0 for n in (trade_count, exit_batches, flat_share_exits)), 'nonnegative event counts required')
    with localcontext() as context:
        context.prec = 50
        previous = high = initial
        returns, maximum_dd = [], Decimal(0)
        for value in navs:
            nav = number(value)
            need(previous > 0 and nav >= 0, 'daily return undefined after nonpositive prior NAV')
            returns.append(nav / previous - 1)
            high = max(high, nav)
            maximum_dd = max(maximum_dd, 1 - nav / high)
            previous = nav
        mean = sum(returns) / len(returns)
        std = (sum((value - mean)**2 for value in returns) / (len(returns) - 1)).sqrt() if len(returns) > 1 else None
        daily_rf = Decimal('1.02') ** (Decimal(1) / 252) - 1
        sharpe0 = mean / std * Decimal(252).sqrt() if std else None
        sharpe2 = (mean - daily_rf) / std * Decimal(252).sqrt() if std else None
        thresholds = {'days_at_least_252': len(navs) >= 252, 'exits_at_least_30': exit_batches >= 30,
            'drawdown_at_most_25_percent': maximum_dd <= Decimal('0.25'),
            'rf2_sharpe_strictly_greater_than_1': sharpe2 is not None and sharpe2 > 1,
            'has_funded_trades': trade_count > 0, 'nonzero_sample_volatility': bool(std)}
        return {'initial_cash': str(initial), 'final_nav': str(previous),
            'net_pnl': str(previous - initial), 'net_return': str(previous / initial - 1),
            'saved_days': len(navs), 'daily_return_count': len(returns),
            'daily_return_hash': digest([str(value) for value in returns]),
            'daily_return_mean': str(mean), 'daily_return_sample_std': str(std) if std is not None else None,
            'rf2_daily': str(daily_rf), 'sharpe_rf2': str(sharpe2) if sharpe2 is not None else None,
            'sharpe_rf0': str(sharpe0) if sharpe0 is not None else None,
            'maximum_drawdown': str(maximum_dd), 'trade_count': trade_count,
            'sell_fill_batches': exit_batches, 'flat_share_exits': flat_share_exits,
            'numeric_thresholds': thresholds, 'passes_numeric_thresholds': all(thresholds.values()),
            'formal_target_success': False}


def snapshot(root, task_id):
    root = Path(root).resolve()
    with sqlite3.connect((root / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        stage = dict(db.execute('SELECT * FROM stage WHERE id=1').fetchone())
        task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
        calls = [dict(row) for row in db.execute('SELECT * FROM calls ORDER BY created,id')]
    plan = json.loads(stage['plan'])
    need(digest(plan) == stage['plan_hash'] and read(root / 'plan.json') == plan, 'stage plan binding drift')
    need(set(plan['tasks']) == {task_id} and json.loads(task['input']) == plan['tasks'][task_id], 'task input binding drift')
    need(digest(plan['tasks'][task_id]['case']) == plan['tasks'][task_id]['case_hash'], 'case hash drift')
    for call in calls:
        need(call['task_id'] == task_id and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', call['id']), 'foreign call identity')
        for key in ('intent', 'receipt', 'result'):
            call[key] = json.loads(call[key]) if call[key] is not None else None
        need(call['intent']['plan_hash'] == stage['plan_hash'], 'call intent scope drift')
    return {'root': root, 'task_id': task_id, 'plan': plan, 'state': task, 'calls': calls}


def stages():
    """Only follow settled parent request identities, never arbitrary globbed roots."""
    parent = snapshot(SCOPE, 'ap')
    output, handoffs = [parent], []
    for call in parent['calls']:
        response = (call['receipt'] or {}).get('response', {})
        if call['status'] != 'applied' or response.get('action') != 'request_research_extension':
            continue
        folder = SCOPE / 'controller_extensions' / call['id']
        item = {'request_id': call['id'], 'status': 'awaiting_controller_decision'}
        handoffs.append(item)
        if not (folder / 'decision.json').exists():
            continue
        request = read(SCOPE / 'tools/ap' / call['id'] / 'artifact.json')
        need(digest(request) == call['result']['artifact_hash'] and request['request'] == json.loads(response['arguments_json']), 'request artifact drift')
        intent, decision = read(folder / 'intent.json'), read(folder / 'decision.json')
        need(Path(intent['parent_root']).resolve() == SCOPE.resolve() and intent['parent_task'] == 'ap'
             and intent['request_id'] == call['id'] and intent['parent_plan_hash'] == digest(parent['plan'])
             and intent['parent_case_hash'] == parent['plan']['tasks']['ap']['case_hash']
             and intent['request_hash'] == digest(request), 'handoff intent binding drift')
        need(decision['intent_hash'] == digest(intent) and decision['status'] == intent['decision']['status'], 'handoff decision drift')
        item['status'] = decision['status']
        if decision['status'] != 'ready':
            need(decision['child_root'] is None, 'ungranted child root')
            continue
        child_root = (folder / 'stage').resolve()
        need(Path(decision['child_root']).resolve() == child_root and child_root.is_relative_to(SCOPE.resolve()), 'child path escaped bound scope')
        child = snapshot(child_root, 'extension')
        lineage = child['plan']['provenance']['research_extension']
        need(decision['child_plan_hash'] == digest(child['plan']) and lineage['intent_hash'] == digest(intent)
             and Path(lineage['intent_path']).resolve() == (folder / 'intent.json').resolve()
             and Path(lineage['decision_path']).resolve() == (folder / 'decision.json').resolve()
             and child['plan']['tasks']['extension']['case_hash'] == intent['child_case_hash'], 'child lineage drift')
        output.append(child)
    need(len(output) <= 2, 'more than one admitted child')
    return output, handoffs


def positions(state, *, pending=True):
    held = defaultdict(int)
    for lot in state['lots'].values():
        held[lot['symbol']] += lot['quantity']
    if pending:
        for action in state['actions'].values():
            if action['active']:
                held[action['symbol']] += action['bonus_pending']
    return {code: value for code, value in held.items() if value}


def account_checks(body):
    """Recompute saved cash/mark/tax identities while replaying recorded events."""
    ledger = module('raw_share_ledger').RawShareLedger.replay(body['genesis'], [])
    entries = body['journal']
    cursor, flat_exits = 0, 0
    filled = {}
    fees_by_date, slippage_by_date = defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0))
    for trade in body['trades']:
        need(trade['event_id'] not in filled and trade['quantity'] > 0, 'duplicate or nonpositive saved fill')
        filled[trade['event_id']] = trade
        fees_by_date[trade['date']] += number(trade['fees']['total'])
        slip = abs(number(trade['raw_price']) - number(trade['raw_open'])) * trade['quantity']
        need(slip == number(trade['slippage_amount']), 'recorded slippage amount mismatch')
        slippage_by_date[trade['date']] += slip
    cumulative_fees = cumulative_slippage = Decimal(0)
    seen_fills = set()
    for row in body['daily']:
        valuation = row['valuation']
        while ledger.snapshot()['journal_head'] != valuation['ledger_head']:
            need(cursor < len(entries), 'daily valuation head absent from saved journal')
            entry = entries[cursor]
            event = entry['event']
            before = positions(ledger.snapshot())
            ledger.apply(event, as_of=entry['applied_at'])
            need(ledger.journal[-1] == entry, 'recorded event replay differs')
            if event['kind'] in ('buy_fill', 'sell_fill'):
                need(event['event_id'] in filled, 'journal fill absent from trade table')
                trade, data = filled[event['event_id']], event['data']
                need(trade['side'] + '_fill' == event['kind'] and trade['quantity'] == data['quantity']
                     and trade['symbol'] == data['symbol'] and number(trade['raw_price']) == number(data['raw_price'])
                     and number(trade['fees']['total']) == number(data['fees']), 'trade/journal binding differs')
                seen_fills.add(event['event_id'])
                if event['kind'] == 'sell_fill' and before.get(data['symbol'], 0) > 0 and not positions(ledger.snapshot()).get(data['symbol'], 0):
                    flat_exits += 1
            cursor += 1
        state = ledger.snapshot()
        need(number(state['external_cash_flow']) == number(body['initial_cash']), 'capital resized or new external funding')
        need(positions(state, pending=False) == row['holdings'], 'saved daily holdings differ')
        raw = valuation['raw_ledger_valuation']
        marked = sum((quantity * number(raw['mark_evidence'][code]['raw_price']) for code, quantity in positions(state).items()), Decimal(0))
        receivable = sum((number(a['cash_gross_due']) for a in state['actions'].values() if a['active'] and not a['cash_paid']), Decimal(0))
        gross = number(state['cash']) + receivable + marked
        unpaid = number(row['realized_tax_unpaid']) + number(row['remaining_tax_reserve'])
        need(number(row['cash_available']) == number(state['cash']) == number(raw['cash_available']), 'cash ledger mismatch')
        need(receivable == number(row['cash_receivable_gross']) == number(raw['cash_receivable_gross']), 'dividend receivable mismatch')
        need(marked == number(raw['share_value_including_pending']) and gross == number(row['gross_asset_value']), 'gross asset valuation mismatch')
        need(unpaid == number(valuation['estimated_total_tax_unpaid']) and gross - unpaid == number(row['simulated_net_asset_value']), 'net asset/tax reserve mismatch')
        need(number(row['simulated_net_pnl']) == number(row['simulated_net_asset_value']) - number(body['initial_cash']), 'full capital PnL mismatch')
        cumulative_fees += fees_by_date[row['date']]
        cumulative_slippage += slippage_by_date[row['date']]
        need(cumulative_fees == number(row['fees_paid_cumulative']) == number(state['fees_paid']), 'cumulative fee mismatch')
        need(cumulative_slippage == number(row['slippage_in_fill_prices_cumulative']), 'cumulative embedded slippage mismatch')
    need(cursor == len(entries) and seen_fills == set(filled) and ledger.snapshot() == body['final_snapshot'], 'final ledger or fill set differs')
    return {'saved_event_replay_verified': True, 'daily_cash_mark_tax_identities_verified': True,
        'fees_paid': str(cumulative_fees), 'slippage_already_in_prices': str(cumulative_slippage),
        'sell_fill_batches': sum(t['side'] == 'sell' for t in filled.values()), 'flat_share_exits': flat_exits,
        'trade_count': len(filled), 'ending_positions': positions(ledger.snapshot()),
        'ending_cash': ledger.snapshot()['cash'], 'historical_tax_and_execution_certified': False}


def candidate(stage, call):
    row = {'call_id': call['id'], 'call_status': call['status'], 'status': 'incomplete', 'formal_target_success': False}
    if call['status'] not in ('applied', 'failed'):
        return row
    result = call['result'] or {}
    if not result.get('artifact_hash'):
        return {**row, 'status': 'failed_without_account', 'saved_failure': result}
    folder = stage['root'] / 'tools' / stage['task_id'] / call['id']
    artifact = read(folder / 'artifact.json')
    need(digest(artifact) == result['artifact_hash'] and read(folder / 'result.json') == result, 'candidate saved artifact binding drift')
    raw = artifact.get('raw')
    row.update(artifact_path=str(folder / 'artifact.json'), artifact_hash=digest(artifact), program_hash=digest(artifact['program']))
    if not raw:
        return {**row, 'status': 'failed_before_raw_execution', 'saved_failure': artifact.get('workbench')}
    raw_root = folder / 'workbench/raw_children/program'
    raw_plan = read(raw_root / 'plan.json')
    case = stage['plan']['tasks'][stage['task_id']]['case']
    need(raw_plan['identity']['data_hash'] == digest(case) and raw_plan['identity']['strategy_hash'] == digest(artifact['program'])
         and raw_plan['calendar'] == case['decision_fixture']['calendar'] and raw_plan['codes'] == case['decision_fixture']['codes']
         and number(raw_plan['initial_cash']) == number(case['initial_cash']), 'candidate execution scope differs')
    replay = saved_execution.SavedRawResearch(raw_root).reconcile_saved_only(expected_plan_sha256=raw_plan['plan_sha256'])
    need(replay == raw, 'saved raw execution differs from candidate artifact')
    row['raw_record_head_sha256'] = replay['record_head_sha256']
    if raw['status'] != 'completed_mechanical' or raw.get('result') is None:
        return {**row, 'status': 'failed_or_interrupted_account', 'raw_status': raw['status'],
            'saved_failure': raw.get('error'), 'last_valued_date': raw.get('valuation_complete_through'), 'metrics': None}
    body = raw['result']
    need([r['date'] for r in body['daily']] == case['decision_fixture']['calendar'], 'incomplete, reordered or duplicate NAV calendar')
    checks = account_checks(body)
    metrics = nav_metrics(body['initial_cash'], [r['simulated_net_asset_value'] for r in body['daily']],
        **{key: checks[key] for key in ('trade_count', 'flat_share_exits')}, exit_batches=checks['sell_fill_batches'])
    summary = result.get('public', {}).get('account_summary', {})
    for key, metric in (('initial_cash', 'initial_cash'), ('final_net_asset_value', 'final_nav'),
                        ('net_return_on_full_initial_cash', 'net_return'), ('maximum_daily_drawdown', 'maximum_drawdown')):
        need(key in summary and abs(number(summary[key]) - number(metrics[metric])) <= Decimal('1e-24'), 'public account summary differs: ' + key)
    return {**row, 'status': 'complete_saved_account_verified', 'accounting': checks, 'metrics': metrics,
        'source_market_files_reopened': False, 'strategy_reexecuted': False}


def audit(policy_path):
    frozen = read(policy_path)
    need(frozen['policy'] == POLICY and frozen['policy_hash'] == digest(POLICY)
         and frozen['script_sha256'] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'frozen audit script/policy changed')
    scoped, handoffs = stages()
    reports = []
    for stage in scoped:
        candidates, calls = [], []
        for call in stage['calls']:
            action = (call['receipt'] or {}).get('response', {}).get('action')
            calls.append({'call_id': call['id'], 'status': call['status'], 'action': action,
                'known_tokens': call['known_tokens'], 'unresolved_reserve': call['reserve'] if call['known_tokens'] is None and call['status'] in ('pending', 'unknown', 'received', 'applying') else 0})
            if action == 'develop_strategy':
                try:
                    candidates.append(candidate(stage, call))
                except (ValueError, KeyError, TypeError, OSError, AssertionError) as exc:
                    candidates.append({'call_id': call['id'], 'status': 'audit_failed', 'reason': str(exc), 'formal_target_success': False})
        reports.append({'root': str(stage['root']), 'task_id': stage['task_id'], 'plan_hash': digest(stage['plan']),
            'terminal': stage['state']['terminal'], 'calls': calls, 'candidates': candidates,
            'complete': bool(stage['state']['terminal']) and all(c['status'] in ('applied', 'failed') for c in calls)})
    return {'kind': 'v4_structural_cycle_independent_accounting_audit_v1',
        'created_at': datetime.now(timezone.utc).isoformat(), 'frozen_policy_path': str(Path(policy_path).resolve()),
        'policy_hash': digest(POLICY), 'stages': reports, 'handoffs': handoffs,
        'all_scopes_terminal': all(r['complete'] for r in reports),
        'cycle_ended': all(r['complete'] for r in reports) and all(h['status'] != 'awaiting_controller_decision' for h in handoffs),
        'audit_failures': sum(c['status'] == 'audit_failed' for r in reports for c in r['candidates']),
        'research_model_calls': sum(len(r['calls']) for r in reports),
        'research_known_tokens': sum(c['known_tokens'] or 0 for r in reports for c in r['calls']),
        'unknown_or_pending_reserve': sum(c['unresolved_reserve'] for r in reports for c in r['calls']),
        'actual_currency_cost': None, 'formal_target_success': False,
        'limitations': ['Saved arithmetic and event consistency only; no original market files were reopened.',
            'Tax, cost, availability, capacity and source-completeness assumptions remain declared simulation.',
            'All candidates and failures remain separate; no return-based filtering or cross-scope matched-performance claim.',
            '2019 exposed development with 244 days does not satisfy formal OOS or architecture-generalization requirements.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-policy', help='Write a new immutable policy artifact without reading results')
    parser.add_argument('--policy', help='Previously frozen policy artifact')
    parser.add_argument('--output', help='New independent audit output; existing paths are refused')
    args = parser.parse_args()
    if args.freeze_policy:
        need(args.policy is None and args.output is None, 'policy freeze is a separate operation')
        save(args.freeze_policy, {'kind': 'v4_saved_account_audit_policy',
            'created_at': datetime.now(timezone.utc).isoformat(), 'policy': POLICY,
            'policy_hash': digest(POLICY), 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        print(serial({'policy_path': str(Path(args.freeze_policy).resolve()), 'results_read': False}))
    else:
        need(args.policy and args.output and not Path(args.output).exists(), 'frozen policy and new output required')
        result = audit(args.policy)
        save(args.output, result)
        print(serial({'output': str(Path(args.output).resolve()), 'all_scopes_terminal': result['all_scopes_terminal'],
            'audit_failures': result['audit_failures'], 'candidates': [c['status'] for r in result['stages'] for c in r['candidates']]}))


if __name__ == '__main__':
    main()

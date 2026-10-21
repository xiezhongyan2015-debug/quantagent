"""Read-only completed-final audit; no gateway, query execution or final application.

Reuses the already saved independent Decimal bookkeeper only. All supporting
tables come from this campaign's returned query journal. Frozen v9 arithmetic
is an evaluator cross-check, never researcher input.
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

PROJECT = Path(__file__).resolve().parents[4]
ROOT = PROJECT/'experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T183231732416Z'
ACCEPTED = PROJECT/'experiment_traces/meta_calibration_prototypes/attempts/20260906T164840172917Z'
OUT = Path(__file__).resolve().parent
INPUTS = {}
LEGACY = PROJECT/'docs/research/meta_framework_v10_handoff/actual_reviews_artifacts_001/audit_saved_cases.py'
assert hashlib.sha256(LEGACY.read_bytes()).hexdigest() == 'bf4231169437bd42e07687882912c3e6ab1a98ea7a43292648ab5654e7def8f4'
spec = importlib.util.spec_from_file_location('frozen_independent_arithmetic_v10', LEGACY)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
digest, dec = previous.digest, previous.dec
sys.path.insert(0, str(PROJECT/'experiment_traces/meta_ashare_revision12/src'))
from quanta_agents.meta.diagnostic_calibration import validate_submission_contract


def read(path, parse=True):
    raw = path.read_bytes()
    INPUTS[str(path.relative_to(PROJECT))] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    return json.loads(raw) if parse else raw


def save(name, value):
    with (OUT/name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def extended_arithmetic(tables, packet, computed):
    days = computed['daily']
    all_codes = sorted({row['code'] for row in tables['trades']})
    inputs = {(row['date'], row['symbol']): row for row in tables.get('horizon_inputs', [])}
    if inputs:
        all_codes = sorted(set(all_codes) | {key[1] for key in inputs})
    availability, target_audit = [], []
    target_lookup = {row['date']: row['weights'] for row in tables['target_weights']}
    for index, day in enumerate(days):
        held = [code for code, row in day['inventory'].items() if dec(row['quantity']) != 0]
        stocks = []
        marks = {}
        for code in all_codes:
            inv = day['inventory'].get(code, {'quantity': '0', 'basis': '0'})
            quantity, basis = dec(inv['quantity']), dec(inv['basis'])
            if quantity == 0:
                marked = Decimal(0)
            elif inputs:
                assert packet['initial_information']['marking_contract']['kind'] == 'synthetic_open_equals_end_of_day_mark'
                marked = quantity * dec(inputs[(day['date'], code)]['open'])
            elif len(held) == 1:
                marked = dec(tables['daily'][index]['position_value'])
            else:
                marked = None
            if marked is not None:
                marks[code] = marked
            stocks.append({'code': code, 'quantity': str(quantity), 'cost_basis_cny': str(basis),
                'marked_value_cny': str(marked) if marked is not None else None,
                'unrealized_pnl_cny': str(marked-basis) if marked is not None else None,
                'identifiable': marked is not None})
        if len(marks) == len(all_codes):
            assert abs(sum(marks.values())-dec(tables['daily'][index]['position_value'])) <= dec('0.000001')
        availability.append({'date': day['date'], 'held_codes': held,
            'stock_pnl_status': 'complete' if len(marks) == len(all_codes) else 'partial', 'stocks': stocks})
        if inputs:
            before = days[index-1] if index else {'cash': packet['initial_information']['capital'], 'inventory': {}}
            equity = dec(before['cash']) + sum((dec(inv['quantity'])*dec(inputs[(day['date'], code)]['open'])
                for code, inv in before['inventory'].items()), Decimal(0))
            target_audit.append({'date': day['date'], 'pre_trade_open_marked_equity': str(equity),
                'derived_desired_units': {code: str(dec(weight)*equity/dec(inputs[(day['date'], code)]['open']))
                    for code, weight in target_lookup[day['date']].items()}})
    dates = [row['date'] for row in days]
    inventory, entries, closed = defaultdict(Decimal), {}, []
    for order in tables['trades']:
        code, q = order['code'], dec(order['shares'])
        if order['side'] == 'buy':
            if inventory[code] == 0:
                entries[code] = order['date']
            inventory[code] += q
        else:
            inventory[code] -= q
            assert inventory[code] >= 0
            if inventory[code] == 0:
                closed.append({'code': code, 'entry_date': entries.pop(code), 'exit_date': order['date']})
    by_year = []
    previous_unrealized = Decimal(0)
    for year in sorted({day['date'][:4] for day in days}):
        year_days = [day for day in days if day['date'].startswith(year)]
        terminal = dec(year_days[-1]['portfolio_unrealized'])
        by_year.append({'year': year, 'ending_unrealized_cny': str(terminal),
                        'unrealized_change_cny': str(terminal-previous_unrealized)})
        previous_unrealized = terminal
    return {'availability': availability,
        'unknown_multistock_dates': [day['date'] for day in availability if day['stock_pnl_status'] == 'partial'],
        'target_units': target_audit,
        'target_zero_and_nonzero_inventory': [{'date': day['date'], 'code': code, 'quantity': inv['quantity']}
            for day in days for code, inv in day['inventory'].items()
            if dec(inv['quantity']) != 0 and dec(target_lookup[day['date']][code]) == 0],
        'closed_spells': closed,
        'open_inventory': [{'code': code, 'quantity': str(q), 'entry_date': entries[code],
            'observed_sessions': dates.index(dates[-1])-dates.index(entries[code])}
            for code,q in inventory.items() if q], 'yearly_unrealized': by_year}


def audit_case(case, plan, run, calls):
    case_calls = sorted([call for call in calls if call['case_id'] == case], key=lambda call: call['round_index'])
    assert all(call['status'] == 'completed' for call in case_calls)
    final_call = case_calls[-1]
    folder = ROOT/'calls'/(final_call['step']+'-1')
    response, receipt, schema = [read(folder/name) for name in ('response.json', 'receipt.json', 'output_schema.json')]
    previous.shape(response, schema)
    assert response['action'] == 'submit_diagnostic' and response['queries'] == [] and len(response['submissions']) == 1
    assert response == receipt['response'] == final_call['receipt']['response']
    action = run['actions'][final_call['id']]
    assert action['status'] == 'applied' and action['action'] == response
    for name, expected in receipt['artifact_sha256'].items():
        assert hashlib.sha256(read(folder/name, False)).hexdigest() == expected
    packet = read(ROOT/'cases'/case/'public/packet.json')
    with sqlite3.connect((ROOT/'cases'/case/'public/journal.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        pages = [{'seq': r[0], 'request_id': r[1], 'request': json.loads(r[2]), 'result': json.loads(r[3])}
                 for r in db.execute('SELECT seq,request_id,request,result FROM queries ORDER BY seq')]
        submissions = list(db.execute('SELECT body,receipt FROM submission'))
    assert len(submissions) == 1 and json.loads(submissions[0][0]) == response['submissions'][0]
    report = response['submissions'][0]
    evidence, tables, evidence_tables = {packet['public_package_hash']}, {}, {}
    for page in pages:
        body = page['result']['response']
        assert body['page_sha256'] == digest({key:value for key,value in body.items() if key != 'page_sha256'})
        assert page['result']['evidence_id'] == 'evidence-'+digest(body)
        assert body['next_cursor'] is None and body['returned_rows'] == body['total_rows'] == len(body['rows'])
        evidence.add(page['result']['evidence_id'])
        evidence_tables[page['result']['evidence_id']] = page['request']['table']
        assert page['request']['table'] not in tables
        tables[page['request']['table']] = body['rows']
    validate_submission_contract(report, packet, evidence_ids=evidence)  # pure validation; never submit/apply
    computed = previous.arithmetic(tables, packet['initial_information']['capital'])
    extended = extended_arithmetic(tables, packet, computed)
    private = ACCEPTED/case/'evaluator_private'
    accounting, ident = read(private/'independent_accounting.json'), read(private/'independent_identifiability.json')
    expected = dict(accounting['totals'])
    expected['reference_pnl_cny'] = expected['same_filled_path_reference_pnl_cny']
    expected.update(closed_spells_count=str(len(accounting['closed_spells'])),
        right_censored_count=str(len(accounting['open_inventory'])),
        closed_duration_sessions=str(max(row['duration_sessions'] for row in accounting['closed_spells'])),
        terminal_single_stock_unrealized_cny=ident['terminal_single_stock_unrealized_cny'])
    if case == 'calibration_01':
        horizon = read(private/'independent_horizons.json')
        expected.update(horizon['means'], common_event_count=str(horizon['common_event_count']))
        assert [row['date'] for row in tables['session_calendar']] == [row['date'] for row in tables['daily']]
    assert extended['unknown_multistock_dates'] == ident['availability_audit']['unknown_multistock_dates']
    for key, value in computed['totals'].items():
        if value is not None:
            assert abs(dec(value)-dec(expected[key])) <= dec('0.000001')
    comparisons = []
    for finding in report['findings']:
        if finding['claim_type'] != 'numeric':
            continue
        assert finding['availability'] != 'not_identifiable'
        quantity = finding['quantity_id']
        tolerance = Decimal(0) if finding['unit'] in ('sessions','count') else dec('1e-12') if finding['unit'] == 'return_fraction' else dec('1e-6')
        residual = abs(dec(finding['value_decimal'])-dec(expected[quantity]))
        assert residual <= tolerance
        comparisons.append({'quantity_id': quantity, 'claimed': finding['value_decimal'], 'v9_oracle': str(expected[quantity]),
            'independent_returned_rows_arithmetic': computed['totals'][quantity], 'absolute_residual': str(residual),
            'correct': True, 'cited_tables': [evidence_tables.get(eid,'public_packet') for eid in finding['evidence_ids']]})
    numeric = {row['quantity_id'] for row in comparisons}
    result = {'case_id': case, 'frozen_case_status': run['cases'][case]['status'], 'legal_unique_final': True,
        'model_calls': len(case_calls), 'queries': len(pages), 'findings': len(report['findings']),
        'input_tokens': sum(call['usage']['input_tokens'] for call in case_calls),
        'output_tokens': sum(call['usage']['output_tokens'] for call in case_calls),
        'queries_by_table': [page['request']['table'] for page in pages], 'evidence_tables': evidence_tables,
        'all_citations_returned_to_this_case': True, 'numeric_claims': comparisons,
        'frozen_numeric_coverage_inventory_omitted_as_numeric': [q for q in plan['evaluation_dimensions'][case]['numeric_coverage_inventory'] if q not in numeric],
        'computed_arithmetic': computed, 'extended_arithmetic': extended,
        'oracle_terminal_scalar_status': ident['terminal_single_stock_status'],
        'query_logical_sha256': digest(pages), 'final_response_sha256': hashlib.sha256((folder/'response.json').read_bytes()).hexdigest(),
        'provider_request_binding_verified': receipt['identity_verification']['provider_request_binding_verified'],
        'new_research_gateway_calls': 0, 'new_backtests': 0, 'original_records_modified': False}
    save(case+'_source_snapshot.json', {'calls': case_calls, 'applied_action': action, 'query_pages': pages,
        'public_packet': packet, 'submission': report, 'submission_receipt': json.loads(submissions[0][1])})
    save(case+'_audit.json', result)
    return {key: result[key] for key in ('case_id','legal_unique_final','model_calls','queries','input_tokens','output_tokens',
        'numeric_claims','frozen_numeric_coverage_inventory_omitted_as_numeric')}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    frozen = read(ROOT/'post_run_frozen_records_001.json')
    assert INPUTS[str((ROOT/'post_run_frozen_records_001.json').relative_to(PROJECT))]['sha256'] == 'ae0db82a8883bc20bc8162b8b66841e6a1b2d04bf67e3fdbed9976b1968ba50b'
    for name, expected in frozen['files'].items():
        assert hashlib.sha256(read(ROOT/name, False)).hexdigest() == expected
    plan = read(ROOT/'campaign.json')['plan']
    with sqlite3.connect((ROOT/'ledger.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        run = json.loads(db.execute('SELECT body FROM runs').fetchone()[0])
        calls = [json.loads(row[0]) for row in db.execute('SELECT body FROM calls')]
    summaries = [audit_case(case, plan, run, calls) for case in ('calibration_01','calibration_02')]
    save('input_manifest.json', {'files': INPUTS, 'audit_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'reused_arithmetic_source_sha256': hashlib.sha256(LEGACY.read_bytes()).hexdigest(),
        'read_only_database_policy': 'mode=ro SELECT only', 'new_research_gateway_calls': 0,
        'engineering_model_usage': 'unknown; not metered by study ledger', 'original_campaign_modified': False})
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

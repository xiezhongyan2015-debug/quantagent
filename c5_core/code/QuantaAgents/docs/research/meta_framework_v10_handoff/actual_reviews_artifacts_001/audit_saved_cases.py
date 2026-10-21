"""Read-only audit of two completed saved submissions; no gateway/replay/import.

Only this new audit directory receives output. Decimal bookkeeping consumes
saved returned raw columns, never calls the strategy or diagnostic oracle code.
"""
from collections import defaultdict
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

getcontext().prec = 50
PROJECT = Path(__file__).resolve().parents[4]
CAMPAIGN = PROJECT/'experiment_traces/meta_diagnostic_calibrations/campaigns/diagnostic_20260906T172850309804Z'
ACCEPTED = PROJECT/'experiment_traces/meta_calibration_prototypes/attempts/20260906T164840172917Z'
OUTPUT = Path(__file__).resolve().parent
INPUTS = {}


def canonical(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(x):
    return hashlib.sha256(canonical(x).encode()).hexdigest()


def read(path, *, parse=True):
    raw = path.read_bytes()
    INPUTS[str(path.relative_to(PROJECT))] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    return json.loads(raw.decode('utf-8')) if parse else raw


def save(name, body):
    with (OUTPUT/name).open('x', encoding='utf-8') as stream:
        json.dump(body, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def shape(x, schema):
    kind = schema.get('type')
    if 'enum' in schema:
        assert x in schema['enum']
    if kind == 'object':
        assert type(x) is dict and set(schema.get('required', [])) <= set(x)
        if schema.get('additionalProperties') is False:
            assert set(x) <= set(schema.get('properties', {}))
        for key, child in schema.get('properties', {}).items():
            if key in x:
                shape(x[key], child)
    elif kind == 'array':
        assert type(x) is list and schema.get('minItems', 0) <= len(x) <= schema.get('maxItems', float('inf'))
        for item in x:
            shape(item, schema['items'])
    elif kind == 'string':
        assert type(x) is str and schema.get('minLength', 0) <= len(x) <= schema.get('maxLength', float('inf'))
    elif kind == 'integer':
        assert type(x) is int and schema.get('minimum', -float('inf')) <= x <= schema.get('maximum', float('inf'))
    else:
        raise AssertionError('unreviewed schema vocabulary')


def dec(x):
    return Decimal(str(x))


def arithmetic(tables, capital):
    daily, trades = tables['daily'], tables['trades']
    dates = [r['date'] for r in daily]
    cash = dec(capital)
    book = defaultdict(lambda: [Decimal(0), Decimal(0), None])
    realized = fees = slip = Decimal(0)
    by_code = defaultdict(Decimal)
    days, closed, sell_checks = [], [], []
    for index, day in enumerate(daily):
        for order in (r for r in trades if r['date'] == day['date']):
            code = order['code']
            q, price, fee = (dec(order[k]) for k in ('shares', 'price', 'commission'))
            amount = q * price
            assert abs(amount - dec(order['turnover'])) <= dec('0.000001')
            fees += fee
            slip += dec(order['slippage'])
            inventory = book[code]
            if order['side'] == 'buy':
                if inventory[0] == 0:
                    inventory[2] = index
                inventory[0] += q
                inventory[1] += amount + fee
                cash -= amount + fee
            else:
                assert q <= inventory[0]
                released = inventory[1] * q / inventory[0]
                pnl = amount - fee - released
                realized += pnl
                by_code[code] += pnl
                sell_checks.append({'source_order_ordinal': order['source_order_ordinal'],
                    'computed_realized': str(pnl), 'reported_realized': str(order['realized_pnl']),
                    'absolute_residual': str(abs(pnl - dec(order['realized_pnl'])))})
                cash += amount - fee
                inventory[0] -= q
                inventory[1] -= released
                if inventory[0] == 0:
                    closed.append({'code': code, 'entry_date': dates[inventory[2]], 'exit_date': day['date'],
                                   'duration_sessions': index - inventory[2]})
                    inventory[1], inventory[2] = Decimal(0), None
        assert abs(cash - dec(day['cash'])) <= dec('0.000001')
        basis = sum((r[1] for r in book.values()), Decimal(0))
        days.append({'date': day['date'], 'cash': str(cash), 'basis': str(basis),
            'portfolio_unrealized': str(dec(day['position_value']) - basis),
            'inventory': {code: {'quantity': str(row[0]), 'basis': str(row[1])} for code, row in book.items()}})
    terminal = days[-1]
    net = dec(daily[-1]['balance']) - dec(capital)
    assert abs(sum((dec(r['net_pnl']) for r in daily), Decimal(0)) - net) <= dec('0.000001')
    unrealized = dec(terminal['portfolio_unrealized'])
    assert abs(net - realized - unrealized) <= dec('0.000001')
    active = [code for code, row in book.items() if row[0] != 0]
    totals = {'net_pnl_cny': str(net), 'reference_pnl_cny': str(net + fees + slip),
        'fees_cny': str(fees), 'slippage_cny': str(slip), 'realized_net_pnl_cny': str(realized),
        'unrealized_change_cny': str(unrealized), 'closed_spells_count': str(len(closed)),
        'right_censored_count': str(len(active)),
        'closed_duration_sessions': str(max((r['duration_sessions'] for r in closed), default=0)),
        'terminal_single_stock_unrealized_cny': str(unrealized) if len(active) == 1 else None}
    result = {'totals': totals, 'daily': days, 'closed': closed, 'sell_checks': sell_checks,
        'terminal_held_codes': active, 'realized_by_code': {k: str(v) for k, v in by_code.items()},
        'buy_turnover': str(sum((dec(r['turnover']) for r in trades if r['side'] == 'buy'), Decimal(0))),
        'sell_turnover': str(sum((dec(r['turnover']) for r in trades if r['side'] == 'sell'), Decimal(0))),
        'buy_slippage': str(sum((dec(r['slippage']) for r in trades if r['side'] == 'buy'), Decimal(0))),
        'sell_slippage': str(sum((dec(r['slippage']) for r in trades if r['side'] == 'sell'), Decimal(0))),
        'cash_only_days': sum(dec(r['position_value']) == 0 for r in daily),
        'no_trade_dates': [r['date'] for r in daily if dec(r['trade_count']) == 0],
        'initial_capital_return': str(net / dec(capital))}
    if 'horizon_inputs' in tables:
        inputs = tables['horizon_inputs']
        lookup = {(r['date'], r['symbol']): r for r in inputs}
        events = []
        for r in inputs:
            if not (r['eligible'] and dec(r['filter_value']).is_finite() and dec(r['filter_value']) != 0
                    and dec(r['score']).is_finite() and dec(r['signal_value']).is_finite()):
                continue
            t = dates.index(r['date'])
            if t + 11 >= len(dates):
                continue
            entry = lookup[dates[t + 1], r['symbol']]
            event = {'signal_date': r['date'], 'symbol': r['symbol'], 'entry_date': entry['date'],
                     'entry_open': str(entry['open']), 'outcomes': {}}
            for h in (1, 5, 10):
                endpoint = lookup[dates[t + h + 1], r['symbol']]
                event['outcomes'][str(h)] = {'exit_date': endpoint['date'],
                    'gross_return': str(dec(endpoint['open']) / dec(entry['open']) - 1)}
            events.append(event)
        result['horizon_events'] = events
        totals['common_event_count'] = str(len(events))
        for h in (1, 5, 10):
            totals[f'horizon_{h}_mean'] = str(sum((dec(e['outcomes'][str(h)]['gross_return']) for e in events), Decimal(0)) / len(events))
    return result


def audit(case, plan):
    ledger = sqlite3.connect((CAMPAIGN/'ledger.sqlite3').as_uri()+'?mode=ro', uri=True)
    calls = [json.loads(row[0]) for row in ledger.execute("SELECT body FROM calls WHERE json_extract(body,'$.case_id')=? ORDER BY json_extract(body,'$.round_index')", (case,))]
    action = json.loads(ledger.execute("SELECT value FROM runs,json_each(runs.body,'$.actions') WHERE json_extract(value,'$.case_id')=? AND json_extract(value,'$.status')='rejected'", (case,)).fetchone()[0])
    ledger.close()
    assert calls and all(c['status'] == 'completed' for c in calls)
    folder = CAMPAIGN/'calls'/calls[-1]['step']
    folder = folder.with_name(folder.name+'-1')
    response, receipt, schema = [read(folder/name) for name in ('response.json','receipt.json','output_schema.json')]
    shape(response, schema)
    assert response == receipt['response'] == action['action']
    for name, expected in receipt['artifact_sha256'].items():
        assert hashlib.sha256(read(folder/name, parse=False)).hexdigest() == expected
    packet = read(CAMPAIGN/'cases'/case/'public/packet.json')
    wb = sqlite3.connect((CAMPAIGN/'cases'/case/'public/journal.sqlite3').as_uri()+'?mode=ro', uri=True)
    pages = [{'seq': r[0], 'request_id': r[1], 'request': json.loads(r[2]), 'result': json.loads(r[3])}
             for r in wb.execute('SELECT seq,request_id,request,result FROM queries ORDER BY seq')]
    assert wb.execute('SELECT count(*) FROM submission').fetchone()[0] == 0
    wb.close()
    evidence = {packet['public_package_hash']}
    tables = {}
    for page in pages:
        page_body = page['result']['response']
        assert page_body['page_sha256'] == digest({k: v for k, v in page_body.items() if k != 'page_sha256'})
        assert page['result']['evidence_id'] == 'evidence-'+digest(page_body)
        assert page_body['next_cursor'] is None
        evidence.add(page['result']['evidence_id'])
        tables[page['request']['table']] = page_body['rows']
    report = response['submissions'][0]
    errors, seen = [], set()
    for i, f in enumerate(report['findings'], 1):
        assert set(f['evidence_ids']) <= evidence
        if f['claim_type'] == 'numeric':
            if f['quantity_id'] not in packet['quantity_dictionary'] or f['quantity_id'] in seen:
                errors.append({'finding_number': i, 'reason': 'numeric quantity is unknown or duplicated', 'finding': f})
            seen.add(f['quantity_id'])
        elif f['value_decimal'] or f['unit'] != 'none':
            errors.append({'finding_number': i, 'reason': 'non-numeric finding must not contain a number', 'finding': f})
    assert len(errors) == 1 and errors[0]['reason'] == action['error']
    private = ACCEPTED/case/'evaluator_private'
    accounting, ident = read(private/'independent_accounting.json'), read(private/'independent_identifiability.json')
    expected = dict(accounting['totals'])
    expected['reference_pnl_cny'] = expected['same_filled_path_reference_pnl_cny']
    expected.update(closed_spells_count=str(len(accounting['closed_spells'])),
        right_censored_count=str(len(accounting['open_inventory'])),
        closed_duration_sessions=str(max(r['duration_sessions'] for r in accounting['closed_spells'])),
        terminal_single_stock_unrealized_cny=ident['terminal_single_stock_unrealized_cny'])
    if case == 'calibration_01':
        horizon = read(private/'independent_horizons.json')
        expected.update(horizon['means'], common_event_count=str(horizon['common_event_count']))
    computed = arithmetic(tables, packet['initial_information']['capital'])
    for k, v in computed['totals'].items():
        if v is not None:
            assert abs(dec(v) - dec(expected[k])) <= dec('0.000001')
    comparisons = []
    for i, f in enumerate(report['findings'], 1):
        if f['claim_type'] != 'numeric':
            continue
        val = expected.get(f['quantity_id'])
        if f['quantity_id'] == 'retained_session_count':
            val = str(accounting['coverage']['days'])
        residual = abs(dec(f['value_decimal']) - dec(val)) if val is not None else None
        tolerance = dec('0') if f['unit'] in ('sessions', 'count') else dec('0.000000000001') if f['unit'] == 'return_fraction' else dec('0.000001')
        comparisons.append({'finding_number': i, 'quantity_id': f['quantity_id'], 'claimed': f['value_decimal'],
            'oracle': val, 'absolute_residual': str(residual) if residual is not None else None,
            'arithmetic_correct': residual is not None and residual <= tolerance,
            'registered': f['quantity_id'] in packet['quantity_dictionary']})
    result = {'case_id': case, 'original_status': 'failed', 'original_reason': action['error'],
        'public_schema_structurally_valid': True, 'semantic_interface_errors': errors,
        'model_calls': len(calls), 'queries': len(pages), 'accepted_final_submissions': 0,
        'reported_input_tokens': sum(c['usage']['input_tokens'] for c in calls),
        'reported_output_tokens': sum(c['usage']['output_tokens'] for c in calls),
        'reported_io_tokens': sum(c['usage']['input_tokens']+c['usage']['output_tokens'] for c in calls),
        'queries_by_table': [p['request']['table'] for p in pages],
        'all_citations_returned_to_this_case': True, 'final_artifact_hashes_match_saved_receipt': True,
        'numeric_claims': comparisons, 'computed_arithmetic': computed,
        'frozen_evaluation_inventory_omitted_as_numeric': [q for q in plan['evaluation_dimensions'][case]['numeric_coverage_inventory'] if q not in seen],
        'registered_quantities_omitted_as_numeric': [q for q in packet['quantity_dictionary'] if q not in seen],
        'oracle_unknown_multistock_dates': ident['availability_audit']['unknown_multistock_dates'],
        'oracle_terminal_scalar_status': ident['terminal_single_stock_status'],
        'ledger_case_calls_sha256': digest(calls), 'rejected_action_sha256': digest(action),
        'query_journal_logical_sha256': digest(pages),
        'original_outputs_changed': False, 'new_research_gateway_calls': 0, 'new_backtests': 0}
    save(case+'_source_snapshot.json', {'calls': calls, 'rejected_action': action, 'query_pages': pages, 'public_packet': packet})
    save(case+'_audit.json', result)
    return {k: result[k] for k in ('case_id','original_reason','model_calls','queries','reported_io_tokens','numeric_claims','frozen_evaluation_inventory_omitted_as_numeric')}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    plan = read(CAMPAIGN/'campaign.json')['plan']
    results = [audit(case, plan) for case in ('calibration_01', 'calibration_02')]
    save('input_manifest.json', {'files': INPUTS, 'audit_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'database_access': 'sqlite URI mode=ro, case-specific SELECT only; logical snapshots hashed',
        'original_campaign_modified': False, 'provider_identity_independently_attested': False,
        'engineering_model_usage': 'not measured by this research ledger', 'new_research_gateway_calls': 0})
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

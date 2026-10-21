"""One retained GENERATED selector/account integration, never market research.

Requires a fresh output directory. Prices are frozen to cents before deriving
the same-path returns; original case dates and full cash remain in the account.
No model, network, account credential, old study, or market source is invoked.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import time

import pandas as pd

from quanta_agents.meta_v3.ledger import digest
from quanta_agents.meta_v3.research_tools import ResearchTools, save_once
from quanta_agents.meta_v3 import conditional_risk_derivation as derivation
from quanta_agents.meta_v3 import saved_execution
from quanta_agents.meta_v3.runtime import source_pins, verify_case_sources


def generated_paths():
    """A deterministic priced version of the independent paired-vector design."""
    days = list(pd.bdate_range('2019-01-02', periods=77).strftime('%Y-%m-%d'))
    codes = [f'sh600{index:03d}' for index in range(12)]
    market = [Decimal(v) / 100 for v in [-3, -3, -2, -2, -1, -1] * 6 + [1, 2, 3, 4] * 6]
    directions = [[int(j == i) for j in range(18)] for i in range(10)]
    directions += [[int(j < 3) for j in range(18)], [-1 if j == 0 else int(j < 3) for j in range(18)]]
    theory = {'GENERATED_MARKET': market}
    for code, direction in zip(codes, directions):
        residual = [Decimal(sign * value) / 1000 for value in direction for sign in (1, -1)] + [Decimal(0)] * 24
        theory[code] = [Decimal('.002') + Decimal('.6') * m + u for m, u in zip(market, residual)]
    prices, returns = {}, {}
    for symbol, sequence in theory.items():
        values = [Decimal('100.00')]
        for i in range(76):
            values.append((values[-1] * (1 + sequence[i % 60])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        prices[symbol] = values
        returns[symbol] = [None] + [float(values[i] / values[i-1] - 1) for i in range(1, 77)]
    return days, codes, prices, returns


def raw_sources(folder, days, codes, prices):
    artifacts, obligations = [], []
    for code in codes:
        source = folder / code
        source.mkdir(parents=True)
        rows = []
        for i, day in enumerate(days):
            previous = prices[code][max(0, i-1)]
            # Opening equals the preceding frozen close; the signal cannot use
            # this session's later close to size the already dispatched order.
            opening, close = previous, prices[code][i]
            rows.append({'code': code, 'date': day, 'raw_open': float(opening), 'raw_close': float(close),
                         'raw_prev_close': float(previous), 'raw_price_text': {'raw_open': str(opening),
                         'raw_close': str(close), 'raw_prev_close': str(previous)}, 'volume': 10_000_000,
                         'stock_name': 'GENERATED engineering stock', 'execution_valid': False})
        content = gzip.compress(json.dumps(rows, allow_nan=False).encode(), mtime=0)
        manifest = json.dumps({'codes': [code], 'evidence_class': 'generated_engineering',
                               'execution_valid': False, 'adjusted_price_or_factor_inversion_used': False}).encode()
        (source / 'rows.json.gz').write_bytes(content)
        (source / 'manifest.json').write_bytes(manifest)
        artifacts.append({'code': code, 'root': str(source.resolve()), 'rows_file': 'rows.json.gz',
                          'rows_sha256': hashlib.sha256(content).hexdigest(), 'manifest_file': 'manifest.json',
                          'manifest_sha256': hashlib.sha256(manifest).hexdigest()})
        for day in days[-16:]:
            for kind in saved_execution.KINDS:
                obligations.append({'code': code, 'date': day, 'kind': kind,
                                    'status': 'documented_scope' if kind == 'corporate_actions' else 'declared_simulation',
                                    'evidence_sha256': digest({'generated': True, 'code': code, 'date': day, 'kind': kind}),
                                    'note': 'GENERATED engineering only; all assumptions explicit, no market certification'})
    return {'source_artifacts': artifacts, 'obligations': obligations}


def bundle_and_fixture(days, codes, prices, returns):
    market_rows, stock_rows, field_rows, eligibility_rows = [], [], [], []
    account_days = days[-16:]
    late_day = account_days[5]
    for i, day in enumerate(days[1:], 1):
        stamp = day + 'T15:05:00+08:00'
        common = {'session': day, 'previous_session': days[i-1], 'effective_at': stamp,
                  'available_at': stamp, 'source_status': 'generated_known'}
        market = {**common, 'value': returns['GENERATED_MARKET'][i]}
        market['source_evidence_id'] = derivation.generated_row_id(market)
        market_rows.append(market)
        for code in codes:
            arrival = day + 'T15:11:00+08:00' if day == late_day and code == codes[-1] else stamp
            eligibility = {'eligible': True, 'effective_at': stamp, 'available_at': arrival,
                           'source_status': 'generated_known'}
            eligibility['source_evidence_id'] = derivation.generated_row_id(eligibility)
            stock = {**common, 'symbol': code, 'value': returns[code][i],
                     'return_basis': 'generated_causal_total_economic_return',
                     'action_coverage_status': 'generated_complete', 'eligibility': eligibility}
            stock['source_evidence_id'] = derivation.generated_row_id(stock)
            stock_rows.append(stock)
            if day in account_days:
                field_rows.append({'session': day, 'symbol': code, 'field': 'close',
                                   'value': float(prices[code][i]), 'effective_at': stamp,
                                   'available_at': stamp, 'source_evidence_id': stock['source_evidence_id']})
                eligibility_rows.append({'session': day, 'symbol': code, 'eligible': True,
                                         'effective_at': stamp, 'available_at': arrival,
                                         'source_evidence_id': eligibility['source_evidence_id']})
    bundle = {'version': derivation.VERSION, 'evidence_class': derivation.CLASS, 'original_calendar': days,
              'symbols': codes, 'market_rows': market_rows, 'stock_rows': stock_rows}
    fixture = {'kind': 'exposed_real_decision_table', 'codes': codes, 'calendar': account_days,
               'fields': [{'name': 'close', 'unit': 'CNY'}], 'field_rows': field_rows,
               'eligibility_rows': eligibility_rows}
    return bundle, fixture


def run(output):
    output = Path(output).absolute()
    if output.exists() or output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError('Use a fresh directory under an existing unredirected parent')
    output.mkdir()
    started, cpu = time.perf_counter(), time.process_time()
    save_once(output / 'intent.json', {'evidence_class': derivation.CLASS, 'case_count': 1,
        'candidate_count': 1, 'symbols': 12, 'account_sessions': 16, 'original_close_positions': 77,
        'source_pins': source_pins(), 'recipe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'new_research_model_calls': 0, 'real_research': False, 'formal_target_success': False})
    days, codes, prices, returns = generated_paths()
    save_once(output / 'generated_price_path.json', {'evidence_class': derivation.CLASS,
        'original_calendar': days, 'close_prices': {s: list(map(str, p)) for s, p in prices.items()},
        'returns_derived_after_cent_rounding': returns, 'corporate_actions': [],
        'market_identity': 'GENERATED substitute, no actual 000905.SH source is supplied or admitted'})
    bindings = raw_sources(output / 'generated_sources', days, codes, prices)
    bundle, fixture = bundle_and_fixture(days, codes, prices, returns)
    price_path = output / 'generated_price_path.json'
    case = {'research_class': 'real_saved_development', 'evidence_class': derivation.CLASS,
            'execution_backend': 'v3_streamed_001', 'description': 'GENERATED engineering only; never a registered research case',
            'initial_cash': '1000000.00', 'decision_fixture': fixture, 'raw_source_bindings': bindings,
            'evidence_sources': [{'path': str(price_path), 'sha256': hashlib.sha256(price_path.read_bytes()).hexdigest()}]}
    base_hash = digest(case)
    built = derivation.build(case, bundle)
    assert digest(case) == base_hash
    case.update(built)
    save_once(output / 'case.json', case)
    receipts = case[derivation.KEY]['anchor_receipts']
    assert receipts[0]['measurement']['status'] == 'ready', receipts[0]['measurement']['reason']
    assert receipts[1]['measurement']['status'] == 'target_cash'
    assert receipts[1]['source_failures']
    verify_case_sources({'case': case, 'case_hash': digest(case)})
    tools = ResearchTools(output / 'stage', 'generated', case, [])
    save_once(output / 'public_contract.json', tools.contract())
    program = {'version': 'factor_strategy_program_v1', 'factors': [], 'target_weight_expression': derivation.FIELD,
               'hypothesis': 'GENERATED fixed-controller conditional residual selector integration',
               'applicability': ['Engineering only; no financial inference'],
               'invalidation_conditions': ['Wrong input window, dispatch clock, full-cash accounting or custody']}
    response = tools.execute('fixed_selector_001', 'develop_strategy', {'program': program})
    save_once(output / 'response.json', response)
    artifact_path = tools.folder / 'fixed_selector_001/artifact.json'
    artifact = json.loads(artifact_path.read_text(encoding='utf-8'))
    target_path = tools.folder / 'fixed_selector_001/workbench/targets.json'
    targets = json.loads(target_path.read_text(encoding='utf-8'))
    raw = artifact['raw']
    assert raw['status'] == 'completed_mechanical', raw.get('error')
    body = raw['result']; account_days = days[-16:]
    assert [r['date'] for r in body['daily']] == account_days
    assert body['final_snapshot']['external_cash_flow'] == '1000000.00'
    assert sum(e['event']['kind'] == 'cash_deposit' for e in body['journal']) == 1
    assert all(o['trade_date'] in {account_days[i] for i in (1,6,11,15)} for o in body['orders'])
    assert len(body['daily'][1]['holdings']) == 10
    assert all(body['daily'][i]['holdings'] == body['daily'][1]['holdings'] for i in range(2,6))
    assert body['daily'][6]['holdings'] == body['daily'][-1]['holdings'] == {}
    assert all(body['daily'][i]['orders'] == 0 for i in (2,3,4,5,7,8,9,10,12,13,14))
    assert Decimal(body['final_snapshot']['fees_paid']) > 0
    assert all(Decimal(r['cash_available']) >= 0 for r in body['daily'])
    report = {'kind': 'generated_conditional_selector_full_account_001', 'evidence_class': derivation.CLASS,
        'assertions_passed': True, 'case_sha256': digest(case), 'program_sha256': digest(program),
        'artifact_sha256': hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        'targets_sha256': hashlib.sha256(target_path.read_bytes()).hexdigest(),
        'derivation_verification': derivation.verify_case(case),
        'original_close_positions': len(days), 'original_stock_count': len(codes), 'account_sessions': len(body['daily']),
        'original_anchor_statuses': [{'signal_date': r['signal_date'], 'status': r['measurement']['status'],
                                    'reason': r['measurement']['reason']} for r in receipts],
        'selected_at_first_anchor': receipts[0]['measurement']['selected'],
        'first_target_gross_weight': receipts[0]['measurement']['target_gross_weight'],
        'scheduled_target_rows': len(targets['targets']), 'omitted_target_rows': len(targets['omitted_target_rows']),
        'orders': len(body['orders']), 'trades': len(body['trades']), 'fees_paid': body['final_snapshot']['fees_paid'],
        'external_cash_flow': body['final_snapshot']['external_cash_flow'], 'final_holdings': body['daily'][-1]['holdings'],
        'full_initial_cash': '1000000.00', 'new_engineering_account_replays': 1, 'new_real_strategy_runs': 0,
        'new_research_model_calls': 0, 'new_formal_starts': 0, 'real_research': False, 'formal_target_success': False,
        'preparation_and_verification_wall_ms': round((time.perf_counter() - started) * 1000, 3),
        'preparation_and_verification_controller_cpu_ms': round((time.process_time() - cpu) * 1000, 3),
        'measurement_scope': 'This whole generated script after imports, including input preparation and all internal verification/account work. No registered trial budget or AI currency measurement.',
        'controller_and_review_ai_currency_cost': 'unknown_not_zero',
        'limitations': ['Generated prices, index substitute, eligibility, costs and capacity; zero independent market evidence.',
                       'Fixed controller selector; researcher cannot define arbitrary matrix algorithms through this field.',
                       'Real source/arrival/action authentication, original real case admission, fair study costs and formal evaluation remain unfulfilled.']}
    save_once(output / 'receipt.json', report)
    print(json.dumps({'output': str(output), 'assertions_passed': True, 'account_sessions': len(body['daily']),
                      'orders': len(body['orders']), 'trades': len(body['trades']), 'fees_paid': report['fees_paid'],
                      'formal_target_success': False}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.output)

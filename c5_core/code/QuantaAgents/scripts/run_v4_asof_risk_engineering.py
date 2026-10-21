"""One retained generated version-view/risk/full-account integration.

Reuses pure generated recipe helpers, never a previous run or saved account.
The fixed index name labels a generated stand-in, not acquired index evidence.
"""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from run_v4_conditional_risk_engineering import generated_paths, raw_sources, bundle_and_fixture
from quanta_agents.meta_v3 import asof_return_views as views
from quanta_agents.meta_v3 import conditional_risk_derivation as derivation
from quanta_agents.meta_v3.ledger import digest
from quanta_agents.meta_v3.research_tools import ResearchTools, save_once
from quanta_agents.meta_v3.runtime import source_pins, verify_case_sources


def code_pins():
    result = source_pins()
    root = Path(__file__).resolve().parents[1]
    # Importing quanta_agents runs its package initializer and common workflow
    # imports; the older runtime inventory alone does not bind those modules.
    for p in sorted((root/'src/quanta_agents').rglob('*.py')):
        result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    for name in ('run_v4_asof_risk_engineering.py', 'run_v4_conditional_risk_engineering.py'):
        p = Path(__file__).with_name(name)
        result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def versioned_bundle(days, codes, prices, returns):
    bundle, fixture = bundle_and_fixture(days, codes, prices, returns)
    bundle['version'] = derivation.ASOF_VERSION
    for row in [*bundle['market_rows'], *bundle['stock_rows']]:
        row.update(value=None, effective_at=None, available_at=None, source_status='unknown')
        if 'symbol' in row:
            row.update(return_basis='unknown', action_coverage_status='unknown')
        row['source_evidence_id'] = derivation.generated_row_id(row)
    sources = {'price_versions': [], 'action_versions': [], 'coverage_versions': [], 'evidence_receipts': []}
    for symbol in [*codes, views.INDEX]:
        path = prices['GENERATED_MARKET' if symbol == views.INDEX else symbol]
        for index, day in enumerate(days):
            common = {'symbol': symbol, 'effective_at': day + 'T15:00:00+08:00',
                'observed_arrival_at': day + 'T15:05:00+08:00', 'arrival_basis': 'generated', 'evidence_id': None}
            price = {**common, 'version_id': symbol + '/' + day + '/price-v1', 'session': day,
                'close': str(path[index]), 'reference_previous_close': str(path[max(0, index-1)]),
                'source_id': digest({'generated': True, 'symbol': symbol, 'session': day, 'close': str(path[index])})}
            coverage = {**common, 'version_id': symbol + '/' + day + '/coverage-v1', 'session': day,
                'status': 'not_applicable_price_index' if symbol == views.INDEX else 'generated_complete',
                'action_ids': [], 'source_id': digest({'generated': True, 'symbol': symbol, 'session': day, 'actions': []})}
            for kind, row in (('price_versions', price), ('coverage_versions', coverage)):
                sealed, receipt = views.bind_generated(row)
                sources[kind].append(sealed); sources['evidence_receipts'].append(receipt)
    # A valid later version of a historical warm-up close. Its date predates the
    # account; changing it cannot replace current execution prices or old fills.
    revised_day, arrived_day = days[30], days[-16:][8]
    original = next(v for v in sources['price_versions'] if v['symbol'] == codes[0] and v['session'] == revised_day)
    revision = {**original, 'version_id': original['version_id'].replace('v1', 'v2'),
        'effective_at': arrived_day + 'T15:00:00+08:00', 'observed_arrival_at': arrived_day + 'T15:05:00+08:00',
        'close': str(Decimal(original['close']) + Decimal('0.01')), 'evidence_id': None,
        'source_id': digest({'generated_revision': True, 'original_source_id': original['source_id'], 'arrival': arrived_day})}
    sealed, receipt = views.bind_generated(revision)
    sources['price_versions'].append(sealed); sources['evidence_receipts'].append(receipt)
    bundle['asof_sources'] = sources
    return bundle, fixture, {'revised_session': revised_day, 'arrival_date': arrived_day,
        'version_id': sealed['version_id'], 'receipt_id': receipt['receipt_id'], 'original_price': original['close'], 'revised_price': sealed['close']}


def run(output):
    output = Path(output).absolute()
    if output.exists() or not output.parent.is_dir() or output.parent.resolve() != output.parent:
        raise ValueError('A new engineering output directory under an existing unredirected parent is required')
    pins = code_pins(); output.mkdir()
    started, cpu = time.perf_counter(), time.process_time()
    save_once(output/'intent.json', {'kind': 'generated_asof_risk_account_v1', 'code_pins': pins,
        'case_count': 1, 'candidate_count': 1, 'initial_cash': '1000000.00', 'account_sessions': 16,
        'new_research_model_calls': 0, 'real_research': False, 'formal_target_success': False})
    try:
        days, codes, prices, returns = generated_paths()
        bundle, fixture, revision = versioned_bundle(days, codes, prices, returns)
        source_file = output/'generated_price_versions.json'
        save_once(source_file, {'evidence_class': derivation.CLASS, 'source_kind': 'generated_engineering_only',
            'calendar': days, 'prices': {k: list(map(str, v)) for k, v in prices.items()}, 'revision': revision,
            'index_identity': 'Generated stand-in labeled 000905.SH; no real index is supplied',
            'asof_sources': bundle['asof_sources']})
        bindings = raw_sources(output/'generated_sources', days, codes, prices)
        case = {'research_class': 'real_saved_development', 'evidence_class': derivation.CLASS,
            'execution_backend': 'v3_streamed_001', 'initial_cash': '1000000.00',
            'description': 'Generated version-view/risk engineering integration; no registered real case',
            'decision_fixture': fixture, 'raw_source_bindings': bindings,
            'evidence_sources': [{'path': str(source_file), 'sha256': hashlib.sha256(source_file.read_bytes()).hexdigest()}]}
        built = derivation.build(case, bundle)
        # Compare the same early signals without the future revision. No account
        # is executed for this pure information-boundary check.
        original_bundle = deepcopy(bundle)
        original_bundle['asof_sources']['price_versions'] = [v for v in original_bundle['asof_sources']['price_versions'] if v['version_id'] != revision['version_id']]
        original_bundle['asof_sources']['evidence_receipts'] = [v for v in original_bundle['asof_sources']['evidence_receipts'] if v['receipt_id'] != revision['receipt_id']]
        original_built = derivation.build(case, original_bundle)
        early = lambda value: [r for r in value['decision_fixture']['field_rows'] if r['session'] < revision['arrival_date']]
        assert early(built) == early(original_built)
        receipts = built[derivation.KEY]['anchor_receipts']
        assert [r['measurement']['status'] for r in receipts] == ['ready', 'target_cash', 'ready', 'ready']
        assert receipts[0]['asof_view_sha256'] == original_built[derivation.KEY]['anchor_receipts'][0]['asof_view_sha256']
        assert receipts[2]['asof_view_sha256'] != original_built[derivation.KEY]['anchor_receipts'][2]['asof_view_sha256']
        case.update(built); save_once(output/'case.json', case)
        verify_case_sources({'case': case, 'case_hash': digest(case)})
        research = ResearchTools(output/'stage', 'generated', case, [])
        save_once(output/'public_contract.json', research.contract())
        program = {'version': 'factor_strategy_program_v1', 'factors': [], 'target_weight_expression': derivation.FIELD,
            'hypothesis': 'Generated per-signal versioned inputs drive the fixed risk selector',
            'applicability': ['Engineering only'], 'invalidation_conditions': ['Future revision affects an earlier signal or unknown input gains qualification']}
        assert code_pins() == pins, 'Source code changed before generated account dispatch'
        response = research.execute('asof_selector_001', 'develop_strategy', {'program': program})
        save_once(output/'response.json', response)
        artifact_path = research.folder/'asof_selector_001/artifact.json'
        artifact = json.loads(artifact_path.read_text(encoding='utf-8'))
        assert artifact['raw']['status'] == 'completed_mechanical', artifact['raw'].get('error')
        body = artifact['raw']['result']; account_days = days[-16:]
        assert [r['date'] for r in body['daily']] == account_days
        assert body['final_snapshot']['external_cash_flow'] == '1000000.00'
        assert sum(e['event']['kind'] == 'cash_deposit' for e in body['journal']) == 1
        assert len(body['daily'][1]['holdings']) == 10
        assert all(body['daily'][i]['holdings'] == body['daily'][1]['holdings'] for i in range(2, 6))
        assert body['daily'][6]['holdings'] == body['daily'][-1]['holdings'] == {}
        assert all(o['trade_date'] in {account_days[i] for i in (1, 6, 11, 15)} for o in body['orders'])
        assert all(Decimal(d['cash_available']) >= 0 for d in body['daily'])
        assert Decimal(body['final_snapshot']['fees_paid']) > 0
        verification = derivation.verify_case(case)
        assert code_pins() == pins, 'Source code changed during engineering execution'
        receipt = {'kind': 'generated_asof_risk_account_v1', 'state': 'COMPLETE', 'assertions_passed': True,
            'case_sha256': digest(case), 'program_sha256': digest(program),
            'artifact_sha256': hashlib.sha256(artifact_path.read_bytes()).hexdigest(), 'revision': revision,
            'early_field_rows_unchanged': True, 'later_view_revision_selected': True,
            'original_close_positions': 77, 'stock_count': 12, 'generated_index_count': 1,
            'anchor_statuses': [r['measurement']['status'] for r in receipts], 'account_sessions': len(body['daily']),
            'orders': len(body['orders']), 'trades': len(body['trades']), 'fees_paid': body['final_snapshot']['fees_paid'],
            'final_snapshot': body['final_snapshot'], 'final_holdings': body['daily'][-1]['holdings'],
            'derivation_verification': verification, 'new_engineering_account_replays': 1,
            'new_real_strategy_runs': 0, 'new_research_model_calls': 0, 'new_formal_starts': 0,
            'source_authenticated': False, 'real_research': False, 'formal_target_success': False,
            'wall_ms_after_imports': round((time.perf_counter()-started)*1000, 3),
            'controller_cpu_ms_after_imports': round((time.process_time()-cpu)*1000, 3),
            'controller_and_review_ai_currency_cost': 'unknown_not_zero',
            'limitations': ['Generated prices, index, receipts, eligibility, costs and capacity; no actual market source admission.',
                'Whole recipe preparation, verification and account measured after imports; fair study resource admission is not implemented.',
                'Frozen real evidence, cross-case inference and prospective evaluation remain outstanding.']}
        save_once(output/'receipt.json', receipt)
    except Exception as exc:
        save_once(output/'failure.json', {'state': 'INCOMPLETE_TERMINAL', 'type': type(exc).__name__, 'message': str(exc),
            'formal_target_success': False, 'original_output_preserved': True})
        raise
    print(json.dumps({k: receipt[k] for k in ('state', 'account_sessions', 'orders', 'trades', 'fees_paid', 'formal_target_success')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    run(parser.parse_args().output)

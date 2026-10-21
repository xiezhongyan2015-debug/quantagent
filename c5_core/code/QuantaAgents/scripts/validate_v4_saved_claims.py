"""Bounded claim counterexamples over exposed 2019 saved accounts; no research call.

This evaluates the checker, not a strategy. It never reopens or executes the
source studies and never treats their development returns as new market data.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from quanta_agents.meta_v3.claim_support import review_claims
from quanta_agents.meta_v3.kernel import ROOT
from quanta_agents.meta_v3.ledger import digest, need
from quanta_agents.meta_v3.research_tools import save_once


def examples():
    numeric = {'claim_id': 'original_return', 'kind': 'numeric', 'evidence_id': 'saved_attribution',
        'path': ['accounts', 0, 'return_on_full_initial_cash'], 'relation': 'eq',
        'value': '12.6474', 'unit': 'percent', 'decimal_places': 4,
        'research_class': 'real_saved_development'}
    causal = {k: numeric[k] for k in ('claim_id', 'kind', 'evidence_id', 'research_class')}
    return [
        ('correct_original_return', numeric, 'supported'),
        ('reversed_sign', {**numeric, 'value': '-12.6474'}, 'contradicted'),
        ('fraction_as_percent', {**numeric, 'value': '0.1265'}, 'contradicted'),
        ('wrong_currency_unit', {**numeric, 'unit': 'CNY'}, 'unsupported'),
        ('development_promoted_to_oos', {**numeric, 'research_class': 'formal_oos'}, 'unsupported'),
        ('foreign_evidence', {**numeric, 'evidence_id': 'another_task'}, 'unsupported'),
        ('correct_benchmark_return', {**numeric, 'path': ['accounts', 2, 'return_on_full_initial_cash'], 'value': '22.4597'}, 'supported'),
        ('correct_net_difference', {**numeric, 'path': ['pairs', 1, 'comparison_minus_reference_net_pnl'],
            'value': '98123.54', 'unit': 'CNY', 'decimal_places': 2}, 'supported'),
        ('fees_equal_total_gap', {**numeric, 'path': ['pairs', 1, 'comparison_minus_reference_net_pnl'],
            'value': '10247.04', 'unit': 'CNY', 'decimal_places': 2}, 'contradicted'),
        ('cash_days_retained', {**numeric, 'path': ['accounts', 0, 'saved_cash_days'],
            'value': '244', 'unit': 'count', 'decimal_places': 0}, 'supported'),
        ('fixed_path_proves_cause', {**causal, 'kind': 'causal',
            'text': 'Recorded fixed-path fee differences identify the economic cause.'}, 'unsupported'),
        ('cautious_description', {**causal, 'kind': 'descriptive',
            'text': 'The benchmark had different exposure; the economic cause remains unresolved.'}, 'needs_review'),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='New, non-existing output scope')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    start = time.perf_counter()
    base = ROOT / 'experiment_traces/meta_framework_v3/year_attribution_001'
    old_plan = json.loads((base / 'plan.json').read_text(encoding='utf-8'))
    closed = json.loads((base / 'closed.json').read_text(encoding='utf-8'))
    summary_path = base / 'summary.json'
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    need(sha(summary_path) == closed['files']['summary.json'], 'closed attribution summary drift')
    originals = old_plan['sources']
    need(len(originals) == 3, 'exact three previously exposed account scope')
    for item in originals:
        path = Path(item['path'])
        need(path.stat().st_size <= 8 * 1024**2 and sha(path) == item['sha256'], 'bounded saved account identity drift')
    checks = examples()
    plan = {'kind': 'v4_offline_saved_claim_acceptance_v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'evidence_level': 'offline_counterexamples_using_exposed_real_development_accounts',
        'question': 'Can explicit report claims reverse signs/units, substitute fee differences, or promote descriptive development evidence?',
        'necessary_evidence': 'Original full-capital terminal values and closed saved attribution; no new strategy execution',
        'stop_condition': 'Evaluate these 12 fixed checks once; retain every mismatch, no retuning of expected outcomes',
        'examples': [{'id': name, 'claim': claim, 'expected': expected} for name, claim, expected in checks],
        'source_files': {str(summary_path): sha(summary_path), **{x['path']: x['sha256'] for x in originals}},
        'source_code': {str(p): sha(p) for p in [Path(__file__).resolve(), ROOT / 'src/quanta_agents/meta_v3/claim_support.py']},
        'budget': {'max_examples': 12, 'max_saved_accounts': 3, 'max_input_bytes': 24 * 1024**2,
            'new_gateway_calls': 0, 'new_market_downloads': 0, 'strategy_executions': 0, 'parameter_searches': 0},
        'exposure': 'All source accounts and reference numbers previously observed in development; not hidden cases or new OOS',
        'formal_target_success': False}
    root.mkdir(parents=True, exist_ok=False)
    save_once(root / 'plan.json', plan)
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    # Independent, narrow arithmetic from original accounts. Do not rerun the
    # already completed full attribution audit or infer execution certification.
    originals_check = []
    for item, reported in zip(originals, summary['accounts']):
        body = json.loads(Path(item['path']).read_text(encoding='utf-8'))['raw']['result']
        capital = Decimal(body['initial_cash'])
        nav = Decimal(body['daily'][-1]['simulated_net_asset_value'])
        originals_check.append({'evidence_id': item['evidence_id'], 'capital': str(capital),
            'net_pnl': str(nav - capital), 'return_fraction': str(nav / capital - 1),
            'saved_days': len(body['daily']),
            'matches_cited_summary': (reported['evidence_id'] == item['evidence_id']
                and Decimal(reported['return_on_full_initial_cash']) == nav / capital - 1
                and reported['saved_cash_days'] == len(body['daily']) == 244)})
    need(all(x['matches_cited_summary'] for x in originals_check), 'numeric evidence disagrees with saved accounts')
    evidence = {'saved_attribution': {'action': 'diagnose_execution', 'public': summary}}
    rows = []
    for name, claim, expected in checks:
        actual = review_claims([claim], evidence, research_class='real_saved_development',
            report_text=claim.get('text', 'Explicit offline counterexample, not a researcher final'))
        rows.append({'id': name, 'expected': expected, 'actual': actual,
                     'matches_expected': actual['claims'][0]['status'] == expected})
    result = {'plan_hash': digest(plan), 'evidence_level': plan['evidence_level'],
        'independent_terminal_arithmetic': originals_check, 'examples': rows,
        'all_expected_decisions': all(x['matches_expected'] for x in rows),
        'positive_checks': 4, 'negative_checks': 7, 'cautious_prose_review_checks': 1,
        'new_market_samples': 0, 'model_capability_acceptance': False, 'formal_target_success': False,
        'cost': {'project_gateway_calls': 0, 'project_gateway_tokens': 0,
                 'controller_and_review_provider_tokens': None, 'provider_bill': None,
                 'offline_wall_seconds': time.perf_counter() - start,
                 'unknown_cost_is_not_zero': True},
        'sources_unchanged': all(sha(Path(p)) == pin for p, pin in plan['source_files'].items()),
        'conclusion': 'This tests bounded structured claim checking. Prose entailment, causal identification and real research behavior remain unverified.'}
    save_once(root / 'result.json', result)
    need(result['all_expected_decisions'] and result['sources_unchanged'], 'offline acceptance failure retained')
    print(json.dumps({'root': str(root), 'all_expected_decisions': result['all_expected_decisions'],
                      'examples': len(rows), 'offline_wall_seconds': result['cost']['offline_wall_seconds']}, ensure_ascii=True))


if __name__ == '__main__':
    main()

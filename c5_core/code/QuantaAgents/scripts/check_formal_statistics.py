"""Hand-calculated generated fixture for original-slot descriptive statistics.

Accepts no market, result or manifest input paths. It reads only the proposed
statistical design identity and creates a new verification artifact. Generated
classifications and Sharpe values are test inputs, never strategy evidence.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.formal_statistics import summarize_frozen_slots

ARCHITECTURES = ('v4_evidence_workflow', 'strong_single', 'fixed_template_search')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fixture(draft_sha256):
    slots, outcomes = [], []
    for case, family, role in (('A', 'F1', 'real_research'), ('B', 'F2', 'real_research'),
                               ('C', 'F2', 'real_research'), ('N', 'control', 'negative_control')):
        for repeat in (1, 2, 3):
            for architecture in ARCHITECTURES:
                sid = f'{case}.{repeat}.{architecture}'
                slots.append({'slot_id': sid, 'case_id': case, 'case_definition_sha256': digest({'generated_case': case}),
                    'family': family, 'role': role, 'repeat': repeat, 'architecture': architecture,
                    'calendar_sha256': digest(['generated_day_1', 'generated_day_2', 'generated_day_3']),
                    'initial_capital_cny': '1000000'})
                if case == 'C' and repeat == 3 and architecture == ARCHITECTURES[0]:
                    continue  # Missing evidence must leave this original slot in the denominator.
                success = (case, architecture) in set(zip(('A', 'B', 'C'), ARCHITECTURES))
                classification = 'valid_success' if success else 'valid_nonsuccess'
                if role == 'negative_control':
                    correct = architecture == ARCHITECTURES[0] or (architecture == ARCHITECTURES[1] and repeat < 3)
                    classification = 'correct_abstention' if correct else 'incorrect_positive'
                if case == 'C' and repeat == 2 and architecture == ARCHITECTURES[0]:
                    classification = 'budget_stop'
                outcomes.append({'slot_id': sid, 'classification': classification,
                    'reason': 'Hand-created fixture, no model, market or account execution',
                    'sharpe_rf2': ('2' if success else '0.5') if role == 'real_research'
                                  and classification in ('valid_success', 'valid_nonsuccess') else None,
                    'outcome_unknown': classification == 'budget_stop',
                    'shared_shock_ids': ['generated_joint_budget_stop'], 'audit_binding': None,
                    'provider_version': 'generated_fixture', 'costs': {'known': None, 'unknown_is_not_zero': True}})
    manifest = {'version': 'frozen_slot_statistics_v1', 'input_kind': 'generated_fixture',
        'statistical_draft_sha256': draft_sha256, 'slots': slots,
        'original_order': [slot['slot_id'] for slot in slots],
        'shared_shock_ids': ['generated_joint_budget_stop']}
    return manifest, outcomes


def check(manifest, outcomes):
    before = digest([manifest, outcomes])
    actual = summarize_frozen_slots(manifest, outcomes)
    assert digest([manifest, outcomes]) == before
    for architecture, family_rate in zip(ARCHITECTURES, ('1/2', '1/4', '1/4')):
        row = actual['architectures'][architecture]
        assert row['original_real_slots'] == 9 and row['successes'] == 3
        assert row['raw_success_rate'] == '1/3' and row['family_equal_success_rate'] == family_rate
        assert row['eligible_sharpe_rf2_median'] == '1/2'
    for baseline in ARCHITECTURES[1:]:
        pair = actual['paired_gains'][baseline]
        assert pair['original_pairs'] == 9 and len(pair['pairs']) == 9
        assert pair['raw_mean_difference'] == '0' and pair['family_equal_mean_difference'] == '1/4'
    for architecture, rate in zip(ARCHITECTURES, ('1', '2/3', '0')):
        row = actual['negative_controls'][architecture]
        assert row['original_control_slots'] == 3 and row['correct_abstention_rate'] == rate
    assert len(actual['slot_outcomes']) == len(manifest['slots']) == 36
    assert actual['primary_weighting'] is None and actual['lower_bound'] is None
    assert actual['formal_financial_accepted'] is False and actual['formal_architecture_improvement_accepted'] is False
    empty = summarize_frozen_slots(manifest, [])
    assert len(empty['slot_outcomes']) == 36
    assert all(row['original_real_slots'] == 9 and row['successes'] == 0
               for row in empty['architectures'].values())
    bad = deepcopy(manifest)
    bad['slots'].pop(); bad['original_order'].pop()
    try:
        summarize_frozen_slots(bad, [])
    except ValueError:
        pass
    else:
        raise AssertionError('Incomplete original architecture pairing accepted')
    return actual, empty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New verification JSON file; never overwritten')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError('Refuse overwrite')
    draft = ROOT / 'docs/research/meta_framework_v4_handoff/formal_preflight/statistical_execution_draft_001.md'
    source = ROOT / 'src/quanta_agents/meta_v3/formal_statistics.py'
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    pins = {str(path): sha(path) for path in (draft, source, Path(__file__).resolve())}
    manifest, outcomes = fixture(pins[str(draft)])
    actual, empty = check(manifest, outcomes)
    assert all(sha(Path(path)) == pin for path, pin in pins.items())
    result = {'kind': 'generated_formal_slot_statistics_hand_arithmetic_v1',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'source_pins': pins,
        'input_kind': 'generated_fixture', 'manifest': manifest, 'outcomes': outcomes,
        'expected': {'original_real_slots_per_architecture': 9, 'successes_per_architecture': 3,
            'raw_success_rate_all': '1/3', 'family_equal_v4': '1/2', 'family_equal_baselines': '1/4',
            'paired_raw_gain': '0', 'paired_family_gain': '1/4', 'eligible_sharpe_median_all': '1/2',
            'control_rates': ['1', '2/3', '0']},
        'actual': actual, 'all_outcomes_missing': empty,
        'incomplete_original_pair_rejected': True, 'all_hand_checks_passed': True,
        'new_research_calls': 0, 'new_strategy_runs': 0, 'market_value_reads': 0,
        'new_formal_slots_registered': 0, 'formal_target_success': False,
        'interpretation': 'Generated arithmetic and denominator verification only; source audit bindings, sampling assumptions and valid inference remain unverified.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'output': str(output), 'hand_checks_passed': True, 'generated_slots': len(manifest['slots'])}, ensure_ascii=True))


if __name__ == '__main__':
    main()

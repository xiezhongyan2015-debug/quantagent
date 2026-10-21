"""Exact finite-support coverage and a shared-shock design counterexample.

Generated distributions only: no market reader, model, strategy, account or
formal registration. This validates stated examples, not real independence.
"""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.bounded_inference import lower_bound

ALPHA = Fraction(1, 60)  # Unfrozen three-claim Bonferroni design example.


def distribution(atoms):
    """Exact convolution of independent bounded finite-support units."""
    masses = {Fraction(): Fraction(1)}
    for unit in atoms:
        assert sum(unit.values(), Fraction()) == 1
        nxt = {}
        for previous, prob in masses.items():
            for value, chance in unit.items():
                assert 0 <= value <= 1 and 0 <= chance <= 1
                nxt[previous + value] = nxt.get(previous + value, Fraction()) + prob * chance
        masses = nxt
    assert sum(masses.values(), Fraction()) == 1
    return masses


@lru_cache(maxsize=8192)
def bound_from_sum(total, n):
    # The method depends only on n and mean. Equal constant scores reproduce it.
    return lower_bound([total / n] * n, ALPHA)['lower']


def coverage(name, atoms):
    n = len(atoms)
    mu = sum((sum((x * p for x, p in atom.items()), Fraction()) for atom in atoms), Fraction()) / n
    masses = distribution(atoms)
    bad = sum((p for total, p in masses.items()
               if bound_from_sum(total, n) > float(mu)), Fraction())
    assert bad <= ALPHA, (name, bad, ALPHA)
    return {'name': name, 'blocks': n, 'true_mean_exact': str(mu),
            'failure_probability_exact': str(bad), 'alpha_exact': str(ALPHA),
            'finite_sum_support_size': len(masses), 'coverage_passed': True}


def bernoulli(p):
    return {Fraction(): 1 - p, Fraction(1): p}


def check():
    scenarios = []
    grid = [Fraction(v) for v in ('0', '1/20', '1/10', '1/4', '1/3', '1/2',
                                '2/3', '3/4', '9/10', '19/20', '1')]
    for n in (1, 3, 6, 18, 54):
        for p in grid:
            scenarios.append(coverage(f'independent_bernoulli_n{n}_p{p}', [bernoulli(p)] * n))
    for n in (6, 18, 54):
        scenarios.append(coverage(f'heterogeneous_n{n}',
            [bernoulli(Fraction(1, 10) if i % 2 else Fraction(9, 10)) for i in range(n)]))
        scenarios.append(coverage(f'deterministic_mixed_n{n}',
            [{Fraction(i % 2): Fraction(1)} for i in range(n)]))
    scenarios.append(coverage('independent_bounded_non_bernoulli',
        [{Fraction(0): Fraction(1, 3), Fraction(1, 3): Fraction(1, 3),
          Fraction(1): Fraction(1, 3)} for _ in range(6)]))
    copied = []
    for pretend_n in (3, 6, 18, 54):
        wrong = lower_bound([1] * pretend_n, ALPHA)['lower']
        correct = lower_bound([1], ALPHA)['lower']
        probability = Fraction(1, 2) if wrong > 0.5 else Fraction()
        assert correct < 0.5
        if pretend_n >= 6:
            assert probability > ALPHA
        copied.append({'copies_of_one_bernoulli_half_shock': pretend_n,
            'actual_independent_blocks': 1, 'all_success_probability_exact': '1/2',
            'invalid_independence_lower_on_all_success': wrong,
            'invalid_independence_false_positive_probability_exact': str(probability),
            'correct_single_block_lower_on_all_success': correct,
            'correct_single_block_false_positive_probability_exact': '0'})
    boundary = []
    for n in (1, 3, 6, 18, 54):
        least = next((k for k in range(n + 1)
                      if bound_from_sum(Fraction(k), n) > 0.5), None)
        boundary.append({'independent_bernoulli_blocks': n, 'minimum_successes_for_lower_gt_half': least,
                         'all_success_lower': lower_bound([1] * n, ALPHA)['lower']})
    assert next(x for x in boundary if x['independent_bernoulli_blocks'] == 18)['minimum_successes_for_lower_gt_half'] == 15
    return {'coverage_scenarios': scenarios, 'shared_shock_counterexamples': copied,
            'method_power_boundaries_not_protocol_requirements': boundary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Saved generated evidence is not overwritten')
    files = [Path(__file__).resolve(), ROOT / 'src/quanta_agents/meta_v3/bounded_inference.py',
             ROOT / 'src/quanta_agents/meta_v3/formal_statistics.py',
             ROOT / 'docs/research/meta_framework_v4_handoff/formal_preflight/independent_block_inference_contract_001.md']
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    pins = {str(p): sha(p) for p in files}
    result = check()
    assert all(sha(Path(p)) == h for p, h in pins.items())
    result.update(kind='generated_bounded_inference_exact_coverage_v1',
        observed_at=datetime.now(timezone.utc).isoformat(), input_kind='generated_finite_distributions',
        source_pins=pins, alpha_family_example='1/20', claim_count=3, alpha_per_claim_exact=str(ALPHA),
        all_declared_coverage_scenarios_passed=True, real_independence_established=False,
        numerical_root_uses_float_with_downward_allowance=True, exact_math_proof_certificate=False,
        actual_strategy_results_used=0, market_files_opened=0, new_model_research_calls=0,
        new_strategy_runs=0, new_formal_slots=0, formal_target_success=False,
        interpretation='Exact enumeration of stated generated distributions. The common-shock example invalidates treating copied outcomes as independent; it does not estimate actual market dependence or strategy returns.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'output': str(args.output), 'coverage_scenarios': len(result['coverage_scenarios']),
                      'shared_shock_counterexamples': len(result['shared_shock_counterexamples']),
                      'formal_target_success': False}))


if __name__ == '__main__':
    main()

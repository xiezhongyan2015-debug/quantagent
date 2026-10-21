"""Fixed-time inference conditional on independent bounded block vectors.

Within each block dependence is unrestricted. Neither a family label, a file
hash nor this function authenticates cross-block independence or source data.
The numerical output is conditional inference, never formal acceptance.
"""
from collections import defaultdict
from datetime import date
from fractions import Fraction
import hashlib
import json
import math

from .formal_statistics import ARCHITECTURES, summarize_frozen_slots

VERSION = 'equal_mass_independent_block_kl_v1'


def _need(ok, message):
    if not ok:
        raise ValueError(message)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _fraction(value):
    _need(type(value) in (str, int, float, Fraction) and len(str(value)) <= 100,
          'bounded finite rational required')
    try:
        number = Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('bounded finite rational required') from exc
    _need(number.numerator.bit_length() <= 512 and number.denominator.bit_length() <= 512,
          'oversized rational')
    return number


def lower_bound(values, alpha):
    """Hoeffding (1963), Theorem 1, KL-Chernoff inversion.

    Independent, not necessarily identically distributed X_g in [0,1]. Target
    is G^-1 sum E[X_g], not a selected winner's probability or a new population
    without a sampling assumption. Alpha/G/weights must be fixed before scores.
    Computation uses a conservative numerical bracket; no exact-arithmetic or
    floating-point proof certificate is claimed.
    """
    _need(type(values) is list and 0 < len(values) <= 864, 'bounded nonempty block scores required')
    xs = [_fraction(v) for v in values]; a = _fraction(alpha)
    _need(all(0 <= x <= 1 for x in xs), 'block score outside [0,1]')
    _need(Fraction(1, 10**12) <= a < Fraction(1, 2), 'alpha outside supported range')
    exact_mean = sum(xs, Fraction()) / len(xs)
    x = float(exact_mean); threshold = -math.log(float(a)) / len(xs)
    if x == 0:
        lo, hi = 0.0, 0.0
    else:
        lo, hi = 0.0, x
        for _ in range(90):
            mid = (lo + hi) / 2
            if mid == lo or mid == hi:
                break
            divergence = math.log(1 / mid) if x == 1 else (
                x * math.log(x / mid) + (1 - x) * math.log((1 - x) / (1 - mid)))
            if divergence >= threshold:
                lo = mid
            else:
                hi = mid
        # Small downward allowance for the floating log/root computation.
        lo = max(0.0, lo - 1e-12)
    return {'blocks': len(xs), 'mean_exact': str(exact_mean), 'alpha_exact': str(a),
            'lower': lo, 'root_upper_bracket': hi, 'conditional_on_independent_blocks': True,
            'numerical_downward_allowance': 1e-12, 'formal_accepted': False}


def infer_frozen_blocks(manifest, outcomes, design):
    """Bind exact original-slot weights and pairs to equal-mass blocks.

    All original real slots are retained. Unknown V4 scores use zero; unknown
    baseline scores use one when bounding paired gain. Original descriptive
    gain is retained separately, since missing baseline evidence must not create
    optimistic inferential gains. Negative controls have a separate denominator.

    The currently supported conservative dependence model clusters whole cases,
    whole families, overlapping market dates and all declared shared shocks.
    More general valid designs require another reviewed method, not removing
    inconvenient links here. Different blocks still need scientific justification.
    """
    required = {'version', 'manifest_sha256', 'primary_weighting', 'alpha_family',
                'blocks', 'calendar_dates', 'dependency_groups', 'population_statement',
                'independence_review_reference'}
    _need(type(design) is dict and set(design) == required and design['version'] == VERSION,
          'invalid frozen inference design')
    _need(design['manifest_sha256'] == _hash(manifest), 'original manifest binding drift')
    weighting = design['primary_weighting']
    _need(weighting in ('raw_start', 'case_equal', 'family_equal'), 'unfrozen endpoint weighting')
    alpha_family = _fraction(design['alpha_family'])
    _need(Fraction(3, 10**12) <= alpha_family < Fraction(1, 2), 'invalid family error budget')
    for key in ('population_statement', 'independence_review_reference'):
        _need(type(design[key]) is str and 0 < len(design[key]) <= 16384, 'explicit assumption scope required')
    summary = summarize_frozen_slots(manifest, outcomes)
    rows = [r for r in summary['slot_outcomes'] if r['role'] == 'real_research']
    _need(rows, 'real endpoints required')
    indexed = {r['slot_id']: r for r in rows}
    blocks, membership = design['blocks'], {}
    _need(type(blocks) is list and 0 < len(blocks) <= 864, 'bounded frozen blocks required')
    names = set()
    for block in blocks:
        _need(type(block) is dict and set(block) == {'id', 'slot_ids'}, 'invalid dependence block')
        bid = block['id']
        _need(type(bid) is str and 0 < len(bid) <= 120 and bid not in names, 'duplicate/invalid block id')
        names.add(bid)
        _need(type(block['slot_ids']) is list and 0 < len(block['slot_ids']) <= len(rows), 'empty/oversized block')
        for sid in block['slot_ids']:
            _need(type(sid) is str and sid in indexed and sid not in membership, 'dropped/aliased/control block slot')
            membership[sid] = bid
    _need(set(membership) == set(indexed), 'every original real slot must occur once')
    groups = defaultdict(set)
    calendars = design['calendar_dates']
    _need(type(calendars) is dict and set(calendars) == {r['calendar_sha256'] for r in rows},
          'all original real calendars required')
    for digest, dates in calendars.items():
        _need(type(dates) is list and 0 < len(dates) <= 4096
              and all(type(day) is str and len(day) == 10 for day in dates)
              and dates == sorted(set(dates)) and _hash(dates) == digest, 'calendar identity/coverage drift')
        _need(all(date.fromisoformat(day).isoformat() == day for day in dates), 'invalid ISO trading date')
    for row in rows:
        sid = row['slot_id']
        groups[('case', row['case_id'])].add(sid)
        groups[('family', row['family'])].add(sid)
        for day in calendars[row['calendar_sha256']]:
            groups[('market_date', day)].add(sid)
        for shock in summary['shared_shock_ids'] + row.get('shared_shock_ids', []):
            groups[('declared_shock', shock)].add(sid)
    dependencies = design['dependency_groups']
    _need(type(dependencies) is list and len(dependencies) <= 864, 'bounded dependency groups required')
    for i, group in enumerate(dependencies):
        _need(type(group) is dict and set(group) == {'reason', 'slot_ids'}
              and type(group['reason']) is str and 0 < len(group['reason']) <= 16384
              and type(group['slot_ids']) is list and 0 < len(group['slot_ids']) <= len(rows)
              and all(type(sid) is str and sid in indexed for sid in group['slot_ids']),
              'invalid additional dependency group')
        groups[('additional', str(i))].update(group['slot_ids'])
    for reason, members in groups.items():
        _need(len({membership[sid] for sid in members}) == 1, 'dependent original slots split across blocks: ' + str(reason))
    template = [r for r in rows if r['architecture'] == ARCHITECTURES[0]]
    cases = {r['case_id'] for r in template}; families = {r['family'] for r in template}
    counts = {case: sum(r['case_id'] == case for r in template) for case in cases}
    family_cases = {family: {r['case_id'] for r in template if r['family'] == family} for family in families}
    weights = {}
    for row in template:
        weight = Fraction(1, len(template)) if weighting == 'raw_start' else (
            Fraction(1, len(cases) * counts[row['case_id']]) if weighting == 'case_equal' else
            Fraction(1, len(families) * len(family_cases[row['family']]) * counts[row['case_id']]))
        weights[(row['case_id'], row['repeat'])] = weight
    masses = {bid: sum((weights[(r['case_id'], r['repeat'])] for r in template
              if membership[r['slot_id']] == bid), Fraction()) for bid in names}
    equal_mass = all(mass == Fraction(1, len(blocks)) for mass in masses.values())
    result = {'version': VERSION, 'design_sha256': _hash(design), 'input_sha256': summary['input_sha256'],
        'primary_weighting': weighting, 'original_slots': summary['original_slots'],
        'original_real_slots': len(rows), 'negative_controls': summary['negative_controls'],
        'block_masses_exact': {bid: str(masses[bid]) for bid in sorted(names)},
        'descriptive_architectures': summary['architectures'], 'descriptive_paired_gains': summary['paired_gains'],
        'conditional_inference': None, 'status': 'unequal_mass_not_evaluable' if not equal_mass else 'conditional_only',
        'independence_authenticated': False, 'input_authentication_performed': False,
        'formal_financial_accepted': False, 'formal_architecture_improvement_accepted': False,
        'limitations': ['Block independence, source truth, actual freeze and population sampling are not authenticated.',
            'Whole-family clustering is a conservative supported model, not a new required protocol family count.',
            'Unequal block masses are not reweighted into a different endpoint.',
            'Shared-calendar/shock checks only cover the supplied authenticated-by-caller metadata.',
            'Unknown baseline success uses its upper envelope for inference, retaining every original pair.']}
    if not equal_mass:
        return result
    alpha = alpha_family / 3
    lookup = {(r['case_id'], r['repeat'], r['architecture']): r for r in rows}
    endpoints = {}
    for target in ('v4_success', *ARCHITECTURES[1:]):
        totals = {bid: Fraction() for bid in names}
        for v4 in template:
            key = (v4['case_id'], v4['repeat']); score = Fraction(v4['success'])
            if target != 'v4_success':
                other = lookup[(*key, target)]
                unresolved = (other['outcome_unknown'] or not other['source_outcome_present']
                              or other['counting_reason'] == 'missing_audit_reference')
                other_upper = 1 if unresolved else other['success']
                score = Fraction(v4['success'] - other_upper + 1, 2)
            totals[membership[v4['slot_id']]] += weights[key] * score
        values = [totals[b['id']] / masses[b['id']] for b in blocks]
        bound = lower_bound(values, alpha)
        endpoints[target] = {'block_scores_exact': [str(v) for v in values], **bound,
            'endpoint_lower': bound['lower'] if target == 'v4_success' else 2 * bound['lower'] - 1,
            'scale': 'success_probability' if target == 'v4_success' else 'paired_success_gain'}
    result['conditional_inference'] = endpoints
    result['alpha_family_exact'] = str(alpha_family)
    result['method_reference'] = 'Hoeffding 1963, Theorem 1, fixed-time independent bounded variables'
    return result

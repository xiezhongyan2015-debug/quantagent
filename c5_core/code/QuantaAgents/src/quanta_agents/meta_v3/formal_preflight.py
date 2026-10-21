"""Metadata-only formal comparison planning checks. Never authorizes a run.

This module does not open files, import market adapters, register opportunities,
or evaluate returns. Its caller supplies independently pinned source metadata.
Ready means the named metadata/planning condition only, never financial success.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json

from .architecture_contract import NAMES, make

VERSION = 'formal_comparison_metadata_preflight_v1'
FINANCIAL_POLICY = {
    'initial_capital_cny': '1000000', 'capital_basis': 'full_initial_capital',
    'daily_return_denominator': 'all_calendar_days_including_cash_and_locked_positions',
    'annualization': 252, 'standard_deviation_ddof': 1, 'risk_free_annual': '0.02',
    'risk_free_daily_conversion': '(1+annual_rate)^(1/252)-1',
    'sensitivity_risk_free_annual': '0', 'net_sharpe_operator': 'gt', 'net_sharpe_threshold': '1',
    'minimum_real_oos_sessions': 252, 'minimum_exit_batches': 30,
    'maximum_drawdown': '0.25', 'cash_interest': '0', 'leverage': 'none',
    'final_strategies_per_start': 1,
    'required_execution_gates': ['full_capital', 'full_calendar', 'locked_positions', 't_plus_1',
        'integer_lots', 'historical_fees', 'slippage', 'partial_fills', 'rejections',
        'suspension_and_price_limits', 'company_actions', 'tax', 'observed_arrival', 'executable_capacity'],
}
STATISTICAL_ENDPOINTS = {
    'success_rate_minimum': '2/3', 'valid_strategy_sharpe_median_operator': 'gt',
    'valid_strategy_sharpe_median_threshold': '1', 'success_lower_bound_operator': 'gt',
    'success_lower_bound_threshold': '1/2', 'paired_gain_lower_bound_operator': 'gt',
    'paired_gain_lower_bound_threshold': '0', 'denominator': 'all_original_real_starts',
    'failures': ['invalid_final','missing_final','error','no_trades','budget_stop',
        'unrecovered_failure','negative_return','contamination'],
    'negative_control_endpoint': 'correct_abstention_separate_from_real_strategy_success',
    'decision_timing': 'once_after_complete_frozen_batch',
}
DEPENDENCIES = ['mechanism_family', 'same_case_repeats', 'common_market_dates']
GRANT_FIELDS = {'model_calls','model_tokens','candidates','tool_actions','scan_cells',
    'comparisons','tool_wall_ms','controller_cpu_ms','retained_output_bytes',
    'process_launches','process_cpu_ms','process_io_bytes','duration_seconds',
    'closing_calls','closing_tokens','closing_seconds'}
FUNDED_FIELDS = GRANT_FIELDS-{'closing_calls','closing_tokens','closing_seconds','duration_seconds'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def current_proposal(metadata):
    """Map existing definitions only. This creates no sixth real case or grant."""
    slots = []
    for case in metadata['cases']:
        for repeat in (1,2,3):
            for architecture in NAMES:
                slots.append({'id':f"{case['key']}.{architecture}.{repeat}",
                    'case_key':case['key'],'architecture':architecture,'repeat':repeat,
                    'input_binding':deepcopy(case['input_binding']),
                    'initial_capital_cny':'1000000','grant':None})
    return {'version':VERSION,'scope':'unregistered_metadata_proposal', 'slots':slots,
        'architectures':{name:make(name) for name in NAMES},
        'implementation_pins':deepcopy(metadata['implementation_pins']),
        'financial_policy':deepcopy(FINANCIAL_POLICY),
        'statistical_endpoints':deepcopy(STATISTICAL_ENDPOINTS),
        'statistical_design':None,'prospective_design':None,
        'whole_study_allocation':None,
        'execution_order_rule':'paired_interleaved', 'execution_order':[s['id'] for s in slots]}


def evaluate(metadata, proposal):
    """Fail closed on missing metadata; positive labels do not replace sources."""
    gates = []
    def gate(name, ready, observed, missing, phase='historical_prestart'):
        gates.append({'id':name,'status':'ready' if ready else 'incomplete','phase':phase,
            'observed':observed,'missing':[] if ready else missing})
    cases = metadata['cases']
    catalog = {c['key']:c for c in cases}
    slots = proposal.get('slots',[])
    shape = type(slots) is list and 0 < len(slots) <= 864 and all(type(s) is dict for s in slots)
    if not shape: slots=[]
    known = [s for s in slots if s.get('case_key') in catalog]
    selected = {s['case_key']:catalog[s['case_key']] for s in known}
    real = [c for c in selected.values() if c['role']=='real_research']
    # Case identity is source-defined scientific content, not a mutable display ID.
    semantic = {c['definition_hash'] for c in real}
    identifiers = {c['scientific_case_id'] for c in real}
    unique_real = min(len(semantic),len(identifiers))
    distinct = len(catalog)==len(cases) and len(semantic)==len(real)==len(identifiers)
    gate('real_case_denominator',distinct and unique_real>=6,
        {'distinct_real_cases':unique_real,'negative_controls':sum(c['role']=='negative_control' for c in selected.values()),
         'required_real_cases':6,'selected_definitions':len(selected)},
        ['At least six independently defined real research cases; controls and relabeled duplicates do not count.'])
    families = sorted({c['family'] for c in real})
    gate('mechanism_families',len(families)>=3,{'real_families':families,'minimum':3},['At least three real mechanism families.'])
    ids = [s.get('id') for s in slots]
    triples = [(s.get('case_key'),s.get('architecture'),s.get('repeat')) for s in slots]
    expected = {(key,arm,r) for key in selected for arm in NAMES for r in (1,2,3)}
    mapping = shape and len(known)==len(slots) and all(type(i) is str and i for i in ids)
    mapping = mapping and all(type(s.get('repeat')) is int for s in slots)
    mapping = mapping and len(set(ids))==len(ids) and len(set(triples))==len(triples) and set(triples)==expected
    positive_slots = sum(catalog[s['case_key']]['role']=='real_research' for s in known)
    gate('three_architectures_three_original_starts',mapping and positive_slots>=54,
        {'candidate_slots':len(slots),'real_case_slots':positive_slots,'control_slots':len(known)-positive_slots,
         'minimum_formal_real_slots':54,'per_architecture_real_slots':dict(Counter(s['architecture'] for s in known if catalog[s['case_key']]['role']=='real_research')),
         'complete_selected_definition_cartesian_mapping':bool(mapping)},
        ['Unique original (scientific case, architecture, repeat 1..3) mapping, at least 54 real-case slots; no replacement opportunities.'])
    reviewed = [c['key'] for c in real if c.get('exposure_class')=='reviewed_local_holdout']
    # The current metadata reader has no authority to certify an exposure review.
    gate('heldout_exposure_review',False,
        {'source_exposure_by_case':{c['key']:c['exposure_class'] for c in real},
         'claims_of_reviewed_holdout_not_independently_certified':reviewed,
         'periods':metadata['periods']},
        ['A scoped, independently checked cross-run exposure review before selection; an absent record or final_sealed=true is insufficient.'])
    contracts = {name:make(name) for name in NAMES}
    gate('actual_architecture_contracts',proposal.get('architectures')==contracts
        and proposal.get('implementation_pins')==metadata['implementation_pins'] and bool(metadata['implementation_pins']),
        {'expected':contracts,'actual_source_identity':metadata['implementation_pins']},
        ['Exact behavior contracts and the actual runtime/producer/controller source identities; architecture labels alone are insufficient.'])
    mismatches = []
    for slot in known:
        if slot.get('input_binding') != catalog[slot['case_key']]['input_binding']:
            mismatches.append(slot.get('id'))
    bindings_complete = all(all(c['input_binding'].get(k) for k in ('task_sha256','initial_strategy_sha256',
        'manifest_sha256','calendar_sha256','membership_sha256','data_snapshot_id','split')) for c in selected.values())
    gate('common_data_and_inputs',mapping and bindings_complete and not mismatches,
        {'mismatched_slots':mismatches,'missing_binding_fields':{c['key']:[k for k,v in c['input_binding'].items() if not v]
            for c in selected.values() if not all(c['input_binding'].values())},
         'shared_calendar_hashes':sorted({c['input_binding']['calendar_sha256'] for c in selected.values()}),
         'metadata_identity_scope':metadata['market_identity_scope']},
        ['All arms receive identical per-case input, calendar, membership, split, and dataset identities.'])
    grants = [s.get('grant') for s in slots]
    valid_grants = bool(grants) and all(type(g) is dict and set(g)==GRANT_FIELDS
        and all(type(v) is int and v>0 for v in g.values())
        and g['closing_calls']<g['model_calls'] and g['closing_tokens']<g['model_tokens']
        and g['closing_seconds']<g['duration_seconds'] for g in grants)
    common_grants = valid_grants and all(len({digest(s['grant']) for s in known
        if s['case_key']==case and s.get('repeat')==repeat})==1 for case in selected for repeat in (1,2,3))
    allocation=proposal.get('whole_study_allocation')
    funding_ok=valid_grants and type(allocation) is dict and set(allocation)=={'ceilings','headroom','grant_transfer'}
    if funding_ok:
        funding_ok=(allocation['grant_transfer']=='forbidden'
            and type(allocation['ceilings']) is dict and set(allocation['ceilings'])==FUNDED_FIELDS
            and type(allocation['headroom']) is dict and set(allocation['headroom'])==FUNDED_FIELDS)
    if funding_ok:
        funding_ok=all(type(allocation['ceilings'][k]) is int and 0<allocation['ceilings'][k]<=10**15
            and type(allocation['headroom'][k]) is int and allocation['headroom'][k]>=0
            and allocation['ceilings'][k]==sum(g[k] for g in grants)+allocation['headroom'][k]
            for k in FUNDED_FIELDS)
        # Additional launch/call/candidate headroom must not mint opportunities.
        funding_ok=funding_ok and all(allocation['headroom'][k]==0 for k in ('model_calls','candidates','process_launches'))
    gate('common_full_stack_grants',mapping and common_grants and funding_ok,
        {'missing_grant_slots':[s.get('id') for s in slots if s.get('grant') is None],
         'required_resource_fields':sorted(GRANT_FIELDS),'whole_study_allocation':allocation,
         'whole_study_grant_arithmetic_complete':bool(funding_ok),
         'allocation_scope':'Proposed total ceilings and explicit overrun headroom only; no budget authorization or canonical freeze.'},
        ['Freeze equal per-case/repeat model, all-candidate, tool, process and closing ceilings, including whole-study headroom.'])
    gate('financial_policy_preserved',digest(proposal.get('financial_policy'))==digest(FINANCIAL_POLICY)
        and all(s.get('initial_capital_cny')=='1000000' for s in slots),
        {'required':FINANCIAL_POLICY,'proposed':proposal.get('financial_policy')},
        ['Preserve original full-capital/RF2/252-day/30-exit/25%-drawdown and strict Sharpe>1 policy without loosening.'])
    gate('statistical_endpoints_preserved',digest(proposal.get('statistical_endpoints'))==digest(STATISTICAL_ENDPOINTS),
        {'required':STATISTICAL_ENDPOINTS,'proposed':proposal.get('statistical_endpoints')},
        ['Preserve original failure denominator, success/median/lower-bound thresholds and separate paired gain.'])
    design = proposal.get('statistical_design')
    design_ok = type(design) is dict
    if design_ok:
        try: alpha=Decimal(str(design.get('one_sided_alpha')))
        except (InvalidOperation,ValueError): alpha=Decimal('NaN')
        design_ok = (alpha.is_finite() and 0<alpha<1
            and design.get('dependence_units')==DEPENDENCIES
            and type(design.get('date_block_length')) is int and design['date_block_length']>0
            and type(design.get('block_length_sensitivity')) is list and bool(design['block_length_sensitivity'])
            and all(type(n) is int and n>0 for n in design['block_length_sensitivity'])
            and design.get('resampling')=='synchronized_date_blocks_and_case_clusters'
            and all(type(design.get(k)) is str and bool(design[k]) for k in
                ('case_weighting','multiple_comparison_rule','failure_score_rule','analysis_implementation_hash')))
    gate('dependence_aware_statistical_design',design_ok,{'proposed':design,'required_dependencies':DEPENDENCIES},
        ['Predefine alpha, block length/sensitivity, family/case-repeat/common-date dependence, weighting and multiplicity. The original protocol does not specify an alpha value.'])
    order = proposal.get('execution_order')
    order_ok=mapping and type(order) is list and len(order)==len(ids) and set(order)==set(ids)
    if order_ok:
        by_id={s['id']:s for s in slots}
        order_ok=proposal.get('execution_order_rule')=='paired_interleaved' and all(
            len({(by_id[name]['case_key'],by_id[name]['repeat']) for name in order[i:i+3]})==1
            and {by_id[name]['architecture'] for name in order[i:i+3]}==set(NAMES)
            for i in range(0,len(order),3))
    gate('paired_original_order',order_ok,
        {'proposed_order':order},['Freeze an exhaustive paired/interleaved or randomized original order before scores.'])
    gate('metadata_source_scope',False,{'market_manifest':metadata['market_manifest_summary'],
        'legacy_execution_scope':metadata['legacy_execution_scope']},
        ['Original metadata uses inventory identity and integration execution. Content identity, coverage and historical arrival need independently checked admission evidence.'])
    for evidence in FINANCIAL_POLICY['required_execution_gates']:
        gate('execution_evidence.'+evidence,False,{'existing_scope':metadata['legacy_execution_scope']},
            ['A factual audit binding this execution obligation to the formal data and account kernel; policy declarations alone do not certify it.'])
    gate('one_time_frozen_evaluator_release',False,
        {'registered_by_this_preflight':False,'source_admission_changed':False},
        ['Bind a frozen formal cohort and statistical evaluator to a one-way heldout release. This metadata checker cannot authorize ordinary development tools to open final values.'])
    prospective = proposal.get('prospective_design')
    prospective_ok = type(prospective) is dict and all(type(prospective.get(k)) is str and bool(prospective[k].strip())
        for k in ('coverage','effective_trades','return_and_risk','execution_discrepancy','pass','fail','continue_observing'))
    gate('prospective_predeclared_design',prospective_ok,{'proposed':prospective,'new_fixed_minimum_duration':None},
        ['Freeze coverage, effective trades, risk/return, execution tolerance and pass/fail/continue criteria before new observations.'],phase='later_prospective')
    gate('prospective_observed_evidence',False,{'observations_read':0},
        ['New post-freeze market/paper observations and independent review are a later completion gate; absence does not block metadata engineering.'],phase='later_prospective')
    return {'kind':VERSION,'status':'incomplete','gates':gates,
        'counts':{'ready':sum(g['status']=='ready' for g in gates),'incomplete':sum(g['status']=='incomplete' for g in gates)},
        'candidate_mapping':slots,'proposal_hash':digest(proposal),
        'formal_financial_accepted':False,'formal_architecture_improvement_accepted':False,
        'formal_success_denominator_contribution':0,'new_research_calls':0,'new_registered_slots':0,
        'sealed_value_reads':0,'old_scopes_reopened':False,
        'scope':'Metadata and plan completeness only; ready rows are not financial certification or run authorization.'}

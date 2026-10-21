"""Frozen behavior identities for a bounded architecture comparison.

These contracts bind actual runtime choices, not superiority or scientific
case identity. Fixed template search uses its own registered producer runner.
Unflagged scopes retain their original runtime and study semantics.
"""
from copy import deepcopy

from .ledger import need


VERSION = 'research_architecture_contract_v1'
RUNTIME_ENTRY = 'quanta_agents.meta_v3.runtime:ResearchRuntime._run_scoped'
FIXED_ENTRY = 'quanta_agents.meta_v3.registered_producer:dispatch'
NAMES = ('v4_evidence_workflow', 'strong_single', 'fixed_template_search')


def make(name):
    need(name in NAMES, 'unknown research architecture')
    fixed = name == 'fixed_template_search'
    return {'version': VERSION, 'architecture': name,
        'entrypoint': FIXED_ENTRY if fixed else RUNTIME_ENTRY,
        'action_selection': 'frozen_template_order' if fixed else 'autonomous_model',
        'research_model': None if fixed else {'model': 'gpt-6-astra', 'effort': 'xhigh'},
        'tool_authority': 'same_frozen_case_tools_and_execution_kernel',
        'history': 'no_model_context' if fixed else 'own_complete_action_history',
        'prompt_assistance': ['work_contract', 'working_evidence_index']
            if name == 'v4_evidence_workflow' else [],
        'acceptance': 'shared_frozen_report_and_accounting_checks',
        'search_scope': 'frozen_template_parameter_space' if fixed else 'public_program_language'}


def validate(contract):
    if contract is None:
        return None
    need(type(contract) is dict and contract.get('architecture') in NAMES,
         'invalid research architecture contract')
    need(contract == make(contract['architecture']), 'research architecture behavior contract drift')
    return contract


def validate_trial(trial):
    if 'architecture_contract' not in trial:
        return
    from .ledger import digest
    contract = trial['architecture_contract']
    need(contract is not None, 'declared architecture contract cannot be null')
    validate(contract)
    need(trial['architecture_hash'] == digest(contract), 'architecture hash does not bind behavior contract')


def verify_binding(trial, provenance):
    validate_trial(trial)
    expected = trial.get('architecture_contract')
    actual = provenance.get('architecture_contract')
    validate(actual)
    need(expected == actual, 'study architecture contract missing or changed')


def validate_runtime(provenance, *, model='gpt-6-astra', effort='xhigh'):
    contract = validate(provenance.get('architecture_contract'))
    if contract is None:
        return
    need(contract['entrypoint'] == RUNTIME_ENTRY,
         'fixed_template_search execution entry is not integrated; ordinary runtime cannot run it')
    need(provenance.get('controller_policy') is not None,
         'architecture contract requires the scoped runtime controller')
    need(contract['research_model'] == {'model': model, 'effort': effort},
         'architecture research model or effort changed')


def validate_stage_entry(provenance, *, producer_contract=None):
    contract = validate(provenance.get('architecture_contract'))
    if contract is not None and contract['entrypoint'] == FIXED_ENTRY:
        from .registered_producer import REAL_VERSION, validate_contract
        need(type(producer_contract) is dict and producer_contract.get('kind') == REAL_VERSION,
             'fixed_template_search entry not integrated without its real fixed producer contract')
        validate_contract(producer_contract)
        return
    need(contract is None or producer_contract is None,
         'autonomous architecture cannot dispatch through a fixed producer')
    validate_runtime(provenance)


def prompt_assistance(provenance):
    validate_runtime(provenance)
    contract = provenance.get('architecture_contract')
    # This was the scoped runtime behavior before architecture contracts.
    return (['work_contract', 'working_evidence_index'] if contract is None
            else deepcopy(contract['prompt_assistance']))


def describe(contract):
    validate(contract)
    need(contract is not None, 'architecture contract required')
    runnable = contract['entrypoint'] == RUNTIME_ENTRY
    return {'contract': deepcopy(contract), 'runtime_entry_integrated': runnable,
        'registered_producer_entry_integrated': contract['entrypoint'] == FIXED_ENTRY,
        'entry_requirements': ['matching real fixed producer contract, source admission and native process grant']
            if contract['entrypoint'] == FIXED_ENTRY else ['scoped runtime controller and per-call identity'],
        'missing': [],
        'full_stack_comparison_admitted': False, 'formal_target_success': False}

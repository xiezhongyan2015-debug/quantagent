"""Prepare one fully declared exposed-case cohort, without research or registration."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3 import architecture_contract as ac, process_envelope as pe
from quanta_agents.meta_v3 import registered_producer as rp, study_allocation as allocation
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.controller_plan import VERSION as CONTROLLER_VERSION
from quanta_agents.meta_v3.experiment_design import VERSION as DESIGN_VERSION
from quanta_agents.meta_v3.ledger import digest, need
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3.runtime import source_pins
from run_v4_architecture_study import validate_spec


def build(root):
    root = Path(root).resolve()
    need(not root.exists(), 'new unexposed destination required; never replace an old cohort')
    case_path = ROOT / 'experiment_traces/meta_framework_v3/year_inputs_001/case.json'
    case = json.loads(case_path.read_text(encoding='utf-8'))
    case['report_policy'] = {'version': 'claim_support_v1'}
    case['experiment_design_policy'] = {'version': DESIGN_VERSION}
    case['research_policy'] = {'version': 'structural_research_v1', 'action_limits': {
        'inspect_inputs': 3, 'diagnose_horizons': 3, 'develop_strategy': 8,
        'inspect_execution': 4, 'read_evidence': 8, 'diagnose_execution': 4,
        'register_experiment': 3, 'request_research_extension': 1,
        'register_batch': 2, 'execute_batch': 2, 'inspect_batch': 8}}
    case['batch_policy'] = {'version': 'bounded_batch_v1', 'max_candidates_total': 8,
        'max_scan_cells_total': 1000000, 'max_wall_seconds': 1800,
        'output_stop_threshold_bytes': 67108864,
        'field_semantics': {f['name']: {'unit': f['unit'], 'raw_precision': 'unknown', 'digit_derivation': None}
                            for f in case['decision_fixture']['fields']}}
    question = ('研究缩量上涨是否包含覆盖成本的后续交易信息，还是仅反映参与程度、持仓暴露或行情阶段的差异。'
        '自主提出竞争解释、选择可区分的检验、开发仓位规则并据证据决定是否进行结构修订或弃权。')
    documents = [{'name': 'common_frozen_scope', 'text':
        '这是2019年sh600004的已暴露真实保存资料开发；100万元及244个完整交易日、原公司行动和机械执行规则全部保留。'
        '这不是金融样本外验收或隐藏案例。所有候选、重复、失败、资料请求和费用均计入原额度。'
        '总额度覆盖父任务及扩展子任务，共24次AI调用、1400000名义token、最多8个候选、2小时；收尾机会保留。'
        '当前字段close/volume/amount。若必要，可申请同一保存来源open/high/low，最多一次、无新增股票/日期/下载；'
        '批准的子任务也消耗同一总额度。其他请求会如实返回能力缺口。无需为了展示能力强行登记或扩展。'
        '研究者不知道其他启动的回答和结果；原题及同股其他研究的既有暴露不因此消失。'}]
    template = {'version': 'factor_strategy_program_v1', 'factors': [
        {'name': 'contracting_rise', 'expression': '(close > lag(close, 1)) & (volume < lag(rolling_mean(volume, 20), 1) * {{ratio}})'}],
        'target_weight_expression': 'where(rolling_max(contracting_rise, {{hold}}) > 0, 1, 0)',
        'hypothesis': 'Prespecified simple contraction-and-rise baseline; no claim of a causal mechanism or profitability.',
        'applicability': ['Exposed 2019 ordinary cash-equity development'],
        'invalidation_conditions': ['Incomplete funded execution or nonpositive development RF2 Sharpe yields no selection.']}
    space = {'program_template': template, 'parameters': {'ratio': [0.5, 1], 'hold': [2, 5, 10, 20]},
             'axis_order': ['ratio', 'hold']}
    protocol = {'kind': 'local_exposed_case_architecture_comparison_v1',
        'created_at': datetime.now(timezone.utc).isoformat(), 'case_path': str(case_path),
        'question': question, 'case_count': 1, 'repeats_per_architecture': 3, 'trial_count': 9,
        'architecture_names': list(ac.NAMES),
        'uncertainty': 'Whether the implemented V4 work/evidence assistance changes research quality and resource use relative to the same strong model and a simple fixed search on this exposed case.',
        'not_tested': ['Hidden cross-mechanism generalization', 'Formal profitable strategy success', 'Entire V4 versus every possible strong baseline'],
        'controller_assistance': {'all': 'Same question, source admission, tools, funding, identity and acceptance policies.',
            'fixed_only': 'Controller authored this eight-cell simple baseline before any result from this new question; this is narrow template search.',
            'fixed_template_space': space},
        'assignment': 'All three repeats declared at once; fixed, strong-single and V4 order rotates by repeat. No result-dependent replacement or budget transfer.',
        'outcomes': ['Delivery and trace validity', 'Preregistered distinguishing checks and evidence-supported interpretation',
            'Correctly bounded abstention, unsupported positive claims', 'All candidate funded net returns/RF2 and RF0 Sharpe/drawdown/exits',
            'All known and unresolved model/tool/process costs'],
        'statistical_scope': 'Descriptive paired local results only. Repeated starts share one exposed market case; no independent-market confidence bound or formal success-rate claim.',
        'formal_gates_retained': {'minimum_oos_sessions': 252, 'minimum_exit_batches': 30, 'maximum_drawdown': 0.25,
            'minimum_net_sharpe_rf2_strict': 1, 'minimum_real_cases': 6, 'minimum_mechanism_families': 3,
            'success_rate_minimum': '2/3', 'one_sided_success_lower_bound_strict': '1/2',
            'actual_execution_and_capacity_required': True, 'subsequent_frozen_prospective_evidence_required': True},
        'formal_success_denominator_contribution': 0, 'formal_target_success': False,
        'old_record_policy': 'No old scope, expired deadline, failed delivery, unknown expense or data exposure is rewritten.',
        'prior_v4_cycle': {'root': 'experiment_traces/v4c1', 'model_calls': 22, 'known_tokens': 682064},
        'outside_trial_cost': 'Study preparation, outer supervision and engineering agent/model costs are separate and currency unknown, never zero.'}
    policy = ClosingPolicy(task_calls=24, stage_calls=24, task_tokens=1400000, stage_tokens=1400000, closing_seconds=600)
    tools = {'actions': 48, 'candidates': 8, 'scan_cells': 1000000, 'comparisons': 128,
        'wall_ms': 3600000, 'controller_cpu_ms': 600000, 'retained_output_bytes': 134217728}
    process = {'launches': 1, 'cpu_ms': 3600000, 'io_transfer_bytes': 17179869184,
        'closing_cpu_ms': 60000, 'closing_io_transfer_bytes': 268435456}
    entry = ROOT / 'scripts/run_v4_architecture_study.py'
    pins = source_pins(); trials = []; stages = {}; sequence = []
    for repeat in (1, 2, 3):
        names = list(ac.NAMES); names = names[repeat - 1:] + names[:repeat - 1]
        for name in names:
            code = {'v4_evidence_workflow': 'v', 'strong_single': 's', 'fixed_template_search': 'f'}[name]
            tid = code + str(repeat); destination = root / tid
            task_id = 'producer' if code == 'f' else 'research'
            tasks = {task_id: {'idea': question, 'documents': documents, 'case': case, 'case_hash': digest(case)}}
            contract = ac.make(name)
            controller = {'version': CONTROLLER_VERSION, 'work': {task_id: {
                'question': question, 'necessary_evidence': ['Full funded execution and counterexamples', 'Frozen predictions and accountable final evidence'],
                'depends_on': [], 'unlocks': 'A local paired observation of the declared research behavior',
                'stop_condition': 'Original whole-trial model, candidate, process and deadline ceilings; no forced strategy or replacement.'}}}
            provenance = {'source_pins': pins, 'architecture_contract': contract, 'controller_policy': controller,
                'runtime_identity_route': {'version': 'codex_session_v1'},
                'report_argument_policy': {'version': 'report_lf_escape_v1'},
                'extension_context_policy': {'version': 'parent_programs_v1'},
                'extension_handoff_policy': {'version': 'yield_to_controller_v1'},
                'extension_service_policy': {'fields': ['open', 'high', 'low'], 'maximum_child_model_calls': 14,
                    'maximum_child_tokens': 900000, 'maximum_child_wall_seconds': 3600},
                'comparison_protocol_hash': digest(protocol), 'old_scope_resume_authorized': False,
                'exposure': 'One already exposed 2019 stock; local comparison, zero formal-case contribution',
                'old_v2_known_tokens': 491954, 'old_v2_unknown_reserve': 80000, 'old_v3_known_tokens': 1427801,
                'outer_controller_currency_cost': None, 'formal_target_success': False}
            trial = {'id': tid, 'root': str(destination), 'case_hash': digest(case), 'architecture_contract': contract,
                'architecture_hash': digest(contract), 'repeat': repeat, 'tasks_hash': digest(tasks),
                'source_pins_hash': digest(pins), 'policy': asdict(policy), 'duration_seconds': 7200,
                'tool_budget': tools, 'process_envelope': {'version': pe.VERSION, 'entrypoint': str(entry),
                    'entrypoint_sha256': hashlib.sha256(entry.read_bytes()).hexdigest(),
                    'arguments': ['worker', '--root', str(destination)], 'budget': process}}
            if code == 'f':
                trial['producer_contract'] = rp.make_real_contract(space, count=8,
                    seed=digest({'case': digest(case), 'architecture': name, 'repeat': repeat, 'version': 1}))
            trials.append(trial); stages[tid] = {'tasks': tasks, 'provenance': provenance}; sequence.append(tid)
    study = {'study_id': root.name, 'trials': trials, 'allocation': {'version': allocation.VERSION,
        'model_tokens': 13860000, 'model_calls': 216,
        'tool_resources': {k: 9 * v for k, v in tools.items()},
        'process_resources': {k: 9 * process[k] for k in pe.LIMITS}}}
    protocol['dispatch_order'] = sequence
    for stage in stages.values(): stage['provenance']['comparison_protocol_hash'] = digest(protocol)
    spec = {'kind': 'v4_local_architecture_study_v1', 'study_plan': study, 'stages': stages, 'protocol': protocol}
    validate_spec(spec)
    save_once(root / 'spec.json', spec)
    return {'spec_path': str(root / 'spec.json'), 'slots': 9, 'new_model_calls': 0,
            'canonical_registry_frozen': False, 'formal_target_success': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root', required=True)
    print(json.dumps(build(parser.parse_args().root), ensure_ascii=False))

"""Freeze an actual model-selected research cycle, including one data extension."""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.controller_plan import VERSION, HARNESS_VERSION
from quanta_agents.meta_v3.experiment_design import VERSION as DESIGN_VERSION
from quanta_agents.meta_v3.ledger import Ledger, digest, serial
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins, verify_case_sources


def main():
    case_path = ROOT / 'experiment_traces/meta_framework_v3/year_inputs_001/case.json'
    case = json.loads(case_path.read_text(encoding='utf-8'))
    case['report_policy'] = {'version': 'claim_support_v1'}
    case['experiment_design_policy'] = {'version': DESIGN_VERSION}
    case['research_policy'] = {'version': 'structural_research_v1', 'action_limits': {
        'inspect_inputs': 3, 'diagnose_horizons': 2, 'develop_strategy': 4,
        'inspect_execution': 3, 'read_evidence': 4, 'diagnose_execution': 3,
        'register_experiment': 2, 'request_research_extension': 1,
        'register_batch': 0, 'execute_batch': 0, 'inspect_batch': 0}}
    question = ('研究“交易活跃却价格推进有限”是否包含覆盖成本的后续交易信息。'
        '自行提出和区分解释，包括无增量信息的可能；必要时请求能区分解释的新资料。'
        '在已准入的完整资金机械开发口径下形成可执行仓位规则，诊断结果，并在有必要时登记和检验结构修改。'
        '研究动作由你选择，证据不支持时可以弃权。不要复用旧year_reversal_001策略继续调参。')
    documents = [{'name': 'frozen_scope_and_data_service', 'text':
        '这是使用已暴露2019单股资料的新问题开发，不是正式金融验收或架构泛化比较。'
        '初始本金100万元和244日完整日历、费用/滑点/税务/部分成交规则已经冻结，供机械开发使用；'
        '本轮需研究给定问题，已有的真实到达、真实券商费用和容量缺口保留在结论边界。'
        '当前可用close/volume/amount。若研究需要，同一已保存来源可由控制器提供open/high/low字段，'
        '不增加股票或日期，不下载；每字段244个原值，仍是名义15:05可用。'
        '最多准入一次有实际新字段的资料扩展，独立子研究最多10次模型调用；请求不自动获准。'
        '不预设必须交易、必须盈利、固定假说数量或必须申请扩展；所有尝试、失败与费用保留。'}]
    task_id = 'ap'
    task = {'idea': question, 'documents': documents, 'case': case, 'case_hash': digest(case)}
    verify_case_sources(task)
    controller = {'version': VERSION, 'work': {task_id: {'question': question,
        'necessary_evidence': ['可审查的机制预测与对照', '完整资金策略执行、成本及暴露诊断', '原始资料引用和终稿主张检查'],
        'depends_on': [], 'unlocks': 'Determine whether the model can use research, revision and bounded data extension tools on real saved inputs',
        'stop_condition': 'At most14 parent model calls and4 strategies; at most1 separately admitted data extension with10 calls; retain failures and final reserve'}}}
    service = {'version': 'saved_ohlc_service_v1', 'fields': ['open', 'high', 'low'],
        'maximum_extensions': 1, 'maximum_child_model_calls': 10, 'maximum_child_tokens': 600000,
        'maximum_total_model_calls': 24, 'maximum_total_nominal_tokens': 1400000,
        'maximum_child_wall_seconds': 3600, 'maximum_source_bytes': 2097152,
        'symbols': ['sh600004'], 'sessions': 244, 'download_bytes': 0,
        'cross_scope_comparison': 'Not matched evidence if information differs; preserve parent and all child costs.'}
    root = ROOT / 'experiment_traces/v4c1'
    job = Ledger.create(root, tasks={task_id: task},
        policy=ClosingPolicy(task_calls=14, stage_calls=14, task_tokens=800000,
            stage_tokens=800000, closing_seconds=600), deadline_epoch=time.time()+7200,
        provenance={'source_pins': source_pins(), 'controller_policy': controller,
            'harness_version': HARNESS_VERSION, 'runtime_identity_route': {'version': 'codex_session_v1'},
            'extension_handoff_policy': {'version': 'yield_to_controller_v1'},
            'extension_service_policy': service,
            'controller_scripts': {p.name: digest(p.read_text(encoding='utf-8')) for p in
                (ROOT/'scripts/prepare_v4_structural_cycle.py', ROOT/'scripts/run_v4_structural_cycle.py')},
            'exposure': '2019 sh600004 already exposed development, with declared execution assumptions',
            'source_case_path': str(case_path), 'old_scope_resume_authorized': False,
            'formal_architecture_comparison': False, 'formal_target_success': False,
            'development_metrics': {'capital': 'Original initial cash and every frozen calendar session, including cash-only days',
                'returns': 'NAV_t / NAV_previous - 1; previous NAV on first session is full initial cash',
                'sharpe': 'sqrt(252) * (mean daily return - ((1.02**(1/252))-1)) / sample standard deviation ddof=1; also RF0',
                'zero_variance': 'undefined, never pass',
                'drawdown': 'Full daily net NAV, including original capital in the running peak',
                'exits': 'Report positive sell execution batches and position-to-zero events separately',
                'comparison': 'All candidates and failures retained; no independent OOS or causal claim from exposed single-case performance'},
            'acceptance': {'action_selection': 'Model-generated choices only, no controller-authored action responses',
                'revision': 'Prospective registration before different executable rules, evidence-based interpretation',
                'extension': 'Actual saved model request, additive delivered fields, child model use and cited result',
                'research_quality': 'Legal reports separate from factual support and financial validity'},
            'prior_v4_research_calls': 3, 'prior_v4_research_known_tokens': 52182,
            'account_diagnostic_calls': 1, 'account_diagnostic_known_tokens': 8691,
            'old_v3_known_tokens': 1427801, 'old_v2_known_tokens': 491954, 'old_v2_unknown_reserve': 80000,
            'provider_currency_cost': None, 'controller_and_subagent_currency_cost': None})
    state = ResearchRuntime(job.root).inspect_work()
    print(serial({'root': str(root), 'mode': state['work_schedule']['stage_disposition'],
        'parent_calls': 14, 'maximum_total_calls': 24, 'initial_cash': case['initial_cash']}))


if __name__ == '__main__':
    main()

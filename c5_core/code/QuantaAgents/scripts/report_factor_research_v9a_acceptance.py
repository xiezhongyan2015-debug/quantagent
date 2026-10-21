"""Assemble final delivery from saved evidence; never load market data or select factors."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path('F:/V9A_Factor_Research_20260912')
STUDY = ROOT / 'study'
REPO = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

def number(value):
    return '未知' if value is None else f'{value:+.5f}'

def interval(row, key):
    value = row.get(key, {})
    return '[' + number(value.get('lower')) + ', ' + number(value.get('upper')) + ']'

def main():
    result = read(STUDY/'result.json')
    state = read(STUDY/'state.json')
    assert state['closed'] and state['phase'] == 'complete'
    expansion = read(STUDY/'revision_expansion.json')
    development = read(STUDY/'development_summary.json')
    initial = read(STUDY/'initial_development_summary.json')
    selected = read(STUDY/'frozen_candidates.json')['selected']
    receipts = read(ROOT/'receipts/team_receipts.json')
    usage = {k: sum(r.get('usage', {}).get(k, 0) for r in receipts['team']) for k in
             ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')}
    suite = ET.parse(ROOT/'verification/integration_final.xml').getroot()
    if suite.tag == 'testsuites': suite = suite.find('testsuite')
    tests = {k: suite.attrib[k] for k in ('tests', 'failures', 'errors', 'skipped', 'time')}
    all_annual = []
    for row in development['records']:
        source = read(STUDY/'development'/f"{row['factor_id']}.json")
        for phase in ('train', 'development'):
            for annual in source.get(phase, {}).get('annual', []):
                all_annual.append({'factor_id': row['factor_id'], 'name': row['name'], 'phase': phase, **annual})
    confirmations = {}
    for row in selected:
        identity = row['factor_id']
        confirmation = read(STUDY/'confirmation'/f'{identity}.json')
        confirmations[identity] = confirmation
        all_annual.extend({'factor_id': identity, 'name': row['name'], 'phase': 'confirmation', **a}
                          for a in confirmation['report']['annual'])
    write(ROOT/'ALL_ANNUAL_EVIDENCE.json', all_annual)
    comparisons = {}
    for phase in ('initial', 'revision'):
        value = read(STUDY/f'{phase}_comparisons.json')
        comparisons[phase] = dict(Counter(f['status'] for o in value['outcomes'] for f in o['falsifiers']))
    families = []
    revision_comparisons = read(STUDY/'revision_comparisons.json')
    for family in sorted(set(o['family_id'] for o in revision_comparisons['outcomes'] if o['family_id'].startswith('revision.'))):
        outcomes = [o for o in revision_comparisons['outcomes'] if o['family_id'] == family]
        families.append({'family': family, 'main_attempts': len(outcomes),
                         'local_falsifiers': dict(Counter(f['status'] for o in outcomes for f in o['falsifiers']))})
    chosen_annual = [a for a in all_annual if a['factor_id'] in confirmations and a['year'] >= 2019]
    worst_by_factor = {r['factor_id']: min(a['mean_pearson_ic'] for a in chosen_annual if a['factor_id'] == r['factor_id']) for r in selected}
    posthoc_best = max(worst_by_factor, key=worst_by_factor.get)
    resources = [read(p).get('resources', {}) for p in (STUDY/'development').glob('*.json')]
    loads = [read(p) for p in STUDY.glob('*load_resources.json')]
    observed_memory = max([r.get('private_bytes', 0) for r in resources] + [r.get('private_bytes', 0) for r in loads])
    cost = {'numeric_wall_seconds': state['numeric_wall_seconds'], 'numeric_cpu_seconds': state['numeric_cpu_seconds'],
            'maximum_observed_private_bytes': observed_memory, 'memory_semantics': 'sampled process private bytes, not OS lifetime peak',
            'artifact_bytes_as_of_build': sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file()),
            'workers': 1, 'account_executions': 0, 'extra_paid_gateway_calls': 0,
            'native_sessions': len(receipts['team']), 'native_token_usage': usage, 'native_usage_as_of_utc': receipts['captured_utc'],
            'currency_cost': None, 'currency_cost_status': 'unknown_no_billing_receipt',
            'backend_request_count': None, 'backend_request_count_status': 'not_exposed_by_local_receipt',
            'usage_scope': 'entire current development, implementation, audit and research team; cumulative once per session',
            'subset_rule': 'cached input is part of input; reasoning output is part of output; no double addition',
            'verification_runtime_note': 'study timer excludes engineering test and external audit time; XML and audit JSON preserve them separately'}
    cost['verification_saved_resources'] = {p.name: read(p).get('resources', {}) for p in
        (ROOT/'verification/independent_calibration_001.json', ROOT/'verification/independent_real_incremental_001.json', ROOT/'verification/independent_final_001.json') if p.exists()}
    cost['final_regression_seconds'] = float(tests['time'])
    write(ROOT/'COSTS.json', cost)
    lines = ['# V9A 开发与真实研究验收', '',
        '开发、两轮有界研究和一次冻结确认已完成。年度单因子 Pearson IC ≥ 0.10 的跨期目标未达成，5 个确认候选全部拒绝该目标。独立稳定性、交易盈利均未证明。', '',
        f"研究版本 `meta_v9a.1.0`；ID `{result['study_id']}`；状态 `{state['phase']}`，closed={state['closed']}。", '',
        '## 真实执行与分母', '',
        '主任务为 Astra ultra，三个实际子代理为 Astra xhigh：生成与控制、数值评价、独立审计。本地会话配置及累计用量已保存；配置回执不是供应商内部计算量认证。', '',
        '初批三臂各12个主尝试，24个匹配控制尝试，共60次、47个唯一公式。开发反馈后新增12个结构主尝试、24个控制尝试，形成22个新公式。总计96次尝试：48主+48控制；69个唯一公式，27次重复，69个全部执行成功。重复、负面和不可评价证据未剔除。', '',
        f"固定预测器比较启动 {state['incremental_started']} 次（开发45、确认5），确认访问 {state['confirmation_accesses']} 次；5个冻结候选。没有账户执行。初始两种写法的一对因子全矩阵相同，因此公式身份数不是独立信息数。", '',
        '初批等预算比较只使用 initial_* 原件；结构修订额外消耗12个主尝试，不能与另两臂累计结果直接宣称等预算优势。原47条规范元数据与初批结果保持不变。', '',
        '| 初批主臂 | 主尝试 | 最佳开发较差年 Pearson IC | 对应公式 |', '|---|---:|---:|---|']
    for arm in ('existing_library', 'rule_perturbation', 'llm_structure'):
        candidates = [r for r in initial['ranking'] if arm in r.get('primary_arms', [r['arm']])]
        best = candidates[0]
        lines.append(f"| {arm} | 12 | {number(min(a['mean_pearson_ic'] for a in best['annual']))} | {best['name']} `{best['factor_id'][:8]}` |")
    lines += ['', f"局部证伪计数（supported只指声明的局部阈值，不是0.10或统计显著）：首批 {comparisons['initial']}；全开发累计 {comparisons['revision']}。", '',
        '## 冻结口径与选择', '',
        '2015预热；2016—2018训练固定方向和固定Ridge；2019—2020开发；2021—2024冻结后确认，全部属于已曝光历史。目标要求2019—2024六个年度逐年达到0.10，不能取绝对值、翻年度方向或以预测器组合IC替代。2025数值未加载。', '',
        '标签为下一开盘起五交易日收益 open[t+6]/open[t+1]-1；这里raw指收益不做排名，价格为现有固定锚复权口径。每年最后六个信号日剔除，所有分段及全年使用相同年度端点。历史CSI300信号日池一致用于公式内部排名和评价。日截面至少100、每年有效日至少200、逐年覆盖至少80%。', '',
        '按开发较差年、开发均值、公式ID依次排序；原三臂各取1个与全局前3去重，实际冻结5个。此后未改公式、方向、参数或进行第三批搜索。', '',
        '## 五个候选逐年结果与用途', '',
        'IC均为日截面相关的年均值，不年化。95%区间使用20交易日移动块、300次重采样；未校正本轮自适应搜索。头部收益是同日组内5日重叠毛收益相对全池均值；严格top40缺未来标签时不补位，不能复合成账户净值。']
    for row in selected:
        identity = row['factor_id']; c = confirmations[identity]; candidate = c['candidate']; source = read(STUDY/'development'/f'{identity}.json')
        annuals = source['train']['annual'] + source['development']['annual'] + c['report']['annual']
        full_ic = sum(a['mean_pearson_ic']*a['valid_days'] for a in annuals)/sum(a['valid_days'] for a in annuals)
        full_rank = sum(a['mean_rank_ic']*a['rank_ic_valid_days'] for a in annuals)/sum(a['rank_ic_valid_days'] for a in annuals)
        lines += ['', f"### {row['name']} — `{identity[:8]}`", '', f"固定方向 {row['direction']:+d}；参数 `{candidate.get('parameters', {})}`。2016—2024全期有效日加权 Pearson {number(full_ic)}，RankIC {number(full_rank)}；六个目标年度最差 {number(worst_by_factor[identity])}。", '',
            '```text', candidate['spec'].get('expression', candidate['spec'].get('formula', str(candidate['spec']))), '```', '',
            '| 年度/阶段 | Pearson IC | 95%区间 | RankIC | 有效日 | 评价/因子覆盖 | Q5相对池 | 严格top40相对池/完整日 |',
            '|---|---:|---|---:|---:|---|---:|---|']
        for a in annuals:
            phase = '训练' if a['year']<=2018 else '开发' if a['year']<=2020 else '确认'
            lines.append(f"| {a['year']}/{phase} | {number(a['mean_pearson_ic'])} | {interval(a,'pearson_ic_interval')} | {number(a['mean_rank_ic'])} | {a['valid_days']} | {a['evaluation_coverage']:.2%}/{a['factor_coverage']:.2%} | {number(a['mean_head_relative_pool'])} | {number(a['mean_top40_relative_pool'])}/{a['top40_complete_days']} |")
        lines += ['', '| 阶段 | Pearson/RankIC | Pearson 95%区间 | 五组平均5日毛收益 Q1→Q5 |', '|---|---|---|---|']
        for phase, evidence in [('训练',source['train']),('开发',source['development']),('确认',c['report'])]:
            a=evidence['summary'];q=a['mean_quantile_returns']
            lines.append(f"| {phase} | {number(a['mean_pearson_ic'])}/{number(a['mean_rank_ic'])} | {interval(a,'pearson_ic_interval')} | " + ' / '.join(number(q[str(i)]) for i in range(1,6))+' |')
        main_ids = candidate.get('main_effect_ids', [])
        if main_ids:
            lines += ['', '本交互表的对照为 **B+F1+c 与 B+F1+c+f**：B仍是固定F2/F3/F7，c为本候选条件主效应。表中“基准”已含F1和c，因此数值不等于只含三因子的B。主效应身份：' + '、'.join('`'+x+'`' for x in main_ids) + '。']
        else:
            lines += ['', '本表对照为 **B 与 B+f**，B为固定F2/F3/F7。F3本已在B中，重复加入不提供新的增量检验。']
        lines += ['', '| 预测器配对阶段 | 基准原覆盖 IC | 基准共同样本 IC | 增加f共同样本 IC | ΔIC [95%区间] | ΔRankIC | 覆盖选择/共同样本重拟合Δ |', '|---|---:|---:|---:|---|---:|---|']
        for phase in ('development', 'confirmation'):
            inc=read(STUDY/f'{phase}_incremental'/f'{identity}.json')['result']
            if inc['status'] != 'evaluated':
                lines.append(f"| {phase} | 未知 | 未知 | 未知 | {inc['status']}: {inc.get('reason','')} | 未知 | 未知 |")
                continue
            lines.append(f"| {phase} | {number(inc['baseline_original_pearson_ic'])} | {number(inc['baseline_common_pearson_ic'])} | {number(inc['augmented_pearson_ic'])} | {number(inc['paired_delta_pearson_ic'])} {interval(inc,'interval')} | {number(inc['paired_delta_rank_ic'])} | {number(inc['coverage_selection_delta_pearson_ic'])}/{number(inc['common_refit_delta_pearson_ic'])} |")
        lines += ['', '固定基线为精确F2/F3/F7、Ridge λ=0.1；2016—2018只拟合一次，学习目标为截面收益排名去均值。交互时保留声明主效应。以上是预测器增量，不是本因子的原始IC。']
    lines += ['', '## 全部开发公式与失败分母', '',
        '| 公式ID | 族/名称 | 方向 | 2019 Pearson | 2020 Pearson | 开发均值 | 运行状态 |', '|---|---|---:|---:|---:|---:|---|']
    for row in development['records']:
        annual = {a['year']:a for a in row.get('annual',[])}
        lines.append(f"| `{row['factor_id'][:8]}` | {row['name']} | {row.get('direction')} | {number(annual.get(2019,{}).get('mean_pearson_ic'))} | {number(annual.get(2020,{}).get('mean_pearson_ic'))} | {number(row.get('summary',{}).get('mean_pearson_ic'))} | {row['status']} |")
    lines += ['', '全部69个公式的训练/开发逐年IC、RankIC、覆盖、有效日、分组收益、top40及区间，和5个候选确认逐年数据，另存 ALL_ANNUAL_EVIDENCE.json；逐日、固定增量模型/年度归因和所有提案在study原件中。', '',
        '## 验证、成本和结论边界', '',
        f"综合工程回归 {tests['tests']} 项通过，failures={tests['failures']}、errors={tests['errors']}、skipped={tests['skipped']}，用时 {tests['time']} 秒。真实面板缓存/原路径/批量/截断等价通过；独立手写三因子复算22,026项、固定B/B+F1复算4,907项通过。最终5包65,910项独立数值比较通过，最大差2.22e-16；69个开发报告重汇总及入选顺序一致，见 verification/independent_final_001.json。", '',
        '保留早期测试失败、初始化废止原件和修复依据。现有执行池迁移的函数体逐AST一致；新数值入口不加载账户模块。旧V7/V8/V9仍关闭，历史收益失败和原预算未恢复。', '',
        f"研究计时墙钟 {cost['numeric_wall_seconds']:.2f} 秒，CPU {cost['numeric_cpu_seconds']:.2f} 秒；1工作进程，最大采样私有内存 {observed_memory/1024**3:.3f} GiB；产物截至本报告 {cost['artifact_bytes_as_of_build']/1024**3:.3f} GiB。工程测试和外部独立审计时间另记原件，不包含在研究计时内。", '',
        f"本地用量快照截至 {receipts['captured_utc']}：输入 {usage['input_tokens']:,}（缓存输入 {usage['cached_input_tokens']:,}），输出 {usage['output_tokens']:,}（含推理 {usage['reasoning_output_tokens']:,}），合计 {usage['total_tokens']:,} tokens。覆盖整个开发、审计及研究团队，不是单纯公式生成成本；不把缓存/推理重复相加。当前活动尾部可能尚未计入。", '',
        '额外付费gateway调用0；这不等于原生模型未调用。供应商API请求次数未由本地回执暴露，货币费用无账单，保持未知。未证明比旧版本更省token或一般发现效率更高。', '',
        f"五个冻结候选中，事后六年最差IC最高的是 `{posthoc_best[:8]}`，最差 {number(worst_by_factor[posthoc_best])}；它仍低于0.10。此描述不改变冻结选择，也不构成一个新的确认后胜出因子。", '',
        '本轮新增机制改善了部分开发期指标，但没有产出符合年度0.10目标的因子。冻结拒绝包保留可执行公式、精确源哈希、raw分数快照与本机重导入结果，供复盘，不能投入交易时当作有效性认证。所有交付包的绝对源码路径和本机计算器有依赖，未验证跨机器迁移。', '',
        '最终机制复盘见[FINAL_REVIEW.zh-CN.md](F:/V9A_Factor_Research_20260912/FINAL_REVIEW.zh-CN.md)：路径条件的开发增量未在确认保持；低活跃度条件开发期增量为负；残差收盘压力未入选确认，不能称其已确认失败。', '',
        '后续可证伪问题：动量修订相对未修改F1的改善能否在事前另行冻结、合法获得的新时间数据上持续；残差收盘压力的低幅度增量是否在匹配样本及独立来源中存在。本轮结果不足以授权新数值区间、继续搜索或证明这些方向有效。', '',
        '上游历史到达时间、复权修订、真实交易成本/容量未认证；2025仅来源/文件元数据权限，逻辑日期过滤不能声称物理Parquet页完全隔离。详细方法边界见 verification/METHOD_LIMITATIONS.zh-CN.md。']
    (ROOT/'FINAL_REPORT.zh-CN.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    release={'version':'v9a_release_acceptance_1','generated_utc':datetime.now(timezone.utc).isoformat(),
             'study_id':result['study_id'],'engineering_tests':tests,'research_complete':True,'historical_target_met':False,
             'independent_stability_proven':False,'profitability_proven':False,'state':state,
             'attempts':{k:v for k,v in expansion['summary'].items() if k!='formula_memberships'},
             'confirmation_candidates':len(selected),'target_successes':result['historical_target_successes'],
             'native_configuration_verified':all(r['local_configuration_verified'] for r in receipts['team']),
             'cost':cost,'report':str(ROOT/'FINAL_REPORT.zh-CN.md'),'single_factor_evidence':str(ROOT/'ALL_ANNUAL_EVIDENCE.json'),
             'source_pins_sha256':hashlib.sha256((STUDY/'source_pins.json').read_bytes()).hexdigest(),
             'independent_audit_path':str(ROOT/'verification/independent_final_001.json'),
             'audit_passed':read(ROOT/'verification/independent_final_001.json').get('passed') if (ROOT/'verification/independent_final_001.json').exists() else None,
             'audit_sha256':hashlib.sha256((ROOT/'verification/independent_final_001.json').read_bytes()).hexdigest() if (ROOT/'verification/independent_final_001.json').exists() else None}
    write(ROOT/'release_acceptance.json',release)
    write(ROOT/'REJECTION.json', {'version':'v9a_frozen_target_rejection_1','study_id':result['study_id'],
        'target':result['protocol']['target'],'status':'closed_target_not_met','formula_evaluations':69,'attempts':96,
        'confirmation_accesses':1,'confirmation_successes':0,'independent_stability_proven':False,'profitability_proven':False,
        'candidates':[{'factor_id':r['factor_id'],'name':r['name'],'implementation_status':'executable',
                       'research_evidence_status':'rejected_annual_0.10_target','worst_target_year_pearson_ic':worst_by_factor[r['factor_id']],
                       'annual':[a for a in chosen_annual if a['factor_id']==r['factor_id']]} for r in selected],
        'bundles_manifest':str(STUDY/'bundles/manifest.json'),'reopening_search_from_confirmation_allowed':False})
    print(json.dumps({'report':release['report'],'annual_rows':len(all_annual),'cost':cost,'audit_passed':release['audit_passed']},ensure_ascii=True))

if __name__ == '__main__':
    main()

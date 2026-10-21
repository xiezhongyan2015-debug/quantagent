"""Controller-mediated research actions. Models never execute host code or read final data."""
from __future__ import annotations

import json
import hashlib
import zipfile
from .runtime import digest
from .store import dumps, now


def _object(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def action_schema(*, final=False, allow_submit=True):
    from .ashare_case import strategy_schema
    actions = ['submit'] if final else ['diagnose', 'backtest'] + (['submit'] if allow_submit else [])
    text = {'type': 'string', 'maxLength': 4000}
    return _object({'action': {'type': 'string', 'enum': actions}, 'strategy': strategy_schema(),
        'diagnostic_expression': {'type': 'string', 'maxLength': 2000},
        'diagnostic_horizon': {'type': 'integer', 'minimum': 1, 'maximum': 20},
        'hypothesis': text, 'expected_observation': text, 'decision_summary': text,
        'self_check': _object({'assessment': text, 'next_step': text})})


def proposal_schema():
    return _object({key: {'type': 'string', 'maxLength': 6000} for key in (
        'name', 'causal_hypothesis', 'research_instructions', 'expected_effect', 'risk',
        'assessment', 'next_step')})


def fixture_response(step, schema):
    from .ashare_case import baseline_strategy
    if step == 'meta_proposal':
        return {'name': '竞争解释与判别实验', 'causal_hypothesis': '明确反证可改善实验选择。',
                'research_instructions': '提出竞争解释，优先获取能区分它们的开发证据。',
                'expected_effect': '减少没有判别能力的实验。', 'risk': '增加上下文消耗。',
                'assessment': '离线夹具只检验记录与恢复。', 'next_step': '接入真实模型检验研究。'}
    actions = schema['properties']['action']['enum']
    action = 'submit' if actions == ['submit'] else 'diagnose' if step.endswith('_1') else 'backtest'
    return {'action': action, 'strategy': baseline_strategy(),
            'diagnostic_expression': 'pct_change(close, 10)', 'diagnostic_horizon': 5,
            'hypothesis': '仅验证因果工具接口。', 'expected_observation': '程序返回开发区间证据。',
            'decision_summary': '离线夹具，无模型研究结论。',
            'self_check': {'assessment': '已提供可重放的固定动作。', 'next_step': '检查程序证据与恢复。'}}


def _self_check(engine, run_id, step, value, evidence=None):
    existing = next((x for x in engine.store.get(run_id).get('self_checks', []) if x['step'] == step), None)
    if existing:
        # A restarted worker may have saved the check but not its JSON artifact.
        engine.artifact(run_id, 'self_checks.json', engine.store.get(run_id)['self_checks'], '逐轮两问自检')
        return
    record = {'ts': now(), 'step': step, 'questions': ['这一步做得怎么样？', '下一步该做什么，如何改进？'],
              'assessment': value['assessment'], 'next_step': value['next_step'],
              'source': 'model_public_assessment_before_current_action',
              'evidence_hash': digest(evidence) if evidence else None}
    def apply(run):
        run['self_checks'] = [x for x in run.get('self_checks', []) if x['step'] != step] + [record]
    engine.store.update(run_id, apply)
    engine.store.event(run_id, 'self_check', record['assessment'] + '\n下一步：' + record['next_step'],
                       role='researcher', step=step, data=record)
    engine.artifact(run_id, 'self_checks.json', engine.store.get(run_id)['self_checks'], '逐轮两问自检')


def _save_raw(engine, run_id, step, case, strategy, observation, split='development'):
    if 'strategy_hash' not in observation or not hasattr(case, '_evaluation_raw'):
        return
    name = step + '_execution.zip'
    path = engine.root / run_id / name
    if not path.exists():
        key = (split, observation['strategy_hash'])
        if key not in case._evaluation_raw:
            recovered = case.evaluate(strategy, split)
            if digest(recovered) != digest(observation):
                raise ValueError('恢复时重算证据不一致，禁止覆盖历史记录。')
        raw = case._evaluation_raw[key]
        temporary = path.with_suffix('.tmp')
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for field, filename in (('_daily_df', 'daily.csv'), ('_trades_df', 'trades.csv'),
                                    ('_target_weights', 'target_weights.csv')):
                if field in raw:
                    archive.writestr(filename, raw[field].to_csv(index=field == '_target_weights'))
        temporary.replace(path)
    entry = {'name': name, 'label': step + ' · 完整资金与订单账', 'type': 'application/zip',
             'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    engine.store.update(run_id, lambda body: body.__setitem__('artifacts',
        [x for x in body['artifacts'] if x['name'] != name] + [entry]))


def _persist_record(engine, run_id, prefix, record, case, reviewed_evidence):
    step = f"{prefix}_round_{record['round']}"
    _save_raw(engine, run_id, step, case, record['response']['strategy'], record['observation'])
    engine.artifact(run_id, step + '.json', record, f"{prefix} 第 {record['round']} 轮 · 假设与程序证据")
    _self_check(engine, run_id, step, record['response']['self_check'], reviewed_evidence)


def _trace_for_model(records):
    # All development attempts remain in the ledger; the model gets bounded evidence.
    return [{'round': x['round'], 'action': x['response']['action'],
             'strategy': x['response']['strategy'], 'hypothesis': x['response']['hypothesis'],
             'expected_observation': x['response']['expected_observation'],
             'decision_summary': x['response']['decision_summary'],
             'self_check': x['response']['self_check'], 'observation': x['observation']}
            for x in records]


def _active_architectures(config):
    only = config.get('only_architecture')
    return [only] if only is not None else list(config.get('research_order', ['baseline', 'candidate']))


def run_step(engine, run_id, step, case):
    from .ashare_case import baseline_strategy
    run = engine.store.get(run_id)
    steps = run['steps']
    if step == 'prepare':
        engine.store.event(run_id, 'tool', '读取开发时期与历史成分，准备真实 A 股证据；不读取最终时期收益。', role='evaluator')
        packet = case.development_packet()
        initial_strategy = case.initial_strategy() if hasattr(case, 'initial_strategy') else baseline_strategy()
        evidence = case.evaluate(initial_strategy, 'development')
        engine.artifact(run_id, 'development_packet.json', packet, '真实案例 · 开发数据与工具说明')
        engine.artifact(run_id, 'starting_evidence.json', evidence, '普通策略起点 · 开发期证据')
        _save_raw(engine, run_id, 'starting_evidence', case, initial_strategy, evidence)
        return {'packet': packet, 'evidence': evidence, 'initial_strategy': initial_strategy}
    if step == 'meta_proposal':
        if run['config'].get('only_architecture') == 'baseline':
            return {'skipped': True, 'reason': 'baseline_only_run_has_no_candidate_or_meta_call'}
        frozen = run['config'].get('frozen_candidate')
        if frozen is not None:
            if digest(frozen) != run['config']['frozen_candidate_hash']:
                raise ValueError('冻结候选来源或文本已变化，禁止继续。')
            result = {'name': frozen['name'], 'research_instructions': frozen['research_instructions'],
                      'source_run_id': frozen['source_run_id'], 'source_proposal': frozen,
                      'source_proposal_hash': digest(frozen),
                      'architecture_hash': digest(frozen['research_instructions']),
                      'change_scope': 'frozen_research_guidance', 'frozen': True,
                      'assessment_policy': 'source_proposal assessments belong to the earlier run; no new model assessment was generated'}
            engine.artifact(run_id, 'candidate_architecture.json', result, '已冻结候选 · 来源及原文哈希')
            engine.store.event(run_id, 'status', '复用已冻结研究指导；本步骤不调用元研究模型，也不生成本轮模型自检。',
                               role='harness', step=step,
                               data={'source_run_id': frozen['source_run_id'], 'source_proposal_hash': digest(frozen)})
            return result
        prompt = ('你是元研究员，所有模型固定 Astra/xhigh。分析基线的开发轨迹，提出一个可归因的研究方法改动。'
            '研究工具、交易规则、评分、预算不能更改。本轮只修改研究指导，未来将做跨题冻结检验。'
            '指导必须能迁移到其他题目，不得复制本题参数、股票、日期、因子公式或已知答案。'
            '允许认为当前改动没有效果。assessment回答这一步做得怎么样；next_step回答下一步如何改进。'
            '只返回规定 JSON；不要调用宿主工具或输出隐藏思维链。\n' + dumps({
                'baseline_instructions': run['config']['baseline_instructions'],
                'development_trace': _trace_for_model(run['research']['baseline'])}))
        response = engine._model(run_id, step, 'meta_designer', prompt, proposal_schema())
        _self_check(engine, run_id, step, response, _trace_for_model(run['research']['baseline']))
        result = {**response, 'parent_hash': digest(run['config']['baseline_instructions']),
                  'architecture_hash': digest(response), 'change_scope': 'research_guidance',
                  'evaluation_access': 'development_only'}
        engine.artifact(run_id, 'candidate_architecture.json', result, '候选研究方法与修改假设')
        return result
    if step.startswith(('baseline_', 'candidate_')):
        prefix = 'candidate' if step.startswith('candidate') else 'baseline'
        if prefix not in _active_architectures(run['config']):
            return {'skipped': True, 'reason': 'architecture_not_scheduled', 'architecture': prefix}
        initial = step.endswith('initial')
        frozen = run['config'].get('frozen_candidate')
        if prefix == 'candidate':
            instructions = frozen['research_instructions'] if frozen is not None else steps['meta_proposal']['research_instructions']
        else:
            instructions = run['config']['baseline_instructions']
        total_calls = run['config'].get('research_calls_per_architecture', 6)
        halfway = total_calls // 2
        start, end = (0, halfway) if initial else (halfway, total_calls)
        for index in range(start, end):
            engine._gate(run_id)
            run = engine.store.get(run_id)
            records = run['research'][prefix]
            if records and records[-1]['response']['action'] == 'submit' and not records[-1]['observation'].get('error'):
                last = records[-1]
                reviewed = records[-2]['observation'] if len(records) > 1 else steps['prepare']['evidence']
                _persist_record(engine, run_id, prefix, last, case, reviewed)
                break
            if len(records) > index:
                reviewed = records[index - 1]['observation'] if index else steps['prepare']['evidence']
                _persist_record(engine, run_id, prefix, records[index], case, reviewed)
                continue
            call_step = f'{prefix}_round_{index + 1}'
            schema = action_schema(final=index == total_calls - 1, allow_submit=not initial)
            prompt = ('你是 A 股策略研究 agent。目标是可复核的真实规律和可执行含成本收益，允许否定初始想法。'
                '只使用控制器给出的开发数据与工具观察。你不能读文件、调用终端、联网或访问确认/最终时期。'
                '使用因子表达式定义新信号、过滤、评分和组合；控制器解释表达式，不执行任意代码。'
                '可选动作 diagnose（分组诊断）、backtest（固定开发期组合回测）、submit（冻结单一最终策略）。'
                '工具由控制器在 JSON 动作返回后执行，结果在下一轮提供。诊断事件收益不是组合收益。'
                '每轮只请求一个动作；所有尝试和成本保留。hypothesis和expected_observation需可被程序证据推翻。'
                'self_check.assessment 回答“这一步做得怎么样？”，self_check.next_step 回答“下一步该做什么、如何改进？”。'
                'assessment评价已有证据，不预先声称当前尚未执行的动作成功。给出简短决策摘要，不输出隐藏思维链。'
                '最后一次调用必须 submit；提交后不再向你回传留出结果。\n研究指导：' + instructions + '\n' + dumps({
                    'round': index + 1, 'max_calls': total_calls, 'remaining_calls_after_this': total_calls - 1 - index,
                    'development_packet': steps['prepare']['packet'],
                    'starting_evidence': steps['prepare']['evidence'],
                    'your_development_history': _trace_for_model(records)}))
            response = engine._model(run_id, call_step, prefix + '_researcher', prompt, schema)
            engine._gate(run_id)
            engine.store.event(run_id, 'tool', '执行开发研究动作：' + response['action'], role='evaluator', step=call_step,
                               data={'action': response['action'], 'strategy': response['strategy'], 'split': 'development'})
            try:
                if response['action'] == 'diagnose':
                    observation = case.diagnose(response['strategy'], response['diagnostic_expression'], response['diagnostic_horizon'])
                else:
                    observation = case.evaluate(response['strategy'], 'development')
            except (ValueError, KeyError, ArithmeticError) as exc:
                observation = {'error': f'{type(exc).__name__}: {exc}', 'valid_experiment': False,
                               'instruction': '修正策略或表达式后，在下一研究轮请求；失败仍占本轮机会。'}
            record = {'round': index + 1, 'response': response, 'observation': observation,
                      'request_hash': digest(response), 'observation_hash': digest(observation),
                      'assessment_timing': 'before_current_action',
                      'reviewed_evidence_hash': digest(records[-1]['observation'] if records else steps['prepare']['evidence'])}
            engine.store.update(run_id, lambda body: body['research'][prefix].append(record))
            _persist_record(engine, run_id, prefix, record, case,
                            records[-1]['observation'] if records else steps['prepare']['evidence'])
            engine.store.event(run_id, 'tool', '开发动作完成' if not observation.get('error') else '开发动作未通过校验',
                               role='evaluator', step=call_step, data=observation)
        records = engine.store.get(run_id)['research'][prefix]
        last = records[-1]
        if not initial and (last['response']['action'] != 'submit' or last['observation'].get('error')):
            raise ValueError(f'{prefix} 用尽研究轮次但没有合法冻结提交；保留失败，不挑历史最佳补交。')
        return {'response': last['response'], 'evaluation': last['observation'],
                'submission_hash': digest(last['response']['strategy']), 'architecture_hash': digest(instructions),
                'research_rounds': len(records), 'submitted': last['response']['action'] == 'submit'}
    if step == 'final_evaluation':
        frozen = {key: steps[key + '_refine']['response']['strategy'] for key in _active_architectures(run['config'])}
        split = run['config'].get('evaluation_split', 'confirmation')
        if split not in {'development', 'confirmation'}:
            raise ValueError('只能在开发期或确认期复核冻结提交。')
        engine.artifact(run_id, 'frozen_submissions.json', frozen, '复核前冻结的实际研究提交')
        manifest = case.manifest()
        exposure = None
        if split == 'confirmation':
            family = manifest.get('evaluation_family_id', manifest.get('case_id', 'ashare_daily_reversal'))
            exposure = engine.store.expose_evaluation(str(family), 'confirmation', run_id, digest(frozen))
            engine.artifact(run_id, 'evaluation_exposure.json', exposure, '留出时期查看账本')
        # Final is deliberately not accessed in this integration batch.
        engine.store.update(run_id, {'confirmation_opened': split == 'confirmation', 'final_opened': False,
                                    'confirmation_exposure': exposure})
        results = {key: case.evaluate(strategy, split) for key, strategy in frozen.items()}
        for key, result in results.items():
            _save_raw(engine, run_id, key + '_' + split, case, frozen[key], result, split)
        engine.artifact(run_id, split + '_evaluation.json', results,
                        '开发期复核 · 非样本外' if split == 'development' else '确认期 · 不回传研究模型')
        return results
    if step == 'finish':
        results = steps['final_evaluation']
        split = run['config'].get('evaluation_split', 'confirmation')
        frozen_candidate = run['config'].get('frozen_candidate')
        limitations = [
            ('候选指导在本轮前已冻结；仍需跨题重复和独立评测验证迁移'
             if frozen_candidate is not None else '候选方法使用本题开发轨迹提出，尚无跨题迁移证据'),
            ('仅复核开发期，结果不是样本外证据；未访问确认期'
             if split == 'development' else '确认期已查看，后续架构修改不得把该区间继续称为盲测'),
            '最终时期未打开', 'L2 尚未纳入本次日线案例',
            '只解释因子表达式，未开放任意 Python；交易近似以案例报告为准']
        delta = (results['candidate']['score'] - results['baseline']['score']
                 if {'baseline', 'candidate'} <= set(results) else None)
        comparison = {**results, 'arm_results': results, 'score_delta': delta,
            'promotion': False, 'scope': run['config']['comparison_scope'], 'comparable': run['comparable'],
            'final_opened': False, 'target_net_sharpe': 1.0,
            'evaluation_split': split, 'evaluation_is_oos': False if split == 'development' else None,
            'candidate_frozen': frozen_candidate is not None,
            'candidate_source_run_id': frozen_candidate['source_run_id'] if frozen_candidate is not None else None,
            'research_order': run['config'].get('research_order', ['baseline', 'candidate']),
            'only_architecture': run['config'].get('only_architecture'),
            'executed_architectures': _active_architectures(run['config']),
            'research_calls_per_architecture': run['config'].get('research_calls_per_architecture', 6),
            'confirmation_exposure': run.get('confirmation_exposure'),
            'verdict': ('开发期比较完成，非样本外；不能证明架构改进或稳定生成夏普大于 1。'
                        if split == 'development' else '真实案例开发联调完成；单题单次不能证明架构改进或稳定生成夏普大于 1。'),
            'evidence_status': 'integration_only', 'usage_by_role': engine.get(run_id)['usage_by_role'],
            'limitations': limitations}
        engine.store.update(run_id, {'comparison': comparison})
        engine.artifact(run_id, 'comparison.json', comparison, '真实案例比较与证据边界')
        return comparison
    raise ValueError(step)

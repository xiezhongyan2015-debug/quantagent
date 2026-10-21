from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

from . import benchmark
from .store import Store, dumps, now

MODEL = 'gpt-6-astra'
EFFORT = 'xhigh'
TERMINAL = {'completed', 'cancelled', 'failed', 'interrupted', 'budget_exceeded'}
BASE_INSTRUCTIONS = '根据任务定义与开发期证据研究趋势反转策略。提出可检验解释，选择下一实验，允许保留原策略或认定证据不足。不要追逐某个指定指标组合。'
PHASES = {
    'prepare': '冻结案例与评测协议',
    'baseline_initial': '基线架构 · 初始研究',
    'baseline_refine': '基线架构 · 诊断与改进',
    'meta_proposal': '元研究 · 提出架构修改',
    'candidate_initial': '候选架构 · 初始研究',
    'candidate_refine': '候选架构 · 诊断与改进',
    'final_evaluation': '冻结提交 · 独立评测',
    'finish': '生成比较报告',
}


def digest(value) -> str:
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def validate_research_config(value: dict | None) -> dict:
    """Freeze research controls before creating files, loading data or starting work."""
    allowed = {'calls_per_architecture', 'frozen_candidate', 'research_order', 'evaluation_split',
               'only_architecture'}
    if value is not None and (type(value) is not dict or set(value) - allowed):
        raise ValueError('research_config must be an object with only declared research controls')
    cfg = dict(value or {})
    calls = cfg.get('calls_per_architecture', 6)
    if type(calls) is not int or not 6 <= calls <= 18:
        raise ValueError('calls_per_architecture must be an integer from 6 to 18')
    order = cfg.get('research_order', ['baseline', 'candidate'])
    if type(order) is not list or order not in (['baseline', 'candidate'], ['candidate', 'baseline']):
        raise ValueError('research_order must contain baseline and candidate exactly once')
    split = cfg.get('evaluation_split', 'confirmation')
    if type(split) is not str or split not in {'development', 'confirmation'}:
        raise ValueError('evaluation_split must be development or confirmation')
    only = cfg.get('only_architecture')
    if only is not None and (type(only) is not str or only not in {'baseline', 'candidate'}):
        raise ValueError('only_architecture must be null, baseline or candidate')
    frozen = cfg.get('frozen_candidate')
    if frozen is not None:
        fields = {'name', 'research_instructions', 'source_run_id', 'causal_hypothesis',
                  'expected_effect', 'risk', 'assessment', 'next_step', 'parent_hash',
                  'architecture_hash', 'change_scope', 'evaluation_access',
                  'source_case_id', 'source_artifact_sha256'}
        required = {'name', 'research_instructions', 'source_run_id'}
        if type(frozen) is not dict or not required <= set(frozen) or set(frozen) - fields:
            raise ValueError('frozen_candidate must be a research proposal with name, research_instructions and source_run_id; execution overrides are forbidden')
        for key, text in frozen.items():
            if type(text) is not str or len(text) > 6000 or (key in required and not text.strip()):
                raise ValueError(f'frozen_candidate.{key} must be bounded text')
        # Copy without stripping or rewriting instructions; provenance hashes refer
        # to the exact caller-supplied proposal and text, including whitespace.
        frozen = dict(frozen)
    elif only == 'candidate' or (order[0] == 'candidate' and only != 'baseline'):
        raise ValueError('candidate-first research requires frozen_candidate instructions')
    return {'calls_per_architecture': calls, 'frozen_candidate': frozen,
            'research_order': list(order), 'evaluation_split': split, 'only_architecture': only}


def validate_response(value, schema: dict, path: str = 'response') -> None:
    """Validate the deliberately small schema vocabulary used by this harness."""
    kind = schema['type']
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: value is not allowed')
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['required']):
            raise ValueError(f'{path}: missing or extra fields')
        for key, child in schema['properties'].items():
            validate_response(value[key], child, path + '.' + key)
    elif kind == 'array':
        if not isinstance(value, list):
            raise ValueError(f'{path}: expected list')
        for index, item in enumerate(value):
            validate_response(item, schema['items'], f'{path}[{index}]')
    elif kind == 'string':
        if not isinstance(value, str):
            raise ValueError(f'{path}: expected string')
        if len(value) > schema.get('maxLength', 100_000):
            raise ValueError(f'{path}: string exceeds bound')
    elif kind in {'integer', 'number'}:
        expected = type(value) is int if kind == 'integer' else type(value) in {int, float}
        if not expected or not math.isfinite(value):
            raise ValueError(f'{path}: expected finite {kind}')
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ValueError(f'{path}: outside parameter bounds')
    else:
        raise ValueError(f'unsupported schema type {kind}')


class PauseRequested(Exception):
    pass


class CancelRequested(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class GraphState(TypedDict):
    run_id: str
    last_step: str


class Engine:
    """One local worker; model work, research state and evaluation remain separate."""

    def __init__(self, root: Path, gateway=None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root / 'ledger.sqlite3')
        self.gateway = gateway
        self._gateway_injected = gateway is not None
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.active: str | None = None
        self.case_instances: dict[str, object] = {}
        self.checkpoint_db = sqlite3.connect(self.root / 'checkpoints.sqlite3', check_same_thread=False)
        self.checkpointer = SqliteSaver(self.checkpoint_db)
        graph = StateGraph(GraphState)
        previous = START
        for name in PHASES:
            graph.add_node(name, self._make_node(name))
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        self.graph = graph.compile(checkpointer=self.checkpointer)
        # Do not blindly repeat a paid call after process loss.
        for run in self.store.runs():
            if run['status'] in {'running', 'queued', 'pausing', 'cancelling'}:
                active = run.get('active_started_epoch')
                last_known = datetime.fromisoformat(run['updated_at']).timestamp()
                self.store.update(run['id'], {'status': 'interrupted', 'active_started_epoch': None,
                    'active_seconds': run.get('active_seconds', 0) + max(0, last_known - active) if active else run.get('active_seconds', 0),
                    'last_error': '进程中断；恢复前会核对已保存的调用结果。'})
                for call in self.store.calls(run['id']):
                    if call['status'] == 'running':
                        receipt_file = Path(call['workdir']) / 'receipt.json'
                        try:
                            receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
                            if not isinstance(receipt.get('response'), dict):
                                raise ValueError('invalid receipt')
                            if run['mode'] == 'live' and (receipt.get('model'), receipt.get('effort')) != (MODEL, EFFORT):
                                raise ValueError('receipt model mismatch')
                            call.update(status='completed', ended_at=now(), receipt=receipt, usage=receipt.get('usage'))
                        except (OSError, ValueError, TypeError, AttributeError):
                            call.update(status='uncertain', ended_at=now())
                        self.store.save_call(call)
                self.store.event(run['id'], 'error', '检测到中断，未自动重试模型调用。')

    def source_manifest(self) -> dict:
        source_root = Path(__file__).resolve().parents[1]
        files = {str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(source_root.rglob('*'))
                 if p.is_file() and p.suffix in {'.py', '.yaml', '.html'}}
        return {'files': files, 'hash': digest(files)}

    def create(self, mode: str = 'live', budget: dict | None = None, *, start: bool = True,
               case: str = 'synthetic', case_config: dict | None = None,
               research_config: dict | None = None, request_key: str | None = None) -> str:
        if mode not in {'live', 'fixture'}:
            raise ValueError('mode must be live or fixture')
        if case not in {'synthetic', 'ashare'}:
            raise ValueError('unknown case')
        if case_config and case != 'ashare':
            raise ValueError('case_config requires ashare')
        if research_config is not None and case != 'ashare':
            raise ValueError('research_config requires ashare')
        research = validate_research_config(research_config) if case == 'ashare' else None
        if request_key is not None and (type(request_key) is not str or
                re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,159}', request_key) is None):
            raise ValueError('request_key must be a safe identifier of 1 to 160 characters')
        request_hash = digest(json.loads(json.dumps(
            {'mode': mode, 'budget': budget, 'case': case, 'case_config': case_config,
             'research_config': research_config}, sort_keys=True, ensure_ascii=False,
            allow_nan=False, default=str)))
        with self.lock:
            if request_key is not None:
                existing = next((r for r in self.store.runs() if r.get('request_key') == request_key), None)
                if existing is not None:
                    if existing.get('create_request_hash') != request_hash:
                        raise ValueError('request_key already exists with a different creation request')
                    # A transport retry must not restart a paused, failed or queued
                    # run, and must work even while its original worker is active.
                    return existing['id']
            if self.worker and self.worker.is_alive():
                raise ValueError('当前已有运行；请等待完成或暂停后再新建。')
            limits = {'soft_tokens': 250_000, 'hard_tokens': 500_000, 'max_calls': 12,
                      'reserve_tokens_per_call': 32_000, 'max_runtime_seconds': 7200,
                      'max_call_seconds': 1800, 'max_attempts_per_step': 2,
                      'max_architecture_tokens': 200_000}
            if case == 'ashare':
                limits.update(soft_tokens=600_000, hard_tokens=1_200_000,
                              max_calls=26, max_architecture_tokens=500_000,
                              max_runtime_seconds=14_400)
                for field in ('soft_tokens', 'hard_tokens', 'max_calls',
                              'max_architecture_tokens', 'max_runtime_seconds'):
                    limits[field] = math.ceil(limits[field] * research['calls_per_architecture'] / 6)
            if budget:
                if set(budget) - set(limits):
                    raise ValueError('unknown budget fields')
                limits.update(budget)
            if any(type(v) is not int or v <= 0 for v in limits.values()) or limits['soft_tokens'] > limits['hard_tokens']:
                raise ValueError('invalid budget')
            run_id = 'meta-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6]
            folder = self.root / run_id
            folder.mkdir()
            source = self.source_manifest()
            with zipfile.ZipFile(folder / 'source_snapshot.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                source_root = Path(__file__).resolve().parents[1]
                for name in source['files']:
                    archive.write(source_root / name, name)
            body = {
                'id': run_id, 'status': 'queued', 'created_at': now(), 'updated_at': now(),
                'request_key': request_key, 'create_request_hash': request_hash,
                'phase': 'prepare', 'phase_label': PHASES['prepare'], 'model': MODEL, 'effort': EFFORT,
                'mode': mode, 'data_kind': 'ashare_daily' if case == 'ashare' else 'synthetic_fixture', 'budget': limits,
                'config': {'model': MODEL, 'effort': EFFORT, 'source_manifest': source,
                           'case': benchmark.case_manifest(),
                           'baseline_instructions': BASE_INSTRUCTIONS,
                           'research_calls_per_architecture': 2,
                           'comparison_scope': 'development_smoke_only',
                           'model_tools': 'disabled', 'promotion_enabled': False},
                'steps': {}, 'artifacts': [{'name': 'source_snapshot.zip', 'label': '冻结的源码快照', 'type': 'application/zip'}],
                'interventions': [], 'comparable': True, 'pause_requested': False, 'cancel_requested': False,
                'active_seconds': 0.0, 'active_started_epoch': None, 'comparison': None,
            }
            versions = {}
            for package in ('langgraph', 'langgraph-checkpoint-sqlite', 'pandas', 'numpy', 'pyarrow', 'duckdb'):
                try:
                    versions[package] = importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError:
                    versions[package] = None
            body['config']['environment'] = {'python': sys.version, 'packages': versions}
            if case == 'ashare':
                from .ashare_case import AShareCase
                instance = AShareCase(case_config)
                self.case_instances[run_id] = instance
                calls = research['calls_per_architecture']
                frozen = research['frozen_candidate']
                split = research['evaluation_split']
                order = research['research_order']
                only = research['only_architecture']
                phase_order = list(PHASES)
                if frozen is not None:
                    phase_order = ['prepare', 'meta_proposal', order[0] + '_initial', order[0] + '_refine',
                                   order[1] + '_initial', order[1] + '_refine', 'final_evaluation', 'finish']
                phase_labels = {**PHASES, 'final_evaluation': (
                    '冻结提交 · 开发期复核（非样本外）' if split == 'development' else '冻结提交 · 确认期独立评测')}
                if frozen is not None:
                    phase_labels['meta_proposal'] = '冻结候选 · 复用既有研究方法'
                body['config'].update(case_type='ashare', case_config=case_config or {},
                    case=instance.manifest(), research_calls_per_architecture=calls,
                    research_config=research, frozen_candidate=frozen,
                    frozen_candidate_hash=digest(frozen) if frozen is not None else None,
                    research_order=order, only_architecture=only, phase_order=phase_order,
                    comparison_scope=('frozen_architecture_development_comparison' if split == 'development'
                                      else 'real_case_development_integration'),
                    model_tools='controller_mediated_factor_diagnosis_backtest',
                    baseline_instructions='根据普通策略定义和开发证据自主研究，允许提出新因子、组合、过滤条件或放弃假设。每次实验提出可核查解释；不要追求已知答案。',
                    evaluation_split=split, final_opened=False,
                    phase_labels=phase_labels,
                    experiment_protocol={'max_research_calls_per_architecture': calls,
                        'initial_calls_per_architecture': calls // 2,
                        'remaining_calls_per_architecture': calls - calls // 2,
                        'expected_max_model_calls_without_retries': (1 if only else 2) * calls + (1 if frozen is None and only is None else 0),
                        'candidate_frozen': frozen is not None, 'research_order': order,
                        'only_architecture': only,
                        'evaluation_split': split,
                        'target_net_sharpe': 1.0, 'promotion_enabled': False,
                        'statement': ('开发期复核不构成样本外结果，不访问确认期。' if split == 'development'
                                      else '真实案例为开发联调；确认期一经查看即记为已使用，不能再次称为盲测。')})
                body['research'] = {'baseline': [], 'candidate': []}
                body['self_checks'] = []
            self.store.create(body)
            self.artifact(run_id, 'manifest.json', body['config'], '案例与执行协议')
            self.store.event(run_id, 'status', '已创建真实模型联调' if mode == 'live' else '已创建离线夹具测试（没有模型调用）',
                             data={'model': MODEL, 'effort': EFFORT, 'mode': mode, 'data_kind': body['data_kind']})
            if start:
                self.start(run_id)
            return run_id

    def artifact(self, run_id: str, name: str, data, label: str = '') -> dict:
        if Path(name).name != name:
            raise ValueError('invalid artifact name')
        path = self.root / run_id / name
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(dumps(data), encoding='utf-8')
        os.replace(temporary, path)
        entry = {'name': name, 'label': label or name, 'type': 'application/json',
                 'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        def apply(run):
            run['artifacts'] = [a for a in run['artifacts'] if a['name'] != name] + [entry]
        self.store.update(run_id, apply)
        self.store.event(run_id, 'artifact', label or name, data=entry)
        return entry

    def usage(self, run_id: str) -> dict:
        calls = self.store.calls(run_id)
        fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
        result = {field: sum((c.get('usage') or {}).get(field) or 0 for c in calls) for field in fields}
        result['unknown_calls'] = sum(1 for c in calls if not c.get('usage') or
                                      c['usage'].get('input_tokens') is None or c['usage'].get('output_tokens') is None)
        result['reasoning_unknown_calls'] = sum(1 for c in calls if (c.get('usage') or {}).get('reasoning_output_tokens') is None)
        # Reasoning is a subset of output; cached input is a subset of input.
        result['total_tokens'] = result['input_tokens'] + result['output_tokens']
        result['reported_total_tokens'] = result['total_tokens']
        result['is_partial'] = bool(result['unknown_calls'])
        result['cost_amount'] = None
        result['cost_note'] = '账户调用未提供逐次账单金额；不按未核实价格估算。'
        return result

    def get(self, run_id: str) -> dict:
        body = self.store.get(run_id)
        body['calls'] = self.store.calls(run_id)
        body['usage'] = self.usage(run_id)
        body['usage_by_role'] = {}
        for call in body['calls']:
            role_usage = body['usage_by_role'].setdefault(call['role'], {'calls': 0, 'reported_tokens': 0, 'duration_seconds': 0.0})
            role_usage['calls'] += 1
            role_usage['reported_tokens'] += sum((call.get('usage') or {}).get(k) or 0 for k in ('input_tokens', 'output_tokens'))
            role_usage['duration_seconds'] += (call.get('receipt') or {}).get('duration_seconds', 0)
        active = body.get('active_started_epoch')
        body['elapsed_seconds'] = body.get('active_seconds', 0) + (time.time() - active if active else 0)
        ending = body.get('ended_at') or (body['updated_at'] if body['status'] in TERMINAL else None)
        ending_epoch = datetime.fromisoformat(ending).timestamp() if ending else time.time()
        body['wall_elapsed_seconds'] = ending_epoch - datetime.fromisoformat(body['created_at']).timestamp()
        return body

    def start(self, run_id: str) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError('已有任务正在执行。')
            run = self.store.get(run_id)
            if run['status'] in {'completed', 'cancelled'}:
                raise ValueError('已结束的运行不能重放；请新建运行。')
            if run['config']['source_manifest']['hash'] != self.source_manifest()['hash']:
                raise ValueError('源码已变化；请新建运行，避免混用架构版本。')
            uncertain = [c for c in self.store.calls(run_id) if c['status'] in {'uncertain', 'running', 'failed'}]
            if uncertain:
                raise ValueError('存在结果未知的付费调用。请核对调用目录；需要重试时使用明确的 retry 操作。')
            self.store.update(run_id, {'status': 'running', 'pause_requested': False,
                                      'active_started_epoch': time.time(), 'last_error': None})
            self.active = run_id
            self.worker = threading.Thread(target=self._work, args=(run_id,), daemon=True, name=run_id)
            self.worker.start()

    def control(self, run_id: str, action: str, text: str = '') -> None:
        run = self.store.get(run_id)
        if action == 'message':
            if not text.strip() or len(text) > 8000:
                raise ValueError('留言长度应为 1–8000 字符。')
            if run['status'] in TERMINAL:
                raise ValueError('运行已结束；不能再注入指令。')
            def add(body):
                body['interventions'].append({'ts': now(), 'text': text.strip()})
                body['comparable'] = False
            self.store.update(run_id, add)
            self.store.event(run_id, 'interaction', '人工补充将在下一次模型调用时送达；本次运行已标记为人工干预。',
                             role='user', data={'text': text.strip()})
        elif action == 'pause':
            if run['status'] != 'running':
                raise ValueError('只能暂停正在运行的任务。')
            self.store.update(run_id, {'pause_requested': True, 'status': 'pausing'})
            self.store.event(run_id, 'status', '将在当前步骤完成并落盘后暂停。')
        elif action == 'cancel':
            if run['status'] in TERMINAL:
                raise ValueError('运行已结束。')
            running = self.worker and self.worker.is_alive() and self.active == run_id
            self.store.update(run_id, {'cancel_requested': True, 'status': 'cancelling' if running else 'cancelled'})
            self.store.event(run_id, 'status', '正在停止当前调用；已保存成果保留。')
        elif action in {'resume', 'retry'}:
            if self.worker and self.worker.is_alive():
                raise ValueError('请等待当前步骤暂停或结束。')
            if action == 'retry':
                for call in self.store.calls(run_id):
                    if call['status'] in {'uncertain', 'running', 'failed'}:
                        call.update(status='retry_authorized')
                        self.store.save_call(call)
                self.store.event(run_id, 'interaction', '用户明确请求重试；结果未知的旧调用仍计入账本，新尝试可能额外消耗。', role='user')
            self.start(run_id)
        else:
            raise ValueError('unknown control action')

    def _gate(self, run_id: str) -> None:
        run = self.get(run_id)
        if run['cancel_requested']:
            raise CancelRequested()
        if run['pause_requested']:
            raise PauseRequested()
        if run['elapsed_seconds'] >= run['budget']['max_runtime_seconds']:
            raise BudgetExceeded('达到运行时间事故上限。')

    def _work(self, run_id: str) -> None:
        config = {'configurable': {'thread_id': run_id}}
        try:
            snapshot = self.graph.get_state(config)
            graph_input = None if snapshot.values else {'run_id': run_id, 'last_step': ''}
            self.graph.invoke(graph_input, config, durability='sync')
            self.store.update(run_id, {'status': 'completed'})
            self.store.event(run_id, 'status', '最小闭环完成；没有晋升架构，也不构成收益证明。')
        except PauseRequested:
            self.store.update(run_id, {'status': 'paused'})
            self.store.event(run_id, 'status', '已在步骤边界暂停，可恢复。')
        except CancelRequested:
            self.store.update(run_id, {'status': 'cancelled'})
            self.store.event(run_id, 'status', '运行已停止。')
        except BudgetExceeded as exc:
            self.store.update(run_id, {'status': 'budget_exceeded', 'last_error': str(exc)})
            self.store.event(run_id, 'error', str(exc))
        except Exception as exc:
            status = 'cancelled' if self.store.get(run_id)['cancel_requested'] else 'failed'
            self.store.update(run_id, {'status': status, 'last_error': f'{type(exc).__name__}: {exc}'})
            self.store.event(run_id, 'error', f'{type(exc).__name__}: {exc}')
        finally:
            # Market bundles and factor matrices can be large. A paused or failed
            # run reloads its own frozen snapshot on resume; no other run is touched.
            self.case_instances.pop(run_id, None)
            def clock_stop(run):
                if run.get('active_started_epoch'):
                    run['active_seconds'] += time.time() - run['active_started_epoch']
                    run['active_started_epoch'] = None
                if run['status'] in TERMINAL:
                    run['ended_at'] = now()
            self.store.update(run_id, clock_stop)
            self.artifact(run_id, 'calls.json', self.store.calls(run_id), '全部调用与token账本')
            self.active = None

    def _make_node(self, name):
        def node(state):
            run_id = state['run_id']
            self._gate(run_id)
            run = self.store.get(run_id)
            # The checkpoint graph keeps its stable slot names. Each run freezes
            # the semantic phase in each slot, including candidate-first order.
            phase = run['config'].get('phase_order', list(PHASES))[list(PHASES).index(name)]
            if phase not in run['steps']:
                label = run['config'].get('phase_labels', PHASES)[phase]
                self.store.update(run_id, {'phase': phase, 'phase_label': label})
                self.store.event(run_id, 'status', label, step=phase)
                result = self._step(run_id, phase)
                self.store.update(run_id, lambda body: body['steps'].__setitem__(phase, result))
            return {'run_id': run_id, 'last_step': phase}
        return node

    def _response_schema(self) -> dict:
        properties = {'strategy': benchmark.strategy_schema(), 'summary': {'type': 'string'},
                      'hypotheses': {'type': 'array', 'items': {'type': 'string'}}, 'next_test': {'type': 'string'}}
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}

    def _step(self, run_id: str, step: str) -> dict:
        run = self.store.get(run_id)
        if run['config'].get('case_type') == 'ashare':
            from .ashare_research import run_step
            from .ashare_case import AShareCase
            if run_id not in self.case_instances:
                self.case_instances[run_id] = AShareCase(run['config'].get('case_config'))
            instance = self.case_instances[run_id]
            if digest(instance.manifest()) != digest(run['config']['case']):
                raise ValueError('真实数据或案例协议已变化；禁止混用旧运行。')
            return run_step(self, run_id, step, instance)
        steps = run['steps']
        if step == 'prepare':
            result = benchmark.evaluate(benchmark.baseline_strategy(), 'development')
            self.artifact(run_id, 'starting_evidence.json', result, '共同起点 · 开发期证据')
            return {'evidence': result}
        if step == 'meta_proposal':
            schema = {'type': 'object', 'properties': {key: {'type': 'string', 'maxLength': 6000} for key in
                       ('name', 'causal_hypothesis', 'research_instructions', 'expected_effect', 'risk')},
                      'required': ['name', 'causal_hypothesis', 'research_instructions', 'expected_effect', 'risk'],
                      'additionalProperties': False}
            prompt = ('你是元框架研究员。只改变研究指导方式，模型与effort、研究调用次数、评测和数据权限保持不变。'
                      '从执行证据提出一个可归因的小修改，指导应能迁移到其他策略，禁止抄写本题数值、具体策略参数或标准答案。'
                      '不要声称已证明架构进步。research_instructions限制在1200字以内。\n' + dumps({
                          'baseline_instructions': BASE_INSTRUCTIONS,
                          'baseline_initial': steps['baseline_initial'],
                          'baseline_refine': steps['baseline_refine']}))
            response = self._model(run_id, step, 'meta_designer', prompt, schema)
            if set(response) != set(schema['required']) or any(not isinstance(v, str) or not v.strip() for v in response.values()):
                raise ValueError('invalid architecture proposal')
            if len(response['research_instructions']) > 6000:
                raise ValueError('architecture instructions too long')
            response['architecture_hash'] = digest(response)
            response['parent_hash'] = digest(BASE_INSTRUCTIONS)
            self.artifact(run_id, 'candidate_architecture.json', response, '元研究提出的候选架构')
            return response
        if step in {'baseline_initial', 'baseline_refine', 'candidate_initial', 'candidate_refine'}:
            candidate = step.startswith('candidate')
            prefix = 'candidate' if candidate else 'baseline'
            instructions = steps['meta_proposal']['research_instructions'] if candidate else BASE_INSTRUCTIONS
            evidence = steps[prefix + '_initial'] if step.endswith('refine') else steps['prepare']
            prompt = ('你是量化研究agent。以下是受限DSL的合成数据联调，不是实盘盈利研究。'
                      '仅使用提供的开发期数据和证据。不要调用任何工具、读本地文件或联网。'
                      '给出一组合法参数作为本轮实验；可保持原参数，不能修改评测、预算或交易约束。'
                      'summary给出简短、可核查的决策说明；不要输出隐藏思维链。最终仅返回schema要求的JSON。\n'
                      + instructions + '\n' + dumps({'case': benchmark.development_packet(), 'evidence': evidence,
                                                       'stage': '冻结最终策略' if step.endswith('refine') else '提出初始策略'}))
            response = self._model(run_id, step, prefix + '_researcher', prompt, self._response_schema())
            self.store.event(run_id, 'tool', '提交策略给固定开发期回测器', role='evaluator', step=step,
                             data={'strategy': response['strategy'], 'split': 'development'})
            evaluation = benchmark.evaluate(response['strategy'], 'development')
            result = {'response': response, 'evaluation': evaluation,
                      'architecture_hash': digest(instructions), 'submission_hash': digest(response['strategy'])}
            self.artifact(run_id, step + '.json', result, PHASES[step] + ' · 产物与证据')
            self.store.event(run_id, 'tool', '开发期回测完成', role='evaluator', step=step, data=evaluation)
            return result
        if step == 'final_evaluation':
            frozen = {key: steps[key + '_refine']['response']['strategy'] for key in ('baseline', 'candidate')}
            self.artifact(run_id, 'frozen_submissions.json', frozen, '评测前冻结的两个提交')
            results = {key: benchmark.evaluate(strategy, 'final') for key, strategy in frozen.items()}
            self.artifact(run_id, 'final_evaluation.json', results, '程序独立评测 · 不回传研究agent')
            return results
        if step == 'finish':
            comparison = {'baseline': steps['final_evaluation']['baseline'],
                          'candidate': steps['final_evaluation']['candidate'],
                          'verdict': '仅完成合成案例联调；样本量不足，禁止自动晋升架构。',
                          'promotion': False, 'comparable': run['comparable'],
                          'scope': 'development_smoke_only',
                          'limitations': ['合成行情；无真实收益证明', '每架构仅一次研究，存在随机性',
                                          '候选架构参考了本题开发证据，不能据此证明跨任务泛化',
                                          '受限DSL与提示流程原型；未接入完整V2自由代码研究',
                                          '没有提供模型原始隐藏思维链']}
            comparison['score_delta'] = comparison['candidate']['score'] - comparison['baseline']['score']
            comparison['evidence_status'] = 'insufficient_trades' if not all(comparison[k]['eligible'] for k in ('baseline', 'candidate')) else 'smoke_only'
            comparison['usage_by_role'] = self.get(run_id)['usage_by_role']
            self.store.update(run_id, {'comparison': comparison})
            self.artifact(run_id, 'comparison.json', comparison, '比较结果与证据边界')
            self.artifact(run_id, 'calls.json', self.store.calls(run_id), '全部调用与token账本')
            return comparison
        raise ValueError(step)

    def _model(self, run_id: str, step: str, role: str, prompt: str, schema: dict) -> dict:
        self._gate(run_id)
        run = self.get(run_id)
        if run['interventions']:
            prompt += '\n人工补充（本次运行不再作为公平竞赛证据）：\n' + dumps(run['interventions'])
        prior = [c for c in run['calls'] if c['step'] == step]
        for call in prior:
            if call['status'] == 'completed' and call['prompt_hash'] == digest(prompt):
                try:
                    validate_response(call['receipt']['response'], schema)
                except (ValueError, KeyError, TypeError) as exc:
                    call.update(status='failed', error=f'invalid recovered response: {exc}')
                    self.store.save_call(call)
                    raise ValueError('已保存的模型产物未通过校验；允许明确重试，但不能继续复用。') from exc
                self.store.event(run_id, 'status', '复用已落盘的模型结果，没有重新调用。', step=step)
                return call['receipt']['response']
        if any(c['status'] in {'uncertain', 'running', 'failed'} for c in prior):
            raise ValueError('该步骤存在未知调用；禁止自动重试。')
        limits, usage = run['budget'], run['usage']
        reserved = usage['unknown_calls'] * limits['reserve_tokens_per_call']
        if len(run['calls']) >= limits['max_calls'] or len(prior) >= limits['max_attempts_per_step']:
            raise BudgetExceeded('达到调用/重试事故上限。')
        if usage['total_tokens'] + reserved + limits['reserve_tokens_per_call'] > limits['hard_tokens']:
            raise BudgetExceeded('剩余预算不足以预留下一次调用。')
        if role in {'baseline_researcher', 'candidate_researcher'}:
            architecture_calls = [c for c in run['calls'] if c['role'] == role]
            architecture_tokens = sum(sum((c.get('usage') or {}).get(k) or 0 for k in ('input_tokens', 'output_tokens')) for c in architecture_calls)
            architecture_unknown = sum(1 for c in architecture_calls if not c.get('usage') or any(c['usage'].get(k) is None for k in ('input_tokens', 'output_tokens')))
            if architecture_tokens + (architecture_unknown + 1) * limits['reserve_tokens_per_call'] > limits['max_architecture_tokens']:
                raise BudgetExceeded('达到该架构的统一研究预算上限。')
        if usage['total_tokens'] >= limits['soft_tokens']:
            self.store.event(run_id, 'budget', '已达到软提醒预算，仍允许有意义的研究继续。', data=usage)
        call_id = f'{step}-{len(prior) + 1}'
        folder = self.root / run_id / 'calls' / call_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        call = {'id': run_id + '/' + call_id, 'run_id': run_id, 'step': step, 'role': role,
                'attempt': len(prior) + 1, 'status': 'running', 'started_at': now(),
                'model': MODEL, 'effort': EFFORT, 'prompt_hash': digest(prompt), 'usage': None,
                'workdir': str(folder), 'receipt': None}
        self.store.save_call(call)
        self.store.event(run_id, 'interaction', '发送研究任务', role=role, step=step, data={'prompt': prompt, 'schema': schema})
        call_started = time.monotonic()
        stop_reason = {'kind': None}
        def event(item):
            self.store.event(run_id, item.get('kind', 'status'), item.get('text', ''), role=role, step=step, data=item.get('data') or {})
            if item.get('kind') == 'usage':
                data = item.get('data') or {}
                incoming = data.get('usage', data)
                if 'input_tokens' in incoming or 'output_tokens' in incoming:
                    call['usage'] = incoming
                    self.store.save_call(call)
        def cancelled():
            body = self.get(run_id)
            if body['cancel_requested']:
                stop_reason['kind'] = 'user_cancel'
            elif body['elapsed_seconds'] >= limits['max_runtime_seconds']:
                stop_reason['kind'] = 'runtime_budget'
            elif time.monotonic() - call_started >= limits['max_call_seconds']:
                stop_reason['kind'] = 'call_timeout'
            elif body['usage']['total_tokens'] >= limits['hard_tokens']:
                stop_reason['kind'] = 'token_budget'
            return stop_reason['kind'] is not None
        try:
            if run['mode'] == 'fixture':
                event({'kind': 'status', 'text': '离线夹具响应：验证流程，不调用模型，也不生成思考摘要。'})
                if run['config'].get('case_type') == 'ashare':
                    from .ashare_research import fixture_response
                    response = fixture_response(step, schema)
                elif step == 'meta_proposal':
                    response = {'name': '证据驱动诊断', 'causal_hypothesis': '明确反证可减少无效改动',
                                'research_instructions': '比较竞争解释，以开发期子期证据决定下一实验。',
                                'expected_effect': '更清楚地记录证据', 'risk': '可能增加推理消耗'}
                else:
                    response = {'strategy': benchmark.baseline_strategy(), 'summary': '夹具保持默认参数。',
                                'hypotheses': ['仅验证数据与执行链路'], 'next_test': '需要真实模型与真实行情研究'}
                receipt = {'response': response, 'usage': {k: 0 for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')},
                           'duration_seconds': 0, 'model': None, 'effort': None, 'model_verified': False,
                           'verification': 'fixture_no_model'}
            else:
                if self.gateway is None:
                    from .codex_gateway import CodexGateway
                    self.gateway = CodexGateway(timeout_seconds=limits['max_call_seconds'])
                elif not self._gateway_injected:
                    self.gateway.timeout_seconds = limits['max_call_seconds']
                receipt = self.gateway.run(prompt=prompt, schema=schema, workdir=folder, on_event=event, cancelled=cancelled)
                if receipt.get('model') != MODEL or receipt.get('effort') != EFFORT:
                    raise ValueError('model policy mismatch; no fallback is permitted')
            receipt_path = folder / 'receipt.json'
            temporary = receipt_path.with_suffix('.tmp')
            temporary.write_text(dumps(receipt), encoding='utf-8')
            os.replace(temporary, receipt_path)
            call.update(usage=receipt.get('usage'), receipt=receipt)
            validate_response(receipt['response'], schema)
            call.update(status='completed', ended_at=now(), usage=receipt.get('usage'), receipt=receipt)
            self.store.save_call(call)
            self.store.event(run_id, 'usage', '模型调用已完成，账本已更新。', role=role, step=step, data={'usage': receipt.get('usage'), 'verification': receipt.get('verification')})
            return receipt['response']
        except BaseException as exc:
            if getattr(exc, 'usage', None):
                call['usage'] = exc.usage
            call.update(status='failed', ended_at=now(), error=f'{type(exc).__name__}: {exc}')
            call['stop_reason'] = stop_reason['kind']
            self.store.save_call(call)
            if stop_reason['kind'] == 'user_cancel':
                raise CancelRequested() from exc
            if stop_reason['kind'] or type(exc).__name__ == 'GatewayTimeout':
                raise BudgetExceeded('事故风控停止：' + (stop_reason['kind'] or 'call_timeout')) from exc
            raise
        finally:
            trace_name = call_id + '_trace.zip'
            trace_path = self.root / run_id / trace_name
            with zipfile.ZipFile(trace_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in folder.iterdir():
                    if path.is_file():
                        archive.write(path, path.name)
            entry = {'name': trace_name, 'label': PHASES.get(step, step) + ' · 原始交互记录', 'type': 'application/zip', 'size': trace_path.stat().st_size}
            self.store.update(run_id, lambda body: body['artifacts'].append(entry))
            self.store.event(run_id, 'artifact', entry['label'], step=step, data=entry)

    def close(self):
        if self.worker and self.worker.is_alive():
            if self.active:
                self.store.update(self.active, {'cancel_requested': True})
            self.worker.join(timeout=15)
        if not self.worker or not self.worker.is_alive():
            self.checkpoint_db.close()
            self.store.close()

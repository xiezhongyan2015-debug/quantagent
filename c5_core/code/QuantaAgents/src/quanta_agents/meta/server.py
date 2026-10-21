from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .runtime import Engine
from .store import dumps


def _creation_options(engine: Engine, body: dict) -> dict:
    """Resolve registered controls and same-ledger proposal provenance only."""
    research_fields = {'task', 'research_calls', 'frozen_candidate_run_id',
                       'research_order', 'evaluation_split', 'only_architecture'}
    if set(body) - {'mode', 'case', 'request_key', *research_fields}:
        raise ValueError('unknown run creation fields')
    mode, case = body.get('mode', 'live'), body.get('case', 'synthetic')
    if type(mode) is not str or mode not in {'live', 'fixture'}:
        raise ValueError('mode must be live or fixture')
    if type(case) is not str or case not in {'synthetic', 'ashare'}:
        raise ValueError('unknown registered case')
    options = {'mode': mode, 'case': case}
    if 'request_key' in body:
        request_key = body['request_key']
        if type(request_key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', request_key):
            raise ValueError('request_key must contain 1..160 letters, digits, underscores or hyphens')
        options['request_key'] = request_key
    if case != 'ashare':
        if research_fields & set(body):
            raise ValueError('A-share research controls require case=ashare')
        return options

    from .ashare_tasks import TASKS
    task = body.get('task', 'price_repair')
    if type(task) is not str or task not in TASKS:
        raise ValueError('unknown registered A-share task')
    calls = body.get('research_calls', 6)
    if type(calls) is not int or not 6 <= calls <= 18:
        raise ValueError('research_calls must be an integer from 6 to 18')
    order = body.get('research_order', ['baseline', 'candidate'])
    if type(order) is not list or order not in (['baseline', 'candidate'], ['candidate', 'baseline']):
        raise ValueError('research_order must contain baseline and candidate exactly once')
    split = body.get('evaluation_split', 'confirmation')
    if type(split) is not str or split not in {'development', 'confirmation'}:
        raise ValueError('evaluation_split must be development or confirmation')
    arm = body.get('only_architecture')
    if arm is not None and (type(arm) is not str or arm not in {'baseline', 'candidate'}):
        raise ValueError('only_architecture must be baseline, candidate or null')
    research = {'calls_per_architecture': calls, 'research_order': list(order),
                'evaluation_split': split, 'only_architecture': arm}
    if 'frozen_candidate_run_id' in body:
        source_id = body['frozen_candidate_run_id']
        if type(source_id) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', source_id):
            raise ValueError('frozen_candidate_run_id must be an existing local run ID, not a path')
        source = engine.store.get(source_id)
        source_steps = source.get('steps')
        proposal = source_steps.get('meta_proposal') if isinstance(source_steps, dict) else None
        if type(proposal) is not dict or any(type(proposal.get(key)) is not str or not proposal[key].strip()
                                             for key in ('name', 'research_instructions')):
            raise ValueError('source run has no completed research proposal')
        # Frozen runs wrap proposals with execution metadata. Export only the
        # research-definition fields; keep the direct source run as provenance.
        proposal_fields = {'name', 'research_instructions', 'causal_hypothesis', 'expected_effect',
                           'risk', 'assessment', 'next_step', 'parent_hash', 'architecture_hash',
                           'change_scope', 'evaluation_access', 'source_case_id', 'source_artifact_sha256'}
        frozen = {key: deepcopy(value) for key, value in proposal.items() if key in proposal_fields}
        frozen['source_run_id'] = source_id
        research['frozen_candidate'] = frozen
    if (order[0] == 'candidate' or arm == 'candidate') and not research.get('frozen_candidate'):
        raise ValueError('candidate-first or candidate-only research requires an existing frozen proposal')
    options.update(case_config={'task': task}, research_config=research)
    return options


def _batch_summaries(root: Path) -> list[dict]:
    """Read bounded, atomic batch reports; never load plans, prompts or entries."""
    batch_root = root / 'batches'
    if not batch_root.is_dir() or batch_root.resolve().parent != root.resolve():
        return []

    def number(value):
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

    def text(value, limit=300):
        return value[:limit] if isinstance(value, str) else None

    summaries = []
    for folder in sorted(batch_root.iterdir(), key=lambda p: p.name, reverse=True)[:200]:
        if not folder.is_dir() or folder.resolve().parent != batch_root.resolve():
            continue
        report_path = folder / 'report.json'
        try:
            if report_path.resolve().parent != folder.resolve() or report_path.stat().st_size > 2_000_000:
                raise ValueError('invalid report location or size')
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if not isinstance(report, dict) or report.get('batch_id') != folder.name:
                raise ValueError('invalid batch identity')
            counts = report.get('counts') if isinstance(report.get('counts'), dict) else {}
            usage = report.get('usage') if isinstance(report.get('usage'), dict) else {}
            limits = report.get('limits') if isinstance(report.get('limits'), dict) else {}
            summaries.append({
                'batch_id': folder.name, 'status': text(report.get('status')) or 'unknown',
                'updated_at': text(report.get('updated_at')), 'scope': text(report.get('scope')),
                'counts': {key: number(counts.get(key)) for key in
                           ('planned', 'completed', 'failed', 'cancelled', 'not_started', 'attention', 'active')},
                'usage': {**{key: number(usage.get(key)) for key in
                             ('known_tokens', 'unknown_entries', 'reserved_tokens', 'accident_token_limit')},
                          'is_partial': usage.get('is_partial') if type(usage.get('is_partial')) is bool else None},
                'limits': {key: number(limits.get(key)) for key in
                           ('hard_tokens', 'max_wall_seconds', 'reserve_tokens_per_entry')},
                'elapsed_seconds': number(report.get('elapsed_seconds')),
                'current_entry_id': text(report.get('current_entry_id')),
                'current_run_id': text(report.get('current_run_id')),
                'stop_reason': text(report.get('stop_reason'), 1000),
                'promotion': report.get('promotion') if type(report.get('promotion')) is bool else None,
                'formal_target_success': report.get('formal_target_success') if type(report.get('formal_target_success')) is bool else None,
            })
        except (OSError, ValueError, TypeError, OverflowError):
            summaries.append({'batch_id': folder.name, 'status': 'unavailable',
                              'report_error': '批次报告暂不可读；计数和用量未知。'})
    return summaries


class StateLock:
    """Prevent two servers from owning the same worker/checkpoint database."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.file = (root / '.worker.lock').open('a+b')
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b'1')
                self.file.flush()
            self.file.seek(0)
            import os
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('这个运行目录已有服务占用，禁止重复启动 worker。')

    def close(self):
        self.file.close()


def make_server(engine: Engine, port: int = 8767) -> ThreadingHTTPServer:
    loaded_source_hash = engine.source_manifest()['hash']

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format, *args):
            # Polling must not fill the disk with access logs.
            pass

        def send(self, status: int, body, content_type='application/json; charset=utf-8', download=None):
            payload = body if isinstance(body, bytes) else dumps(body).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def trusted_request(self):
            port_value = self.server.server_port
            hosts = {f'192.0.2.10:{port_value}', f'localhost:{port_value}'}
            if self.headers.get('Host') not in hosts:
                self.send(403, {'error': 'local host only'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + h for h in hosts}:
                self.send(403, {'error': 'cross-origin requests are disabled'})
                return False
            return True

        def do_GET(self):
            if not self.trusted_request():
                return
            parsed = urlsplit(self.path)
            parts = [unquote(p) for p in parsed.path.split('/') if p]
            try:
                if not parts or parts == ['index.html']:
                    self.send(200, (Path(__file__).parent / 'web' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
                elif parts == ['api', 'health']:
                    self.send(200, {'ok': True, 'model': 'gpt-6-astra', 'effort': 'xhigh',
                                    'active_run': engine.active, 'source_hash': loaded_source_hash})
                elif parts == ['api', 'batches']:
                    self.send(200, {'batches': _batch_summaries(engine.root)})
                elif parts == ['api', 'runs']:
                    runs = []
                    for item in engine.store.runs():
                        run = engine.get(item['id'])
                        summary = {k: v for k, v in run.items() if k not in {'steps', 'config', 'calls', 'comparison', 'research', 'self_checks'}}
                        config = run.get('config', {})
                        summary.update(task_key=config.get('case', {}).get('task_key', config.get('case_config', {}).get('task')),
                                       evaluation_split=config.get('evaluation_split'),
                                       only_architecture=config.get('only_architecture', config.get('research_config', {}).get('only_architecture')))
                        runs.append(summary)
                    self.send(200, {'runs': runs})
                elif len(parts) >= 3 and parts[:2] == ['api', 'runs']:
                    run_id = parts[2]
                    run = engine.get(run_id)
                    if len(parts) == 3:
                        self.send(200, run)
                    elif parts[3:] == ['events']:
                        after = int(parse_qs(parsed.query).get('after', ['0'])[0])
                        events = engine.store.events(run_id, after=max(0, after))
                        self.send(200, {'events': events, 'last_id': events[-1]['id'] if events else after})
                    elif len(parts) == 5 and parts[3] == 'artifacts':
                        name = parts[4]
                        if name not in {a['name'] for a in run['artifacts']} or Path(name).name != name:
                            raise KeyError(name)
                        path = (engine.root / run_id / name).resolve()
                        if path.parent != (engine.root / run_id).resolve():
                            raise KeyError(name)
                        mime = 'application/zip' if path.suffix == '.zip' else 'application/json; charset=utf-8'
                        self.send(200, path.read_bytes(), mime, name)
                    else:
                        raise KeyError(self.path)
                else:
                    raise KeyError(self.path)
            except (KeyError, FileNotFoundError):
                self.send(404, {'error': 'not found'})
            except ValueError as exc:
                self.send(400, {'error': str(exc)})

        def do_POST(self):
            if not self.trusted_request():
                return
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('application/json required')
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 32_000:
                    raise ValueError('invalid request length')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('object required')
                parts = [unquote(p) for p in urlsplit(self.path).path.split('/') if p]
                if parts == ['api', 'runs']:
                    if engine.source_manifest()['hash'] != loaded_source_hash:
                        raise RuntimeError('源码已变化，请重启服务后再创建运行；当前服务仍使用启动时加载的模块。')
                    run_id = engine.create(**_creation_options(engine, body))
                    self.send(201, {'run_id': run_id})
                elif len(parts) == 4 and parts[:2] == ['api', 'runs'] and parts[3] == 'control':
                    action, text = body.get('action', ''), body.get('text', '')
                    if not isinstance(action, str) or not isinstance(text, str):
                        raise ValueError('action/text must be strings')
                    engine.control(parts[2], action, text)
                    self.send(200, {'ok': True})
                else:
                    raise KeyError(self.path)
            except KeyError:
                self.send(404, {'error': 'not found'})
            except (ValueError, TypeError) as exc:
                self.send(400, {'error': str(exc)})
            except RuntimeError as exc:
                self.send(409, {'error': str(exc)})

    server = ThreadingHTTPServer(('192.0.2.10', port), Handler)
    server.daemon_threads = True
    return server

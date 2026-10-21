"""Persist one Codex account session alongside the immutable gateway artifacts.

The rollout attests local runtime configuration, not provider-side computation.
Original JSONL is never injected with synthetic identity events. Missing capture
keeps a completed, billable response recoverable and prevents tool application.
"""
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from .kernel import module

base = module('meta.codex_gateway')
CAPTURE_VERSION = 'codex_session_v1'
MODEL, EFFORT = base.MODEL, base.EFFORT
_strict_json, _validate_output = base._strict_json, base._validate_output
GatewayError = base.GatewayError
SESSION = 'runtime_session.jsonl'


def _require(condition, message):
    if not condition:
        raise GatewayError(message)


def verify_runtime_session(raw, receipt, prompt):
    """Verify original runtime fields plus one-turn input/output lineage."""
    rows = [_strict_json(line) for line in raw.decode('utf-8').splitlines()]
    _require(all(type(row) is dict for row in rows), 'Invalid runtime session rows')
    _require(all(type(row.get('payload')) is dict for row in rows
                 if row.get('type') in ('session_meta', 'turn_context', 'event_msg', 'response_item')),
             'Invalid runtime session payload')
    meta = [r['payload'] for r in rows if r.get('type') == 'session_meta']
    contexts = [r['payload'] for r in rows if r.get('type') == 'turn_context']
    events = [r['payload'] for r in rows if r.get('type') == 'event_msg']
    starts = [r for r in events if r.get('type') == 'task_started']
    ends = [r for r in events if r.get('type') == 'task_complete']
    _require(len(meta) == len(contexts) == len(starts) == len(ends) == 1,
             'Runtime capture needs exactly one session and completed turn')
    context, start, end = contexts[0], starts[0], ends[0]
    thread_id = receipt['identity_verification']['provider_ids']['thread_id']
    _require(meta[0].get('id') == thread_id and thread_id, 'Runtime thread binding mismatch')
    turn_id = context.get('turn_id')
    _require(turn_id and turn_id == start.get('turn_id') == end.get('turn_id'),
             'Runtime turn binding mismatch')
    stdout_turn = receipt['identity_verification']['provider_ids'].get('turn_id')
    _require(stdout_turn is None or stdout_turn == turn_id, 'Runtime and stdout turn IDs differ')
    _require(context.get('model') == MODEL and context.get('effort') == EFFORT,
             'Runtime model or effort mismatch')
    _require(meta[0].get('model_provider') == 'openai', 'Runtime provider configuration mismatch')
    messages = [r['payload'] for r in rows if r.get('type') == 'response_item'
                and r.get('payload', {}).get('type') == 'message']
    users = [r for r in messages if r.get('role') == 'user']
    def text_of(message):
        content = message.get('content')
        _require(type(content) is list and all(type(item) is dict
                 and type(item.get('text')) is str for item in content),
                 'Invalid runtime message content')
        return ''.join(item.get('text', '') for item in message.get('content', []))
    # CLI may write one environment-context user message before the submitted input.
    _require(sum(text_of(r) == prompt for r in users) == 1,
             'Runtime prompt does not bind to the invocation')
    context_position = next(i for i, row in enumerate(rows) if row.get('type') == 'turn_context')
    extra_users = [(i, text_of(row['payload'])) for i, row in enumerate(rows)
                   if row.get('type') == 'response_item' and row['payload'].get('type') == 'message'
                   and row['payload'].get('role') == 'user' and text_of(row['payload']) != prompt]
    _require(len(extra_users) <= 1 and all(i < context_position
                 and text.strip().startswith('<environment_context>')
                 and text.strip().endswith('</environment_context>') for i, text in extra_users),
             'Unexpected extra user input in runtime session')
    finals = [r for r in messages if r.get('role') == 'assistant'
              and r.get('phase') in ('final', 'final_answer')]
    _require(len(finals) == 1, 'Runtime final response is missing or ambiguous')
    _require(base._canonical(_strict_json(text_of(finals[0]))) == base._canonical(receipt['response'])
             and base._canonical(_strict_json(end.get('last_agent_message') or '')) == base._canonical(receipt['response']),
             'Runtime completed output mismatch')
    _require(not any(r.get('type') in ('turn_aborted', 'model_rerouted') for r in events),
             'Runtime recorded an abort or model reroute')
    return {'verified': True, 'level': 'local_runtime_configuration',
        'model': context['model'], 'effort': context['effort'],
        'thread_id': thread_id, 'turn_id': turn_id, 'model_provider': meta[0]['model_provider'],
        'cli_version': meta[0].get('cli_version'), 'artifact': SESSION,
        'provider_request_binding_verified': False,
        'scope': 'Original local runtime configuration bound to one input and completed output; not provider-side routing or compute attestation.'}


def capture_saved_session(workdir, receipt):
    """Saved-only recovery; exact new thread suffix, no account/prompt-wide scan."""
    folder = Path(workdir).resolve()
    destination = folder / SESSION
    if destination.exists():
        return
    thread_id = receipt['identity_verification']['provider_ids']['thread_id']
    _require(isinstance(thread_id, str) and str(UUID(thread_id)) == thread_id,
             'Missing canonical thread ID for saved session capture')
    codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    matches = list((codex_home / 'sessions').glob(f'*/*/*/*-{thread_id}.jsonl'))
    _require(len(matches) == 1, 'Exactly one persisted runtime session is required')
    _require(matches[0].stat().st_size <= 32 * 1024 * 1024, 'Runtime session exceeds capture limit')
    raw = matches[0].read_bytes()
    prompt = (folder / 'prompt.txt').read_text(encoding='utf-8')
    verify_runtime_session(raw, receipt, prompt)
    with destination.open('xb') as stream:
        stream.write(raw)


def verify_saved_completion(workdir, *, expected_artifact_sha256=None, **kwargs):
    expected = dict(expected_artifact_sha256 or {})
    session_hash = expected.pop(SESSION, None)
    receipt = base.verify_saved_completion(workdir, expected_artifact_sha256=expected, **kwargs)
    receipt['runtime_identity'] = {'verified': False, 'level': 'not_captured',
        'provider_request_binding_verified': False}
    path = Path(workdir) / SESSION
    if not path.is_file():
        _require(session_hash is None, 'Previously captured runtime session is missing')
        return receipt
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    _require(session_hash is None or session_hash == actual, 'Runtime session artifact hash mismatch')
    receipt['runtime_identity'] = verify_runtime_session(
        raw, receipt, (Path(workdir) / 'prompt.txt').read_text(encoding='utf-8'))
    receipt['artifact_sha256'][SESSION] = actual
    # Preserve the old field, whose source is the original stdout event stream.
    receipt['verification'] += ' Local runtime model/effort confirmed separately from the persisted session.'
    return receipt


class CodexGateway(base.CodexGateway):
    def _command(self, schema_path, response_path, feature_flags):
        command = super()._command(schema_path, response_path, feature_flags)
        command.remove('--ephemeral')
        return command

    def run(self, **kwargs):
        receipt = super().run(**kwargs)
        try:
            capture_saved_session(kwargs['workdir'], receipt)
        except (ValueError, OSError, KeyError, GatewayError) as exc:
            # A capture defect is not an unknown bill or a reason to repeat inference.
            kwargs['on_event']({'kind': 'warning', 'text': str(exc),
                                'data': {'completed_response_preserved': True}})
        return verify_saved_completion(kwargs['workdir'])

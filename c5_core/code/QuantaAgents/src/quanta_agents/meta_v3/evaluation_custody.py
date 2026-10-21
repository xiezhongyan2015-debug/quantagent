"""One-consumption controller book, currently wired to a saved-account audit.

This is not an OS sandbox or formal source authorization. The formal release
entry remains unavailable until a specific source verifier and evaluator exist.
The only live backend reads an explicitly bound, already exposed old account;
its passing receipt cannot change an original abstention or certify execution.
"""
from contextlib import contextmanager, closing
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .kernel import ROOT
from .ledger import digest, need, serial
from .evaluation_snapshot import closed_study_snapshot

VERSION = 'evaluation_consumption_custody_v1'
KIND = 'independent_saved_account_audit'
REGISTRY = ROOT / 'experiment_traces/meta_framework_v4/evaluation_custody.sqlite3'
BACKEND = 'saved_v1_frozen_terms_audit_v1'
SUBJECT = 'v4s1/v1/research_011_c7700a001b27'
MAX_JSON = 32 * 1024**2


def _bounded(value):
    value = serial(value)
    need(len(value.encode('utf-8')) <= MAX_JSON, 'bounded custody payload required')
    return value


def _backend():
    path = ROOT / 'scripts/audit_frozen_account_terms.py'
    spec = importlib.util.spec_from_file_location('_quanta_frozen_terms_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _code_pins():
    pins = _backend().code_pins()
    paths = [Path(__file__), ROOT / 'scripts/run_evaluation_custody.py']
    paths += [ROOT / 'src/quanta_agents/meta_v3' / (name + '.py') for name in
              ('evaluation_snapshot', 'study_registry', 'report_arguments')]
    pins.update({path.resolve().relative_to(ROOT).as_posix():
                 hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    return pins


def _exposure(manifest):
    return digest({'kind': manifest['kind'], 'study_id': manifest['study_id'],
                   'subject': manifest['audit_subject']})


def _inputs_bound(inputs):
    need(type(inputs) is dict and inputs.get('study_id') == 'v4s1'
         and inputs.get('trial_id') == 'v1' and inputs.get('task_id') == 'research'
         and inputs.get('call_id') == SUBJECT.rsplit('/', 1)[1]
         and inputs.get('original_final_outcome') == 'abstain'
         and inputs.get('selection_scope') == 'explicit_old_candidate_audit_not_final_selection'
         and inputs.get('formal_target_success') is False, 'fixed exposed audit input scope drift')


@contextmanager
def _db(create=False):
    if create:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    else:
        need(REGISTRY.is_file(), 'canonical evaluation book missing')
    db = sqlite3.connect(REGISTRY, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        if create:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS evaluations(
                    id TEXT PRIMARY KEY, exposure_key TEXT NOT NULL UNIQUE,
                    manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                    state TEXT NOT NULL, token TEXT, consumed_at REAL,
                    receipt TEXT, receipt_hash TEXT);
                CREATE TABLE IF NOT EXISTS evaluation_events(
                    evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
                    ordinal INTEGER NOT NULL, body TEXT NOT NULL, previous_hash TEXT,
                    event_hash TEXT NOT NULL, PRIMARY KEY(evaluation_id,ordinal));
            ''')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _event(db, identity, body):
    prior = db.execute('SELECT ordinal,event_hash FROM evaluation_events WHERE evaluation_id=? '
                       'ORDER BY ordinal DESC LIMIT 1', (identity,)).fetchone()
    ordinal, previous = (prior['ordinal'] + 1, prior['event_hash']) if prior else (1, None)
    event = {'ordinal': ordinal, 'previous_hash': previous, 'body': body}
    db.execute('INSERT INTO evaluation_events VALUES(?,?,?,?,?)',
               (identity, ordinal, _bounded(body), previous, digest(event)))


def _read(db, identity):
    row = db.execute('SELECT * FROM evaluations WHERE id=?', (identity,)).fetchone()
    need(row is not None, 'unknown evaluation identity')
    manifest = json.loads(row['manifest'])
    need(digest(manifest) == row['manifest_hash'], 'frozen evaluation manifest drift')
    need(manifest['version'] == VERSION and manifest['kind'] == KIND
         and manifest['study_id'] == 'v4s1' and manifest['audit_subject'] == SUBJECT
         and manifest['backend_id'] == BACKEND and manifest['formal_target_success'] is False,
         'frozen evaluation route drift')
    need(_exposure(manifest) == row['exposure_key'], 'original audit exposure binding drift')
    need(digest(manifest['audit_inputs']) == manifest['audit_inputs_hash']
         and digest(manifest['study_snapshot']) == manifest['study_snapshot_hash'],
         'frozen evaluation input binding drift')
    _inputs_bound(manifest['audit_inputs'])
    events = []
    previous = None
    for item in db.execute('SELECT * FROM evaluation_events WHERE evaluation_id=? ORDER BY ordinal', (identity,)):
        body = json.loads(item['body'])
        event = {'ordinal': len(events) + 1, 'previous_hash': previous, 'body': body}
        need(item['ordinal'] == event['ordinal'] and item['previous_hash'] == previous
             and item['event_hash'] == digest(event), 'evaluation event chain drift')
        events.append(body); previous = item['event_hash']
    need(events and events[0] == {'action': 'freeze', 'manifest_hash': row['manifest_hash']}, 'missing original freeze')
    need(row['state'] in ('FROZEN', 'CONSUMED', 'COMPLETE', 'INCOMPLETE_TERMINAL'), 'unknown evaluation state')
    need(len(events) == (1 if row['state'] == 'FROZEN' else 2 if row['state'] == 'CONSUMED' else 3), 'state/event mismatch')
    if row['state'] == 'FROZEN':
        need(row['token'] is None and row['consumed_at'] is None, 'unconsumed row has a dispatch')
    else:
        need(type(row['token']) is str and len(row['token']) == 32
             and type(row['consumed_at']) in (int, float), 'missing consumption identity')
        need(events[1] == {'action': 'consume', 'token': row['token'], 'consumed_at': row['consumed_at']}, 'consumption binding drift')
    receipt = json.loads(row['receipt']) if row['receipt'] else None
    if len(events) == 3:
        need(receipt is not None and digest(receipt) == row['receipt_hash']
             and receipt['manifest_hash'] == row['manifest_hash'] and receipt['token'] == row['token']
             and receipt['state'] == row['state'] and events[2] ==
             {'action': 'settle', 'state': row['state'], 'token': row['token'], 'receipt_hash': row['receipt_hash']},
             'saved evaluation receipt drift')
    else:
        need(receipt is None and row['receipt_hash'] is None, 'unsettled evaluation has a result')
    return {'evaluation_id': identity, 'manifest_hash': row['manifest_hash'], 'manifest': manifest,
            'state': row['state'], 'token': row['token'], 'receipt': receipt,
            'event_head': previous, 'formal_target_success': False}


def inspect(identity):
    """Read-only inspection never re-enters a backend after an interruption."""
    need(REGISTRY.is_file(), 'canonical evaluation book missing')
    with closing(sqlite3.connect(REGISTRY.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        return _read(db, identity)


def freeze(identity, *, kind=KIND, study_id='v4s1'):
    # Reject before inspecting any source, importing a reader, or creating a DB.
    need(kind == KIND, 'formal_final_release unavailable: trusted formal source verifier and evaluator missing')
    need(type(identity) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity), 'bounded evaluation identity')
    need(study_id == 'v4s1', 'saved backend has only this explicitly exposed study scope')
    snapshot = closed_study_snapshot(study_id)
    inputs = _backend().build_spec()
    _inputs_bound(inputs)
    manifest = {'version': VERSION, 'kind': kind, 'study_id': study_id,
        'study_snapshot': snapshot, 'study_snapshot_hash': digest(snapshot),
        'audit_subject': SUBJECT, 'audit_inputs': inputs, 'audit_inputs_hash': digest(inputs),
        'backend_id': BACKEND, 'backend_code_pins': _code_pins(),
        'limits': {'backend_dispatches': 1, 'maximum_custody_json_bytes': MAX_JSON},
        'formal_target_success': False,
        'selection_rule': 'audit_inputs are independent audit objects; original slot final selections remain unchanged'}
    body = _bounded(manifest)
    # Directory, display label, implementation edits and changed audit budgets
    # cannot buy another consumption of this same original saved subject.
    exposure = _exposure(manifest)
    with _db(create=True) as db:
        for existing in db.execute('SELECT id FROM evaluations').fetchall():
            _read(db, existing['id'])
        need(db.execute('SELECT 1 FROM evaluations WHERE id=? OR exposure_key=?', (identity, exposure)).fetchone() is None,
             'evaluation or equivalent exposed audit subject already registered')
        need(digest(closed_study_snapshot(study_id)) == manifest['study_snapshot_hash'], 'original study changed before freeze')
        db.execute('INSERT INTO evaluations VALUES(?,?,?,?,?,?,?,?,?)',
                   (identity, exposure, body, digest(manifest), 'FROZEN', None, None, None, None))
        _event(db, identity, {'action': 'freeze', 'manifest_hash': digest(manifest)})
    return inspect(identity)


def run(identity):
    """Consume and commit before the one allowed backend invocation.

    A repeated command returns the existing state/receipt. CONSUMED without a
    receipt means unknown dispatch outcome and never authorizes another read.
    Ordinary errors get a terminal receipt; abrupt termination stays consumed.
    """
    with _db() as db:
        current = _read(db, identity)
        if current['state'] != 'FROZEN':
            return current
        manifest = current['manifest']
        need(manifest['kind'] == KIND and manifest['backend_id'] == BACKEND, 'unsupported release route')
        need(_code_pins() == manifest['backend_code_pins'], 'frozen evaluator implementation changed')
        need(digest(closed_study_snapshot(manifest['study_id'])) == manifest['study_snapshot_hash'], 'frozen original slot/selection drift')
        need(digest(_backend().build_spec()) == manifest['audit_inputs_hash'], 'frozen audit input metadata drift')
        token, started = uuid.uuid4().hex, time.time()
        db.execute("UPDATE evaluations SET state='CONSUMED',token=?,consumed_at=? WHERE id=? AND state='FROZEN'",
                   (token, started, identity))
        _event(db, identity, {'action': 'consume', 'token': token, 'consumed_at': started})
    # No backend execution appears above this committed transaction.
    receipt = {'manifest_hash': current['manifest_hash'], 'token': token,
               'audit_inputs_hash': manifest['audit_inputs_hash'], 'kind': KIND,
               'original_selections_changed': False, 'new_research_opportunities': 0,
               'formal_target_success': False, 'financial_accepted': False}
    try:
        result = _backend().audit_saved_candidate(manifest['audit_inputs'])
        need(type(result) is dict and result.get('formal_target_success') is False,
             'saved audit cannot grant formal acceptance')
        _bounded(result)
        need(_code_pins() == manifest['backend_code_pins'], 'evaluator source changed during audit')
        need(digest(closed_study_snapshot(manifest['study_id'])) == manifest['study_snapshot_hash'], 'original study changed during audit')
        receipt.update(state='COMPLETE', backend_result=result, error=None)
    except Exception as exc:
        receipt.update(state='INCOMPLETE_TERMINAL', backend_result=None,
                       error={'type': type(exc).__name__, 'message': str(exc)[:4000]})
    receipt['observed_wall_seconds'] = time.time() - started
    with _db() as db:
        saved = _read(db, identity)
        need(saved['state'] == 'CONSUMED' and saved['token'] == token, 'original consumption no longer unsettled')
        db.execute('UPDATE evaluations SET state=?,receipt=?,receipt_hash=? WHERE id=?',
                   (receipt['state'], _bounded(receipt), digest(receipt), identity))
        _event(db, identity, {'action': 'settle', 'state': receipt['state'], 'token': token, 'receipt_hash': digest(receipt)})
    return inspect(identity)

"""Original controller measurement receipts; never reexecute to infer a cost."""
import hashlib
import json
from pathlib import Path
import re

from .ledger import digest, need, serial
from . import study_registry as sr

VERSION = 'original_tool_application_measurement_v1'
IDENTITY_FIELDS = ('study_id','trial_id','id','task_id','action','request_hash','quote','started_at')


def protocol(db, study_id, trial_id, *, admission=False):
    _,_,spec=sr._trial(db,study_id,trial_id)
    if 'tool_budget' not in spec: return None
    version=spec.get('measurement_protocol')
    need(version in (None,VERSION), 'unknown frozen measurement protocol')
    if admission:
        need(version==VERSION, 'legacy measurement scope is read-only; no automatic protocol conversion')
    if version==VERSION:
        need(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_measurements'").fetchone() is not None,
             'canonical measurement journal missing')
    return version


def validate_trial(db, study_id, trial_id, *, admission=False):
    _,_,spec=sr._trial(db,study_id,trial_id)
    if 'tool_budget' not in spec: return None
    version=protocol(db,study_id,trial_id,admission=admission)
    for row in db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=?',(study_id,trial_id)):
        saved(db,row)
    return version


def identity(db, row):
    value = {k:row[k] for k in IDENTITY_FIELDS}
    if type(value['quote']) is str: value['quote']=json.loads(value['quote'])
    trial = db.execute('SELECT stage_plan_hash FROM trials WHERE study_id=? AND id=?',
        (row['study_id'],row['trial_id'])).fetchone()
    model = db.execute('SELECT proof_hash FROM calls WHERE study_id=? AND trial_id=? AND id=?',
        (row['study_id'],row['trial_id'],row['id'])).fetchone()
    need(trial is not None and model is not None and model['proof_hash'] is not None, 'measurement model binding missing')
    from .study_descendants import call_stage_hash
    return value|{'stage_plan_hash':call_stage_hash(db,row,trial['stage_plan_hash']),
                  'model_proof_hash':model['proof_hash']}


def inventory(root, paths):
    root = Path(root).resolve(); rows=[]
    for base in paths:
        if not base.exists(): continue
        for p in sorted(base.rglob('*')):
            if not p.is_file(): continue
            resolved=p.resolve()
            need(resolved.is_relative_to(root), 'measurement output escaped stage')
            content=p.read_bytes()
            rows.append({'path':str(p.relative_to(root)).replace('\\','/'),
                'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
    return rows


def _validate(body, expected):
    from .tool_resources import MEASURED
    need(type(body) is dict and set(body)=={'kind','tool_intent','metrics','outcome','result_hash','before_bytes','outputs'}, 'exact measurement receipt')
    need(body['kind']==VERSION and body['tool_intent']==expected, 'measurement belongs to another tool intent')
    metrics=body['metrics']
    need(type(metrics) is dict and set(metrics)==set(MEASURED) and all(type(v) is int and v>=0 for v in metrics.values()), 'invalid original measurement metrics')
    need(body['outcome'] in ('returned','raised') and type(body['result_hash']) is str and re.fullmatch('[a-f0-9]{64}',body['result_hash']), 'measurement result binding')
    need(type(body['before_bytes']) is int and body['before_bytes']>=0 and type(body['outputs']) is list, 'measurement output inventory')
    names=set()
    for output in body['outputs']:
        need(type(output) is dict and set(output)=={'path','bytes','sha256'}, 'measurement output fields')
        name=output['path']
        need(type(name) is str and name and not Path(name).is_absolute() and '..' not in Path(name).parts and name not in names, 'measurement output path')
        need(type(output['bytes']) is int and output['bytes']>=0 and type(output['sha256']) is str and re.fullmatch('[a-f0-9]{64}',output['sha256']), 'measurement output size/hash')
        names.add(name)
    need(metrics['retained_output_bytes']==max(0,sum(x['bytes'] for x in body['outputs'])-body['before_bytes']), 'measurement output arithmetic changed')


def saved(db, row, *, required=False):
    version=protocol(db,row['study_id'],row['trial_id'])
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_measurements'").fetchone()
    receipt=db.execute('SELECT * FROM tool_measurements WHERE study_id=? AND trial_id=? AND id=?',
        (row['study_id'],row['trial_id'],row['id'])).fetchone() if exists else None
    if receipt is None:
        need(not (version==VERSION and row['metrics'] is not None), 'required original measurement missing for projected cost')
        need(not required, 'no anchored original measurement; remains unknown')
        return None
    body=json.loads(receipt['body'])
    need(digest(body)==receipt['receipt_hash'], 'canonical measurement receipt drift')
    _validate(body,identity(db,row))
    if row['metrics'] is not None:
        metrics=json.loads(row['metrics']) if type(row['metrics']) is str else row['metrics']
        need(metrics==body['metrics'] and row['outcome']==body['outcome'] and row['result_hash']==body['result_hash'], 'measurement projection drift')
    return body


def capture(root, intent, metrics, outcome, result_hash, before_bytes, outputs):
    from .research_tools import save_once
    from .tool_resources import _binding
    body={'kind':VERSION,'tool_intent':intent,'metrics':metrics,'outcome':outcome,
          'result_hash':result_hash,'before_bytes':before_bytes,'outputs':outputs}
    _validate(body,intent)
    # Local copy survives database loss, but an unanchored copy alone is not
    # authority to synthesize/rewrite a historical measurement after a crash.
    save_once(Path(root)/'calls'/intent['id']/'tool_measurement.json',body)
    with sr._db() as db:
        binding=_binding(db,root)
        need(binding is not None and binding[0]['study_id']==intent['study_id'] and binding[0]['id']==intent['trial_id'], 'measurement stage binding changed')
        protocol(db,intent['study_id'],intent['trial_id'],admission=True)
        row=db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? AND id=?',
            (intent['study_id'],intent['trial_id'],intent['id'])).fetchone()
        need(row is not None and row['metrics'] is None, 'measurement requires original unresolved application')
        _validate(body,identity(db,row))
        db.execute('INSERT INTO tool_measurements(study_id,trial_id,id,receipt_hash,body) VALUES(?,?,?,?,?)',
            (intent['study_id'],intent['trial_id'],intent['id'],digest(body),serial(body)))


def reconcile(root, call_id):
    from .tool_resources import _binding, _settle
    with sr._db() as db:
        binding=_binding(db,root)
        need(binding is not None and 'tool_budget' in binding[1], 'recovery requires registered tool budget')
        trial=binding[0]
        protocol(db,trial['study_id'],trial['id'],admission=True)
        row=db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? AND id=?',
            (trial['study_id'],trial['id'],call_id)).fetchone()
        need(row is not None, 'unregistered tool measurement')
        body=saved(db,row,required=True)
    # Use the original canonical receipt even when a mutable local copy or
    # output was deleted/changed. Cost recovery never certifies that output.
    updated=_settle(root,call_id,body['metrics'],body['outcome'],body['result_hash'])
    local=Path(root)/'calls'/call_id/'tool_measurement.json'
    try:
        state='missing' if not local.is_file() else 'matching' if local.read_bytes()==serial(body).encode('utf-8') else 'drifted'
    except OSError: state='unreadable'
    return {'call_id':call_id,'projection_updated':updated,'receipt_hash':digest(body),'local_copy':state,
        'metrics':body['metrics'],'producer_reexecuted':False,'model_call_repeated':False,
        'application_status_changed':False,'financial_evidence_certified':False}


def reconcile_available(root):
    from .tool_resources import _binding
    if not sr.REGISTRY.is_file():
        _binding(None,root)
        return {'reconciled':[],'unknown_without_receipt':[]}
    with sr._db() as db:
        binding=_binding(db,root)
        if binding is None or 'tool_budget' not in binding[1]:
            return {'reconciled':[],'unknown_without_receipt':[]}
        trial=binding[0];ready=[];unknown=[]
        version=protocol(db,trial['study_id'],trial['id'])
        for row in db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? ORDER BY started_at,id',
            (trial['study_id'],trial['id'])):
            receipt=saved(db,row)  # Projected rows cannot evade anchor checks.
            if row['metrics'] is None:
                (ready if receipt is not None else unknown).append(row['id'])
        if version!=VERSION:
            return {'reconciled':[],'unknown_without_receipt':unknown,'legacy_read_only':True}
    return {'reconciled':[reconcile(root,key) for key in ready],'unknown_without_receipt':unknown}

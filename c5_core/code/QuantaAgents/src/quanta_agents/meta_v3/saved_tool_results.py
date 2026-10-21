"""Read original returned outputs without rewriting application failures."""
import hashlib
import json
from pathlib import Path

from .ledger import Ledger, digest, need
from . import study_registry as sr, tool_measurements as tm, tool_resources as tr


def returned(root, call_id, task_id):
    """None means no original returned receipt; corrupt evidence raises."""
    root = Path(root).resolve()
    call = Ledger(root).call(call_id)
    need(call['task_id'] == task_id, 'saved result belongs to another task')
    if call['receipt'] is None or call['receipt']['response']['action']=='submit_research_report':
        return None
    if not sr.REGISTRY.is_file():
        tr._binding(None, root)
        return None
    with sr._db() as db:
        binding = tr._binding(db, root)
        if binding is None or 'tool_budget' not in binding[1]: return None
        trial = binding[0]
        if tm.protocol(db, trial['study_id'], trial['id']) != tm.VERSION: return None
        row = db.execute('SELECT * FROM tool_actions WHERE study_id=? AND trial_id=? AND id=?',
            (trial['study_id'], trial['id'], call_id)).fetchone()
        if row is None: return None
        body = tm.saved(db, row)
        if body is None or body['outcome'] != 'returned': return None
        response = call['receipt']['response']
        proof = digest({k:call['receipt'][k] for k in ('request_identity','usage','artifact_sha256','response')})
        model = db.execute('SELECT * FROM calls WHERE study_id=? AND trial_id=? AND id=?',
            (trial['study_id'], trial['id'], call_id)).fetchone()
        need(model['intent_hash']==digest(call['intent']) and model['proof_hash']==proof,
             'saved result canonical model receipt changed')
        need(row['task_id']==task_id and row['action']==response['action'] and row['request_hash']==digest(response),
             'saved result request binding changed')
    folder = root/'tools'/task_id/call_id
    required = {str((folder/name).relative_to(root)).replace('\\','/')
                for name in ('request.json','result.json','artifact.json')}
    need(required <= {x['path'] for x in body['outputs']}, 'original returned output inventory incomplete')
    originals = {}
    for item in body['outputs']:
        path = (root/item['path']).resolve()
        need(path.is_relative_to(root) and path.is_file(), 'original returned output missing or escaped stage')
        content = path.read_bytes()
        need(len(content)==item['bytes'] and hashlib.sha256(content).hexdigest()==item['sha256'],
             'original returned output changed')
        if item['path'] in required: originals[path.name] = json.loads(content)
    need(originals['request.json']=={'action':response['action'],'arguments':json.loads(response['arguments_json'])},
         'original returned request changed')
    result = originals['result.json']
    need(result['evidence_id']==call_id and result['action']==response['action'] and digest(result)==body['result_hash'],
         'original returned result binding changed')
    need(result['artifact_hash']==digest(originals['artifact.json']), 'original returned artifact binding changed')
    return {'result':result, 'proof':{'kind':'verified_original_returned_result_view_v1',
        'measurement_receipt_hash':digest(body),'model_proof_hash':proof,
        'verified_output_files':len(body['outputs']),'producer_reexecuted':False,
        'original_failure_preserved':call['status']=='failed','financial_evidence_certified':False}}


def history_view(root, task_id, rows):
    result = []
    for row in rows:
        saved = returned(root, row['id'], task_id) if row['status']=='failed' else None
        if saved is not None:
            application = json.loads((Path(root)/'calls'/row['id']/'application.json').read_text(encoding='utf-8'))
            need(application['failed'] is True and application['result']==row['result']
                 and application['model_response_hash']==digest(row['response']), 'original failed application changed')
            row = row | {'result':saved['result'], 'original_application_result':row['result'],
                         'saved_result_recovery':saved['proof']}
        result.append(row)
    return result


def verify_application_result(root, call_id, task_id, result):
    """The frozen protocol, never a mutable recovery marker, selects authority."""
    state = tr.status(root)
    if not state['integrated']: return False
    need(state['measurement_protocol_current'], 'legacy measurement scope is read-only; application recovery cannot convert it')
    original = returned(root, call_id, task_id)
    need(original is not None, 'successful application requires an anchored original returned result')
    need(result == original['result'], 'application result differs from canonical original return')
    return True

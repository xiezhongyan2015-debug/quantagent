"""Check capture capability before dispatch; verify each response before use.

New session capture does not require an already verified historical response.
Legacy demonstrated routes remain readable without rewriting old evidence.
"""
import hashlib
import json
from pathlib import Path

from .kernel import ROOT
from .ledger import Ledger, need


def verify(plan, gateway_module):
    route = plan['provenance'].get('runtime_identity_route')
    if route == {'version': 'codex_session_v1'}:
        need(getattr(gateway_module, 'CAPTURE_VERSION', None) == route['version'],
             'Session capture route requires its pinned adapter')
        gateway_module.CodexGateway()._preflight()  # CLI help/features only, no inference.
        return {'status': 'available', 'capture_version': route['version'],
            'identity_verified_before_dispatch': False, 'provider_binding_verified': False,
            'boundary': 'Capture capability only. Every completed call needs separately bound runtime model/effort evidence before tool application.'}
    need(type(route) is dict and set(route) == {'root', 'call_id'},
         'No demonstrated runtime identity capture route; new paid V4 dispatch is blocked before reservation')
    need(all(type(route[k]) is str and route[k] for k in route), 'invalid runtime identity route')
    ledger = Ledger(route['root'])
    call = ledger.call(route['call_id'])
    ledger.status(call['task_id'])  # Verify database plan hash and its original saved plan.
    prior_plan = json.loads((ledger.root / 'plan.json').read_text(encoding='utf-8'))
    source = Path(gateway_module.__file__).resolve()
    relative = str(source.relative_to(ROOT)).replace('\\', '/')
    need(prior_plan['provenance']['source_pins'].get(relative) == hashlib.sha256(source.read_bytes()).hexdigest(),
         'identity route uses another gateway version')
    need(call['receipt'] is not None and call['status'] in ('received', 'applying', 'applied', 'failed'),
         'identity route needs an existing verified completion, not a new probe')
    need(call['intent']['model'] == plan['model'] == 'gpt-6-astra'
         and call['intent']['effort'] == plan['effort'] == 'xhigh', 'identity route requested settings differ')
    receipt = gateway_module.verify_saved_completion(ledger.root / 'calls' / call['id'],
        expected_prompt_hash=call['intent']['prompt_hash'], expected_schema=call['intent']['schema'],
        expected_artifact_sha256=call['receipt']['artifact_sha256'])
    need(receipt.get('request_identity', {}).get('intent_id') == call['id'], 'identity route completion binding mismatch')
    need(receipt.get('model_verified') is True,
         'Existing completion does not confirm runtime model/effort; request configuration is insufficient')
    return {'status': 'available', 'root': str(ledger.root), 'call_id': call['id'],
        'gateway_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'provider_binding_verified': receipt.get('identity_verification', {}).get('provider_request_binding_verified', False),
        'boundary': 'Prior runtime-event capture only. Recheck every future completion; this does not certify provider-side routing or research quality.'}

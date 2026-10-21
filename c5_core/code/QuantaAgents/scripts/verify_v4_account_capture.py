"""One bounded account-call diagnostic, never a strategy or architecture trial."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3 import codex_session_gateway as gateway
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.ledger import Ledger, digest, serial, worker_lease
from quanta_agents.meta_v3.runtime import source_pins


def main():
    root = ROOT / 'experiment_traces/meta_framework_v4/account_capture_001'
    schema = {'type': 'object', 'additionalProperties': False,
        'properties': {'action': {'type': 'string', 'enum': ['submit_research_report']},
            'conclusion': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
        'required': ['action', 'conclusion']}
    prompt = ('This is one bounded engineering diagnostic using the existing Codex account. '
        'Explain in <=100 Chinese characters why saved client runtime model/effort settings '
        'can be useful for reproducibility but do not independently attest provider-side computation. '
        'Do not claim to know your identity, do not use tools, and do not give investment advice. '
        'Return action submit_research_report and conclusion according to the schema.')
    job = Ledger.create(root, policy=ClosingPolicy(task_tokens=80000, stage_tokens=80000,
        task_calls=1, stage_calls=1, closing_seconds=120),
        tasks={'capture_check': {'kind': 'account_capture_diagnostic', 'prompt_hash': digest(prompt)}},
        deadline_epoch=time.time()+600, provenance={'source_pins': source_pins(),
            'kind': 'engineering_account_call_diagnostic', 'new_market_research': False,
            'maximum_model_calls': 1, 'maximum_process_seconds': 180,
            'no_automatic_retry': True, 'provider_cost_currency': None})
    with worker_lease(root):
        intent = job.reserve('capture_check', ('submit_research_report',),
            lambda *args: (prompt, schema))
        folder = root / 'calls' / intent['intent_id']
        folder.mkdir(parents=True)
        (folder / 'intent.json').write_text(serial(intent), encoding='utf-8')
        try:
            gateway.CodexGateway(timeout_seconds=180).run(prompt=prompt, schema=schema,
                workdir=folder, on_event=lambda event: None, cancelled=lambda: False)
            receipt = job.receive_saved(intent['intent_id'], gateway.verify_saved_completion)
        except Exception as exc:
            job.unknown(intent['intent_id'], exc)
            print(serial({'status': 'preserved_failure', 'error_class': type(exc).__name__,
                'root': str(root), 'no_automatic_retry': True}))
            raise
        result = {'engineering_diagnostic_only': True, 'formal_target_success': False,
            'model_response': receipt['response'], 'runtime_identity': receipt['runtime_identity']}
        job.begin_apply(intent['intent_id'])
        (folder / 'application.json').write_text(serial({'model_response_hash': digest(receipt['response']),
            'result': result, 'failed': False}), encoding='utf-8')
        job.finish_apply(intent['intent_id'], result, failed=False, final=True)
        summary = {'kind': 'engineering_account_call_diagnostic', 'call_id': intent['intent_id'],
            'new_model_calls': 1, 'new_market_research_calls': 0,
            'known_tokens': receipt['usage']['input_tokens']+receipt['usage']['output_tokens'],
            'usage': receipt['usage'], 'runtime_identity': receipt['runtime_identity'],
            'provider_identity_verified': False, 'root': str(root)}
        (root / 'result.json').write_text(serial(summary), encoding='utf-8')
        print(serial(summary))


if __name__ == '__main__':
    main()

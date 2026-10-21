"""Saved-only verification of the bounded V4 account trial. No inference."""
from pathlib import Path
from datetime import datetime, timezone
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.ledger import digest, serial
from quanta_agents.meta_v3.runtime import ResearchRuntime


def main():
    root = ROOT / 'experiment_traces/meta_framework_v4/account_research_trial_001'
    runtime = ResearchRuntime(root)
    runtime.verify_inputs()
    state = runtime.ledger.status('source_review')
    if state['terminal'] != 'submitted':
        raise ValueError('A submitted report is required for this completed-loop audit')
    rows = []
    for item in state['calls']:
        call = runtime.ledger.call(item['id'])
        receipt = runtime.gateway_module.verify_saved_completion(root / 'calls' / call['id'],
            expected_prompt_hash=call['intent']['prompt_hash'], expected_schema=call['intent']['schema'],
            expected_artifact_sha256=call['receipt']['artifact_sha256'])
        assert receipt['response'] == call['receipt']['response']
        assert receipt['usage'] == call['receipt']['usage']
        assert receipt['runtime_identity']['verified'] is True
        assert receipt['runtime_identity']['provider_request_binding_verified'] is False
        result = call['result']
        if result.get('artifact_hash'):
            artifact = json.loads((root / 'tools/source_review' / call['id'] / 'artifact.json').read_text(encoding='utf-8'))
            assert digest(artifact) == result['artifact_hash']
        rows.append({'call_id': call['id'], 'action': receipt['response']['action'],
            'status': call['status'], 'known_tokens': call['known_tokens'],
            'usage': receipt['usage'], 'runtime_identity': receipt['runtime_identity'],
            'recovered_intermediate_errors': len(receipt.get('recovered_intermediate_errors', [])),
            'artifact_sha256': receipt['artifact_sha256']})
    report = runtime.ledger.call(state['calls'][-1]['id'])['result']
    assert report['legal_submission'] is True and report['formal_target_success'] is False
    summary = {'observed_at': datetime.now(timezone.utc).isoformat(),
        'kind': 'saved_only_account_trial_verification', 'new_model_calls_in_this_audit': 0,
        'research_model_calls': len(rows), 'research_known_tokens': sum(r['known_tokens'] for r in rows),
        'account_diagnostic_calls_separate': 1, 'account_diagnostic_tokens_separate': 8691,
        'total_new_account_call_tokens': 8691+sum(r['known_tokens'] for r in rows),
        'runtime_configuration_verified_every_call': True, 'provider_identity_independently_verified': False,
        'source_pins_and_case_verified': True, 'original_artifacts_verified': True,
        'calls': rows, 'report': report, 'new_market_samples': 0, 'new_strategy_executions': 0,
        'formal_architecture_comparison': False, 'formal_target_success': False,
        'provider_currency_cost': None, 'controller_and_subagent_currency_cost': None,
        'boundary': 'Actual model tool-use and report delivery on admitted exposed historical evidence; neither report truth nor investment performance is automatically certified.'}
    with (root / 'verification_001.json').open('x', encoding='utf-8') as stream:
        stream.write(serial(summary))
    args = report['model_report']
    text = '# V4 有界资料审阅：模型原始报告\n\n'
    text += '范围：已暴露的2019年单股历史资料。以下结论为模型原文；不构成策略收益或正式验证。\n\n'
    text += args['conclusion'] + '\n\n局限：\n\n'
    text += '\n'.join('- ' + line for line in args['limitations'])
    text += '\n\n模型建议的下一步：\n\n' + args['next_step'] + '\n'
    with (root / 'model_report.md').open('x', encoding='utf-8') as stream:
        stream.write(text)
    print(serial({k: v for k, v in summary.items() if k not in ('calls', 'report')}))
    print(serial({'report': report}))


if __name__ == '__main__':
    main()

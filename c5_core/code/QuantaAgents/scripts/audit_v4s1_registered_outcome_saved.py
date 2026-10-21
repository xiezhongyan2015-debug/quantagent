"""Independent arithmetic for the ended v4s1 scope; stdlib and saved JSON only.

No current research implementation is imported, and no ledger is written.
Earlier per-slot audits establish canonical receipts and full cash-ledger replay.
This review authenticates their inputs and independently recomputes the frozen
v1 checks and control comparison, preserving the original model knowledge.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/research/meta_framework_v4_handoff/v4s1_registered_outcome_audit_001.json'
INPUTS = {}


def read(path):
    path = path.resolve()
    body = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = hashlib.sha256(body).hexdigest()
    return json.loads(body)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def measures(raw):
    assert raw['status'] == 'completed_mechanical' and raw['partial'] in (None, False) and raw['error'] is None
    body = raw['result']
    dates = [row['date'] for row in body['daily']]
    assert dates == body['calendar'] and len(dates) == 244 and len(set(dates)) == 244
    initial = Decimal(body['initial_cash'])
    assert initial == Decimal('1000000')
    # Use the recorded trade notional, not desired orders or target weights.
    fees = sum((Decimal(t['fees']['total']) for t in body['trades']), Decimal(0))
    turnover = sum((Decimal(t['raw_price']) * t['quantity'] for t in body['trades']), Decimal(0)) / initial
    fractions = []
    for day in body['daily']:
        marked = day['valuation']['raw_ledger_valuation']
        assert Decimal(marked['gross_asset_value']) == Decimal(day['gross_asset_value'])
        fractions.append(Decimal(marked['share_value_including_pending']) / Decimal(day['gross_asset_value']))
    exposure = sum(fractions, Decimal(0)) / len(fractions)
    pnl = Decimal(body['daily'][-1]['simulated_net_asset_value']) - initial
    return {'fees_on_recorded_trades': fees,
            'turnover_multiple_of_initial_cash': turnover,
            'mean_marked_share_fraction_of_gross_assets': exposure,
            'net_pnl': pnl}, digest(dates)


def main():
    assert not OUT.exists(), 'append-only review output already exists'
    docs = ROOT / 'docs/research/meta_framework_v4_handoff'
    audits, slots = {}, []
    for trial in ['v1', 's1', 's2', 'v2', 'f1', 'f2', 'f3']:
        suffix = '_account_audit_001.json' if trial[0] == 'f' else '_model_account_audit_001.json'
        path = docs / ('v4s1_' + trial + suffix)
        audit = read(path)
        audits[trial] = audit
        assert audit['original_artifacts_unchanged'] is True
        proc = audit['process_receipt']
        assert proc['exit_code'] == 0 and proc['metrics']['active_processes'] == 0
        if trial[0] == 'f':
            slots.append({'trial_id': trial, 'terminal': audit['selection']['outcome'],
                'calls': 0, 'known_tokens': 0, 'unresolved_call_reserves': 0,
                'complete_accounts': audit['complete_account_count'],
                'candidate_status_counts': audit['status_counts'],
                'selected_candidate_id': audit['selection']['selected_candidate_id'],
                'audit_path': str(path.relative_to(ROOT))})
        else:
            assert audit['canonical_model_and_process_binding_verified'] is True
            slots.append({'trial_id': trial, 'terminal': audit['terminal'],
                'calls': len(audit['calls']),
                'known_tokens': sum(c['known_tokens'] for c in audit['calls']),
                'unresolved_call_reserves': sum(c['unresolved_reserve'] for c in audit['calls']),
                'complete_accounts': sum(a.get('metrics') is not None for a in audit['accounts']),
                'candidate_opportunity_count': audit['candidate_opportunity_count'],
                'audit_path': str(path.relative_to(ROOT))})
    v1 = ROOT / 'experiment_traces/v4s1/v1'
    index = audits['v1']['original_file_hashes']

    def bound_v1(relative):
        value = read(v1 / relative)
        assert hashlib.sha256((v1 / relative).read_bytes()).hexdigest() == index[str(Path(relative))]
        return value

    registered = bound_v1('tools/research/research_010_d981afd0107d/artifact.json')
    revision = bound_v1('tools/research/research_011_c7700a001b27/artifact.json')
    baseline = bound_v1('batches/research/research_003_4b46ab79d4b7/candidates/c001/worker_result.json')['artifact']
    experiment = revision['experiment']
    emitted = experiment['declared_check_results']
    checks = registered['declaration']['contrast_checks']
    assert experiment['registration_hash'] == emitted['registration_hash'] == digest(registered)
    assert experiment['registration_evidence_id'] == 'research_010_d981afd0107d'
    assert experiment['baseline_evidence_id'] == registered['declaration']['baseline_evidence_id'] == 'research_004_b2334452a0ca/c001'
    assert digest(checks) == emitted['checks_hash']
    assert digest(baseline['program']) == registered['baseline_program_hash']
    assert digest(revision['program']) == registered['revision_program_hash']
    with localcontext() as ctx:
        ctx.prec = 28
        base_values, base_calendar = measures(baseline['raw'])
        rev_values, rev_calendar = measures(revision['raw'])
        assert [base_calendar, rev_calendar] == emitted['account_calendar_hashes']
        assert base_calendar == rev_calendar == emitted['frozen_calendar_hash']
        assert Decimal(emitted['frozen_initial_cash']) == Decimal('1000000')
        rows = []
        units = {'fees_on_recorded_trades': 'account currency; recorded transaction fees',
            'turnover_multiple_of_initial_cash': 'turnover divided by full initial capital',
            'mean_marked_share_fraction_of_gross_assets': 'mean marked equity fraction, not beta or matched risk',
            'net_pnl': 'account currency on the full initial capital'}
        operators = {'lt': lambda a,b:a<b, 'lte': lambda a,b:a<=b,
                     'gt': lambda a,b:a>b, 'gte': lambda a,b:a>=b}
        assert len(checks) == len(emitted['checks']) == 6
        for frozen, saved in zip(checks, emitted['checks']):
            assert all(frozen[k] == saved[k] for k in frozen)
            metric = frozen['metric']
            b, r = base_values[metric], rev_values[metric]
            delta = r - b
            assert saved['unit'] == units[metric]
            assert Decimal(saved['baseline_value']) == b and Decimal(saved['revision_value']) == r
            assert Decimal(saved['revision_minus_baseline']) == delta
            matched = operators[frozen['operator']](delta, Decimal(frozen['threshold']))
            assert saved['status'] == ('matched' if matched else 'contradicted')
            rows.append(dict(frozen, baseline_value=str(b), revision_value=str(r),
                revision_minus_baseline=str(delta), unit=units[metric], independently_matched=matched))
    controls = []
    for cid in ['c001','c002','c003']:
        artifact = bound_v1('batches/research/research_012_3e7732f39a7f/candidates/' + cid + '/worker_result.json')['artifact']
        values, calendar = measures(artifact['raw'])
        assert calendar == base_calendar
        controls.append({'candidate_id': cid,
            'program': artifact['program'], 'net_pnl': str(values['net_pnl']),
            'revision_minus_control_net_pnl': str(rev_values['net_pnl'] - values['net_pnl']),
            'revision_exceeds_control': rev_values['net_pnl'] > values['net_pnl']})
    failures = []
    for trial in ['s2', 'v2']:
        ledger = ROOT / 'experiment_traces/v4s1' / trial / 'ledger.sqlite3'
        with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            call = db.execute("SELECT id,status,receipt,result FROM calls ORDER BY ordinal DESC LIMIT 1").fetchone()
        receipt = json.loads(call['receipt'])
        response = receipt['response']
        args = json.loads(response['arguments_json'])
        claims = args['claims']
        original = read(ledger.parent / 'calls' / call['id'] / 'application.json')
        assert original['result'] == json.loads(call['result'])
        assert original['model_response_hash'] == digest(response) and call['status'] == 'failed'
        assert response['action'] == 'submit_research_report' and isinstance(claims,dict)
        assert isinstance(claims['claims'], list) and 1 <= len(claims['claims']) <= 16
        failures.append({'trial_id':trial,'call_id':call['id'], 'original_status':call['status'],
            'error':original['result']['error'],'arguments_json_parsed':True,
            'claims_outer_type':'object','claims_outer_keys':list(claims),
            'declared_wrapper_version':claims.get('version'), 'inner_claim_count':len(claims['claims']),
            'outcome':args['outcome'], 'old_report_repaired_or_accepted':False})
    checks_counts = dict(Counter('matched' if r['independently_matched'] else 'contradicted' for r in rows))
    totals = {key:sum(s[key] for s in slots) for key in ['calls','known_tokens','unresolved_call_reserves','complete_accounts']}
    assert totals == {'calls':37,'known_tokens':995120,'unresolved_call_reserves':0,'complete_accounts':37}
    for name, checksum in INPUTS.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == checksum
    result = {'kind':'v4s1_saved_registered_outcome_independent_audit_v1',
        'observed_at':datetime.now(timezone.utc).isoformat(), 'slots':slots, 'totals':totals,
        'original_unstarted_slots_preserved':['v3','s3'],
        'candidate_failures_preserved':{'fixed_unknown_output_bytes':3,'v2_register_batch_admission_blocked':1},
        'v1_registered_experiment':{'registration_hash':digest(registered), 'checks':rows,
            'independent_counts':checks_counts,'calendar_binding_verified':True,'full_initial_cash_binding_verified':True,
            'hypothesis_interpretation':['H0 has all five numeric predictions matched; preliminary screen only.',
                'H1 sole declared nonpositive-PnL check is contradicted; the broader prose also includes failing controls and is not thereby disproven.',
                'H2 declares no numeric check and remains untested.'],
            'controls':controls, 'all_controls_exceeded':all(c['revision_exceeds_control'] for c in controls),
            'complete_frozen_adoption_criterion_passed':False,
            'model_read_complete_control_results_before_final':False,
            'registered_check_typed_claims_in_final':0,
            'final_outcome':'abstain', 'causal_mechanism_identified':False},
        'invalid_final_causes':failures,
        'v1_report_support':audits['v1']['reports'][0]['result']['claim_support'],
        'formal_success_denominator_contribution':0,'formal_target_success':False,
        'new_model_calls':0,'new_strategy_executions':0,'original_inputs_unchanged':True,
        'audit_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'input_sha256':INPUTS,
        'limitations':['One already exposed 2019 case, 244 sessions; no independent financial acceptance.',
            'Seven started slots and two original unstarted slots do not complete the nine-slot local comparison.',
            'The original per-slot audits verified cash-ledger replay and saved event consistency; this review independently recomputes frozen contrasts only.',
            'Saved simulated costs and capacity are not certified historical executable economics.',
            'Post-study audit knowledge is not supplied retroactively to model reports or accepted as model behavior.',
            'Engineering source changes after the completed-run archive are outside the ended experimental scope.']}
    with OUT.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,sort_keys=True)
        stream.write('\n')
    print(json.dumps({'output':str(OUT.relative_to(ROOT)),'totals':totals,
        'check_counts':checks_counts,'complete_frozen_adoption_criterion_passed':False,
        'original_inputs_unchanged':True},ensure_ascii=True))


if __name__ == '__main__':
    main()

"""Check V21 evidence/counters/identities without repeating complete fingerprints."""
from . import bounded_tail_pipeline as p
BASE=p.BASE
OUT=p.OUT/'v21_complete_checkpoint_consistency_verification'
RAW=BASE/'backfilled_panel_candidate_native_first_hour_extrema_v21_raw'
DEST=BASE/'backfilled_panel_candidate_native_first_hour_extrema_v21_derived'
PREV=BASE/'backfilled_panel_candidate_native_strict_endpoint_v20_derived'
def load(path):return p.json.loads(path.read_text())
def main():
    OUT.mkdir(exist_ok=True);assert not (OUT/'receipt.json').exists()
    meta=load(DEST/'dataset/meta.json');proof=load(DEST/'model_input_independent_verification.json');audit=load(DEST/'dataset/dataset_audit.json');assembly=load(DEST/'model_input_assembly_receipt.json');raw=load(RAW/'dataset/meta.json');summary=load(p.OUT/'resume_summary.json');state=load(p.OUT/'continuation_state.json')
    assert proof['passed'] and proof['full1178_tensor_audit_complete'] and proof['all122_normalized_feature_cells_checked']==1218136816
    assert proof['final_dataset_meta_sha256']==p.sha(DEST/'dataset/meta.json') and proof['dataset_audit_sha256']==p.sha(DEST/'dataset/dataset_audit.json') and proof['audited_assembly_receipt_sha256']==p.sha(DEST/'model_input_assembly_receipt.json')
    assert not proof['training_admission_ready'] and not proof['production_ready'] and not proof['full_data_backfill_acceptance_complete']
    arrays=[]
    for row in audit['arrays']:
        path=DEST/'dataset'/row['name'];assert p.identity(path)==row['verified_identity'];assert meta['files_sha256'][row['name']]==row['sha256'];arrays.append(row['name'])
    assert len(arrays)==15
    norm=audit['normalization'];assert p.identity(DEST/'dataset/X.npy')==norm['X_verified_identity'] and meta['files_sha256']['X.npy']==norm['X_sha256']
    for row in assembly['arrays']:
        assert p.identity(row['path'])==row['verified_identity'] and meta['files_sha256'][p.Path(row['path']).name]==row['sha256']
    assert len(assembly['arrays'])==18
    prev=load(PREV/'dataset/dataset_audit.json')
    for row in load(PREV/'model_input_assembly_receipt.json')['arrays']:assert p.identity(row['path'])==row['verified_identity']
    assert summary['derived_model_admitted_raw_hours']==meta['admitted_raw_hours_this_resume']==raw['admitted_raw_hours']==3227
    assert summary['derived_model_raw_candidate']==RAW.name and not summary['latest_raw_candidate_model_inputs_pending']
    assert state['derived_model_input_admitted_raw_hours']==3227 and not state['latest_raw_model_input_update_pending'] and not state['active_local_workers']
    assert state['new_accepted_execution_windows']==3126 and state['new_late_execution_windows_kept_missing']==100 and state['new_zero_volume_execution_windows_kept_missing']==1
    assert raw['historical_admitted_raw_hours']==464 and raw['historical_minimum_missing_hours']==2578 and raw['tail_admitted_raw_hours']==2763 and raw['tail_missing_hours']==82097
    cancel=summary['additional_sep24_single_stream_archive'];assert cancel['outside_requested_scope_cancelled'] and cancel['native_members_retained']==cancel['native_member_bytes_retained']==cancel['candidate_hours_admitted']==0 and cancel['own_unverified_payload_removed_bytes']==12943890878
    result=dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),model_proof_sha256=p.sha(DEST/'model_input_independent_verification.json'),meta_sha256=proof['final_dataset_meta_sha256'],raw_meta_sha256=p.sha(RAW/'dataset/meta.json'),summary_sha256=p.sha(p.OUT/'resume_summary.json'),continuation_state_sha256=p.sha(p.OUT/'continuation_state.json'),all18_candidate_array_identities_verified=True,previous_all18_candidate_array_identities_unchanged=True,complete_array_hashes_repeated=False,full1178_and_all122_audit_passed=True,admitted_raw_hours=3227,historical_admitted=464,historical_remaining=2578,tail_admitted=2763,tail_remaining=82097,accepted_execution_windows=3126,late_execution_windows_null=100,zero_execution_windows_null=1,scope_error_cancelled_and_payload_removed=True,training_admission_ready=False,production_ready=False,goal_complete=False)
    p.atomic_json(OUT/'receipt.json',result);print(p.json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

"""Verify current raw/model distinction by immutable proofs and accepted identities."""
from . import bounded_tail_pipeline as p
RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'
MODEL=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_derived'
DEST=p.OUT/'v26_raw_v25_model_checkpoint_consistency_verification'
def load(path):return p.json.loads(path.read_text())
def main():
    DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    raw=load(RAW/'dataset/meta.json');ra=load(RAW/'raw_full_history_audit.json');rm=load(RAW/'raw_panel_merge_receipt.json');meta=load(MODEL/'dataset/meta.json');proof=load(MODEL/'model_input_independent_verification.json');audit=load(MODEL/'dataset/dataset_audit.json');assembly=load(MODEL/'model_input_assembly_receipt.json');summary=load(p.OUT/'resume_summary.json');state=load(p.OUT/'continuation_state.json')
    assert ra['passed'] and ra['stocks_checked']==1178 and ra['total_admitted_raw_hours']==raw['admitted_raw_hours']==3699 and ra['added_raw_hours']==2
    assert ra['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json') and ra['audited_merge_receipt_sha256']==p.sha(RAW/'raw_panel_merge_receipt.json')
    for a in rm['arrays']:assert p.identity(a['path'])==a['verified_identity'] and raw['files_sha256'][p.Path(a['path']).name]==a['sha256']
    assert len(rm['arrays'])==4 and ra['unexpected_cell_changes']==ra['primary_suspension_hours_promoted']==0
    assert raw['unknown_local_arrival_hours']==466 and ra['new_unknown_arrival_hours_preserved']==2
    for r in rm['records']:
        for h in r['hours']:assert h['source_local_arrival_ms'] is None and not h['source_arrival_before_execution_certified']
    assert proof['passed'] and proof['full1178_tensor_audit_complete'] and proof['all122_normalized_feature_cells_checked']==1218136816
    assert proof['final_dataset_meta_sha256']==p.sha(MODEL/'dataset/meta.json') and proof['dataset_audit_sha256']==p.sha(MODEL/'dataset/dataset_audit.json') and proof['audited_assembly_receipt_sha256']==p.sha(MODEL/'model_input_assembly_receipt.json')
    assert meta['admitted_raw_hours_this_resume']==3697 and not proof['training_admission_ready'] and not proof['production_ready']
    for a in audit['arrays']:assert p.identity(MODEL/'dataset'/a['name'])==a['verified_identity'] and meta['files_sha256'][a['name']]==a['sha256']
    assert len(audit['arrays'])==15
    norm=audit['normalization'];assert p.identity(MODEL/'dataset/X.npy')==norm['X_verified_identity'] and meta['files_sha256']['X.npy']==norm['X_sha256']
    for a in assembly['arrays']:assert p.identity(a['path'])==a['verified_identity'] and meta['files_sha256'][p.Path(a['path']).name]==a['sha256']
    assert len(assembly['arrays'])==18
    assert summary['candidate_hours_admitted_this_resume']==state['candidate_hours_admitted_this_resume']==3699
    assert summary['new_candidate_raw_panel']['candidate_version']==state['current_new_raw_candidate']==RAW.name
    assert summary['derived_model_admitted_raw_hours']==state['derived_model_input_admitted_raw_hours']==3697
    assert summary['latest_raw_candidate_model_inputs_pending'] and state['latest_raw_model_input_update_pending'] and not state['active_local_workers']
    assert raw['historical_minimum_missing_hours']==state['historical_gap_hours_remaining']==summary['historical_gap_hours_remaining']==2578
    assert raw['tail_missing_hours']==state['tail_gap_hours_remaining']==summary['full_tail_gap_hours_remaining']==81625
    assert raw['tail_admitted_raw_hours']==3235 and raw['historical_admitted_raw_hours']==464
    assert summary['new_scoped_native_historical_matrix_diagnostic']['candidate_hours_admitted']==0 and summary['new_scoped_native_historical_matrix_diagnostic']['complete_coarse_reference_hours']==0
    receipt=dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),raw_audit_sha256=p.sha(RAW/'raw_full_history_audit.json'),raw_meta_sha256=p.sha(RAW/'dataset/meta.json'),model_proof_sha256=p.sha(MODEL/'model_input_independent_verification.json'),model_meta_sha256=p.sha(MODEL/'dataset/meta.json'),summary_sha256=p.sha(p.OUT/'resume_summary.json'),state_sha256=p.sha(p.OUT/'continuation_state.json'),raw_admitted_hours=3699,full_model_admitted_hours=3697,new_raw_model_inputs_pending_hours=2,new_unknown_receiver_times_kept_null=2,raw_all4_artifact_identities_verified=True,previous_model_all18_artifact_identities_unchanged=True,complete_binary_fingerprints_repeated=False,historical_remaining_hours=2578,tail_remaining_hours=81625,training_admission_ready=False,production_ready=False,goal_complete=False)
    p.atomic_json(output,receipt);print(p.json.dumps(receipt,ensure_ascii=False))
if __name__=='__main__':main()

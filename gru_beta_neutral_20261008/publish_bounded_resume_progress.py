"""Publish independently bound raw admission separately from model readiness."""
from html import escape
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p


def main():
    process_lock=p.locked('publish_progress')
    names=['execution_manifest.json','执行进度_2026-10-08.csv','数据补齐核验与实验进度_2026-10-09.html']
    snapshot=p.OUT/'prior_progress_before_resume'
    snapshot.mkdir(exist_ok=True)
    for name in names:
        if not (snapshot/name).exists():
            p.shutil.copyfile(p.BASE/name,snapshot/name)
    acq=p.json.loads((p.OUT/'acquisition.json').read_text())
    ext=p.json.loads((p.OUT/'extraction.json').read_text())
    minutes=p.json.loads((p.OUT/'minute_observation_progress.json').read_text())
    scope=p.json.loads((p.OUT/'full_history_audit.json').read_text())
    controls=p.json.loads((p.OUT/'minute_control_verification.json').read_text())
    reference=p.json.loads((p.OUT/'historical_gap_references/receipt.json').read_text())
    count=sum(r['status']==p.COMPLETE for r in acq['records'])
    remaining=sum(r['published_bytes'] for r in acq['records'] if r['status']!=p.COMPLETE)
    errors=[a for r in acq['records'] for a in r['attempts'] if 'error_type' in a]
    summary=dict(updated_utc=p.utc(),requested_partitions=226,initial_preserved_partitions=177,
        newly_completed_partitions=count-177,complete_partitions=count,remaining_whole_file_bytes=remaining,
        acquisition_status=acq['status'],extraction_status=ext['status'],processed_partitions=ext['processed_partitions'],
        selected_native_rows=ext['selected_rows'],stockdays_with_minute_observations=minutes.get('processed_stockdays',0),
        minute_observation_status=minutes['status'],
        daily_six_fields_exact_stockdays=sum(r['daily_six_fields_exact_stockdays'] for r in minutes['groups']),
        source_absent_stockdays=sum(r['stockdays']-r['inventory_exact_stockdays'] for r in minutes['groups']),
        memory_peak_bytes=dict(download=acq.get('peak_rss_bytes'),extraction=ext.get('peak_rss_bytes'),minutes=minutes.get('peak_rss_bytes'),full_history=scope.get('peak_rss_bytes')),
        current_resume_transfer_errors=len(errors),resume_broken_pipe_errors=sum(a['error_type']=='BrokenPipeError' for a in errors),
        original_BrokenPipeError_cause_remains_unidentified=True,
        all_predeclared_minute_controls_passed_by_clock=controls['all_controls_passed_by_clock'],
        existing_V6_full_history_stocks_checked=scope['stocks_checked'],existing_V6_internal_audit_passed=scope['passed'],
        historical_unchecked_hours_requested=40,historical_hours_with_complete_new_coarse_reference=reference['hours_with_complete_references'],
        historical_new_strict_coarse_diagnostic_hours=reference['strict_coarse_diagnostic_hours'],
        historical_gap_hours_remaining=3042,full_tail_gap_hours_remaining=84860,
        candidate_hours_admitted_this_resume=0,new_candidate_panel_built=False,new_features_or_labels_recalculated=False,
        new_panel_training_or_backtest_run=False,final_new_panel_full_scope_audit_complete=False,goal_complete=False,
        work_directory=str(p.OUT), nighttime_popups_used=False)
    candidate_receipt=p.OUT/'historical_candidate_files/receipt.json'
    if candidate_receipt.exists():
        candidate=p.json.loads(candidate_receipt.read_text())
        summary['additional_historical_minute_comparison']=dict(status=candidate['status'],stocks_requested=candidate['requested_available_stocks'],
            stocks_compared=sum(r['status']=='complete_comparison' for r in candidate['records']))
    pairing_path=p.OUT/'millisecond_pairing/receipt.json'
    if pairing_path.exists():
        pairing=p.json.loads(pairing_path.read_text())
        summary['native_millisecond_pairing']=dict(stockdays_checked=pairing['stockdays_checked'],
            trade_rows_checked=pairing['native_regular_trade_rows'],
            price_quantity_and_floor_seconds_exact=pairing['all_rows_price_quantity_and_floor_clock_exact'],
            upstream_source_independence_certified=pairing['upstream_source_independence_certified'],
            receipt_path=str(pairing_path),peak_rss_bytes=pairing['peak_rss_bytes'])
    quantization_path=p.OUT/'quote_clock_quantization/receipt.json'
    if quantization_path.exists():
        quantization=p.json.loads(quantization_path.read_text())
        summary['global_quote_clock_models']=dict(stockdays_checked=quantization['stockdays_checked'],
            models_tested=len(quantization['clock_models_predeclared']),globally_exact_models=quantization['globally_exact_models'],
            minute_definition_admitted=quantization['minute_definition_admitted'],receipt_path=str(quantization_path))
    recheck_path=p.OUT/'historical_quote_candidate_recheck/receipt.json'
    if recheck_path.exists():
        recheck=p.json.loads(recheck_path.read_text())
        summary['expanded_historical_coarse_recheck']=dict(status=recheck['status'],requested_hours=recheck['requested_hours'],
            requested_stockdays=recheck['requested_stockdays'],completed_stockdays=len(recheck['results']),
            source_year_files_scanned_once=len(recheck['native_files']),
            complete_reference_hours=sum(len(r['slots']) for r in recheck['results'] if r['reference_rows']==48),
            strict_coarse_diagnostic_hours=recheck.get('strict_coarse_diagnostic_hours',0),
            all_fields_exact_diagnostic_hours=recheck.get('all_fields_exact_diagnostic_hours',0),
            candidate_hours_admitted=0,receipt_path=str(recheck_path),peak_rss_bytes=recheck.get('peak_rss_bytes'),
            official_old_code_query_routing_used=True)
        proof_path=recheck_path.parent/'independent_verification.json'
        if proof_path.exists():
            proof=p.json.loads(proof_path.read_text())
            summary['expanded_historical_coarse_recheck']['independent_verification_passed']=proof['passed'] and proof['audited_receipt_sha256']==p.sha(recheck_path)
    tencent_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json'
    if tencent_path.exists():
        tencent=p.json.loads(tencent_path.read_text())
        summary['independent_tail_hour_comparison']=dict(status=tencent['status'],stockdays_compared=tencent['processed_stockdays'],
            hours_requested=tencent['requested_hours'],totals_by_model=tencent['totals_by_model'],
            candidate_hours_admitted=0,receipt_path=str(tencent_path),peak_rss_bytes=tencent['peak_rss_bytes'])
    order_path=p.OUT/'millisecond_pairing/intrasecond_order_verification.json'
    if order_path.exists():
        order=p.json.loads(order_path.read_text())
        summary['native_intrasecond_order']=dict(stockdays_checked=order['stockdays_checked'],
            observed_minutes_checked=order['observed_trade_minutes_checked'],
            minute_open_or_close_changed=order['minute_open_changed_by_intrasecond_order']+order['minute_close_changed_by_intrasecond_order'],
            intrasecond_event_inversions=order['intrasecond_event_inversions'],receipt_path=str(order_path))
    staging_path=p.OUT/'new_candidate_v7_staging/receipt.json'
    if staging_path.exists():
        staged=p.json.loads(staging_path.read_text())
        staged_proof_path=staging_path.parent/'independent_verification.json'
        staged_proof=p.json.loads(staged_proof_path.read_text()) if staged_proof_path.exists() else {}
        summary['new_candidate_minute_staging']=dict(status=staged['status'],stockdays_written=staged['staged_stockdays'],
            hours_staged=staged['staged_hours'],source_genuine_zero_trade_quote_minutes=staged_proof.get('genuine_zero_trade_quote_minutes_verified'),
            independent_verification_passed=staged_proof.get('passed',False) and staged_proof.get('audited_receipt_sha256')==p.sha(staging_path),
            parent_panel_merged=False,features_and_labels_recalculated=False,formal_parent_panel_hours_admitted=0,
            receipt_path=str(staging_path),peak_rss_bytes=staged['peak_rss_bytes'])
    raw_dir=p.BASE/'backfilled_panel_candidate_native_event_v7_raw'
    newer_raw=p.BASE/'backfilled_panel_candidate_native_event_v8_raw'
    if (newer_raw/'raw_full_history_audit.json').exists():
        raw_dir=newer_raw
    special_raw=p.BASE/'backfilled_panel_candidate_native_event_v9_raw'
    if (special_raw/'raw_full_history_audit.json').exists():raw_dir=special_raw
    second_raw=p.BASE/'backfilled_panel_candidate_native_second_v10_raw'
    if (second_raw/'raw_full_history_audit.json').exists():raw_dir=second_raw
    latest_ms_raw=p.BASE/'backfilled_panel_candidate_native_millisecond_v11_raw'
    if (latest_ms_raw/'raw_full_history_audit.json').exists():raw_dir=latest_ms_raw
    latest_quote_raw=p.BASE/'backfilled_panel_candidate_native_quote_v12_raw'
    if (latest_quote_raw/'raw_full_history_audit.json').exists():raw_dir=latest_quote_raw
    latest_history_raw=p.BASE/'backfilled_panel_candidate_historical_reported_v13_raw'
    if (latest_history_raw/'raw_full_history_audit.json').exists():raw_dir=latest_history_raw
    latest_partial_history_raw=p.BASE/'backfilled_panel_candidate_historical_partial_reported_v14_raw'
    if (latest_partial_history_raw/'raw_full_history_audit.json').exists():raw_dir=latest_partial_history_raw
    latest_reconciled_raw=p.BASE/'backfilled_panel_candidate_native_reconciled_v15_raw'
    if (latest_reconciled_raw/'raw_full_history_audit.json').exists():raw_dir=latest_reconciled_raw
    latest_expanded_raw=p.BASE/'backfilled_panel_candidate_native_expanded_v16_raw'
    if (latest_expanded_raw/'raw_full_history_audit.json').exists():raw_dir=latest_expanded_raw
    latest_quote_reconciled_raw=p.BASE/'backfilled_panel_candidate_native_quote_reconciled_v17_raw'
    if (latest_quote_reconciled_raw/'raw_full_history_audit.json').exists():raw_dir=latest_quote_reconciled_raw
    latest_nominal_cut_raw=p.BASE/'backfilled_panel_candidate_native_nominal_cut_v18_raw'
    if (latest_nominal_cut_raw/'raw_full_history_audit.json').exists():raw_dir=latest_nominal_cut_raw
    latest_sampled_quote_raw=p.BASE/'backfilled_panel_candidate_native_sampled_quote_v19_raw'
    if (latest_sampled_quote_raw/'raw_full_history_audit.json').exists():raw_dir=latest_sampled_quote_raw
    latest_strict_endpoint_raw=p.BASE/'backfilled_panel_candidate_native_strict_endpoint_v20_raw'
    if (latest_strict_endpoint_raw/'raw_full_history_audit.json').exists():raw_dir=latest_strict_endpoint_raw
    latest_first_hour_raw=p.BASE/'backfilled_panel_candidate_native_first_hour_extrema_v21_raw'
    if (latest_first_hour_raw/'raw_full_history_audit.json').exists():raw_dir=latest_first_hour_raw
    latest_second_raw=p.BASE/'backfilled_panel_candidate_native_observed_second_v22_raw'
    if (latest_second_raw/'raw_full_history_audit.json').exists():raw_dir=latest_second_raw
    latest_first_quote_raw=p.BASE/'backfilled_panel_candidate_native_first_quote_v23_raw'
    if (latest_first_quote_raw/'raw_full_history_audit.json').exists():raw_dir=latest_first_quote_raw
    latest_session_raw=p.BASE/'backfilled_panel_candidate_native_session_quantity_v24_raw'
    if (latest_session_raw/'raw_full_history_audit.json').exists():raw_dir=latest_session_raw
    latest_actual_quantity_raw=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_raw'
    if (latest_actual_quantity_raw/'raw_full_history_audit.json').exists():raw_dir=latest_actual_quantity_raw
    latest_cached_second_raw=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'
    if (latest_cached_second_raw/'raw_full_history_audit.json').exists():raw_dir=latest_cached_second_raw
    raw_receipt_path=raw_dir/'raw_panel_merge_receipt.json'
    raw_audit_path=raw_dir/'raw_full_history_audit.json'
    if raw_audit_path.exists():
        audit=p.json.loads(raw_audit_path.read_text())
        assert audit['passed'] and audit['stocks_checked']==1178
        assert audit['audited_merge_receipt_sha256']==p.sha(raw_receipt_path)
        assert audit['audited_raw_meta_sha256']==p.sha(raw_dir/'dataset/meta.json')
        latest_raw_meta=p.json.loads((raw_dir/'dataset/meta.json').read_text())
        admitted=audit.get('total_admitted_raw_hours',audit['added_raw_hours'])
        stockdays_any=audit.get('stockdays_with_any_admitted_raw_hours',audit.get('added_raw_stockdays'))
        stockdays_full=audit.get('stockdays_with_all_four_admitted_raw_hours',audit.get('added_raw_stockdays'))
        summary.update(candidate_hours_admitted_this_resume=admitted,full_tail_gap_hours_remaining=latest_raw_meta['tail_missing_hours'],
            historical_gap_hours_remaining=latest_raw_meta['historical_minimum_missing_hours'],
            historical_hours_admitted_this_resume=latest_raw_meta.get('historical_admitted_raw_hours',0),
            tail_hours_admitted_this_resume=latest_raw_meta.get('tail_admitted_raw_hours',admitted),
            new_candidate_panel_built=True,new_raw_panel_full_scope_audit_complete=True,
            new_candidate_raw_panel=dict(candidate_version=raw_dir.name,stockdays_with_any_admitted_raw_hours=stockdays_any,
                stockdays_with_all_four_admitted_raw_hours=stockdays_full,added_raw_hours=admitted,
                stocks_audited=1178,unexpected_cell_changes=audit['unexpected_cell_changes'],
                primary_suspension_hours_promoted=audit['primary_suspension_hours_promoted'],model_input_complete=False,
                receipt_path=str(raw_receipt_path),audit_path=str(raw_audit_path),peak_rss_bytes=audit['peak_rss_bytes']))
        summary['new_candidate_minute_staging'].update(parent_panel_merged=True,formal_parent_panel_hours_admitted=88)
        additional_path=p.OUT/'additional_native_hour_staging/receipt.json'
        if additional_path.exists():
            extra=p.json.loads(additional_path.read_text())
            extra_proof=p.json.loads((additional_path.parent/'independent_verification.json').read_text())
            assert extra_proof['passed'] and extra_proof['audited_receipt_sha256']==p.sha(additional_path)
            summary['additional_native_hour_staging']=dict(stockdays_with_qualified_hours=extra['staged_stockdays'],
                qualified_additional_hours=extra['staged_additional_hours'],independent_verification_passed=True,
                zero_trade_quote_minutes=extra_proof['admitted_hour_zero_quote_minutes'],
                quarantined_hour_reasons=extra['quarantine_reason_counts'],receipt_path=str(additional_path),
                peak_rss_bytes=extra['peak_rss_bytes'],merged_into_current_raw_candidate=True)
    derived_dir=p.BASE/'backfilled_panel_candidate_native_event_v8_derived'
    second_derived=p.BASE/'backfilled_panel_candidate_native_second_v10_derived'
    if (second_derived/'model_input_independent_verification.json').exists():derived_dir=second_derived
    latest_ms_derived=p.BASE/'backfilled_panel_candidate_native_millisecond_v11_derived'
    if (latest_ms_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_ms_derived
    latest_quote_derived=p.BASE/'backfilled_panel_candidate_native_quote_v12_derived'
    if (latest_quote_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_quote_derived
    latest_history_derived=p.BASE/'backfilled_panel_candidate_historical_reported_v13_derived'
    if (latest_history_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_history_derived
    latest_partial_history_derived=p.BASE/'backfilled_panel_candidate_historical_partial_reported_v14_derived'
    if (latest_partial_history_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_partial_history_derived
    latest_reconciled_derived=p.BASE/'backfilled_panel_candidate_native_reconciled_v15_derived'
    if (latest_reconciled_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_reconciled_derived
    latest_expanded_derived=p.BASE/'backfilled_panel_candidate_native_expanded_v16_derived'
    if (latest_expanded_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_expanded_derived
    latest_quote_reconciled_derived=p.BASE/'backfilled_panel_candidate_native_quote_reconciled_v17_derived'
    if (latest_quote_reconciled_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_quote_reconciled_derived
    latest_nominal_cut_derived=p.BASE/'backfilled_panel_candidate_native_nominal_cut_v18_derived'
    if (latest_nominal_cut_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_nominal_cut_derived
    latest_sampled_quote_derived=p.BASE/'backfilled_panel_candidate_native_sampled_quote_v19_derived'
    if (latest_sampled_quote_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_sampled_quote_derived
    latest_strict_endpoint_derived=p.BASE/'backfilled_panel_candidate_native_strict_endpoint_v20_derived'
    if (latest_strict_endpoint_derived/'hour_feature_label_independent_verification.json').exists():derived_dir=latest_strict_endpoint_derived
    latest_first_hour_derived=p.BASE/'backfilled_panel_candidate_native_first_hour_extrema_v21_derived'
    if (latest_first_hour_derived/'model_input_independent_verification.json').exists():derived_dir=latest_first_hour_derived
    latest_actual_quantity_derived=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_derived'
    if (latest_actual_quantity_derived/'model_input_independent_verification.json').exists():derived_dir=latest_actual_quantity_derived
    latest_cached_second_derived=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_derived'
    if (latest_cached_second_derived/'model_input_independent_verification.json').exists():derived_dir=latest_cached_second_derived
    derived_path=derived_dir/'hour_feature_label_rebuild_receipt.json'
    derived_proof_path=derived_dir/'hour_feature_label_independent_verification.json'
    if derived_proof_path.exists():
        derived=p.json.loads(derived_path.read_text())
        derived_proof=p.json.loads(derived_proof_path.read_text())
        assert derived_proof['passed'] and derived_proof['audited_rebuild_receipt_sha256']==p.sha(derived_path)
        derived_raw_audit_path=p.BASE/derived['raw_candidate']/'raw_full_history_audit.json'
        assert derived_proof['raw_full_history_audit_sha256']==p.sha(derived_raw_audit_path)
        summary.update(latest_raw_candidate_model_inputs_pending=derived['raw_candidate']!=raw_dir.name or not (derived_dir/'model_input_independent_verification.json').exists(),derived_model_raw_candidate=derived['raw_candidate'],derived_model_admitted_raw_hours=p.json.loads((p.BASE/derived['raw_candidate']/'dataset/meta.json').read_text())['admitted_raw_hours'],
            new_features_or_labels_recalculated=True,new_hour_features_labels_full_scope_audit_complete=True,
            new_hour_features_and_labels=dict(stocks_recalculated=1178,independent_audit_passed=True,
                hour_feature_rows_changed=derived['total_hour_feature_rows_changed'],label_rows_changed=derived['total_label_rows_changed'],
                accepted_new_execution_windows=derived['counts']['execution_window_accepted'],
                late_recorded_execution_windows_kept_missing=derived['counts']['execution_window_recorded_source_dependency_after_fixed_window_end'],
                zero_volume_execution_windows_kept_missing=derived['counts']['execution_window_zero_true_window_volume'],unknown_arrival_execution_windows_kept_missing=derived['counts'].get('execution_window_unknown_source_arrival_window_kept_missing',0),
                historical_reported_proxy_windows_accepted=derived['counts'].get('historical_reported_execution_proxy_accepted',0),
                historical_reported_zero_proxy_windows_kept_missing=derived['counts'].get('historical_reported_zero_window_kept_missing',0),
                slow_daily_features_recalculated=False,normalized122_feature_X_built=False,
                receipt_path=str(derived_path),audit_path=str(derived_proof_path),peak_rebuild_rss_bytes=derived['peak_rss_bytes'],
                peak_audit_rss_bytes=derived_proof['peak_rss_bytes']))
        summary['new_candidate_minute_staging']['features_and_labels_recalculated']=True
    model_proof_path=derived_dir/'model_input_independent_verification.json'
    if model_proof_path.exists():
        model=p.json.loads(model_proof_path.read_text())
        model_creator_path=derived_dir/'model_input_assembly_receipt.json'
        model_creator=p.json.loads(model_creator_path.read_text())
        assert model['passed'] and model['audited_assembly_receipt_sha256']==p.sha(model_creator_path)
        assert model['final_dataset_meta_sha256']==p.sha(derived_dir/'dataset/meta.json')
        assert model['dataset_audit_sha256']==p.sha(derived_dir/'dataset/dataset_audit.json')
        summary.update(new_normalized122_feature_X_built=True,new_model_input_full1178_tensor_audit_complete=True,
            full_data_backfill_acceptance_complete=False,training_admission_ready=False,
            new_model_inputs=dict(shape=[8476,1178,122],independent_tensor_audit_passed=True,
                normalized_feature_cells_checked=model['all122_normalized_feature_cells_checked'],normalization_max_abs_error=model['normalization_max_abs_error'],
                changed_member_normalized_cells=model['changed_member_normalized_cells'],
                failed_original_coverage_gate_years=model['failed_original_coverage_gate_years'],training_admission_ready=False,
                production_ready=False,slow_raw_source_values_reused_only_after_invariance_proof=True,
                assembly_peak_rss_bytes=model_creator['peak_rss_bytes'],audit_peak_rss_bytes=model['peak_rss_bytes'],
                assembly_receipt_path=str(model_creator_path),audit_path=str(model_proof_path),dataset_path=str(derived_dir/'dataset')))
        summary['new_hour_features_and_labels']['normalized122_feature_X_built']=True
    historical_contract_path=p.OUT/'historical_contract_reconciliation/receipt.json'
    if historical_contract_path.exists():
        contract=p.json.loads(historical_contract_path.read_text())
        summary['historical_original_contract_reconciliation']=dict(next_batch_stockdays=contract['next_batch_stockdays'],
            next_batch_hours=contract['next_batch_hours'],candidate_hours_admitted=0,original_gates_waived=False,
            source_precision_and_failed_cash_diagnostics_preserved=True,receipt_path=str(historical_contract_path))
    manifest_path=p.BASE/names[0]
    manifest=p.json.loads(manifest_path.read_text())
    manifest.update(status='bounded_data_backfill_resumed_validation_pending',latest_local_updated_utc=summary['updated_utc'],checked_utc=summary['updated_utc'])
    manifest['data_backfill']['bounded_streaming_resume']=summary
    manifest['user_resume']=dict(authorized_by='2026-10-09 /goal user request',started_utc=acq['started_utc'],previous_pause_retained_as_history=True)
    sina_path=p.OUT/'sina_tail_minute_expanded/receipt.json'
    sina_proof_path=sina_path.parent/'independent_verification.json'
    if sina_proof_path.exists():
        sina=p.json.loads(sina_path.read_text());sina_proof=p.json.loads(sina_proof_path.read_text())
        assert sina_proof['passed'] and sina_proof['audited_receipt_sha256']==p.sha(sina_path)
        summary['additional_sina_minute_reference']=dict(requested_symbols=sina['symbols_requested'],stocks_verified=sina_proof['stocks_verified'],paired_real_minutes_checked=sina_proof['paired_real_minutes_checked'],all60_price_volume_exact_diagnostic_hours=sina_proof['all60_price_volume_exact_hours'],candidate_hours_admitted=0,independent_verification_passed=True,cash_disagreements_preserved=True,source_arrival_time_or_live_clock_certified=False,receipt_path=str(sina_path),audit_path=str(sina_proof_path),peak_rss_bytes=sina['peak_rss_bytes'])
    staged_second_path=p.OUT/'sina_corrob_native_minute_staging/independent_verification.json'
    if staged_second_path.exists():
        staged_second=p.json.loads(staged_second_path.read_text());staged_second_receipt=staged_second_path.parent/'receipt.json'
        assert staged_second['passed'] and staged_second['audited_receipt_sha256']==p.sha(staged_second_receipt)
        summary['source_second_minutes_pending_arrival_qualification']=dict(raw_market_minutes_admitted=raw_dir.name in ['backfilled_panel_candidate_native_second_v10_raw','backfilled_panel_candidate_native_millisecond_v11_raw','backfilled_panel_candidate_native_quote_v12_raw','backfilled_panel_candidate_historical_reported_v13_raw','backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],stockdays=staged_second['stockdays_verified'],hours=staged_second['hours_minute_truth_verified'],real_minutes=staged_second['real_minutes_verified'],minute_truth_independent_audit_passed=True,initial_stage_source_arrival_certificate=False,source_reception_evidence_bound_in_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_millisecond_v11_raw','backfilled_panel_candidate_native_quote_v12_raw','backfilled_panel_candidate_historical_reported_v13_raw','backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],source_live_clock_certificate=False,candidate_hours_admitted=0,audit_path=str(staged_second_path),peak_rss_bytes=staged_second['peak_rss_bytes'])
    cash_proof_path=p.OUT/'sina_tail_minute_expanded/cash_precision_independent_verification.json'
    if cash_proof_path.exists():
        cash=p.json.loads(cash_proof_path.read_text());cash_receipt=cash_proof_path.parent/'cash_precision_diagnostic.json'
        assert cash['passed'] and cash['audited_diagnostic_receipt_sha256']==p.sha(cash_receipt)
        summary['sina_cash_precision_diagnostic']=dict(paired_minutes=cash['paired_minutes'],float32_product_sum_reproduced=cash['source_amount_reproduced_by_float32_product_sum'],remaining_within_one_reported_decimal=cash['remaining_within_one_reported_cash_decimal'],cash_gate_relaxed=False,source_implementation_not_certified=True,audit_path=str(cash_proof_path))
    extra_minute_path=p.OUT/'sina_tail_nonmonotonic_references/independent_verification.json'
    if extra_minute_path.exists():
        extra_minute=p.json.loads(extra_minute_path.read_text())
        extra_receipt=extra_minute_path.parent/'receipt.json'
        extra_eligibility=extra_minute_path.parent/'raw10_eligibility_diagnostic.json'
        assert extra_minute['passed'] and extra_minute['audited_receipt_sha256']==p.sha(extra_receipt)
        eligibility=p.json.loads(extra_eligibility.read_text())
        assert eligibility['minute_reference_proof_sha256']==p.sha(extra_minute_path)
        summary['additional_nonmonotonic_sina_reference']=dict(stocks_verified=extra_minute['stocks_verified'],
            paired_real_minutes_checked=extra_minute['paired_real_minutes_checked'],all60_exact_diagnostic_hours=extra_minute['all60_price_volume_exact_hours'],
            counts=eligibility['counts'],global_order_failures_preserved=True,candidate_hours_admitted=0,
            diagnostic128_now_merged_in_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_millisecond_v11_raw','backfilled_panel_candidate_native_quote_v12_raw','backfilled_panel_candidate_historical_reported_v13_raw','backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],
            audit_path=str(extra_minute_path),eligibility_path=str(extra_eligibility))
    sep22_archive_path=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive_sep22/acquisition_and_extraction.json'
    if sep22_archive_path.exists():
        sep22_archive=p.json.loads(sep22_archive_path.read_text())
        summary['additional_sep22_native_archive']=dict(status=sep22_archive['status'],received_bytes=sep22_archive.get('received_total_bytes',0),
            published_bytes=sep22_archive['published_bytes'],whole_published_digest_verified=sep22_archive['whole_published_LFS_SHA256_verified'],
            gzip_crc_verified=sep22_archive['whole_gzip_CRC_verified'],members_written=len(sep22_archive['records']),
            transfer_errors=sum('error_type' in r for r in sep22_archive['requests']),
            peak_rss_bytes=sep22_archive['peak_rss_bytes'],candidate_hours_admitted=0,receipt_path=str(sep22_archive_path))
    quote_stage_path=p.OUT/'sep22_quote_witness_minute_staging/receipt.json'
    quote_proof_path=quote_stage_path.parent/'independent_verification.json'
    if quote_proof_path.exists():
        quote_stage=p.json.loads(quote_stage_path.read_text());quote_proof=p.json.loads(quote_proof_path.read_text())
        assert quote_proof['passed'] and quote_proof['audited_receipt_sha256']==p.sha(quote_stage_path)
        summary['sep22_real_quote_witness_expansion']=dict(hours_verified=quote_stage['hours_staged'],stockdays_verified=quote_proof['stocks_verified'],
            real_trade_minutes=quote_proof['counts']['real_trade_minutes'],real_zero_trade_quote_minutes=quote_proof['counts']['exact_prefix_zero_quote_minutes'],
            rejected_hours=quote_stage['excluded'],merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_quote_v12_raw','backfilled_panel_candidate_historical_reported_v13_raw','backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],
            all_prior_market_values_unchanged=True,source_live_clock_certificate=False,audit_path=str(quote_proof_path),
            peak_stage_rss_bytes=quote_stage['peak_rss_bytes'],peak_audit_rss_bytes=quote_proof['peak_rss_bytes'])
    historical_stage_path=p.OUT/'historical_reported_v13_staging/receipt.json'
    historical_proof_path=historical_stage_path.parent/'independent_verification.json'
    if historical_proof_path.exists():
        historical_stage=p.json.loads(historical_stage_path.read_text());historical_proof=p.json.loads(historical_proof_path.read_text())
        assert historical_proof['passed'] and historical_proof['audited_receipt_sha256']==p.sha(historical_stage_path)
        summary['historical_reported_native_expansion']=dict(stockdays_verified=historical_proof['stockdays_verified'],hours_verified=historical_proof['hours_verified'],
            real_observed_reported_minutes=historical_proof['minutes_verified'],quarantined_hours=historical_stage['quarantined_hours'],
            merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_historical_reported_v13_raw','backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],
            reported_integer_lot_precision_preserved=True,true_share_quantity_recovered=False,unknown_receiver_times_preserved=True,
            original_retrospective_execution_proxy_policy_preserved=True,source_live_clock_certificate=False,audit_path=str(historical_proof_path),
            peak_stage_rss_bytes=historical_stage['peak_rss_bytes'],peak_audit_rss_bytes=historical_proof['peak_rss_bytes'])
    partial_stage_path=p.OUT/'historical_partial_reported_v14_staging/receipt.json'
    partial_proof_path=partial_stage_path.parent/'independent_verification.json'
    if partial_proof_path.exists():
        partial_stage=p.json.loads(partial_stage_path.read_text());partial_proof=p.json.loads(partial_proof_path.read_text())
        assert partial_proof['passed'] and partial_proof['audited_receipt_sha256']==p.sha(partial_stage_path)
        summary['historical_partial_reported_native_expansion']=dict(stockdays_verified=partial_proof['stockdays_verified'],hours_verified=partial_proof['hours_verified'],
            real_observed_reported_minutes=partial_proof['minutes_verified'],quarantined_hours=partial_stage['quarantined_hours'],
            merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],
            selected_hour_12_five_minute_references_exact=True,other_hour_source_conflicts_preserved=True,
            reported_integer_lot_precision_preserved=True,true_share_quantity_recovered=False,unknown_receiver_times_preserved=True,
            original_retrospective_execution_proxy_policy_preserved=True,source_live_clock_certificate=False,audit_path=str(partial_proof_path),
            peak_stage_rss_bytes=partial_stage['peak_rss_bytes'],peak_audit_rss_bytes=partial_proof['peak_rss_bytes'])
    reconciliation_path=p.OUT/'tail_previous_close_coarse_reconciliation/receipt.json'
    reconciliation_proof_path=reconciliation_path.parent/'independent_verification.json'
    if reconciliation_proof_path.exists():
        reconciliation=p.json.loads(reconciliation_path.read_text());reconciliation_proof=p.json.loads(reconciliation_proof_path.read_text())
        assert reconciliation_proof['passed'] and reconciliation_proof['audited_receipt_sha256']==p.sha(reconciliation_path)
        summary['tail_previous_close_coarse_reconciliation']=dict(independently_verified=True,
            minute_exact_hours_checked=reconciliation['counts']['minute_exact_diagnostic_hours'],
            remaining_minute_exact_hours=reconciliation['counts']['remaining_minute_exact_hours'],
            fixed_formula_and_previous_minute_close_exact_hours=reconciliation['counts']['remaining_fixed_formula_and_previous_close_reference_exact'],
            other_coarse_conflicts_preserved=reconciliation['counts']['remaining_minute_exact_hours']-reconciliation['counts']['remaining_fixed_formula_exact'],
            candidate_hours_admitted=0,native_first_trade_definition_unchanged=True,source_vendor_implementation_certified=False,
            audit_path=str(reconciliation_proof_path),peak_rss_bytes=reconciliation_proof['peak_rss_bytes'])
    reconciled_stage_path=p.OUT/'reconciled_tail_millisecond_staging/receipt.json'
    reconciled_proof_path=reconciled_stage_path.parent/'independent_verification.json'
    if reconciled_proof_path.exists():
        reconciled_stage=p.json.loads(reconciled_stage_path.read_text());reconciled_proof=p.json.loads(reconciled_proof_path.read_text())
        assert reconciled_proof['passed'] and reconciled_proof['audited_receipt_sha256']==p.sha(reconciled_stage_path)
        summary['reconciled_tail_native_minute_staging']=dict(stockdays_verified=reconciled_proof['stockdays_verified'],hours_verified=reconciled_proof['hours_verified'],
            actual_minutes_verified=reconciled_proof['minutes_verified'],all_native_daily_six_exact=True,actual_receiver_dependencies_verified=True,
            native_first_trade_price_preserved=True,coarse_previous_close_discrepancy_reproduced=True,candidate_hours_admitted=0,
            merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],production_ready=False,audit_path=str(reconciled_proof_path),
            peak_stage_rss_bytes=reconciled_stage['peak_rss_bytes'],peak_audit_rss_bytes=reconciled_proof['peak_rss_bytes'])
    older_probe_path=p.OUT/'older_coarse_frequency_and_alias_probe/independent_verification.json'
    if older_probe_path.exists():
        older_probe=p.json.loads(older_probe_path.read_text());assert older_probe['passed'] and older_probe['audited_receipt_sha256']==p.sha(older_probe_path.parent/'receipt.json')
        summary['older_frequency_and_official_alias_diagnostic']=dict(queries_verified=len(older_probe['queries_verified']),empty_queries=5,positive_hour_control_2021=True,
            official_old_code_001914_query_still_empty=True,all_older_source_availability_not_inferred=True,candidate_hours_admitted=0,audit_path=str(older_probe_path))
    remaining_sina_path=p.OUT/'sina_remaining376_tail_references/receipt.json'
    if remaining_sina_path.exists():
        remaining_sina=p.json.loads(remaining_sina_path.read_text())
        summary['remaining376_sina_query_progress']=dict(status=remaining_sina['status'],symbols_requested=remaining_sina['symbols_requested'],
            symbols_processed=len(remaining_sina['records']),successful_symbols=sum(r['status']=='compared_without_admission' for r in remaining_sina['records']),
            diagnostic_all60_hours=remaining_sina.get('counts',{}).get('all60_price_volume_exact_hours',0),candidate_hours_admitted=0,
            independent_verification_complete=(remaining_sina_path.parent/'independent_verification.json').exists(),
            shared_native_leaves_scanned_once=len(remaining_sina['native_source_leaf_pins']),receipt_path=str(remaining_sina_path),peak_rss_bytes=remaining_sina['peak_rss_bytes'])
    expanded_stage_path=p.OUT/'expanded_tail_millisecond_v16_staging/receipt.json'
    expanded_proof_path=expanded_stage_path.parent/'independent_verification.json'
    if expanded_proof_path.exists():
        expanded_stage=p.json.loads(expanded_stage_path.read_text());expanded_proof=p.json.loads(expanded_proof_path.read_text())
        assert expanded_proof['passed'] and expanded_proof['audited_receipt_sha256']==p.sha(expanded_stage_path)
        summary['expanded376_tail_native_minute_staging']=dict(stockdays_verified=expanded_proof['stockdays_verified'],hours_verified=expanded_proof['hours_verified'],
            actual_minutes_verified=expanded_proof['minutes_verified'],counts=expanded_stage['counts'],rejected_hours_verified=len(expanded_proof['rejections']),
            all_candidate_and_rejected_hours_reproduced=expanded_proof['all_candidate_and_rejected_hours_independently_reproduced'],
            merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],candidate_hours_admitted=0,production_ready=False,
            audit_path=str(expanded_proof_path),peak_stage_rss_bytes=expanded_stage['peak_rss_bytes'],peak_audit_rss_bytes=expanded_proof['peak_rss_bytes'])
    quote_reconciled_stage_path=p.OUT/'two_day_quote_reconciled_v17_staging/receipt.json'
    quote_reconciled_proof_path=quote_reconciled_stage_path.parent/'independent_verification.json'
    if quote_reconciled_proof_path.exists():
        quote_reconciled_stage=p.json.loads(quote_reconciled_stage_path.read_text());quote_reconciled_proof=p.json.loads(quote_reconciled_proof_path.read_text())
        assert quote_reconciled_proof['passed'] and quote_reconciled_proof['audited_receipt_sha256']==p.sha(quote_reconciled_stage_path)
        summary['two_day_quote_reconciled_native_minute_staging']=dict(stockdays_verified=quote_reconciled_proof['stockdays_verified'],hours_verified=quote_reconciled_proof['hours_verified'],
            actual_minutes_verified=quote_reconciled_proof['minutes_verified'],genuine_zero_trade_quote_minutes=quote_reconciled_stage['genuine_zero_trade_quote_minutes'],
            rejected_hours_verified=len(quote_reconciled_proof['rejections']),all_candidate_and_rejected_hours_reproduced=quote_reconciled_proof['all_candidate_and_rejected_hours_independently_reproduced'],
            merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],candidate_hours_admitted=0,production_ready=False,
            audit_path=str(quote_reconciled_proof_path),peak_stage_rss_bytes=quote_reconciled_stage['peak_rss_bytes'],peak_audit_rss_bytes=quote_reconciled_proof['peak_rss_bytes'])
    nominal_stage_path=p.OUT/'nominal_quote_cut_reconciled_v18_staging/receipt.json'
    nominal_proof_path=nominal_stage_path.parent/'independent_verification.json'
    if nominal_proof_path.exists():
        nominal_stage=p.json.loads(nominal_stage_path.read_text());nominal_proof=p.json.loads(nominal_proof_path.read_text())
        assert nominal_proof['passed'] and nominal_proof['audited_receipt_sha256']==p.sha(nominal_stage_path)
        summary['nominal_quote_cut_native_minute_staging']=dict(stockdays_verified=nominal_proof['stockdays_verified'],hours_verified=nominal_proof['hours_verified'],actual_minutes_verified=nominal_proof['minutes_verified'],rejected_hours_verified=nominal_proof['excluded_hours_verified'],all_candidates_and_rejections_reproduced=nominal_proof['all_candidates_and_rejections_independently_reproduced'],merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],candidate_hours_admitted=0,production_ready=False,audit_path=str(nominal_proof_path),peak_stage_rss_bytes=nominal_stage['peak_rss_bytes'],peak_audit_rss_bytes=nominal_proof['peak_rss_bytes'])
    sampled_stage_path=p.OUT/'sampled_quote_reconciled_v19_staging/receipt.json'
    sampled_proof_path=sampled_stage_path.parent/'independent_verification.json'
    if sampled_proof_path.exists():
        sampled_stage=p.json.loads(sampled_stage_path.read_text());sampled_proof=p.json.loads(sampled_proof_path.read_text())
        assert sampled_proof['passed'] and sampled_proof['audited_receipt_sha256']==p.sha(sampled_stage_path)
        summary['sampled_quote_native_minute_staging']=dict(stockdays_verified=sampled_proof['stockdays_verified'],hours_verified=sampled_proof['hours_verified'],actual_minutes_verified=sampled_proof['minutes_verified'],all_actual_prefix_quote_samples_verified=sampled_proof['additional_diagnostics']['all_actual_prefix_quote_samples_independently_verified'],excluded_hours_verified=sampled_proof['excluded_hours_verified'],merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],candidate_hours_admitted=0,production_ready=False,audit_path=str(sampled_proof_path),peak_stage_rss_bytes=sampled_stage['peak_rss_bytes'],peak_audit_rss_bytes=sampled_proof['peak_rss_bytes'])
    strict_stage_path=p.OUT/'strict_endpoint_quote_reconciled_v20_staging/receipt.json'
    strict_proof_path=strict_stage_path.parent/'independent_verification.json'
    if strict_proof_path.exists():
        strict_stage=p.json.loads(strict_stage_path.read_text());strict_proof=p.json.loads(strict_proof_path.read_text())
        assert strict_proof['passed'] and strict_proof['audited_receipt_sha256']==p.sha(strict_stage_path)
        summary['strict_endpoint_quote_native_minute_staging']=dict(stockdays_verified=strict_proof['stockdays_verified'],hours_verified=strict_proof['hours_verified'],actual_minutes_verified=strict_proof['minutes_verified'],all_actual_prefix_quote_samples_verified=strict_proof['additional_diagnostics']['all_actual_prefix_quote_samples_independently_verified'],excluded_hours_verified=strict_proof['excluded_hours_verified'],merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],candidate_hours_admitted=0,production_ready=False,audit_path=str(strict_proof_path),peak_stage_rss_bytes=strict_stage['peak_rss_bytes'],peak_audit_rss_bytes=strict_proof['peak_rss_bytes'])
    daily_fail_path=p.OUT/'historical_daily_failure_definition_diagnostic/receipt.json'
    if daily_fail_path.exists():
        daily_fail=p.json.loads(daily_fail_path.read_text());assert daily_fail['status']=='complete_cached1422_daily_failure_diagnostic_no_admission'
        summary['historical_cached_daily_failure_causes']=dict(hours_checked=daily_fail['missing_hours_checked'],stockdays_checked=daily_fail['stockdays_checked'],field_failure_hours=daily_fail['field_failure_hours'],failure_combination_hours=daily_fail['failure_combination_hours'],source_year_files_reopened=0,whole_file_fingerprints_repeated=0,candidate_hours_admitted=0,receipt_path=str(daily_fail_path))
    kit_qty_path=p.OUT/'kit_wind_minute_quantity_diagnostic/receipt.json'
    if kit_qty_path.exists():
        kit_qty=p.json.loads(kit_qty_path.read_text());assert kit_qty['status']=='complete18day_quantity_diagnostic_no_admission'
        summary['historical_minute_quantity_comparison']=dict(checked_days=18,checked_minutes=4320,all240_floor_model_exact_days=kit_qty['counts']['floor_published_quantity_to100_shares_exact_days'],whole_file_prefingerprints_repeated=0,year_files_usefully_read=kit_qty['source_year_files_usefully_read'],existing_selected_days_reused=kit_qty['existing_selected_days_reused'],candidate_hours_admitted=0,receipt_path=str(kit_qty_path))
    hist_open_path=p.OUT/'historical_opening_row_diagnostic/receipt.json'
    if hist_open_path.exists():
        hist_open=p.json.loads(hist_open_path.read_text());assert hist_open['status']=='complete16_open_only_source_diagnostic_no_admission'
        summary['historical_opening_row_comparison']=dict(checked_stockdays=16,checked_hours=64,source0930_zero_volume_days=hist_open['counts']['zero_volume0930_days'],source0930_open_primary_daily_exact_days=hist_open['counts']['observed0930_open_exact_primary_open_days'],source0930_close_primary_daily_exact_days=0,candidate_hours_admitted=0,zero_volume_quote_not_relabelled_auction_trade=True,receipt_path=str(hist_open_path))
    first_stage_path=p.OUT/'first_hour_cumulative_extrema_v21_staging/receipt.json'
    first_proof_path=first_stage_path.parent/'independent_verification.json'
    if first_proof_path.exists():
        first_stage=p.json.loads(first_stage_path.read_text());first_proof=p.json.loads(first_proof_path.read_text());assert first_proof['passed'] and first_proof['audited_receipt_sha256']==p.sha(first_stage_path)
        summary['first_hour_cumulative_extrema_staging']=dict(stockdays_verified=first_proof['stockdays_verified'],hours_verified=first_proof['hours_verified'],actual_minutes_verified=first_proof['minutes_verified'],all_actual_prefix_cumulative_extrema_independently_verified=True,merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],candidate_hours_admitted=0,production_ready=False,audit_path=str(first_proof_path),useful_native_rowgroup_reads=first_stage['useful_native_rowgroup_reads'],peak_stage_rss_bytes=first_stage['peak_rss_bytes'],peak_audit_rss_bytes=first_proof['peak_rss_bytes'])
    second_stage=p.OUT/'first_hour_observed_second_close_v22_staging/receipt.json'
    second_proof=second_stage.parent/'independent_verification.json'
    if second_proof.exists():
        sp=p.json.loads(second_proof.read_text());ss=p.json.loads(second_stage.read_text());assert sp['passed'] and sp['audited_receipt_sha256']==p.sha(second_stage)
        summary['observed_second_close_native_minutes']=dict(hours_verified=sp['hours_verified'],minutes_verified=sp['minutes_verified'],stockdays_verified=sp['stockdays_verified'],true_native_minute_definition_unchanged=True,provider_implementation_certified=False,merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_observed_second_v22_raw','backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],audit_path=str(second_proof),stage_peak_rss_bytes=ss['peak_rss_bytes'],audit_peak_rss_bytes=sp['peak_rss_bytes'])
    first_quote_stage=p.OUT/'first_observed_quote_open_v23_staging/receipt.json'
    first_quote_proof=first_quote_stage.parent/'independent_verification.json'
    if first_quote_proof.exists():
        fq=p.json.loads(first_quote_proof.read_text());fs=p.json.loads(first_quote_stage.read_text());assert fq['passed'] and fq['audited_receipt_sha256']==p.sha(first_quote_stage)
        summary['first_observed_quote_open_native_minutes']=dict(hours_verified=fq['hours_verified'],minutes_verified=fq['minutes_verified'],stockdays_verified=fq['stockdays_verified'],all_actual_prefix_quote_samples_independently_verified=fq['additional_diagnostics']['all_actual_prefix_quote_samples_independently_verified'],true_native_minute_definition_unchanged=True,provider_implementation_certified=False,merged_into_latest_raw=raw_dir.name in ['backfilled_panel_candidate_native_first_quote_v23_raw','backfilled_panel_candidate_native_session_quantity_v24_raw','backfilled_panel_candidate_native_actual_quantity_v25_raw','backfilled_panel_candidate_native_cached_second_v26_raw'],audit_path=str(first_quote_proof),stage_peak_rss_bytes=fs['peak_rss_bytes'],audit_peak_rss_bytes=fq['peak_rss_bytes'])
    for summary_key,stage_name in [('closed_morning_quantity_native_minutes','afternoon_closed_morning_quantity_v24_staging'),('actual_interval_quantity_native_minutes','actual_interval_quantity_v25_staging')]:
        stage_path=p.OUT/stage_name/'receipt.json';proof_path=stage_path.parent/'independent_verification.json'
        if proof_path.exists():
            sp=p.json.loads(proof_path.read_text());ss=p.json.loads(stage_path.read_text());assert sp['passed'] and sp['audited_receipt_sha256']==p.sha(stage_path)
            summary[summary_key]=dict(hours_verified=sp['hours_verified'],minutes_verified=sp['minutes_verified'],stockdays_verified=sp['stockdays_verified'],all_actual_prefix_quote_samples_independently_verified=sp['additional_diagnostics']['all_actual_prefix_quote_samples_independently_verified'],true_native_minute_definition_unchanged=True,provider_implementation_certified=False,audit_path=str(proof_path),stage_peak_rss_bytes=ss['peak_rss_bytes'],audit_peak_rss_bytes=sp['peak_rss_bytes'])
    earlier_path=p.OUT/'cached_sina_sept21_actual_hour_comparison/receipt.json'
    if earlier_path.exists():
        earlier=p.json.loads(earlier_path.read_text());summary['cached_earlier_minute_comparison']=dict(stockhours_compared=len(earlier['records']),counts=earlier['counts'],actual_source_arrival_certified=False,candidate_hours_admitted=0,receipt_path=str(earlier_path),network_requests=0)
    cached_stage_path=p.OUT/'cached_sept21_source_second_v26_staging/receipt.json'
    cached_proof_path=cached_stage_path.parent/'independent_verification.json'
    if cached_proof_path.exists():
        cs=p.json.loads(cached_stage_path.read_text());cp=p.json.loads(cached_proof_path.read_text())
        assert cp['passed'] and cp['audited_receipt_sha256']==p.sha(cached_stage_path)
        summary['cached_sept21_native_minutes']=dict(hours_verified=cp['hours_minute_truth_verified'],actual_minutes_verified=cp['real_minutes_verified'],merged_into_latest_raw=raw_dir.name=='backfilled_panel_candidate_native_cached_second_v26_raw',source_arrival_or_live_clock_certified=False,unknown_reception_timestamps_preserved=True,excluded_stockdays=cs['excluded_stockdays'],receipt_path=str(cached_stage_path),audit_path=str(cached_proof_path))
    stream_archive_path=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive_sep24_streaming/acquisition_and_extraction.json'
    if stream_archive_path.exists():
        stream_archive=p.json.loads(stream_archive_path.read_text())
        summary['additional_sep24_single_stream_archive']=dict(status=stream_archive['status'],received_compressed_bytes=stream_archive.get('received_total_bytes',0),published_compressed_bytes=stream_archive['published_bytes'],native_members_retained=stream_archive.get('retained_data_members_after_scope_cleanup',len(stream_archive['records'])),native_member_bytes_retained=0 if stream_archive.get('own_unverified_payload_files_removed') else sum(r['bytes'] for r in stream_archive['records']),outside_requested_scope_cancelled=stream_archive['status']=='cancelled_outside_requested_calendar_scope',own_unverified_payload_removed_bytes=stream_archive.get('removed_own_unverified_payload_bytes',0),published_SHA_verified=stream_archive['whole_published_LFS_SHA256_verified'],whole_gzip_CRC_verified=stream_archive['whole_gzip_CRC_verified'],compressed_spool_deleted_after_complete_integrity=stream_archive['compressed_spool_deleted_after_full_SHA_and_CRC'],archive_prehash_or_separate_rescan=False,candidate_hours_admitted=0,receipt_path=str(stream_archive_path),peak_rss_bytes=stream_archive['peak_rss_bytes'])
    new_coarse_path=p.BASE/'public_source_probes/alternative_coarse_public_inventory_20261010/ANTICH__traderharness-ashare-5y/pinned_predicate_reference_comparison/receipt.json'
    if new_coarse_path.exists():
        new_coarse=p.json.loads(new_coarse_path.read_text());assert new_coarse['status']=='complete_two_observed_coarse_day_comparisons_no_admission'
        summary['new_public_coarse_stockday_comparison']=dict(stockdays_compared=2,observed_rows=96,both_all48_six_fields_exact_existing_Baostock=all(r['all48_six_fields_exact_existing_Baostock'] for r in new_coarse['records']),new_original_strict_coarse_pass_hours=sum(len(r['strict_coarse_pass_slots']) for r in new_coarse['records']),complete_publisher_file_SHA_verified=False,new_independent_upstream_certified=False,candidate_hours_admitted=0,receipt_path=str(new_coarse_path))
    matrix_dir=p.BASE/'public_source_probes/additional_native_inventory_20261009/remaining_native_matrix_two_stock_bounded'
    matrix_proof_path=matrix_dir/'independent_daily_diagnostic.json';matrix_coarse_proof_path=matrix_dir/'independent_coarse_wire_verification.json'
    if matrix_coarse_proof_path.exists():
        ma=p.json.loads((matrix_dir/'receipt.json').read_text());md=p.json.loads(matrix_proof_path.read_text());mc=p.json.loads(matrix_coarse_proof_path.read_text())
        assert md['passed'] and mc['passed'] and md['audited_acquisition_sha256']==p.sha(matrix_dir/'receipt.json') and mc['audited_coarse_diagnostic_sha256']==p.sha(matrix_dir/'coarse_reference_diagnostic.json')
        summary['new_scoped_native_historical_matrix_diagnostic']=dict(stockdays=36,actual_missing_hours=144,nonnull_stockdays=35,original_actual_minutes=8640,nonnull_actual_minutes=8400,new_network_bytes=ma['new_network_bytes'],primary_daily_positive_pass_hours=44,previous_daily_quantity_failure_improved_stockdays=29,coarse_actual_empty_stockdays=11,complete_coarse_reference_hours=0,candidate_hours_admitted=0,whole_remote_matrix_SHA_certified=False,source_upstream_independence_certified=False,empty_reference_not_relabelled_suspension=True,source_year_CSV_files_reopened=0,acquisition_peak_rss_bytes=ma['peak_rss_bytes'],independent_decode_peak_rss_bytes=md['peak_rss_bytes'],receipt_path=str(matrix_dir/'receipt.json'),daily_audit_path=str(matrix_proof_path),coarse_audit_path=str(matrix_coarse_proof_path))
    late_matrix=p.BASE/'public_source_probes/additional_native_inventory_20261009/late_remaining_native_matrix_four_stock_bounded'
    late_proof=late_matrix/'independent_coarse_verification.json'
    if late_proof.exists():
        lp=p.json.loads(late_proof.read_text());la=p.json.loads((late_matrix/'receipt.json').read_text());ld=p.json.loads((late_matrix/'independent_daily_diagnostic.json').read_text())
        assert lp['passed'] and ld['passed'] and lp['audited_coarse_diagnostic_sha256']==p.sha(late_matrix/'coarse_reference_diagnostic.json') and lp['audited_native_daily_diagnostic_sha256']==p.sha(late_matrix/'independent_daily_diagnostic.json')
        summary['new_late_native_historical_matrix_diagnostic']=dict(stockdays=6,actual_missing_hours=24,actual_minutes_verified=1440,new_network_bytes=la['new_network_bytes'],primary_daily_positive_pass_hours=16,previous_daily_quantity_failure_improved_stockdays=5,independent_coarse_stockdays_checked=4,actual_coarse_points_checked=192,strict_coarse_pass_hours=0,candidate_hours_admitted=0,original_price_volume_gates_unchanged=True,whole_remote_field_SHA_verified=False,source_upstream_independence_certified=False,receipt_path=str(late_matrix/'receipt.json'),audit_path=str(late_proof),acquisition_peak_rss_bytes=la['peak_rss_bytes'],independent_decode_peak_rss_bytes=ld['peak_rss_bytes'])
    matrix2019=p.BASE/'public_source_probes/additional_native_inventory_20261009/remaining_2019_native_matrix_four_stock_bounded'
    proof2019=matrix2019/'independent_coarse_verification.json'
    if proof2019.exists():
        cp=p.json.loads(proof2019.read_text());ac=p.json.loads((matrix2019/'receipt.json').read_text());dd=p.json.loads((matrix2019/'independent_daily_diagnostic.json').read_text());cr=p.json.loads((matrix2019/'coarse_reference_diagnostic.json').read_text())
        assert cp['passed'] and dd['passed'] and dd['audited_acquisition_sha256']==p.sha(matrix2019/'receipt.json')
        assert cp['audited_coarse_receipt_sha256']==p.sha(matrix2019/'coarse_reference_diagnostic.json') and cp['audited_daily_receipt_sha256']==p.sha(matrix2019/'independent_daily_diagnostic.json')
        summary['new_2019_native_historical_matrix_diagnostic']=dict(stockdays=len(ac['actual_requested_stockdays']),actual_missing_hours=ac['actual_missing_hours'],actual_minutes_verified=dd['counts']['checked_minutes'],new_network_bytes=ac['new_network_bytes'],primary_daily_positive_pass_hours=dd['counts']['daily_positive_pass_hours'],previous_daily_quantity_failure_improved_stockdays=dd['counts']['quantity_corrected_days'],primary_open_failed_stockdays=dd['counts']['open_exact_failed_days'],actual_reference_queries=len(cr['queries']),strict_coarse_pass_hours=cr['strict_coarse_pass_hours'],candidate_hours_admitted=0,whole_remote_field_SHA_verified=False,source_upstream_independence_certified=False,receipt_path=str(matrix2019/'receipt.json'),daily_audit_path=str(matrix2019/'independent_daily_diagnostic.json'),coarse_audit_path=str(proof2019),acquisition_peak_rss_bytes=ac['peak_rss_bytes'],independent_decode_peak_rss_bytes=dd['peak_rss_bytes'])
    qq_m1_proof_path=p.OUT/'tencent_scoped_extended_history_m1_probe/independent_combined_probe_verification.json'
    if qq_m1_proof_path.exists():
        qm=p.json.loads(qq_m1_proof_path.read_text());assert qm['passed'] and qm['candidate_hours_admitted']==0
        for pin in qm['audited_receipt_pins']:assert p.sha(pin['path'])==pin['sha256']
        summary['new_tencent_explicit_end_actual_minute_probe']=dict(actual_public_requests_verified=qm['actual_public_requests_verified'],complete_observed_stockdays=qm['complete_observed_241row_stockdays'],actual_observed_minutes=qm['actual_observed_240_minute_bars'],actual_hour_pilot_checked=4,actual_hour_pilot_exact_hours=qm['actual_four_hour_pilot_exact_hours'],pilot_primary_daily_five_exact_stockdays=qm['pilot_primary_daily_five_exact_stockdays'],candidate_hours_admitted=0,query_policy='explicitYYYYMMDD1501exclusive_end; actual0930auctionand0931through1500endlabels',original_trade_clock_unchanged=True,source_price_volume_conflicts_retained=True,source_live_or_provider_implementation_certified=False,audit_path=str(qq_m1_proof_path))
    full_matrix_root=p.BASE/'public_source_probes/additional_native_inventory_20261009/remaining_historical_native_matrix_full_scope_bounded'
    full_matrix_plan=full_matrix_root/'scope.json'
    if full_matrix_plan.exists():
        fm=p.json.loads(full_matrix_plan.read_text());assert fm['passed']
        batch_info=[]
        for batch_dir in sorted(full_matrix_root.glob('batch_*')):
            qualified_path=batch_dir/'original_minute_and_cash_qualification.json'
            if not qualified_path.exists():continue
            fq=p.json.loads(qualified_path.read_text());fa=p.json.loads((batch_dir/'receipt.json').read_text());fd=p.json.loads((batch_dir/'independent_daily_diagnostic.json').read_text());fc=p.json.loads((batch_dir/'independent_coarse_verification.json').read_text())
            assert fq['passed'] and fd['passed'] and fc['passed'] and fq['source_acquisition_sha256']==p.sha(batch_dir/'receipt.json') and fq['independent_coarse_proof_sha256']==p.sha(batch_dir/'independent_coarse_verification.json')
            batch_info.append(dict(batch=batch_dir.name,stocks=len({r['code'] for r in fa['results']}),stockdays=len(fa['actual_requested_stockdays']),actual_missing_hours=fa['actual_missing_hours'],new_network_bytes=fa['new_network_bytes'],reused_original_fields=sum(r.get('reused_prior_actual_original_range',False) for r in fa['results']),null_field_days=fd['counts'].get('null_field_days',0),daily_positive_pass_hours=fd['counts'].get('daily_positive_pass_hours',0),strict_coarse_pass_hours=fc['counts'].get('strict_coarse_pass_hours',0),original_minute_price_conflict_hours=fq['counts'].get('original_minute_price_conflict_hours',0),cash_feasibility_pass_hours=fq['counts'].get('cash_feasibility_pass_hours',0),candidate_hours_admitted=0,acquisition_peak_rss_bytes=fa['peak_rss_bytes'],original_minute_audit_peak_rss_bytes=fq['peak_rss_bytes'],qualification_path=str(qualified_path)))
        summary['full_remaining_historical_native_matrix_public_source_diagnostic']=dict(planned_stocks=len(fm['stocks']),planned_stockdays=fm['total_stockdays'],planned_missing_hours=fm['total_actual_missing_hours'],estimated_original_column_bytes=fm['total_original_column_bytes'],complete_batches=len(batch_info),complete_stocks=sum(r['stocks'] for r in batch_info),complete_stockdays=sum(r['stockdays'] for r in batch_info),complete_missing_hours_checked=sum(r['actual_missing_hours'] for r in batch_info),new_network_bytes=sum(r['new_network_bytes'] for r in batch_info),strict_coarse_pass_hours=sum(r['strict_coarse_pass_hours'] for r in batch_info),original_minute_price_conflict_hours=sum(r['original_minute_price_conflict_hours'] for r in batch_info),candidate_hours_admitted=0,scope_plan_path=str(full_matrix_plan),batches=batch_info)
    tail_m5_dir=p.OUT/'tail_actual_baostock_five_minute_scope_probe'
    tail_m5_proof=tail_m5_dir/'independent_verification.json'
    if tail_m5_proof.exists():
        bm=p.json.loads(tail_m5_proof.read_text());bs=p.json.loads((tail_m5_dir/'receipt.json').read_text());bc=p.json.loads((tail_m5_dir/'public_response_cap_verification.json').read_text())
        assert bm['passed'] and bm['source_receipt_sha256']==p.sha(tail_m5_dir/'receipt.json') and bc['passed']
        summary['new_tail_actual_five_minute_reference_probe']=dict(**bm['counts'],candidate_hours_admitted=0,original_trade_archives_opened=False,canonical_minute_clock_changed=False,reference_response_cap_bytes=bc['configured_cap_bytes'],response_cap_replay_verified=True,acquisition_peak_rss_bytes=bs['peak_rss_bytes'],independent_verification_peak_rss_bytes=bm['peak_rss_bytes'],source_receipt_path=str(tail_m5_dir/'receipt.json'),audit_path=str(tail_m5_proof))
    qq_remaining_dir=p.OUT/'remaining_tencent_actual_m1_hour_reference_diagnostic'
    qq_remaining_proof=qq_remaining_dir/'independent_verification.json'
    if qq_remaining_proof.exists():
        qr=p.json.loads(qq_remaining_proof.read_text());qs=p.json.loads((qq_remaining_dir/'receipt.json').read_text())
        assert qr['passed'] and qr['all486_actual_missing_hours_accounted_for'] and qr['audited_source_receipt_sha256']==p.sha(qq_remaining_dir/'receipt.json')
        summary['remaining_tencent_actual_minute_hour_reference_audit']=dict(scoped_stockdays=qs['actual_missing_stockdays'],scoped_missing_hours=qs['actual_missing_hours'],actual_stockdays=qr['counts'].get('actual_complete_stockdays',0),actual_regular_reference_minutes=qr['counts'].get('actual_regular_reference_minutes',0),all_scoped_hour_aggregates_checked=qr['counts'].get('all_scoped_hour_aggregates_checked',0),unresolved_hours=qr['counts'].get('unresolved_hours',0),exact_prior_hour_aggregates=qr['counts'].get('prior_hour_five_exact',0),exact_all60_minute_hours=qr['counts'].get('all60_actual_minute_fields_exact_hours',0),pure_trade_minute_incomplete_hours=qr['counts'].get('pure_trade_minute_incomplete_hours',0),candidate_hours_admitted=0,new_network_bytes=qs['new_network_bytes'],network_attempts=qs['network_attempts'],reused_stockdays=sum(r['reused_existing_response'] for r in qs['records']),acquisition_peak_rss_bytes=qs['peak_rss_bytes'],independent_verification_peak_rss_bytes=qr['peak_rss_bytes'],source_clock_price_volume_unchanged=True,trade_archives_reopened=False,source_receipt_path=str(qq_remaining_dir/'receipt.json'),audit_path=str(qq_remaining_proof))
    clone_assembly_path=derived_dir/'model_input_assembly_receipt.json'
    if clone_assembly_path.exists():
        clone_assembly=p.json.loads(clone_assembly_path.read_text())
        cloned=[a for a in clone_assembly.get('arrays',[]) if a.get('independent_copy_on_write_clone')]
        if cloned:
            x_record=next(a for a in cloned if p.Path(a['path']).name=='X.npy')
            summary['independent_clone_storage_optimization']=dict(independent_cloned_arrays=len(cloned),hard_links_used=False,normalized_logical_bytes=x_record['logical_file_bytes'],normalized_explicitly_written_bytes=x_record['explicitly_written_normalized_bytes'],normalized_changed_sessions_written=x_record['changed_normalized_sessions_written'],other_changed_value_bytes_written=sum(a.get('explicitly_written_changed_value_bytes',0) for a in cloned),source_identity_unchanged=True,full_independent_tensor_proof_passed=(derived_dir/'model_input_independent_verification.json').exists(),capability_proof_path=str(p.OUT/'clone_copy_capability_probe/receipt.json'))
    historical_conflicts_path=p.OUT/'historical_remaining_coarse_conflict_definition_diagnostic/receipt.json'
    if historical_conflicts_path.exists():
        historical_conflicts=p.json.loads(historical_conflicts_path.read_text())
        assert historical_conflicts['status']=='complete_all947_coarse_conflicts_diagnosed_no_admission'
        summary['historical_coarse_conflict_definition_diagnostic']=dict(hours_checked=historical_conflicts['hours_checked'],
            price_conflict_hours=historical_conflicts['counts']['price_conflict'],quantity_only_conflict_hours=historical_conflicts['counts']['quantity_only_conflict'],
            fixed_previous_close_formula_all12_matching_hours=historical_conflicts['counts']['all12_fixed_carry_open_and_original_quantity_gate_pass'],
            candidate_hours_admitted=0,price_definitions_and_quantity_thresholds_unchanged=True,receipt_path=str(historical_conflicts_path))
    p.atomic_json(p.OUT/'resume_summary.json',summary)
    admitted=summary['candidate_hours_admitted_this_resume']
    tail_remaining=summary['full_tail_gap_hours_remaining']
    historical_remaining=summary['historical_gap_hours_remaining']
    manifest['required_work_remaining']=[note for note in manifest['required_work_remaining'] if not note.startswith('续跑采用分批读取和按日期前缀落盘；')]
    derived_note='小时特征与标签已全1,178股重算并独立核验；98项慢频特征与122维归一化模型输入尚未完成本版处理，尚未重训回测。' if summary['new_features_or_labels_recalculated'] else '特征标签仍待重算，之后才重训回测。'
    if summary.get('new_normalized122_feature_X_built'):
        derived_note=f'已通过完整模型输入核验的版本包含{summary.get("derived_model_admitted_raw_hours",0)}个新增小时；最新原始候选的{admitted}小时尚待完成对应版本的完整模型输入核验。2020—2022年原覆盖门槛仍失败，尚未重训回测。' if summary.get('latest_raw_candidate_model_inputs_pending') else '全1,178股小时特征、标签及122维归一化模型输入已完成独立核验；98项慢频原始值的全部来源与时点证据核对一致后复用。2020—2022年原覆盖门槛仍失败；数据补齐验收未完成，尚未重训回测。'
    remaining_note=f'续跑采用分批读取和按日期前缀落盘；新原始候选已验收新增{admitted}小时，尾段仍余{tail_remaining}小时、历史仍余{historical_remaining:,}小时。原报价分钟的全局时钟失败与原覆盖门槛保留；原始面板核验不等于完整模型输入验收。{derived_note}'
    if remaining_note not in manifest['required_work_remaining']:
        manifest['required_work_remaining'].append(remaining_note)
    p.atomic_json(manifest_path,manifest)
    label='低内存续跑（下载、观察、准入分列）'
    progress=p.BASE/names[1]
    with progress.open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in p.csv.reader(f) if not r or r[0]!=label]
    rows.append([label,f'{count}/226完整文件；{summary["stockdays_with_minute_observations"]}股票日生成观察层',
        f'本次新完整{count-177}；新原始候选新增{admitted}小时；历史{historical_remaining}、尾段{tail_remaining}仍待验收',str((p.OUT/'resume_summary.json').relative_to(p.BASE)),
        '65536行读取；不拼接整日成交；旧验收指纹复用并用大小/页脚/文件身份保护；最终新面板验收未完成'])
    with progress.open('w',encoding='utf-8-sig',newline='') as f:
        p.csv.writer(f).writerows(rows)
    html_path=p.BASE/names[2]
    html=html_path.read_text()
    historical_pause=''
    pause_marker='<section id="user-paused">'
    if pause_marker in html:
        start=html.index(pause_marker);end=html.index('</section>',start)+len('</section>')
        prior_pause=html[start:end];html=html[:start]+html[end:]
        historical_pause=prior_pause.replace(pause_marker+'<h2>当前状态：用户要求暂停，研究进程已停止</h2>','<details id="historical-user-pause"><summary>2026-10-09的暂停与清理记录（后续已按本次授权恢复）</summary><p>以下保存当时记录；当前状态以本页最新续跑结果为准。</p>').replace('</section>','</details>')
    elif '<details id="historical-user-pause">' in html:
        start=html.index('<details id="historical-user-pause">');end=html.index('</details>',start)+len('</details>');historical_pause=html[start:end];html=html[:start]+html[end:]
    marker='<section id="bounded-resume">'
    if marker in html:
        start=html.index(marker)
        end=html.index('</section>',start)+len('</section>')
        html=html[:start]+html[end:]
    stamp=p.datetime.now(p.timezone.utc).astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M')
    if '<p class="stamp">' in html:
        start=html.index('<p class="stamp">');end=html.index('</p>',start)+4
        html=html[:start]+f'<p class="stamp">更新时间：{stamp}（新加坡／北京时间）｜续跑目标有效；补齐验收未全部完成。</p>'+html[end:]
    block=marker+f'<h2>继续补齐：已控制内存，数据准入仍在核验</h2><p>{stamp}（UTC+8）</p>'
    block+=f'<p>完整文件 {count}/226，本次新增 {count-177} 个；已验收177个完整文件保留。提取已处理 {ext["processed_partitions"]} 个文件；分钟观察记录覆盖 {summary["stockdays_with_minute_observations"]:,} 股票日，其中 {summary["daily_six_fields_exact_stockdays"]:,} 日与主日线六字段精确一致。</p>'
    mem=summary['memory_peak_bytes']
    block+=f'<p>进程峰值：下载约 {mem["download"]/1024**2:.0f} MiB、提取 {mem["extraction"]/1024**2:.0f} MiB、分钟汇总 {mem["minutes"]/1024**2:.0f} MiB。每批最多65,536行，子集及时落盘，核验只累积分钟摘要。49份续传记录错误 {len(errors)} 次；额外原始归档的中断单列。原12次 BrokenPipeError 的成因仍未确认。</p>'
    block+=f'<p><strong>新原始候选已验收新增：{admitted}小时。历史仍余{historical_remaining:,}小时、尾段仍余{tail_remaining:,}小时。</strong>固定两秒竞价宽限后，仍有116个源内股票日的量额不符、136日源记录缺失；这些不被归为停牌。原采样报价的全局时钟失败记录保留，真实成交分钟采用单独记录的来源定义。</p>'
    block+=f'<p>当前 V6 的全部1,178股历史内部一致性核验通过，未发现停牌日被标成完整行情。历史40小时新查参照：2小时获得完整参照并通过粗粒度原价格/数量容差检查，另外38小时还需其他来源。这不代替分钟精度与新候选验收。{derived_note}</p>'
    if 'native_millisecond_pairing' in summary:
        pair=summary['native_millisecond_pairing']
        block+=f'<p>尾段毫秒配对：{pair["stockdays_checked"]}个股票日、{pair["trade_rows_checked"]:,}笔成交编号逐条对应，价格、股数完全一致，秒时间等于事件毫秒时间向下取整。此证据只覆盖已核验的2026-09-23，不能证明两仓库上游独立，也未证明全部尾段分钟价格定义。</p>'
    if 'global_quote_clock_models' in summary:
        clock=summary['global_quote_clock_models']
        block+=f'<p>报价时钟：{clock["models_tested"]}种预先固定的规则，核对全部{clock["stockdays_checked"]}个原分钟正对照；完全一致规则{len(clock["globally_exact_models"])}种。成交价极值与采样报价高低价分别核验，旧失败记录保留。</p>'
    if 'expanded_historical_coarse_recheck' in summary:
        h=summary['expanded_historical_coarse_recheck']
        block+=f'<p>历史扩大核验：{h["source_year_files_scanned_once"]}份年度文件各读取一次、选中{h["completed_stockdays"]}/{h["requested_stockdays"]}个股票日；{h["complete_reference_hours"]}小时获得完整五分钟参照，{h["strict_coarse_diagnostic_hours"]}小时通过四项价格精确一致和数量差不超过100股的旧粗检。连同成交额全部精确一致的小时{h["all_fields_exact_diagnostic_hours"]}；数量和金额原始差异保留，尚未准入。旧代码按官方变更日期查证。峰值约{h["peak_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'independent_tail_hour_comparison' in summary:
        t=summary['independent_tail_hour_comparison']
        floor=t['totals_by_model']['floor_left']
        block+=f'<p>额外尾段参照：624股公开小时响应覆盖全部{t["hours_requested"]:,}个请求小时，未把小时数据展开为分钟。固定向下取整成交分钟聚合后，{floor["all_four_prices_exact_hours"]:,}小时四项价格精确一致，其中{floor["all_four_prices_and_volume_exact_hours"]:,}小时成交量也精确一致。这些粗粒度结果不直接进入模型。</p>'
    if 'new_candidate_minute_staging' in summary:
        s=summary['new_candidate_minute_staging']
        merge_text='首批88小时已合并新原始面板，并通过全1,178股逐块复核，没有补入停牌行情' if s['parent_panel_merged'] else '尚未合并新原始面板'
        block+=f'<p><strong>新候选分钟层已逐批落盘：{s["stockdays_written"]}股票日、{s["hours_staged"]}小时，独立核验{str(s["independent_verification_passed"]).lower()}。</strong>采用已核验的真实成交事件分钟；{s["source_genuine_zero_trade_quote_minutes"]}根零成交分钟来自对应区间的真实报价，原始交易编号、报价编号与可用时间证据保留。其日线六字段及四个独立小时的价格数量全部精确一致。{merge_text}。{derived_note}</p>'
    if 'additional_native_hour_staging' in summary:
        extra=summary['additional_native_hour_staging'];raw_summary=summary['new_candidate_raw_panel']
        block+=f'<p><strong>第二批又准入{extra["qualified_additional_hours"]}个小时，覆盖{extra["stockdays_with_qualified_hours"]}个股票日的合格部分；累计准入{admitted}小时。</strong>其中{extra["zero_trade_quote_minutes"]}根零成交分钟逐根有真实报价证据；1小时因14:22区间缺少真实报价仍为空；特殊代码001914另2小时已通过复权路径及分钟独立核验并进入V9。最新原始面板的全1,178股核验通过、无额外改动、无停牌补值，峰值约{raw_summary["peak_rss_bytes"]/1024**2:.0f} MiB。累计{raw_summary["stockdays_with_any_admitted_raw_hours"]}个股票日至少有1个合格小时，只有{raw_summary["stockdays_with_all_four_admitted_raw_hours"]}日的4小时全部合格。</p>'
    if 'new_hour_features_and_labels' in summary:
        rebuilt=summary['new_hour_features_and_labels']
        block+=f'<p><strong>已完成的候选模型输入：全1,178股小时特征与标签已重算并独立核验。</strong>核对39,938,912个标签单元，{summary.get("derived_model_admitted_raw_hours",0)}个已重算小时的分钟统计与原聚合器逐项一致；{rebuilt["accepted_new_execution_windows"]}个新增五分钟执行窗口可用，{rebuilt["late_recorded_execution_windows_kept_missing"]}个因来源到达晚于固定窗口结束、{rebuilt["zero_volume_execution_windows_kept_missing"]}个因真实成交量为零、{rebuilt["unknown_arrival_execution_windows_kept_missing"]}个因接收时间未知继续不可用。重算峰值约{rebuilt["peak_rebuild_rss_bytes"]/1024**2:.0f} MiB，独立核验峰值约{rebuilt["peak_audit_rss_bytes"]/1024**2:.0f} MiB。历史补齐已按来源日期重算因果特征、标签及完整80小时窗口，未改动的来源区间保持原数值表示。{derived_note}</p>'
    if 'additional_sina_minute_reference' in summary:
        sina=summary['additional_sina_minute_reference']
        block+=f'<p><strong>另查新浪真实分钟参照：{sina["stocks_verified"]}股、{sina["paired_real_minutes_checked"]:,}个实际分钟配对通过独立复核，{sina["all60_price_volume_exact_diagnostic_hours"]}小时的60根分钟价格与成交量全部精确一致。</strong>成交额差异逐项保留。这些比对本身不直接准入；实际通过完整条件的小时由最新原始候选计数。接收时点仍单独核验。峰值约{sina["peak_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'additional_nonmonotonic_sina_reference' in summary:
        extra=summary['additional_nonmonotonic_sina_reference'];pending=extra['counts'].get('pending_native_millisecond_proof_2026-09-22',0)
        extra_status=f'过滤已入库及独立小时冲突后，{pending}个9月22日小时已取得当日毫秒顺序及接收证据，进入最新原始候选；原全天编号顺序异常保留，日内每秒顺序另经逐笔证明' if extra['diagnostic128_now_merged_in_latest_raw'] else f'过滤已入库及独立小时冲突后，还有{pending}个9月22日小时需要当日毫秒成交顺序证据；原编号顺序异常保留，目前未准入'
        block+=f'<p>新增{extra["stocks_verified"]}只全天成交编号不递增股票的独立分钟参照，逐项复核{extra["paired_real_minutes_checked"]:,}个分钟配对；{extra["all60_exact_diagnostic_hours"]}个小时全部60根分钟OHLCV精确一致。{extra_status}。</p>'
    if 'sep22_real_quote_witness_expansion' in summary:
        quote=summary['sep22_real_quote_witness_expansion']
        action='已写入最新原始候选' if quote['merged_into_latest_raw'] else '待原始面板准入'
        block+=f'<p><strong>9月22日真实零成交报价扩展：</strong>{quote["hours_verified"]}个新增小时、{quote["stockdays_verified"]}个股票日{action}；包含{quote["real_trade_minutes"]:,}个真实成交分钟及{quote["real_zero_trade_quote_minutes"]:,}个区间内真实零成交报价分钟。累计实际成交量、计算成交额、末价及报价字段独立核验一致；{sum(quote["rejected_hours"].values())}个小时因缺少合格报价继续留空。逐股处理峰值约{quote["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{quote["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'historical_original_contract_reconciliation' in summary:
        contract=summary['historical_original_contract_reconciliation']
        block+=f'<p><strong>历史原准入流程复核：</strong>首轮整理{contract["next_batch_stockdays"]}个股票日、{contract["next_batch_hours"]}小时的候选：48个五分钟参照价格一致，成交量保留整数手的原报告精度；原日线、分钟网格、代码、复权及覆盖要求保留。金额差异和现金精度诊断仍单独记录，不推算真实逐分钟一股精度成交量；此清单为原流程复核记录，实际准入以随后逐分钟和整面板核验为准。</p>'
    if 'historical_reported_native_expansion' in summary:
        history=summary['historical_reported_native_expansion']
        action='已补入最新原始候选' if history['merged_into_latest_raw'] else '待原始面板准入'
        block+=f'<p><strong>历史分钟候选准入：</strong>{history["stockdays_verified"]}个股票日、{history["hours_verified"]}小时{action}；{history["real_observed_reported_minutes"]:,}个真实记录的分钟价量保留整数手报告精度，未推算逐分钟真实一股精度成交量。日线、完整网格、48个五分钟价格和成交量误差门槛独立核验一致；{history["quarantined_hours"]}小时仍有分钟价格、量额矛盾，继续待查。研究执行采用原观察分钟开盘均价代理；接收时间保留未知，实时资格未认定。</p>'
        rebuilt=summary.get('new_hour_features_and_labels',{})
        block+=f'<p>其中{rebuilt.get("historical_reported_proxy_windows_accepted",0)}个历史执行窗口按原报告价量研究代理口径可用；{rebuilt.get("historical_reported_zero_proxy_windows_kept_missing",0)}个报告窗口成交量为零，继续不可用。该类代理与有实测接收时间的尾段窗口分别记录。</p>'
    if 'historical_partial_reported_native_expansion' in summary:
        partial=summary['historical_partial_reported_native_expansion']
        action='已补入最新原始候选' if partial['merged_into_latest_raw'] else '待原始面板准入'
        block+=f'<p><strong>历史分小时核验扩展：</strong>{partial["stockdays_verified"]}个股票日的{partial["hours_verified"]}小时{action}；{partial["real_observed_reported_minutes"]:,}根观察分钟逐项核对原日线、全天网格及所选小时的12个五分钟参照。其他小时的来源冲突保留，{partial["quarantined_hours"]}个所请求小时存在分钟量额矛盾继续待查。整数手报告精度和未知接收时间保留；累计历史已补{summary["historical_hours_admitted_this_resume"]}小时，尚余{summary["historical_gap_hours_remaining"]:,}小时。</p>'
    if 'additional_sep22_native_archive' in summary:
        archive=summary['additional_sep22_native_archive']
        block+=f'<p>额外公开9月22日毫秒原始归档：已收{archive["received_bytes"]/10**9:.2f}/{archive["published_bytes"]/10**9:.2f} GB，状态{escape(archive["status"])}；记录传输中断{archive["transfer_errors"]}次，已收字节保留续传，错误堆栈保留，不猜测中断原因。下载时计算指纹，解压校验与有效提取合并，不重复整档校验。当前峰值约{archive["peak_rss_bytes"]/1024**2:.0f} MiB。下载完成率不计入行情补齐验收。</p>'
    if 'source_second_minutes_pending_arrival_qualification' in summary:
        second=summary['source_second_minutes_pending_arrival_qualification']
        second_status='已按研究方案的历史行情条件计入最新原始候选；接收延迟与实时执行资格仍不通过，接收时间保持未知' if second['raw_market_minutes_admitted'] else '仍待来源资格核验，未计入原始准入'
        if second['source_reception_evidence_bound_in_latest_raw']:second_status='已按历史行情条件计入最新原始候选，并新取得当日真实毫秒成交与接收时间证明；179个旧原始小时的市场价量未被改写，执行窗口按实测时间重验，实时资格仍单独保留限制'
        block+=f'<p><strong>另{second["hours"]}小时的{second["real_minutes"]:,}根真实分钟已逐股票日落盘，独立价量及日线六字段核验通过。</strong>{second_status}；无补值、无虚构到达时间。历史行情真实性、已记录的接收时点与实时延迟资格分别记录，原时钟及覆盖门槛保留。</p>'
    if 'sina_cash_precision_diagnostic' in summary:
        cash=summary['sina_cash_precision_diagnostic']
        block+=f'<p>试样成交额精度诊断：{cash["paired_minutes"]}个价量配对分钟中，{cash["float32_product_sum_reproduced"]}个来源成交额可由32位浮点价格×数量复现，其余{cash["remaining_within_one_reported_decimal"]}个只差0.0001元末位，独立计算复核通过。实际来源实现尚未证实；数值和现金门槛均未改动。</p>'
    if 'reconciled_tail_native_minute_staging' in summary:
        reconciled=summary['reconciled_tail_native_minute_staging']
        reconciled_note='该批已补入最新原始候选，并完成整面板核验。' if reconciled['merged_into_latest_raw'] else '该批待合并新原始候选，目前未计入已补齐小时。'
        block+=f'<p><strong>尾段分钟口径核验扩展：</strong>{reconciled["stockdays_verified"]}个股票日、{reconciled["hours_verified"]}小时、{reconciled["actual_minutes_verified"]:,}根实际成交分钟。原生毫秒成交、独立一分钟价量、日线六字段及实测接收时间独立核验通过；腾讯小时开盘沿用前一个实际分钟末价的固定公式可复现这些小时的全部价量差异。分钟仍取真实首笔成交价；该轮独立核验保留的523个小时参照冲突未因公式解释而放宽；{reconciled_note}处理峰值约{reconciled["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{reconciled["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'remaining376_sina_query_progress' in summary:
        remaining_sina=summary['remaining376_sina_query_progress']
        block+=f'<p><strong>其余376股分钟参照扩查：</strong>已查询{remaining_sina["symbols_processed"]}/{remaining_sina["symbols_requested"]}股，成功取得{remaining_sina["successful_symbols"]}份原始公开响应，{remaining_sina["diagnostic_all60_hours"]}小时的一分钟价量初步一致；状态{escape(remaining_sina["status"])}。共享分钟文件按日期前缀只读取一次；此查询进度未计入面板准入，仍需独立核验及原生毫秒、日线、固定边界复核。</p>'
    if 'expanded376_tail_native_minute_staging' in summary:
        expanded=summary['expanded376_tail_native_minute_staging']
        expanded_note='该批已通过整面板核验并补入最新原始候选。' if expanded['merged_into_latest_raw'] else '该批尚待合并新原始候选，未计入已补齐小时。'
        block+=f'<p><strong>376股扩查后的分钟准入核验：</strong>{expanded["stockdays_verified"]}个股票日、{expanded["hours_verified"]}个新增小时、{expanded["actual_minutes_verified"]:,}根实际成交分钟通过独立核验；{expanded["rejected_hours_verified"]}个未解释来源冲突继续留空，已有记录单独排除。固定竞价及分钟边界、真实首笔价格、日线六字段、独立一分钟价量及实测接收时间均保持原口径；小时参照必须直接吻合或由既定前一实际分钟末价公式完整解释。{expanded_note}处理峰值约{expanded["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{expanded["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'two_day_quote_reconciled_native_minute_staging' in summary:
        quote_reconciled=summary['two_day_quote_reconciled_native_minute_staging']
        quote_reconciled_note='该批已通过全历史核验并补入最新原始候选。' if quote_reconciled['merged_into_latest_raw'] else '该批仍待合并原始候选，未计入正式补齐小时。'
        block+=f'<p><strong>两日真实空成交分钟核验：</strong>{quote_reconciled["stockdays_verified"]}个股票日、{quote_reconciled["hours_verified"]}个新增小时独立核验通过，其中{quote_reconciled["genuine_zero_trade_quote_minutes"]}根零成交分钟均有同一分钟内的真实报价及完整成交前缀价量证明；{quote_reconciled["rejected_hours_verified"]}小时缺少真实报价继续留空。实际成交分钟、日线六字段、独立分钟价量、固定小时参照公式及记录接收时间已逐项重放；没有用前价推造分钟。{quote_reconciled_note}处理峰值约{quote_reconciled["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{quote_reconciled["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'nominal_quote_cut_native_minute_staging' in summary:
        nominal=summary['nominal_quote_cut_native_minute_staging']
        nominal_note='已补入最新原始候选，并通过全1,178股历史核验。' if nominal['merged_into_latest_raw'] else '尚待原始候选准入，未计入正式补齐小时。'
        block+=f'<p><strong>整点累计成交截点核验：</strong>{nominal["hours_verified"]}个股票日、{nominal["hours_verified"]}个小时、{nominal["actual_minutes_verified"]:,}根真实一分钟行情独立核验通过；{nominal["rejected_hours_verified"]}个完整小时价量冲突继续留空。当前整点报价的累计量、成交额及末笔价格匹配唯一真实成交前缀，前一截点累计量及末笔价格也精确匹配原生逐笔；固定截点间的完整价量重放与腾讯一致。分钟边界及真实首笔价格保持原值，全部60根分钟另与新浪一致，日线六字段及接收时间已复核。缺少前一整点快照时只保存真实逐笔截点，不编造报价。{nominal_note}处理峰值约{nominal["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{nominal["peak_audit_rss_bytes"]/1024**2:.0f} MiB；此证据未认证供应商实现或实时可用性。</p>'
    if 'sampled_quote_native_minute_staging' in summary:
        sampled=summary['sampled_quote_native_minute_staging']
        sampled_note='已补入最新原始候选，并通过全1,178股历史核验。' if sampled['merged_into_latest_raw'] else '尚待原始候选准入，未计入正式补齐小时。'
        block+=f'<p><strong>固定定时报价参照核验：</strong>{sampled["stockdays_verified"]}个股票日、{sampled["hours_verified"]}个小时、{sampled["actual_minutes_verified"]:,}根真实分钟行情独立核验通过；用于解释小时参照的{sampled["all_actual_prefix_quote_samples_verified"]:,}条实测报价均精确绑定真实成交前缀的累计量、成交额及末笔价格。腾讯小时价量由固定报价区间完整重现；实际分钟价量仍为全部真实逐笔的首笔/最高/最低/末笔，与新浪逐分钟一致，日线六字段也一致，没有用采样报价覆盖真实成交极值。本批原有1,447个诊断冲突已交由后续固定边界逐项核对，供应商实现与实时可用性未认证。{sampled_note}处理峰值约{sampled["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{sampled["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'strict_endpoint_quote_native_minute_staging' in summary:
        strict=summary['strict_endpoint_quote_native_minute_staging']
        strict_note='已补入V20及后续原始候选，并通过全1,178股原始历史核验。' if strict['merged_into_latest_raw'] else '尚待原始面板准入，未计入正式补齐小时。'
        block+=f'<p><strong>小时终点报价归属核验：</strong>{strict["stockdays_verified"]}个股票日、{strict["hours_verified"]}个新增小时、{strict["actual_minutes_verified"]:,}根真实一分钟行情独立核验通过；{strict["all_actual_prefix_quote_samples_verified"]:,}条实际报价样本的累计量、成交额和末笔价格均匹配唯一原生成交前缀。固定左闭右开区间完整重现独立小时参照，恰在终点的报价归入下一段。全部60根真实分钟价量与独立分钟参照一致，日线六字段及接收依赖也一致；未移动原始成交分钟，未拟合秒数偏移。本批留下1,039个诊断冲突交由后续固定定义核对。{strict_note}处理峰值约{strict["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{strict["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'historical_cached_daily_failure_causes' in summary:
        block+='<p><strong>历史日线失败定位：</strong>从已验收核验记录复查362个股票日、1,422个小时，未重扫年文件：1,355小时涉及日成交量差，64小时涉及开盘价差，22小时涉及零成交小时，原因有重叠。量差单一原因1,336小时，量差加零成交19小时，开盘价单一原因64小时，零成交单一原因3小时；未放宽原成交量或价格门槛。</p>'
    if 'historical_minute_quantity_comparison' in summary:
        block+='<p><strong>历史分钟量逐条对照：</strong>已核对18个股票日、4,320条实际公开分钟记录。300308的6个日期，全部240分钟均满足公开股数向下取整到100股等于旧整手数据，解释这些日期的日成交量差；其余12个日期存在分钟分配差异。仅在四份相关股票年文件的有效筛选读取中同步计算指纹，复用两个已有日缓存；尚未替换字段或新增补齐小时，价格定义及来源证据继续核对。</p>'
    if 'historical_opening_row_comparison' in summary:
        block+='<p><strong>历史开盘冲突定位：</strong>16个股票日均有成交量为零的09:30原始记录，其中8日的该记录Open字段等于主源日线开盘价，Close字段均不等；另8日该记录Open也不符。原值已落盘，尚未将零量记录改称真实竞价成交、替换第一分钟或新增补齐小时。</p>'
    if 'first_hour_cumulative_extrema_staging' in summary:
        first=summary['first_hour_cumulative_extrema_staging'];first_note=('已补入V21候选，全部1,178股原始历史、特征标签及完整122特征模型输入均已通过独立核验。' if summary.get('derived_model_raw_candidate')==raw_dir.name and not summary.get('latest_raw_candidate_model_inputs_pending',True) else '已补入V21原始候选并通过全1,178股原始历史核验，模型输入核验仍待完成。') if first['merged_into_latest_raw'] else '尚待原始候选准入。'
        block+=f'<p><strong>首小时真实累计极值核验：</strong>{first["hours_verified"]}个股票日、{first["actual_minutes_verified"]:,}根真实一分钟行情独立核验通过。严格10:30之前的来源报价，其累计量、成交额、末笔价格及从开盘至该截点的全部真实成交高低价均独立精确复算。仅首小时使用同范围的累计极值，后续小时未借用全天高低。真实M1逐条与独立一分钟参照一致，日线六字段及接收依赖一致；没有改变实际分钟首笔或极值。{first_note}按索引只读取相关股票的171/1,527个原生成交分区、2,907,533笔实际成交，不重算旧整卷指纹。处理峰值约{first["peak_stage_rss_bytes"]/1024**2:.0f} MiB，独立核验约{first["peak_audit_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'observed_second_close_native_minutes' in summary:
        sec=summary['observed_second_close_native_minutes'];block+=f'<p><strong>固定秒末价格粗参照复算：</strong>{sec["hours_verified"]}个股票日、{sec["minutes_verified"]:,}根真实一分钟独立核验通过并补入V22原始候选。固定10:30之前的来源报价对应唯一真实成交前缀；按每个有成交秒的末笔实际价格归并，小时参照五字段精确复现。全部原始一分钟仍按真实首笔、最高、最低、末笔成交定义，与新浪逐条一致，日线六字段及接收依赖精确核对。没有移动分钟、搜索采样相位或改写价格；这项字段复算不等于腾讯供应商实现认证。处理峰值约{sec["stage_peak_rss_bytes"]/1024**2:.0f} MiB，独立核验约{sec["audit_peak_rss_bytes"]/1024**2:.0f} MiB；特征标签与模型输入是否完成，以本页独立核验通过的最新版本及小时数为准。</p>'
    if 'first_observed_quote_open_native_minutes' in summary:
        fq=summary['first_observed_quote_open_native_minutes'];block+=f'<p><strong>区间第一条真实报价粗参照复算：</strong>{fq["hours_verified"]}个股票日、{fq["minutes_verified"]:,}根真实一分钟独立核验通过并补入V23。区间边界不变，小时参照Open按区间内第一条实际报价而非前一区间末价复现，所有{fq["all_actual_prefix_quote_samples_independently_verified"]:,}条使用的真实报价均与唯一实际成交前缀量、额、末价精确绑定。真实一分钟值全部与独立参照一致，日线六字段精确复算；未改分钟首笔、极值、数量或接收时间，也未认证供应商实现。该批新增111小时，原始候选及模型输入累计小时数以本页最新核验结果为准。</p>'
    if 'closed_morning_quantity_native_minutes' in summary:
        cm=summary['closed_morning_quantity_native_minutes'];block+=f'<p><strong>上午完整收盘累计量复算：</strong>{cm["hours_verified"]}个下午小时、{cm["minutes_verified"]:,}根真实一分钟通过独立核验，全部{cm["all_actual_prefix_quote_samples_independently_verified"]:,}条相关实际报价与真实成交前缀量、额、末价精确绑定。固定下午起点13:00，参照量减去13:00前最后一条实际报价的上午累计量，未搜索时间偏移、匹配成交量的最佳截点或改变分钟边界。已补入V24。</p>'
    if 'actual_interval_quantity_native_minutes' in summary:
        aq=summary['actual_interval_quantity_native_minutes'];block+=f'<p><strong>固定真实区间量复算：</strong>{aq["hours_verified"]}个小时、{aq["minutes_verified"]:,}根真实一分钟独立核验通过。原始区间全部实际成交量之和与小时参照精确一致；粗参照价格使用已核对的实际报价，真实分钟仍保留全部成交首笔、最高、最低、末笔，未替换数量、舍入或放宽误差。V25累计补入3,697小时，完整模型输入仍以最近通过版本为准；新增470小时的重算及完整模型输入核验已通过。</p>'
    if 'cached_sept21_native_minutes' in summary:
        cached=summary['cached_sept21_native_minutes'];block+=f'<p><strong>9月21日缓存分钟准入：</strong>{cached["hours_verified"]}个小时、{cached["actual_minutes_verified"]}根真实分钟通过原始来源、独立分钟、小时参照和日线核验，已补入V26并通过全1,178股原始历史核验。接收时间未知仍为空，未认证执行窗口或实时资格。V26累计3,699小时；完整模型输入当前为{summary.get("derived_model_admitted_raw_hours",0)}小时，是否完成最新版本以完整模型核验状态为准。另一匹配股票的原始成交编号顺序证据未通过，继续保留为空。</p>'
    if 'additional_sep24_single_stream_archive' in summary:
        stream=summary['additional_sep24_single_stream_archive']
        if stream['outside_requested_scope_cancelled']:
            block+='<p><strong>范围纠正记录：</strong>当前面板截至2026年9月23日。误启动的9月24日归档下载已停止，约12.94 GB自有未验收临时数据已清理，仅保留小型检查记录，新增准入0小时。原177份完整文件、226份已验收下载及既有候选均保留。后续取数前必须先核对面板日期及实际缺口交集。</p>'
    if 'new_public_coarse_stockday_comparison' in summary:
        block+='<p><strong>新公开历史参照核对：</strong>仅按股票、日期索引列筛选并取回688122（2023-10-18）与688235（2025-01-10）各48条实际五分钟记录，两日全部六字段均与已保存Baostock参照一致，新增通过小时0。没有提供新的冲突解释，未认证其独立上游，未把五分钟行分配成一分钟，部分范围访问也未误称整份文件指纹验收。</p>'
    if 'new_scoped_native_historical_matrix_diagnostic' in summary:
        matrix=summary['new_scoped_native_historical_matrix_diagnostic'];block+='<p><strong>剩余历史分钟新来源查证：</strong>只为603866、603885的36个实际缺口股票日、144小时读取相关原生字段列，复用旧时间索引和文件尾信息，新增12.26 MB，未下载整套25.9 GB远端文件、未重扫旧年CSV。1日存在源空值；35个非空日的日量进入原100股误差范围，其中29日改善旧量差。仅11日44小时通过全部日线与正成交量条件，10次新查询及1份旧缓存均无完整五分钟参照；实际原始响应独立解码核对，未把空查询称为停牌。来源独立性、分钟字段定义及整份远端指纹仍未认证，本批新增准入0小时。处理峰值约270 MiB，独立原始列解码约227 MiB。</p>'
    if 'new_late_native_historical_matrix_diagnostic' in summary:
        block+='<p><strong>2020—2024年量差缺口续查：</strong>按已保存量差清单，只读取001914、603786、688172、688617的6个实际股票日、24缺口小时，新增18.61 MB原始字段范围。全部1,440分钟直接由原始列独立解码核对；5日的日量进入原100股误差范围，4日16小时通过全部日线和正成交条件。4个股票日都取得48条独立五分钟记录，共192点，但原价量门槛通过小时为0；差异原样保存，未换字段、放宽量差或把五分钟分配为一分钟，本批新增准入0小时。</p>'
    if 'new_2019_native_historical_matrix_diagnostic' in summary:
        md=summary['new_2019_native_historical_matrix_diagnostic']
        block+=f'<p><strong>2019年剩余原始分钟续查：</strong>002821、603379、002812、002382共{md["stockdays"]}个实际缺口股票日、{md["actual_missing_hours"]}小时，只读取相关字段列并立即筛选落盘，新增{md["new_network_bytes"]/10**6:.2f} MB。独立原始列解码核对{md["actual_minutes_verified"]:,}分钟；{md["previous_daily_quantity_failure_improved_stockdays"]}日改善旧日量差，但{md["primary_open_failed_stockdays"]}日开盘价与主日线冲突，全部未通过日线条件，未启动参照查询，新增准入0小时。原价格、量差门槛及竞价口径保留，未把日线开盘价替换进源分钟。读取峰值约{md["acquisition_peak_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'new_tencent_explicit_end_actual_minute_probe' in summary:
        qq=summary['new_tencent_explicit_end_actual_minute_probe']
        block+=f'<p><strong>腾讯历史分钟参数查证：</strong>十二位结束时间YYYYMMDDHHMM可返回所查历史分钟；八位日期和带横杠日期在本次样本中返回空。采用15:01排他结束时间才能保留实际15:00行，09:30竞价另列并按固定口径合并。{qq["actual_public_requests_verified"]}次小请求取得{qq["complete_observed_stockdays"]}个完整股票日、{qq["actual_observed_minutes"]:,}条实际常规分钟；所查2018、2019、2025年和8月7日样本仍为空，未据此推断所有股票覆盖范围或停牌。两个股票日的实际一分钟价量核对未出现完整60分钟一致小时；另四个未补小时直接汇总真实M1参照，整小时全五字段一致为{qq["actual_hour_pilot_exact_hours"]}，有开盘价、区间量和最高价冲突。未更改真实成交、原时钟、量差门槛或把换手率当成交额，新增准入0小时。</p>'
    if 'full_remaining_historical_native_matrix_public_source_diagnostic' in summary:
        fm=summary['full_remaining_historical_native_matrix_public_source_diagnostic']
        block+=f'<p><strong>剩余历史公开分钟矩阵完整范围：</strong>按已有时间索引、股票字段和实际缺口先交集，确认{fm["planned_stocks"]}只股票、{fm["planned_stockdays"]}个未核对缺口日、{fm["planned_missing_hours"]}小时可继续核查，只读取对应原始列，预计约{fm["estimated_original_column_bytes"]/10**6:.1f} MB。已完成{fm["complete_batches"]}批、{fm["complete_stocks"]}只股票、{fm["complete_stockdays"]}个股票日、{fm["complete_missing_hours_checked"]}个实际缺口小时，新增{fm["new_network_bytes"]/10**6:.1f} MB。其中{fm["strict_coarse_pass_hours"]}小时与12条独立五分钟价量一致，但{fm["original_minute_price_conflict_hours"]}小时仍与已有原始一分钟价格存在冲突；未用五分钟一致推断分钟口径正确，也未替换为上一分钟收盘价。本范围新增准入0小时，后续继续分批核对公开来源。</p>'
    if 'new_tail_actual_five_minute_reference_probe' in summary:
        bm=summary['new_tail_actual_five_minute_reference_probe']
        block+=f'<p><strong>尾段独立五分钟参照小范围核对：</strong>4个实际缺口股票日取得{bm["actual_5m_reference_rows"]}条完整真实五分钟记录，独立解码原始响应并与{bm["native_minutes_read"]}条已有真实成交分钟比较，覆盖{bm["actual_missing_hours_checked"]}个待补小时；通过价量条件{bm["strict_coarse_pass_hours"]}小时。本批未扩大取数、未把五分钟拆成一分钟、未变更竞价或时钟，新增准入0小时。取数峰值{bm["acquisition_peak_rss_bytes"]/1024**2:.0f} MiB，核验峰值{bm["independent_verification_peak_rss_bytes"]/1024**2:.0f} MiB。公开接口新增可配置响应上限，后续小范围查询限512 KiB；已用4份实际响应和超限样本离线验证，无额外网络查询。</p>'
    if 'remaining_tencent_actual_minute_hour_reference_audit' in summary:
        rr=summary['remaining_tencent_actual_minute_hour_reference_audit']
        block+=f'<p><strong>9月22—23日剩余小时完整分钟参照续查：</strong>当前仍缺的{rr["scoped_missing_hours"]}小时涉及{rr["scoped_stockdays"]}个股票日，每个股票日仅查询一次真实腾讯M1，取得{rr["actual_regular_reference_minutes"]:,}条常规分钟，复用{rr["reused_stockdays"]}份旧响应，新增{rr["new_network_bytes"]/10**6:.2f} MB。逐小时汇总真实一分钟记录，独立核对{rr["all_scoped_hour_aggregates_checked"]}个小时；与已有完整小时诊断全五字段一致{rr["exact_prior_hour_aggregates"]}小时，60分钟全字段逐条一致{rr["exact_all60_minute_hours"]}小时，尚未核完{rr["unresolved_hours"]}小时。{rr["pure_trade_minute_incomplete_hours"]}小时存在真实成交分钟缺失，未自动填成零；这些小时另与已保存小时诊断比较，仍需完整原始报价与到达时间证据才能准入。没有重扫成交归档，没有变更原生价格、数量或时钟。本批新增准入0小时，取数峰值{rr["acquisition_peak_rss_bytes"]/1024**2:.0f} MiB，独立核验峰值{rr["independent_verification_peak_rss_bytes"]/1024**2:.0f} MiB。</p>'
    if 'independent_clone_storage_optimization' in summary:
        clone=summary['independent_clone_storage_optimization']
        block+=f'<p><strong>新增版本写盘优化：</strong>{clone["independent_cloned_arrays"]}个数组采用独立写时复制副本，未使用硬链接，源文件身份与内容保持不变。归一化面板逻辑大小{clone["normalized_logical_bytes"]/10**9:.2f} GB，仅对{clone["normalized_changed_sessions_written"]}个发生变化的日期区间写入{clone["normalized_explicitly_written_bytes"]/10**9:.2f} GB，其他数组实际修改值写入约{clone["other_changed_value_bytes_written"]/1024**2:.2f} MiB。未变化区间保持共享底层块，整份新面板仍完成独立指纹与逐单元核验；未删除或改写旧候选。</p>'
    if 'new_model_inputs' in summary:
        model=summary['new_model_inputs']
        block+=f'<p><strong>完整122维模型输入已组装，并通过全1,178股、8,476个时间点的独立张量核验。</strong>逐项复核{model["normalized_feature_cells_checked"]:,}个归一化单元，最大误差{model["normalization_max_abs_error"]:.9f}；当前历史成分股归一化值改动{model["changed_member_normalized_cells"]}个。组装峰值约{model["assembly_peak_rss_bytes"]/1024**2:.0f} MiB、独立核验约{model["audit_peak_rss_bytes"]/1024**2:.0f} MiB。慢频原始来源、固定因子定义和约250万行PIT日期证据一致；原2020—2022年覆盖门槛失败保留，来源时钟资格与缺口验收尚未全部完成，训练准入仍为false。</p>'
    links=[('详细续跑状态',p.OUT/'resume_summary.json'),('完整历史检查',p.OUT/'full_history_audit.json'),('分钟正对照核验',p.OUT/'minute_control_verification.json'),('历史参照查询',p.OUT/'historical_gap_references/receipt.json')]
    for title,path in [('成交毫秒配对',pairing_path),('统一报价边界检查',quantization_path),('扩大历史核验',recheck_path)]:
        if path.exists():
            links.append((title,path))
    for title,path in [('尾段独立小时参照',tencent_path),('新候选分钟层',staging_path)]:
        if path.exists():
            links.append((title,path))
    if raw_audit_path.exists():
        links.append(('新原始面板全范围核验',raw_audit_path))
    if derived_proof_path.exists():
        links.append(('新小时特征与标签核验',derived_proof_path))
    if model_proof_path.exists():
        links.append(('完整122维模型输入核验',model_proof_path))
    block+='<p>'+ '；'.join('<a href="'+escape(str(path.relative_to(p.BASE)))+'">'+title+'</a>' for title,path in links)+'</p></section>'
    if '<p class="stamp">' in html:
        at=html.index('</p>',html.index('<p class="stamp">'))+4
    else:
        at=html.index('>',html.index('<body'))+1
    html=html[:at]+block+historical_pause+html[at:]
    html_path.write_text(html)
    process_lock.close()


if __name__=='__main__':
    main()

"""Pre-score V3 factual-overlay registration; keep every V1/V2 byte intact."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import trading_comparison_v3 as driver
import trading_comparison as parent
from primary_action_supplement_v3 import (PrimaryActionSupplementV3,RECEIPT,OVERLAY,
    RECEIPT_SHA,OVERLAY_SHA,SUPPLEMENT,GAP_KEY)


NEW_SOURCE_NAMES=('primary_action_supplement_v3.py','trading_inputs_v3.py',
    'trading_comparison_metrics_v3.py','trading_comparison_v3.py',
    'trading_comparison_register_v3.py','tests/test_primary_action_supplement_v3.py')
TEST_LOG=driver.HERE/'artifacts/trading_comparison_v3_tests_preseal_full.log'
PRIMARY_REVIEW=driver.HERE/'artifacts/experiment_acceptance_primary_ex_reference_cash_bounds_review_20261001.json'
PRIMARY_REVIEW_SHA='e60291372b1faca85e9f17fa030711948ff44a3c4d46cb8c259fe0149dada76e'


def register():
    # This path cannot prepare a model manifest, load a score or replay a strategy.
    old_protocol,old_seal,old_receipt=parent.verify_source_seal()
    proof=PrimaryActionSupplementV3()
    if driver.sha(PRIMARY_REVIEW)!=PRIMARY_REVIEW_SHA:
        raise ValueError('independent primary factual review receipt changed')
    if not TEST_LOG.is_file():raise ValueError('actual preseal test output required')
    if 'Ran 52 tests' not in TEST_LOG.read_text() or not TEST_LOG.read_text().rstrip().endswith('OK'):
        raise ValueError('52 appropriate old/new contract tests must pass before V3 registration')
    sources={str(driver.HERE/n):driver.sha(driver.HERE/n) for n in NEW_SOURCE_NAMES}
    files=dict(old_seal['immutable_files'])
    files.update(old_receipt['driver_source_hashes'])
    for path in (parent.PROTOCOL,parent.SEAL,parent.RECEIPT,RECEIPT,OVERLAY,PRIMARY_REVIEW,TEST_LOG):
        files[str(path)]=driver.sha(path)
    initial_log=driver.HERE/'artifacts/trading_comparison_v3_tests_preseal.log'
    if initial_log.is_file():files[str(initial_log)]=driver.sha(initial_log)
    supplemental=driver.read_json(RECEIPT)['sealed_supplemental_files']
    files.update(supplemental);files.update(sources)
    driver.verify_files(files);driver.verify_files(old_seal['minute_raw_day_files'])
    protocol=deepcopy(old_protocol)
    protocol.update(schema_version=3,protocol_id='GRU_NINE_CONTINUOUS_ACCOUNT_PRIMARY_FACT_COMPARISON_V3',
        registered_at_utc=datetime.now(timezone.utc).isoformat(),
        previous_comparison_protocol_sha256=driver.sha(parent.PROTOCOL),
        previous_source_seal_sha256=driver.sha(parent.SEAL),
        previous_driver_receipt_sha256=driver.sha(parent.RECEIPT),
        change_from_v2='ONLY_VERIFIED_PRIMARY_REFERENCE_FACTS_AND_SEPARATE_MISSING_IMPLEMENTED_CASH_COMPONENT; ALL_POLICY_AND_RETURN_DEFINITIONS_REUSED',
        primary_action_receipt_path=str(RECEIPT),primary_action_receipt_sha256=RECEIPT_SHA,
        primary_action_overlay_path=str(OVERLAY),primary_action_overlay_sha256=OVERLAY_SHA,
        primary_independent_review_path=str(PRIMARY_REVIEW),primary_independent_review_sha256=PRIMARY_REVIEW_SHA,
        primary_case_keys=sorted(proof.cases),primary_factual_case_count=9,
        printed_announced_differential_reference_case_count=8,
        missing_implemented_cash_component_reference_case_count=1,
        original_vendor_reference_and_action_fields_unmodified=True,
        original_all_vendor_query_row_reconciliation_reused=True,
        reference_gate='V2_ORDINARY_MATCH_OR_CASE_SPECIFIC_VERIFIED_PRIMARY_ANNOUNCED_FORMULA_WITH_EXACT_VENDOR_CENT_MATCH; STATUS_NOT_RELABELLED_AS_ORDINARY',
        primary_virtual_terms='REFERENCE_EVIDENCE_ONLY; NEVER_REPLACE_ACTUAL_ENTITLED_CASH_OR_SHARE_RATIO',
        exact_unlimited_sharebase_ratio='DIAGNOSTIC_ONLY_WHEN_PDF_FINAL_EXPRESSION_PRINTS_APPROXIMATE_VALUES',
        missing_cash_component=dict(symbol=GAP_KEY[0],record_on='2025-07-09',ex_on='2025-07-10',pay_on='2025-07-10',
            published_on='2025-07-03',original_vendor_component='.070',additional_official_component='.014',
            actual_sum_cash_per_entitled_share='.084',additional_action_id=proof.official_missing_cash_component().action_id,
            separate_rights_and_per_component_tax_bounds=True,synthetic_merged_total_action_not_added=True),
        historical_source_arrival='RETROSPECTIVE_PRIMARY_PUBLICATION_EVIDENCE_ONLY; ACTUAL_HISTORICAL_FEED_ARRIVAL_NOT_CERTIFIED',
        primary_broker_tax_rounding_or_noncash_fifo_certification=False,
        historical_unknown_case_count=100,future_exdates_outside_cutoff_case_count=39,
        raw_unknowns_remain_unknown=True,account_policy_or_cost_or_risk_or_calendar_or_scores_changed=False,
        actual_model_scores_or_returns_read_at_registration=False)
    protocol.pop('change_from_v1',None)
    seal=deepcopy(old_seal)
    seal.update(schema_version=3,immutable_files=files,
        status='V3_PRIMARY_AND_ALL_PARENT_MARKET_MINUTE_BYTES_SEALED_BEFORE_SCORE_READ',
        parent_source_seal_sha256=driver.sha(parent.SEAL),
        primary_source_files_sealed=len(supplemental),primary_receipt_sha256=RECEIPT_SHA,
        original_V1_V2_bytes_retained=True,scores_read=False,model_returns_computed=False)
    driver.write_new(driver.PROTOCOL,protocol);driver.write_new(driver.SEAL,seal)
    receipt=dict(schema_version=3,status='V3_FACTUAL_DRIVER_REGISTERED_SCORES_AND_ACTUAL_FREEZE_PENDING',
        comparison_protocol_sha256=driver.sha(driver.PROTOCOL),source_seal_sha256=driver.sha(driver.SEAL),
        driver_source_hashes=sources,parent_driver_source_hashes=old_receipt['driver_source_hashes'],
        parent_protocol_sha256=driver.sha(parent.PROTOCOL),parent_source_seal_sha256=driver.sha(parent.SEAL),
        parent_driver_receipt_sha256=driver.sha(parent.RECEIPT),
        primary_receipt_sha256=RECEIPT_SHA,primary_overlay_sha256=OVERLAY_SHA,
        actual_market_source_files_sealed=len(files),actual_minute_days_sealed=len(seal['minute_raw_day_files']),
        source_coverage=seal['monthly_current_member_0931_window_coverage'],
        preseal_test_log_path=str(TEST_LOG),preseal_test_log_sha256=driver.sha(TEST_LOG),
        new_primary_contract_tests_passed=16,original_contract_tests_passed=36,total_tests_passed=52,
        actual_score_or_actual_model_holdings_read=False,actual_model_strategy_returns_computed=False,
        original_production_or_training_or_policy_or_V1_V2_modified=False,
        all_PLAN_acceptance_complete=False,net_point_return_acceptance=False,
        independent_V3_code_review_status='PENDING',
        next_commands=['python -m gru_research.trading_comparison_v3 verify',
            'ONLY_AFTER_AUTHORIZED_LATER_FREEZE: python -m gru_research.trading_comparison_v3 prepare --run ACTUAL_FROZEN_RUN --output NEW_INPUT_MANIFEST.json',
            'ONLY_AFTER_AUTHORIZED_LATER_FREEZE: python -m gru_research.trading_comparison_v3 run --manifest NEW_INPUT_MANIFEST.json --output NEW_COMPARISON_DIRECTORY'])
    driver.write_new(driver.RECEIPT,receipt)
    # Recheck parent exact identities and primary originals after registration.
    parent.verify_source_seal();proof.verify();driver.verify_source_seal()
    return dict(protocol=str(driver.PROTOCOL),protocol_sha256=driver.sha(driver.PROTOCOL),
        source_seal_sha256=driver.sha(driver.SEAL),receipt_sha256=driver.sha(driver.RECEIPT),
        parent_bytes_preserved=True,actual_scores_read=False,actual_strategy_returns_computed=False)


if __name__=='__main__':print(driver.json.dumps(register(),ensure_ascii=False,indent=2))

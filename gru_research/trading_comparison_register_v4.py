"""Pre-score registration only after all three independent source reviews PASS."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import trading_comparison_v4 as driver
import trading_comparison_v3 as parent
from primary_reference_inputs_v4 import PrimaryReferenceInputsV4,INPUT_SHA,CDR_POLICY,CDR_POLICY_SHA
from primary_reference_inputs_v4 import PREFIX_PARSER,PREFIX_PARSER_SHA,PREFIX_HISTORICAL_MANIFEST,PREFIX_HISTORICAL_MANIFEST_SHA

NEW_SOURCE_NAMES=('primary_reference_inputs_v4.py','trading_inputs_v4.py',
    'trading_comparison_metrics_v4.py','trading_comparison_v4.py',
    'trading_comparison_register_v4.py','tests/test_primary_reference_inputs_v4.py')
TEST_LOG=driver.HERE/'artifacts/trading_comparison_v4_tests_preseal_full.log'


def register():
    # Deliberately no model freeze/score preparation or account replay call.
    inputs=PrimaryReferenceInputsV4()
    inputs.require_all100_admitted()
    old_protocol,old_seal,old_receipt=parent.verify_source_seal()
    if not TEST_LOG.is_file() or 'Ran 71 tests' not in TEST_LOG.read_text() or not TEST_LOG.read_text().rstrip().endswith('OK'):
        raise ValueError('all 19 new and 52 unchanged contract tests must actually pass')
    source_hashes={str(driver.HERE/name):driver.sha(driver.HERE/name) for name in NEW_SOURCE_NAMES}
    immutable=dict(old_seal['immutable_files'])
    immutable.update(old_receipt['driver_source_hashes'])
    immutable.update(inputs.source_hashes)
    immutable.update(source_hashes)
    for path in (parent.PROTOCOL,parent.SEAL,parent.RECEIPT,TEST_LOG):
        immutable[str(path)]=driver.sha(path)
    for path in driver.HERE.glob('artifacts/trading_comparison_v4_candidate*.log'):
        immutable[str(path)]=driver.sha(path)
    driver.verify_files(immutable);driver.verify_files(old_seal['minute_raw_day_files'])
    protocol=deepcopy(old_protocol)
    protocol.update(schema_version=4,protocol_id='GRU_NINE_CONTINUOUS_ACCOUNT_REVIEWED_REFERENCE_INSTRUMENT_GATE_V4',
        registered_at_utc=datetime.now(timezone.utc).isoformat(),
        parent_v3_protocol_sha256=driver.sha(parent.PROTOCOL),
        parent_v3_source_seal_sha256=driver.sha(parent.SEAL),
        parent_v3_driver_receipt_sha256=driver.sha(parent.RECEIPT),
        change_from_v3='ONLY_REVIEWED_CASE_SPECIFIC_REFERENCE_INPUTS_PLUS_SEPARATE_UNKNOWN_CDR_INSTRUMENT_GATE',
        primary_reference_v4_input_identity=inputs.source_identity,
        original_unknown100_input_sha256=INPUT_SHA,
        new_primary_reference_admitted_orders=list(range(1,101)),
        new_primary_reference_admitted_count=100,
        previous_primary_case_count=9,total_primary_factual_case_count=109,
        signature_corrections='APPEND_ONLY_CASES_6_10_19_ORIGINAL_FACTS_IMMUTABLE',
        original_vendor_reference_and_action_fields_unmodified=True,
        reference_gate='UNCHANGED_V3_OR_STRICT_PRIMARY_REVIEW_PASS_ORIGINAL_ROW_BOUND_PUBLISHED_EXPRESSION_EXACT_CENT',
        instrument_gate='SH689009_ALL_HELD_REQUESTED_FILLED_DATES_TAX_AND_FEE_QUALIFICATION_UNKNOWN; REFERENCE_RESOLUTION_DOES_NOT_CLEAR',
        instrument_prior_anchor='ANY_NEEDED_UNKNOWN_INSTRUMENT_EXPOSURE_AT_OR_BEFORE_ANCHOR_CARRIES_FORWARD; LATER_EXPOSURES_DO_NOT_REJECT_EARLIER_WINDOW',
        cdr_policy_diagnostic_receipt=str(CDR_POLICY),cdr_policy_diagnostic_receipt_sha256=CDR_POLICY_SHA,
        cdr_policy_effective_date_not_historical_arrival=True,
        cdr_historical_universe_scope=inputs.instrument_scope,
        cdr_cost_path='FROZEN_STYLIZED_DIAGNOSTIC_ONLY; NOT_VERIFIED_ACTUAL_CDR_TAX_OR_FEES',
        cdr_affected_net_bounds=None,cdr_affected_economic_eligibility=False,
        cdr_reference_unit='ACTUAL_CASH_PER_CDR_DISTINCT_FROM_UNDERLYING_SHARE',
        existing_capital_conversion_FIFO_fraction_and_tax_pending_unchanged=True,
        historical_source_arrival='RETROSPECTIVE_PRIMARY_PUBLICATION_ONLY_ACTUAL_HISTORICAL_INGESTION_NOT_CERTIFIED',
        all_policy_model_seed_score_field_and_strategy_parameters_unchanged=True,
        additional_unchanged_running_parser_explicitly_sealed_in_v4=dict(path=str(PREFIX_PARSER),sha256=PREFIX_PARSER_SHA,
            historical_manifest=str(PREFIX_HISTORICAL_MANIFEST),historical_manifest_sha256=PREFIX_HISTORICAL_MANIFEST_SHA,
            not_previously_in_v3_comparison_source_map=True),
        original_v1_v2_v3_bytes_retained=True,actual_scores_holdings_returns_read=False)
    seal=deepcopy(old_seal)
    seal.update(schema_version=4,immutable_files=immutable,
        status='V4_ALL_PARENT_AND_REVIEWED_PRIMARY_AND_CDR_DIAGNOSTIC_BYTES_SEALED_BEFORE_SCORE_READ',
        parent_v3_source_seal_sha256=driver.sha(parent.SEAL),
        independent_reviewed_input_identity=inputs.source_identity,
        new_reviewed_case_count=100,all_parent_minute_files_retained=True,
        scores_read=False,model_returns_computed=False)
    driver.write_new(driver.PROTOCOL,protocol);driver.write_new(driver.SEAL,seal)
    receipt=dict(schema_version=4,status='V4_REVIEWED_INPUT_DRIVER_REGISTERED_ACTUAL_MODEL_FREEZE_PENDING',
        comparison_protocol_sha256=driver.sha(driver.PROTOCOL),source_seal_sha256=driver.sha(driver.SEAL),
        driver_source_hashes=source_hashes,primary_reference_v4_input_identity=inputs.source_identity,
        parent_v3_protocol_sha256=driver.sha(parent.PROTOCOL),
        parent_v3_source_seal_sha256=driver.sha(parent.SEAL),
        parent_v3_driver_receipt_sha256=driver.sha(parent.RECEIPT),
        actual_new_source_and_evidence_files_verified=len(inputs.source_hashes),
        actual_market_source_files_sealed=len(immutable),
        actual_minute_days_sealed=len(seal['minute_raw_day_files']),
        source_coverage=seal['monthly_current_member_0931_window_coverage'],
        preseal_test_log_path=str(TEST_LOG),preseal_test_log_sha256=driver.sha(TEST_LOG),
        new_contract_tests_passed=19,unchanged_contract_tests_passed=52,total_tests_passed=71,
        primary_reference_fact_count=109,additional_account_cash_components=0,
        unchanged_v3_missing_cash_component_rights_retained=True,
        unknown_cdr_instrument_scope_retained=True,noncash_and_broker_rounding_unknowns_retained=True,
        historical_cdr_scope=inputs.instrument_scope,
        original_source_or_policy_or_v1_v2_v3_modified=False,
        actual_score_holdings_or_model_return_read=False,actual_model_backtest_run=False,
        all_PLAN_acceptance_complete=False,point_net_target_accepted=False,
        independent_v4_code_review_status='PENDING')
    driver.write_new(driver.RECEIPT,receipt)
    parent.verify_source_seal();inputs.verify();driver.verify_source_seal()
    return dict(protocol_path=str(driver.PROTOCOL),protocol_sha256=driver.sha(driver.PROTOCOL),
        seal_sha256=driver.sha(driver.SEAL),receipt_sha256=driver.sha(driver.RECEIPT),
        old_bytes_preserved=True,actual_score_holdings_or_return_read=False)


if __name__=='__main__':print(driver.json.dumps(register(),ensure_ascii=False,indent=2))

"""One-time market-only comparison seal; never opens a model score or freeze."""
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import trading_comparison as driver
import pandas as pd
import numpy as np
import pyarrow


def register():
    here=driver.HERE;artifacts=here/'artifacts'
    readiness=driver.read_json(artifacts/'trading_research_readiness_v2.json')
    v2=artifacts/'trading_research_protocol_v2.json'
    if driver.sha(v2)!=driver.V2_SHA or driver.sha(artifacts/'trading_research_readiness_v2.json')!=driver.V2_READINESS_SHA:
        raise ValueError('immutable V2 bridge identity required')
    original=driver.read_json(artifacts/'trading_provider_readiness.json')['frozen_code_hashes']
    files={str(driver.ROOT/p):h for p,h in original.items()}
    files.update({str(here/name):h for name,h in readiness['new_research_source_hashes'].items()})
    source=Path(readiness['source_dir'])
    files.update({str(source/name):h for name,h in readiness['source_hashes'].items()})
    prior_comparison={
        str(artifacts/'trading_comparison_protocol_v1.json'):'9094dcf553c17fd66adf7bc2f1aec1b50afc19948f35de1630dfe2e678c45700',
        str(artifacts/'trading_comparison_source_seal_v1.json'):'7fd22ebd471028a73d377d42244343348fb779dcd892da5c951560ac2e1bc48b',
        str(artifacts/'trading_comparison_driver_receipt_v1.json'):'d0c7280b680876233b317a874189ee48e2d87277525d79518dcd7115fd7e6713',
    }
    driver.verify_files(prior_comparison);files.update(prior_comparison)
    prior_receipt=driver.read_json(artifacts/'trading_comparison_driver_receipt_v1.json')
    archive=artifacts/'trading_comparison_v1_source_archive'
    for old,h in prior_receipt['driver_source_hashes'].items():
        files[str(archive/Path(old).name)]=h
    files[str(archive/'archive_receipt.json')]=driver.sha(archive/'archive_receipt.json')
    driver.verify_files(files)
    # The source bytes behind ALL successful raw daily/status/action queries
    # remain sealed as well as their normalized representations.
    queries=pd.read_parquet(source/'query_coverage.parquet')
    if not queries.query_status.isin(['provider_success_empty','provider_success_nonempty']).all():
        raise ValueError('failed/unrequested source query remains')
    for row in queries.itertuples():
        files[str(Path(row.source_path).resolve())]=row.source_sha256
    additions=[v2,artifacts/'trading_research_readiness_v2.json',artifacts/'trading_provider_readiness.json',
        artifacts/'minute_feature_metadata.json',artifacts/'minute_members.npy',artifacts/'minute_calendar.json',
        artifacts/'minute_tail_calendar_evidence.json',here/'PLAN.md',here/'config.json',
        here/'train.py',here/'score_export_audit.py',here/'experiment_acceptance.py',
        artifacts/'experiment_acceptance_statistics_protocol_20261001.json']
    for name in ['cash_dividend_tax.py','cash_replay_data.py','frozen_pretax_provider.py','scenario_action_catalog.py']:
        additions.append(driver.ROOT/'research_v3/src'/name)
    benchmark=here/'trading_data/official_benchmark_2025_2026/normalized/official_csi500_price_2025_2026.csv'
    additions+=list((here/'trading_data/official_benchmark_2025_2026').rglob('*'))
    additions+=list((here/'trading_data/vendor_benchmark_2025_2026').rglob('*'))
    additions+=list((here/'trading_data/official_sources').rglob('*'))
    for path in additions:
        if path.is_file():files[str(path.resolve())]=driver.sha(path)
    driver.verify_files(files)
    minute=driver.FrozenMinuteTWAPSource()
    days=[d for d in minute.calendar if date(2025,1,2)<=d<=date(2026,9,23)]
    members=driver.historical_members(days)
    pools={d.date():set(g.symbol) for d,g in members.groupby('date')}
    minute_files={};month=defaultdict(lambda:defaultdict(int))
    for d in days:
        path=artifacts/'minute_hourly_raw'/(str(d)+'.parquet')
        minute_files[str(path.resolve())]=driver.sha(path) if path.is_file() else None
        summary=month[str(d)[:7]]
        summary['trading_days']+=1;summary['dated_current_member_stock_days']+=len(pools[d])
        if not path.is_file():
            summary['absent_file_member_stock_days']+=len(pools[d]);continue
        f=pd.read_parquet(path,columns=['symbol','slot','window_complete','window_volume','window_raw_price','window_tradable'])
        f=f.loc[f.slot.eq(0)].copy();f['cash_symbol']=f.symbol.map(driver.cash_symbol)
        if f.cash_symbol.duplicated().any():raise ValueError('duplicate sealed first execution-window key')
        summary['all_source_first_window_stock_days']+=len(f)
        pool=f.loc[f.cash_symbol.isin(pools[d])]
        complete=pool.window_complete.astype(bool)
        positive=complete & pool.window_tradable.astype(bool) & pool.window_volume.gt(0) & pool.window_raw_price.gt(0)
        summary['current_member_window_rows']+=len(pool)
        summary['current_member_absent_window_rows']+=len(pools[d])-len(pool)
        summary['current_member_complete_positive_trade_window_rows']+=int(positive.sum())
        summary['current_member_complete_zero_volume_window_rows']+=int((complete & pool.window_volume.eq(0)).sum())
        summary['current_member_corrupt_or_partial_window_rows']+=int((~complete).sum())
    stat=driver.read_json(artifacts/'experiment_acceptance_statistics_protocol_20261001.json')
    protocol=dict(schema_version=2,protocol_id='GRU_NINE_CONTINUOUS_ACCOUNT_FIXED_COMPARISON_V2',
        previous_comparison_protocol_sha256=driver.sha(artifacts/'trading_comparison_protocol_v1.json'),
        previous_source_seal_sha256=driver.sha(artifacts/'trading_comparison_source_seal_v1.json'),
        previous_driver_receipt_sha256=driver.sha(artifacts/'trading_comparison_driver_receipt_v1.json'),
        change_from_v1='ACTUAL_REQUIRED_UNKNOWN_EXECUTION_AT_OR_BEFORE_ANCHOR_BLOCKS_CONTINUOUS_CARRY_COMPARISON_WITHOUT_REJECTING_EARLIER_UNAFFECTED_WINDOWS',
        registered_at_utc=datetime.now(timezone.utc).isoformat(),
        status='FROZEN_BEFORE_ACTUAL_TEST_SCORE_READ_BY_THIS_DRIVER',
        bridge_protocol_sha256=driver.V2_SHA,bridge_readiness_sha256=driver.V2_READINESS_SHA,
        models=list(driver.MODELS),model_catalog_source='gru_research/train.py:freeze_models',
        model_catalog_source_sha256=driver.sha(here/'train.py'),
        actual_model_identity_source='LATER_ACTUAL_RUN/freeze.json evaluation_models,checkpoint_hashes,freeze_id',
        model_versions='EXACT_ACTUAL_FREEZE_ID:MODEL_NAME',
        selected_model='selected_full_FIXED_FROM_2024_NOT_TEST_WINNER',
        checkpoint_seeds=[11,22,33,44,55],score_columns=driver.SCORE_COLUMNS,
        input_csv='ORIGINAL_RAW_7COL_OR_EXACT_RAW_PRESERVED_CORRECTED_EXPORT_WITH_HASH_RECEIPT',
        scores_blank_or_NaN_remain_missing=True,quality_flags_never_filter_finite_score=True,
        prediction_four_hour_evaluation='SEPARATE_MODEL_ACCEPTANCE_NOT_DAILY_TRADE_RETURN',
        transaction_signal='PRIOR_DAY_15:00_ONLY',initial_capital='1000000',
        source_coverage_gate='FOUR_HOUR_INPUT_COVERAGE_REPORTED_SEPARATELY; ONLY_ACTUALLY_CONSUMED_PRIOR15CLOSE_SIGNALS_GATE_TRADING; CONTINUOUS_STATE_REQUIRES_ALL_PRIOR_CLOSE_SIGNALS',
        account_modes=['pretax','tax_upper_reserve'],cost_scenarios=['primary','stress'],
        continuous_account=True,year_2026_reset_cash_or_holdings=False,
        windows=driver.WINDOWS,first_possible_execution='2025-01-03',
        account_2026_anchor='ACTUAL_2025_12_31_HELD_ACCOUNT_CLOSE_NAV',
        benchmark_2026_anchor='ACTUAL_2025_12_31_CSI500_PRICE_CLOSE',
        whole_window_initial_cash_mark='2025-01-02',initial_no_buy_day_retained=True,
        interval_returns_never_annualized=True,
        arithmetic_excess_percentage_points='100*((NAV_end/NAV_anchor-1)-(CSI_close_end/CSI_close_anchor-1))',
        wealth_relative_excess_return='(NAV_end/NAV_anchor)/(CSI_close_end/CSI_close_anchor)-1',
        drawdown='MAX_EOD_CLOSE_NAV_DRAWDOWN_INCLUDING_ACTUAL_SEGMENT_ANCHOR',
        gross_turnover='SUM_OVER_FILLED_ORDERS((BUY_PLUS_SELL_FILL_NOTIONAL)/PREVIOUS_SESSION_ACTUAL_CLOSE_NAV)',
        one_way_half_turnover='0.5*GROSS_TURNOVER_EXPLICITLY_NAMED',
        costs='ACTUAL_COMMISSION_TRANSFER_STAMP_PLUS_SEPARATE_SLIPPAGE_VS_SEALED_RAW_TWAP_NEVER_DOUBLE_ADDED',
        risk='ORIGINAL_ENGINE_EOD_AND_POSTFILL_BREACH_FLAGS; BUILD_EXEMPTIONS_ONLY_ORIGINAL_PLANNER_ENGINE',
        required_pair_incomplete='NO_SHARED_PREFIX_TRUNCATION_NO_ONLY_COMPLETE_MODEL_WINNER',
        unresolved_net='NONE_NEVER_ZERO; UNCERTAIN_ANCHOR_RETURN_BOUNDS_END_LO/ANCHOR_HI_TO_END_HI/ANCHOR_LO',
        tax_bounds_scope='SAME_SIMULATED_UPPER_RESERVE_TRADING_PATH_NOT_ALL_UNKNOWN_TAX_FEEDBACK_PATHS',
        unknown_special_ex_reference='ACTUAL_HELD_OR_REQUESTED_EXPOSURES_RETAINED_INCLUDING_PRIOR_ANCHOR_EFFECTS',
        prior_execution_evidence_affect_anchor='ALL_ACTUAL_REQUIRED_MISSING_EXECUTION_AT_OR_BEFORE_SEGMENT_ANCHOR_BLOCK_FULL_ECONOMIC_COMPARISON_AND_FINITE_NET_BOUNDS',
        raw_minute_guard='ALL_DECLARED_DAY_PARQUET_HASHES_BEFORE_AND_AFTER_EVERY_RUN_AND_ACTUAL_SOURCE_ACCESS_RECEIPTS',
        full_period_result_requires_all_marks_and_required_execution_evidence=True,
        risk_violation_blocks_economic_eligibility=True,point_net_target_acceptance=False,
        all_PLAN_acceptance_complete=False,contrasts=stat['contrasts'],
        contrasts_source_sha256=driver.sha(artifacts/'experiment_acceptance_statistics_protocol_20261001.json'),
        trading_source_directory=str(source.parent),official_benchmark_csv=str(benchmark),
        official_benchmark_variant='CSI000905_PRICE_ONLY',official_benchmark_csv_sha256=driver.sha(benchmark),
        official_calendar_source=driver.CALENDAR_SOURCE,
        actual_model_scores_or_returns_read_at_registration=False)
    seal=dict(schema_version=2,status='ACTUAL_MARKET_AND_MINUTE_BYTES_SEALED_BEFORE_SCORE_READ',
        immutable_files=files,minute_raw_day_files=minute_files,
        monthly_current_member_0931_window_coverage=dict(month),
        monthly_coverage_scope='TRUE_CURRENT_EXECUTION_DAY_MEMBERS; ACTUAL_CARRIED_HELD_AND_REQUESTED_WINDOWS_RECORDED_PER_REPLAY',
        vendor_query_source_files_sealed=len(queries),
        missing_minute_file_paths=[p for p,h in minute_files.items() if h is None],
        scores_read=False,model_returns_computed=False)
    driver.write_new(driver.PROTOCOL,protocol);driver.write_new(driver.SEAL,seal)
    log=artifacts/'trading_comparison_v2_tests_preseal.log'
    receipt=dict(schema_version=2,status='FIXED_COMPARISON_DRIVER_READY_INPUT_SCORE_FREEZE_PENDING',
        changed_behavior='CONTINUOUS_YEAR_SEGMENT_WITH_PRIOR_ACTUAL_NEEDED_UNKNOWN_EXECUTION_IS_NOT_ECONOMICALLY_COMPLETE; LATER_GAP_DOES_NOT_REJECT_EARLIER_COMPLETE_DIAGNOSTIC',
        actual_bad_model_outcome_seen_before_patch=False,actual_profit_claim=False,
        bridge_rules_accounts_costs_V2_changed=False,
        comparison_protocol_sha256=driver.sha(driver.PROTOCOL),source_seal_sha256=driver.sha(driver.SEAL),
        driver_source_hashes={str(here/n):driver.sha(here/n) for n in ['trading_comparison.py','trading_comparison_metrics.py',
            'trading_comparison_register.py','tests/test_trading_comparison.py']},
        test_log_path=str(log),test_log_sha256=driver.sha(log),unit_contract_tests_passed=15,
        contract_dispatch='36_SEPARATE_INITIAL_CASH_THEN_SOURCE_SCORE_MISSING_BOOKS_ALL_INCOMPLETE_NO_FAKE_FORECAST',
        actual_source_files_sealed=len(files),actual_minute_days_sealed=len(minute_files),
        source_coverage=seal['monthly_current_member_0931_window_coverage'],
        actual_training_model_scores_read=False,actual_strategy_returns_computed=False,
        original_production_modified=False,training_or_model_files_modified=False,
        complete_PLAN_acceptance=False,net_point_return_acceptance=False,
        runtime=dict(python=sys.version,pandas=pd.__version__,numpy=np.__version__,pyarrow=pyarrow.__version__),
        next_commands=['python -m gru_research.trading_comparison prepare --run ACTUAL_FROZEN_RUN --output NEW_INPUT_MANIFEST.json',
            'python -m gru_research.trading_comparison run --manifest NEW_INPUT_MANIFEST.json --output NEW_COMPARISON_DIRECTORY'])
    driver.write_new(driver.RECEIPT,receipt)
    return dict(status=receipt['status'],minute_days=len(minute_files),market_source_files=len(files),
        protocol_sha256=receipt['comparison_protocol_sha256'],source_seal_sha256=receipt['source_seal_sha256'])


if __name__=='__main__':print(driver.json.dumps(register(),ensure_ascii=False,indent=2))

"""One bounded actual-source PIT audit, fixed at2024-06-18 10:30 Shanghai.

Only copied AST financial operators execute. No model, labels, normalization,
selection statistics, network or existing research outputs are written.
Counterfactual frames are audit probes, never replacement historical data.
"""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ART = ROOT / 'gru_research/artifacts'
DATA = ART / 'dataset'
FUND = ROOT / '中证500_历年成分股_基本面_2010-2026_iFinD_PIT'
DAY = '2024-06-18'
SIGNAL = '2024-06-18T10:30:00+08:00'
PROVENANCE = ['event_query_date', 'report_period', 'report_query_date',
              'financial_available_date', 'report_version_available_date',
              'market_query_date', 'market_available_date']
CHECKS = []


def ident_bytes(path, body):
    return dict(path=str(Path(path).resolve()), bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


def ident(path):
    return ident_bytes(path, Path(path).read_bytes())


def stat(path):
    p = Path(path).resolve()
    value = p.stat()
    return dict(path=str(p), bytes=value.st_size, mtime_ns=value.st_mtime_ns, inode=value.st_ino)


def encoded_budget(path, columns, row_group=0):
    p = pq.ParquetFile(path)
    group = p.metadata.row_group(row_group)
    return sum(group.column(p.schema_arrow.names.index(column)).total_compressed_size for column in columns)


def write_new(path, value):
    with path.open('x') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def comparable(frame, fields):
    return frame[['date', 'symbol'] + fields].sort_values(['date', 'symbol']).reset_index(drop=True)


def unchanged(name, baseline, counterfactual, fields):
    pd.testing.assert_frame_equal(comparable(baseline, fields), comparable(counterfactual, fields),
                                  check_exact=True, check_dtype=True)
    CHECKS.append(dict(name=name, status='PASS_EXACT_VALUES_NAN_AND_PROVENANCE_UNCHANGED',
                       actual_prefix_rows=len(baseline), fields=fields))


def numeric_json(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def safe_record(record):
    out = {}
    for key, value in record.items():
        if isinstance(value, (pd.Timestamp, np.datetime64)):
            out[key] = None if pd.isna(value) else str(pd.Timestamp(value))
        elif isinstance(value, (float, np.floating)):
            out[key] = numeric_json(value)
        elif isinstance(value, (np.integer,)):
            out[key] = int(value)
        else:
            out[key] = value
    return out


def audit(out, source_before, big_before):
    source_path = ROOT / 'gru_research/slow_features.py'
    source_body = source_path.read_bytes()
    syntax = ast.parse(source_body)
    names = ('safe_ratio', 'next_session', 'financial_events', 'pit_join')
    functions = [node for node in syntax.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if [node.name for node in functions] != list(names):
        raise ValueError('original AST function roster changed')
    env = {'np': np, 'pd': pd}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source_path), 'exec'), env)
    constants = {}
    for node in syntax.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ('FUND16', 'AGES2'):
                constants[node.targets[0].id] = ast.literal_eval(node.value)
    features = constants['FUND16'] + constants['AGES2']
    fields = features + PROVENANCE + ['ttm_parent_profit', 'parent_equity', 'total_market_cap_cny']
    calendar = json.loads((ART / 'minute_calendar.json').read_bytes())
    dates = calendar['dates']
    symbols = json.loads((DATA / 'symbols.json').read_bytes())
    if symbols != calendar['union_symbols']:
        raise ValueError('original calendar/symbol order differs')
    day_index = dates.index(DAY)
    members = np.load(DATA / 'members.npy', mmap_mode='r')
    member_row = np.array(members[4 * day_index], copy=True)
    if not np.array_equal(member_row, members[4 * day_index + 1]) or not np.array_equal(member_row, members[4 * day_index + 2]) or not np.array_equal(member_row, members[4 * day_index + 3]):
        raise ValueError('daily member identity differs among fixed-day four slots')
    ordered = [(int(i), symbols[int(i)]) for i in np.flatnonzero(member_row)]
    fpath = FUND / 'financial_verified_19_fields.parquet'
    mpath = FUND / 'weekly_market_and_industry.parquet'
    # Read physical input bytes once; key and selected-row decoding reuse memory.
    fb, mb = fpath.read_bytes(), mpath.read_bytes()
    if len(fb) + len(mb) > 13_200_000:
        raise ValueError('declared original source byte budget exceeded')
    source_ids = [ident_bytes(fpath, fb), ident_bytes(mpath, mb)]
    declared = json.loads((ART / 'slow_manifest.json').read_bytes())['sources']
    for item in source_ids:
        if item['sha256'] != declared[Path(item['path']).name]:
            raise ValueError('original PIT source SHA does not match frozen slow manifest')
    keys = pq.read_table(pa.BufferReader(fb), columns=['thscode', 'asof_date', 'report_period'], use_threads=False)
    key_rows = keys.to_pylist()
    visible_symbols = {row['thscode'] for row in key_rows if row['asof_date'] < DAY and row['report_period'] <= row['asof_date']}
    chosen = next((i, symbol) for i, symbol in ordered if symbol in visible_symbols)
    symbol_index, symbol = chosen
    position_map = {(row['thscode'], row['asof_date'], row['report_period']): i for i, row in enumerate(key_rows)}
    ft = pq.read_table(pa.BufferReader(fb), filters=[('thscode', '=', symbol)], use_threads=False)
    mt = pq.read_table(pa.BufferReader(mb), filters=[('thscode', '=', symbol)], use_threads=False)
    financial, market = ft.to_pandas(), mt.to_pandas()
    # Derived exact-row subsets retain original values/schema; not original file bytes.
    pq.write_table(ft, out / 'actual_financial_versions_row_subset.parquet', compression='zstd')
    pq.write_table(mt, out / 'actual_market_versions_row_subset.parquet', compression='zstd')
    actual_member_dates = [dates[i] for i in range(day_index + 1) if bool(members[4 * i, symbol_index])]
    targets = pd.DataFrame({'date': pd.to_datetime(actual_member_dates), 'symbol': symbol})
    if DAY not in actual_member_dates:
        raise ValueError('fixed target is not a true dated member')
    calendar_index = pd.DatetimeIndex(dates)
    baseline = env['pit_join'](targets, financial, market, calendar_index)
    baseline_events = env['financial_events'](financial, calendar_index)
    current = baseline.loc[baseline.date.eq(pd.Timestamp(DAY))].iloc[0]
    if pd.isna(current.financial_available_date) or current.financial_available_date > pd.Timestamp(DAY):
        raise ValueError('chosen first dated member has no valid financial version')
    # Do not silently choose another stock if this first selection fails.
    effective_source = financial.loc[financial.asof_date.lt(DAY)].sort_values(['asof_date', 'report_period'])
    state = {}
    for row in effective_source.to_dict('records'):
        state[row['report_period']] = row
    period = max(state)
    cur = state[period]
    if pd.Timestamp(cur['asof_date']) != current.report_query_date or pd.Timestamp(period) != current.report_period:
        raise ValueError('original source state and PIT provenance disagree')
    numeric_columns = [field.name for field in ft.schema if pa.types.is_floating(field.type)]
    numeric_market = ['total_market_cap_cny']
    future = financial.copy(deep=True)
    future_mask = future.asof_date.gt(DAY)
    future.loc[future_mask, numeric_columns] = future.loc[future_mask, numeric_columns] * 17 + 777
    future_market = market.copy(deep=True)
    fm = future_market.asof_date.gt(DAY)
    future_market.loc[fm, numeric_market] = future_market.loc[fm, numeric_market] * 19 + 999
    future_join = env['pit_join'](targets, future, future_market, calendar_index)
    unchanged('modify_all_strictly_future_actual_financial_and_market_queries', baseline, future_join, fields)
    same_day = dict(cur, asof_date=DAY)
    for column in numeric_columns:
        if pd.notna(same_day[column]):
            same_day[column] = same_day[column] * 17 + 777
    if ((financial.asof_date == DAY) & (financial.report_period == same_day['report_period'])).any():
        raise ValueError('same-day counterfactual query key collides; no automatic alternate case')
    next_day = dates[day_index + 1]
    second_day = dates[day_index + 2]
    known_market = market.loc[market.asof_date.lt(DAY)].sort_values('asof_date').iloc[-1].to_dict()
    same_market = dict(known_market, asof_date=DAY,
                       total_market_cap_cny=known_market['total_market_cap_cny'] * 23 + 111)
    if market.asof_date.eq(DAY).any():
        raise ValueError('same-day market counterfactual key collides')
    same_financial = pd.concat([financial, pd.DataFrame([same_day])], ignore_index=True)
    same_market_frame = pd.concat([market, pd.DataFrame([same_market])], ignore_index=True)
    same_join = env['pit_join'](targets, same_financial, same_market_frame, calendar_index)
    unchanged('append_unavailable_same_day_EOD_financial_and_market_query', baseline, same_join, fields)
    future_targets = pd.DataFrame({'date': pd.to_datetime([DAY, next_day, second_day]), 'symbol': symbol})
    visible_next = env['pit_join'](future_targets, same_financial, same_market_frame, calendar_index)
    next_row = visible_next.loc[visible_next.date.eq(pd.Timestamp(next_day))].iloc[0]
    if not (next_row.event_query_date == pd.Timestamp(DAY) and next_row.financial_available_date == pd.Timestamp(next_day)
            and next_row.report_version_available_date == pd.Timestamp(next_day)
            and next_row.market_query_date == pd.Timestamp(DAY) and next_row.market_available_date == pd.Timestamp(next_day)
            and next_row.age_version_days == 0):
        raise ValueError('same-day query was not first available on next verified session')
    CHECKS.append(dict(name='same_day_queries_first_visible_next_verified_session', status='PASS',
                       query_date=DAY, excluded_signal=SIGNAL, first_usable_session=next_day,
                       observed_query_and_available_provenance=safe_record(next_row[PROVENANCE + ['age_version_days']].to_dict())))
    annual_period = f'{pd.Timestamp(period).year - 1}-12-31'
    if annual_period not in state or annual_period >= period:
        raise ValueError('real old annual operand required; no substitute stock/report')
    old_revision = dict(state[annual_period], asof_date=next_day)
    for column in numeric_columns:
        if pd.notna(old_revision[column]):
            old_revision[column] = old_revision[column] * 29 + 555
    if ((financial.asof_date == next_day) & (financial.report_period == annual_period)).any():
        raise ValueError('future old-report revision key collides')
    revised = pd.concat([financial, pd.DataFrame([old_revision])], ignore_index=True)
    revised_prefix = env['pit_join'](targets, revised, market, calendar_index)
    unchanged('append_strictly_future_query_revising_real_old_annual_report', baseline, revised_prefix, fields)
    revised_after = env['pit_join'](future_targets, revised, market, calendar_index)
    control_after = env['pit_join'](future_targets, financial, market, calendar_index)
    revised_value = revised_after.loc[revised_after.date.eq(pd.Timestamp(second_day)), 'ttm_parent_profit'].iloc[0]
    control_value = control_after.loc[control_after.date.eq(pd.Timestamp(second_day)), 'ttm_parent_profit'].iloc[0]
    if not np.isfinite([revised_value, control_value]).all() or revised_value == control_value:
        raise ValueError('old-report revision probe did not affect its future TTM operand')
    CHECKS.append(dict(name='future_old_report_probe_is_active_only_after_its_query_lag', status='PASS',
                       old_report_period=annual_period, original_real_operand_query=state[annual_period]['asof_date'],
                       counterfactual_query_date=next_day, first_usable_session=second_day,
                       changed_future_ttm_only=True))
    malformed = dict(cur, asof_date=DAY, report_period='2024-09-30')
    try:
        env['financial_events'](pd.concat([financial, pd.DataFrame([malformed])], ignore_index=True), calendar_index)
    except ValueError as exc:
        if str(exc) != 'future report period in PIT data':
            raise
    else:
        raise ValueError('original AST accepted a report period later than its query')
    CHECKS.append(dict(name='future_report_period_cannot_be_used_as_disclosed_data', status='PASS_REJECTED_ORIGINAL_AST',
                       exact_original_error='future report period in PIT data'))
    # The cache is raw float32. No normalization or large array rehash happens.
    meta = json.loads((DATA / 'meta.json').read_bytes())
    if [meta['features'][i] for i in meta['groups']['fund'] + meta['groups']['ages']] != features:
        raise ValueError('frozen cache feature order differs')
    raw = np.load(DATA / 'slow_raw_by_day.npy', mmap_mode='r')
    indices = [i - 24 for i in meta['groups']['fund'] + meta['groups']['ages']]
    actual = np.array(raw[day_index, symbol_index, indices], copy=True)
    calculated = current[features].to_numpy(dtype=np.float64)
    expected_float32 = calculated.astype(np.float32)
    if not np.array_equal(expected_float32, actual, equal_nan=True):
        raise ValueError('source->actual slow_raw_by_day fund16+ages2 mismatch; no repair/stock change')
    CHECKS.append(dict(name='source_original_AST_to_actual_saved_float32_raw_cache_18fields', status='PASS_BITWISE_VALUES_AND_NAN',
                       exact_field_count=18, array_indices=[day_index, symbol_index], normalized_model_input_read=False))
    # One row group's selected provenance columns:405066 encoded bytes, not full308MB.
    slow_path = ART / 'slow_raw.parquet'
    provenance_columns = ['date', 'symbol'] + PROVENANCE
    stored_provenance = pq.read_table(slow_path, columns=provenance_columns,
        filters=[('date', '=', pd.Timestamp(DAY)), ('symbol', '=', symbol)],
        use_threads=False, pre_buffer=False).to_pandas()
    if len(stored_provenance) != 1:
        raise ValueError('actual saved raw provenance requires exactly one fixed date/security row')
    pd.testing.assert_frame_equal(comparable(baseline.loc[baseline.date.eq(pd.Timestamp(DAY))], PROVENANCE),
                                  comparable(stored_provenance, PROVENANCE), check_exact=True)
    CHECKS.append(dict(name='source_original_AST_to_actual_saved_raw_provenance', status='PASS_EXACT_DATES_AND_SYMBOL'))
    operands = []
    operand_periods = [period, annual_period, str(pd.Timestamp(period) - pd.DateOffset(years=1))[:10]]
    for operand_period in dict.fromkeys(operand_periods):
        row = state.get(operand_period)
        if row is not None:
            operands.append(dict(original_file_row_index=position_map[(symbol, row['asof_date'], row['report_period'])],
                report_role=('current' if operand_period == period else 'prior_annual' if operand_period == annual_period else 'prior_same_period'),
                original_row=safe_record(row)))
    source_after = {path: ident(path) for path in source_before}
    big_after = {path: stat(path) for path in big_before}
    if source_before != source_after or big_before != big_after:
        raise ValueError('original selected file bytes/stat changed during audit')
    provider_date_audit = {}
    for name in ('source_update_date', 'first_provider_event_date'):
        values = pd.to_datetime(financial[name], errors='coerce')
        provider_date_audit[name] = dict(null_rows=int(values.isna().sum()),
            later_than_query_rows=int((values > pd.to_datetime(financial.asof_date)).sum()),
            not_an_independent_publication_or_feed_arrival_timestamp=True)
    return dict(schema_version=1, created_utc=datetime.now(timezone.utc).isoformat(),
        status='PASS_BOUNDED_ACTUAL_VENDOR_QUERY_CAUSALITY_AND_SAVED_RAW_PARITY',
        fixed_signal=SIGNAL, chosen_symbol=symbol, original_union_symbol_index=symbol_index,
        original_calendar_day_index=day_index, original_hour_slot=0,
        selection_rule='Ascending original symbols.json index among actual members at fixed signal, first with a real valid report version asof<fixed date; no score/IC/ranking feedback',
        members_order_first_ten=[{'index': i, 'symbol': s} for i, s in ordered[:10]],
        earlier_eligible_members_without_valid_financial_version=[s for i, s in ordered[:ordered.index(chosen)]],
        fixed_date_members_row_sha256=hashlib.sha256(member_row.tobytes()).hexdigest(),
        actual_member_prefix_rows=len(targets), source_identities=source_ids,
        source_file_sizes_and_row_groups={fpath.name:dict(bytes=len(fb), row_groups=1, rows=len(key_rows)),
            mpath.name:dict(bytes=len(mb), row_groups=1, rows=pq.ParquetFile(pa.BufferReader(mb)).metadata.num_rows)},
        input_physical_source_bytes_read_once_for_main_audit=len(fb)+len(mb),
        in_memory_key_projection_encoded_bytes=encoded_budget(fpath, ['thscode','asof_date','report_period']),
        selected_original_financial_rows=len(financial), selected_original_market_rows=len(market),
        selected_source_row_subsets=[dict(identity=ident(out/name),
            status='DERIVED_EXACT_ROW_SUBSET_ORIGINAL_VALUES_SCHEMA_NOT_ORIGINAL_PARQUET_BYTES')
            for name in ('actual_financial_versions_row_subset.parquet','actual_market_versions_row_subset.parquet')],
        earliest_original_financial_query_date=financial.asof_date.min(),
        latest_original_financial_query_strictly_before_target=current.event_query_date.isoformat(),
        visible_report_query=current.report_query_date.isoformat(), visible_report_period=current.report_period.isoformat(),
        latest_original_market_query_strictly_before_target=current.market_query_date.isoformat(),
        actual_current_TTM_operands=operands,
        original_AST=dict(source=ident_bytes(source_path, source_body), functions=list(names),
            function_AST_SHA256={node.name:hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest() for node in functions},
            function_body_modified=False, original_full_module_imported=False),
        checks=CHECKS, original_provider_date_metadata_audit=provider_date_audit,
        fixed_target_fund16_ages2=[dict(feature=name, source_float64=numeric_json(calculated[i]),
            expected_float32=numeric_json(expected_float32[i]), actual_saved_float32=numeric_json(actual[i]),
            source_NaN=bool(pd.isna(calculated[i])), actual_saved_NaN=bool(pd.isna(actual[i])), exact_after_registered_float32_cast=True)
            for i,name in enumerate(features)],
        fixed_target_provenance=safe_record(current[PROVENANCE].to_dict()),
        actual_existing_raw_provenance=safe_record(stored_provenance.iloc[0].to_dict()),
        existing_large_inputs_access=dict(raw_cache=big_before[str(DATA/'slow_raw_by_day.npy')],
            mmap_shape=list(raw.shape), projected_scalar_count=18, projected_data_bytes=actual.nbytes,
            entire_raw_cache_read_or_SHA_recomputed=False,
            actual_source_provenance_Parquet=big_before[str(slow_path)], projected_row_group=0,
            projected_encoded_column_bytes=encoded_budget(slow_path, provenance_columns),
            all_columns_or_all_row_groups_scanned=False,
            entire_provenance_Parquet_SHA_inherited=json.loads((ART/'slow_manifest.json').read_bytes())['raw_sha256'],
            entire_provenance_Parquet_SHA_recomputed=False),
        selected_small_original_file_bytes_unchanged_before_after=True,
        existing_large_original_file_stat_unchanged_before_after=True,
        selected_small_original_file_identities=list(source_before.values()),
        date_semantics=dict(asof_date='Historical EOD query parameter; usable only next verified exchange session',
            report_period='Accounting period, never its availability time',
            source_update_date='Supplier event-list date, not independently verified exact issuer publication',
            first_provider_event_date='Earliest supplier event in query range, not authenticated first issuer announcement or arrival',
            actual_download_arrival_time='Not historical market availability; not used as a query-date substitute'),
        counterfactual_probes_are_not_replacement_market_or_training_data=True,
        unavailable_same_day_and_future_queries_excluded=True, future_report_period_rejected=True,
        independently_verified_exact_issuer_publication_and_actual_historical_feed_arrival=False,
        historical_provider_revision_completeness_independently_proved=False,
        limitations=['This verifies the original historical-query/next-session convention for one fixed original member, not every issuer/version.',
            'No source column authenticates precise historical publication/arrival; undisclosed rows cannot be independently screened by an absent authenticated timestamp.',
            'Pre2018 source versions map into the first available2018 model calendar session; this is not proof of their exact2010 availability.',
            'No normalization/all122/model-output end-to-end causal perturbation was performed.'],
        original_sources_or_data_modified=False, model_forward_training_IC_network_or_old_tests_executed=False,
        complete_all_financial_causality_or_full_PLAN_acceptance=False)


def main():
    now = datetime.now(timezone.utc)
    out = ROOT / ('gru_research/trading_data/financial_pit_fixed_time_' + now.strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True, exist_ok=False)
    small = [Path(__file__), ROOT/'gru_research/slow_features.py', ROOT/'gru_research/assemble.py',
        ART/'slow_manifest.json', ART/'minute_calendar.json', DATA/'symbols.json', DATA/'meta.json',
        FUND/'使用说明_下载状态.md', FUND/'audit/阶段质量报告_19项完成.md']
    source_before = {str(path): ident(path) for path in small}
    big_before = {str(path): stat(path) for path in [DATA/'members.npy', DATA/'slow_raw_by_day.npy', ART/'slow_raw.parquet']}
    try:
        result = audit(out, source_before, big_before)
    except BaseException as exc:
        failure = dict(status='FAILED_ACTUAL_FIXED_CASE_NO_SOURCE_REPAIR_OR_RESELECTION',
            created_utc=datetime.now(timezone.utc).isoformat(), fixed_signal=SIGNAL,
            error_type=type(exc).__name__, error=str(exc), successful_partial_checks=CHECKS,
            original_small_identities=source_before, original_large_stat=big_before,
            actual_failure_preserved=True, retry_alternate_case_or_source_repair=False,
            full_PLAN_complete=False)
        write_new(out/'first_failure_receipt_v1.json', failure)
        with (out/'actual_exception_traceback.txt').open('x') as handle:
            handle.write(traceback.format_exc())
        print(json.dumps({'failure_receipt':ident(out/'first_failure_receipt_v1.json')}, ensure_ascii=False))
        raise
    path = out/'actual_fixed_time_causal_audit_receipt_v1.json'
    write_new(path, result)
    print(json.dumps({'receipt':ident(path), 'symbol':result['chosen_symbol'],
        'check_count':len(CHECKS), 'status':result['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

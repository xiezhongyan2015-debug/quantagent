"""Independent scope, quote, label, PIT and normalized-array audit of V5."""
import ast
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'backfilled_panel_candidate_ipo_prefix_v5'
OLD = BASE / 'backfilled_panel_candidate_closing_rule_v4'
SOURCE = BASE / 'public_source_probes/sina_ipo_factor_prefix_20261009'
CODE = ROOT / 'gru_beta_neutral_20261008/ipo_prefix_v5'
ART = ROOT / 'gru_research/artifacts'


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def same(a, b):
    return (a == b) | (np.isnan(a) & np.isnan(b))


def normalize(values, members):
    result = np.full(values.shape, np.nan, np.float32)
    for k in range(values.shape[1]):
        x = values[:, k].astype(float)
        ref = x[members & np.isfinite(x)]
        if len(ref) < 2:
            continue
        lo, hi = np.percentile(ref, [1, 99])
        ref = np.clip(ref, lo, hi)
        center, scale = ref.mean(), ref.std(ddof=0)
        use = np.isfinite(x)
        result[use, k] = np.clip((np.clip(x[use], lo, hi)-center)/(scale if scale > 0 else 1), -5, 5)
    return result


def main():
    started = time.monotonic()
    prep = read(OUT/'version_preparation_receipt.json')
    source = read(SOURCE/'prefix_validation_receipt.json')
    meta = read(OUT/'dataset/meta.json')
    old_meta = read(OLD/'dataset/meta.json')
    previous_audit = read(OLD/'independent_completion_audit.json')
    assert previous_audit['passed'] and sha(OLD/'dataset/meta.json') == prep['previous_candidate_meta_sha256']
    assert not meta['production_ready'] and meta['research_candidate']
    assert source['passed'] and source['qualified_prefix_hours'] == 24
    assert sha(SOURCE/'prefix_validation_receipt.json') == prep['prefix_validation_receipt_sha256']
    assert sha(ROOT/'gru_beta_neutral_20261008/validate_sina_ipo_factor_prefix.py') == source['source_code_sha256']
    assert sha(SOURCE/'prefix_concordance_audit.json') == source['concordance_receipt_sha256']
    assert sha(SOURCE/'listing_sources/receipt.json') == source['listing_source_receipt_sha256']
    assert sha(SOURCE/'validated_observed_ipo_factor_events.parquet') == source['validated_event_parquet_sha256']
    for r in read(SOURCE/'listing_sources/receipt.json')['records']:
        assert sha(r['source_path']) == r['source_sha256']
        assert sha(r['extracted_text_path']) == r['extracted_text_sha256']
    hashes = []
    for directory in [ART/'dataset', BASE/'backfilled_panel_candidate/dataset',
                      BASE/'backfilled_panel_candidate_code_bridge_v3/dataset', OLD/'dataset', OUT/'dataset']:
        for filename, digest in read(directory/'meta.json')['files_sha256'].items():
            actual = sha(directory/filename)
            assert actual == digest, (str(directory), filename)
            hashes.append({'path': str(directory/filename), 'sha256': actual})
    assert len(prep['copied_files']) == 3525
    for record in prep['copied_files']:
        assert sha(record['source']) == record['sha256'] == sha(record['destination'])
    for record in prep['code_inputs']:
        assert sha(record['source']) == record['source_sha256']
    for name in ['aggregate_matrix', 'observed_arrays', 'donor_qualification']:
        a = ast.parse((CODE/'backfilled_hour_panel.py').read_text())
        b = ast.parse((ROOT/'gru_beta_neutral_20261008/historical_closing_rule_v4/backfilled_hour_panel.py').read_text())
        get = lambda tree: next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert ast.dump(get(a)) == ast.dump(get(b)), name
    hour_receipt = read(OUT/'hour_panel_receipt.json')
    for name, digest in hour_receipt['source_code_sha256'].items():
        assert sha(CODE/name) == digest
    print('Verified all source pins, original arrays, previous versions and 3525 unchanged stock files', flush=True)
    calendar = read(ART/'minute_calendar.json')
    dates, symbols = calendar['dates'], calendar['union_symbols']
    complete = np.load(OUT/'dataset/hour_complete.npy', mmap_mode='r')
    prior = np.load(OLD/'dataset/hour_complete.npy', mmap_mode='r')
    members = np.load(OUT/'dataset/members.npy', mmap_mode='r')
    np.testing.assert_array_equal(members, np.load(ART/'dataset/members.npy', mmap_mode='r'))
    assert not (prior & ~complete).any()
    added = complete & ~prior
    expected_mask = np.zeros(added.shape, bool)
    frames, changed_indices, positive_boundaries = {}, [], 0
    verified = 0
    events = pq.read_table(SOURCE/'validated_observed_ipo_factor_events.parquet').to_pandas()
    for record in source['records']:
        symbol = record['symbol']; u = symbols.index(symbol); changed_indices.append(u)
        ipo = record['observed_ipo_event_date']; wanted = record['factorless_trading_dates']
        primary_path = BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json')
        assert sha(primary_path) == record['canonical_source_sha256']
        primary = read(primary_path)
        daily = pd.DataFrame(primary['rows'], columns=primary['fields']).set_index('date')
        assert sha(SOURCE/(symbol[:6]+'.js')) == record['source_factor_sha256']
        raw_factor = (SOURCE/(symbol[:6]+'.js')).read_text()
        payload, _ = json.JSONDecoder().raw_decode(raw_factor[raw_factor.index('{'):])
        genuine = [r for r in payload['data'] if r['d'] != '1900-01-01']
        assert len(genuine) == 1 and genuine[0]['d'] == ipo and float(genuine[0]['f']) == 1
        actual_events = events[events.symbol.eq(symbol)].sort_values('effective_date')
        assert len(actual_events) == 2 and actual_events.effective_fore_factor.eq(1).all()
        assert actual_events.effective_date.iloc[0] == ipo
        for known in primary['factors']:
            assert float(known[2]) == 1 and actual_events.effective_date.eq(known[1]).any()
        path = BASE/'public_source_probes/huggingface/snapshot/data/stock_1m/SH'/(symbol[:6]+'.parquet')
        assert sha(path) == record['minute_source_sha256']
        quotes = pq.read_table(path, filters=[('timestamp', '>=', pd.Timestamp(min(wanted))),
                                            ('timestamp', '<', pd.Timestamp(max(wanted))+pd.Timedelta(days=1))]).to_pandas()
        assert quotes.symbol.astype(str).eq(symbol[:6]).all() and quotes.exchange.eq('SH').all()
        quotes['date'] = quotes.timestamp.dt.strftime('%Y-%m-%d')
        frame = pq.read_table(OUT/'symbol_hour_features'/(symbol+'.parquet')).to_pandas()
        old_frame = pq.read_table(OLD/'symbol_hour_features'/(symbol+'.parquet')).to_pandas()
        frames[u] = (frame, old_frame)
        assert not frame.loc[frame.date.lt(ipo), 'hour_complete'].any()
        np.testing.assert_array_equal(frame.loc[frame.date.ge(record['first_canonical_factor_date']), 'adjustment_factor'],
                                      old_frame.loc[old_frame.date.ge(record['first_canonical_factor_date']), 'adjustment_factor'])
        for day in wanted:
            assert day >= ipo
            t0 = 4*dates.index(day); expected_mask[t0:t0+4, u] = True
            q = quotes[quotes.date.eq(day)].sort_values('timestamp')
            clock = q.timestamp.dt.hour.to_numpy()*60+q.timestamp.dt.minute.to_numpy()
            grid = np.r_[np.arange(571,691), np.arange(781,901)]
            regular = q.loc[np.isin(clock, grid)]
            assert not q.timestamp.duplicated().any() and q.timestamp.dt.second.eq(0).all()
            assert not ((clock == 780) & q.volume.gt(0)).any()
            assert len(regular) == 240
            np.testing.assert_array_equal(regular.timestamp.dt.hour.to_numpy()*60+regular.timestamp.dt.minute.to_numpy(), grid)
            raw = regular[['open','high','low','close']].to_numpy(float).round(2)
            volume = regular.volume.to_numpy(float).copy()
            boundary = q.loc[(clock == 570) & q.volume.gt(0)]
            assert len(boundary) <= 1
            if len(boundary):
                b = boundary.iloc[0]; positive_boundaries += 1
                raw[0,0] = round(float(b.open),2)
                raw[0,1] = max(raw[0,1],round(float(b.high),2)); raw[0,2] = min(raw[0,2],round(float(b.low),2))
                volume[0] += float(b.volume)
            p = daily.loc[day]
            assert p.tradestatus == '1' and p.adjustflag == '3'
            assert abs(raw[0,0]-float(p.open)) <= 1e-8 and abs(raw[-1,3]-float(p.close)) <= 1e-8
            assert abs(volume.sum()-float(p.volume)) <= 100
            assert raw[:,1].max() <= float(p.high)+1e-8 and raw[:,2].min() >= float(p.low)-1e-8
            for slot in range(4):
                bar = raw[slot*60:(slot+1)*60]; vol = volume[slot*60:(slot+1)*60]
                r = frame.iloc[t0+slot]
                expected = np.array([bar[0,0],bar[:,1].max(),bar[:,2].min(),bar[-1,3]])
                np.testing.assert_array_equal(r[['raw_open','raw_high','raw_low','raw_close']].to_numpy(float), expected)
                np.testing.assert_array_equal(r[['open','high','low','close']].to_numpy(float), expected)
                assert r.volume == vol.sum() and r.adjustment_factor == 1 and r.hour_complete
                assert r.hour_source == 'corroborated_hf_observed_IPO_seed'
                if r.window_complete:
                    assert r.window_volume == vol[1:6].sum()
                    assert abs(r.window_raw_price-bar[1:6,0].mean()) <= 1e-12
                    assert abs(r.window_price-bar[1:6,0].mean()) <= 1e-12
                verified += 1
    np.testing.assert_array_equal(added, expected_mask)
    assert verified == 24 and not members[added].any()
    untouched = np.ones(len(symbols), bool); untouched[changed_indices] = False
    prices = np.load(OUT/'dataset/hour_ohlcv.npy', mmap_mode='r')
    old_prices = np.load(OLD/'dataset/hour_ohlcv.npy', mmap_mode='r')
    for t in range(0, len(complete), 128):
        np.testing.assert_array_equal(prices[t:t+128][prior[t:t+128]], old_prices[t:t+128][prior[t:t+128]])
    for name in ['labels','execution_prices','execution_raw_prices','execution_complete','execution_tradable','execution_all_flat']:
        a = np.load(OUT/'dataset'/(name+'.npy'), mmap_mode='r')
        b = np.load(OLD/'dataset'/(name+'.npy'), mmap_mode='r')
        for t in range(0, len(a), 128):
            np.testing.assert_array_equal(a[t:t+128, untouched], b[t:t+128, untouched])
        for u,(frame,old_frame) in frames.items():
            windows = frame.window_price.where(frame.window_complete).to_numpy(float)
            if name == 'labels':
                for delta in range(1,5):
                    expected = np.full(len(windows), np.nan, np.float32)
                    expected[:-(1+delta)] = np.log(windows[1+delta:]/windows[1:-delta]).astype(np.float32)
                    np.testing.assert_array_equal(a[:,u,delta-1], expected)
            elif name in ['execution_prices','execution_raw_prices']:
                source_windows = windows if name == 'execution_prices' else frame.window_raw_price.where(frame.window_complete).to_numpy(float)
                expected = np.r_[source_windows[1:], np.nan].astype(np.float32)
                np.testing.assert_array_equal(a[:,u], expected)
            changed = ~same(a[:,u], b[:,u]) if a.dtype != bool else a[:,u] != b[:,u]
            if changed.ndim == 2: changed = changed.any(axis=1)
            allowed = np.zeros(len(a), bool)
            for t0 in np.flatnonzero(added[:,u]): allowed[max(0,t0-5):t0+1] = True
            assert not (changed & ~allowed).any(), (name,symbols[u])
    features = np.load(OUT/'dataset/hour_features.npy', mmap_mode='r')
    old_features = np.load(OLD/'dataset/hour_features.npy', mmap_mode='r')
    rounding_count, rounding_max, direct_max = 0, 0., 0.
    for t in range(0,len(features),128):
        np.testing.assert_array_equal(features[t:t+128,untouched], old_features[t:t+128,untouched])
    for u,(frame,old_frame) in frames.items():
        affected = np.zeros(len(features),bool)
        for t0 in np.flatnonzero(added[:,u]): affected[t0:min(t0+81,len(features))] = True
        a,b = features[:,u], old_features[:,u]
        np.testing.assert_array_equal(np.isnan(a[~affected]),np.isnan(b[~affected]))
        exact = [k for k in range(24) if k not in [9,10,11]]
        np.testing.assert_array_equal(a[~affected][:,exact],b[~affected][:,exact])
        different = ~same(a,b) & ~affected[:,None]
        closes = frame.close.where(frame.hour_complete).to_numpy(float)
        old_closes = old_frame.close.where(old_frame.hour_complete).to_numpy(float)
        for t,k in np.argwhere(different):
            assert k in [9,10,11]
            n = {9:4,10:20,11:80}[int(k)]
            observed = closes[t+1-n:t+1]
            np.testing.assert_array_equal(observed,old_closes[t+1-n:t+1])
            assert len(observed) == n and np.isfinite(observed).all()
            direct = np.float32(observed[-1]/observed.mean()-1)
            error = float(abs(a[t,k]-b[t,k])); rounding_max = max(rounding_max,error)
            direct_error = max(float(abs(a[t,k]-direct)),float(abs(b[t,k]-direct)))
            direct_max = max(direct_max,direct_error)
            assert error <= 1e-12 and direct_error <= 1e-12
            rounding_count += 1
    windows = np.load(OUT/'dataset/window80_complete.npy',mmap_mode='r')
    old_windows = np.load(OLD/'dataset/window80_complete.npy',mmap_mode='r')
    assert not (old_windows & ~windows).any()
    expected_windows = np.zeros(complete.shape,bool)
    prefix = np.r_[np.zeros((1,len(symbols)),np.int32),np.cumsum(~complete,axis=0,dtype=np.int32)]
    expected_windows[79:] = prefix[80:] == prefix[:-80]
    np.testing.assert_array_equal(windows,expected_windows)
    slow_receipt = read(OUT/'slow_features/slow_panel_receipt.json')
    assert not slow_receipt['factor_selection_changed'] and not any(slow_receipt['PIT_date_violations'].values())
    assert sha(OUT/'slow_features/slow_raw_by_day.npy') == slow_receipt['raw_sha256']
    contract = slow_receipt['source_contract']
    for path,key in [(CODE/'backfilled_slow_panel.py','code_sha256'),(CODE/'historical_factor_compiler.py','compiler_sha256'),
                     (ROOT/'quantagent/src/quanta_agents/factor_lab_a/dsl.py','original_dsl_sha256'),(ART/'slow_factor_selection.json','selection_sha256')]:
        assert sha(path) == contract[key]
    old_contract = read(OLD/'slow_features/slow_panel_receipt.json')['source_contract']
    assert contract['source_membership_sha256'] == old_contract['source_membership_sha256']
    selection = read(ART/'slow_factor_selection.json')
    definitions = {r['id']:r['canonical'] for r in selection['selected']}
    for r in slow_receipt['frozen_factor_definitions']:
        assert definitions[r['id']] == r['canonical']
        assert sha(OUT/'slow_features/factors'/(r['id']+'.npy')) == r['sha256']
    for r in slow_receipt['primary_sources']:
        assert sha(BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(r['symbol']+'.json')) == r['primary_sha256']
    slow = np.load(OUT/'slow_features/slow_raw_by_day.npy',mmap_mode='r')
    old_slow = np.load(OLD/'slow_features/slow_raw_by_day.npy',mmap_mode='r')
    names = slow_receipt['names']; pit_columns = [names.index(n) for n in names if n.startswith(('f_','age_')) or n == 'd_log_total_cap']
    assert len(pit_columns) == 19
    slow_changes = {}; member_slow_changed_cells = 0; changed_source_dates = set()
    for d in range(0,len(slow),64):
        a,b = slow[d:d+64],old_slow[d:d+64]
        np.testing.assert_array_equal(a[:,untouched],b[:,untouched])
        np.testing.assert_array_equal(a[:,:,pit_columns],b[:,:,pit_columns])
        different = ~same(a,b)
        member_slow_changed_cells += int(different[members[4*d:4*(d+64):4]].sum())
        changed_source_dates.update(d+int(i) for i in np.flatnonzero(different.any(axis=(1,2))))
        for u in changed_indices:
            count = int(different[:,u].sum())
            slow_changes[symbols[u]] = slow_changes.get(symbols[u],0)+count
            for local_d in np.flatnonzero(different[:,u].any(axis=1)):
                assert dates[d+int(local_d)] > next(r['observed_ipo_event_date'] for r in source['records'] if r['symbol'] == symbols[u])
    assert sha(OUT/'slow_features/pit_provenance.parquet') == sha(OLD/'slow_features/pit_provenance.parquet')
    print('All 24 added hours, every affected label, unchanged prices and financial PIT fields verified',flush=True)
    X = np.load(OUT/'dataset/X.npy',mmap_mode='r')
    old_X = np.load(OLD/'dataset/X.npy',mmap_mode='r')
    for t in range(0,len(features),128):
        changed = ~same(features[t:t+128],old_features[t:t+128])
        changed_source_dates.update((t+int(i))//4 for i in np.flatnonzero(changed.any(axis=(1,2))))
    unchanged_days = np.ones(len(dates),bool); unchanged_days[list(changed_source_dates)] = False
    unchanged_hours = np.repeat(unchanged_days,4)
    for t in range(0,len(X),64):
        keep = unchanged_hours[t:t+64]
        np.testing.assert_array_equal(X[t:t+64][keep],old_X[t:t+64][keep])
    sample = set(range(0,len(dates),80))
    sample.update(changed_source_dates)
    for r in source['records']:
        for day in r['factorless_trading_dates']+[r['first_canonical_factor_date']]:
            d = dates.index(day);sample.update(range(max(0,d-1),min(len(dates),d+82)))
    sample.update([0,len(dates)-1])
    maximum = 0.
    for d in sorted(sample):
        expected_slow = normalize(slow[d],members[4*d])
        for slot in range(4):
            t = 4*d+slot
            expected = np.concatenate([normalize(features[t],members[t]),expected_slow],axis=1)
            actual = np.asarray(X[t]);np.testing.assert_array_equal(np.isnan(actual),np.isnan(expected))
            use = np.isfinite(expected)
            maximum = max(maximum,float(np.abs(actual[use]-expected[use]).max()) if use.any() else 0)
            np.testing.assert_allclose(actual,expected,atol=2e-6,rtol=2e-6,equal_nan=True)
    gap = read(OUT/'gap_audit/receipt.json');old_gap = read(OLD/'gap_audit/receipt.json')
    assert gap['minimum_intervals'] == old_gap['minimum_intervals']
    assert gap['full_union_august_september'] == old_gap['full_union_august_september']
    assert hour_receipt['coverage_by_year'] == old_meta['coverage_by_year']
    assert gap['coverage_by_year'] == old_gap['coverage_by_year']
    result = {'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_code_sha256':sha(Path(__file__)),
              'candidate_meta_sha256':sha(OUT/'dataset/meta.json'),'candidate_fingerprint':meta['data_fingerprint'],
              'array_sha_checks':hashes,'unchanged_stock_files_checked':3525,'original_and_checked_prior_candidate_arrays_preserved':True,
              'prior_array_versions_checked':['original_frozen','backfilled_panel_candidate','code_bridge_v3','closing_rule_v4'],
              'factor_bridge_v2_has_hour_arrays_but_no_completed_full_X_or_meta':True,
              'added_raw_minute_quote_hours_verified':verified,'stockdays_with_added_hours_verified':6,'stocks_with_added_hours':3,
              'positive_volume_auction_boundaries_verified':positive_boundaries,'added_hours_exactly_equal_pinned_IPO_prefix_scope':True,
              'unknown_before_IPO_kept_missing':True,'canonical_factor_values_preserved_exactly':True,
              'previous_complete_raw_and_adjusted_hour_prices_exactly_preserved':True,'unaffected_1175_stock_hour_arrays_exactly_preserved':True,
              'all_affected_labels_and_execution_prices_independently_recomputed':True,'lost_complete_hours':0,
              'rolling_ma_floating_point_differences_outside_new_history':rounding_count,'rolling_ma_max_absolute_difference_v4':rounding_max,
              'rolling_ma_direct_window_mean_max_absolute_error':direct_max,'rolling_ma_numerical_tolerance':1e-12,
              'frozen_64_factor_definitions_preserved':True,'all_19_financial_PIT_fields_and_provenance_exactly_preserved':True,
              'unaffected_1175_stock_slow_values_exactly_preserved':True,'new_slow_changed_cells_by_symbol':slow_changes,
              'historical_member_slow_changed_cells_from_new_observed_IPO_history':member_slow_changed_cells,
              'all_unaffected_session_normalized_X_exactly_equal_v4':True,
              'all_session_crosssections_with_any_changed_raw_input_independently_recomputed':True,
              'sessions_with_changed_raw_hour_or_slow_inputs':len(changed_source_dates),
              'normalization_sample_sessions':len(sample),'normalization_sample_full_crosssections':4*len(sample),
              'independent_normalization_max_abs_error':maximum,'added_complete80_windows':int((windows & ~old_windows).sum()),
              'lost_complete80_windows':0,'all_member_annual_coverage_unchanged':True,
              'minimum_interval_missing_primary_trading_hours':gap['minimum_intervals']['missing_primary_trading_hours'],
              'full_union_tail_missing_nonmember_primary_trading_hours':gap['full_union_august_september']['missing_nonmember_primary_trading_hours'],
              'pending_factor_method_conflict_symbols':source['pending_factor_method_conflict_symbols'],
              'synthetic_or_daily_substituted_minute_prices':False,'tick_extrema_or_live_source_admission_certified':False,
              'production_ready':False,'elapsed_seconds':round(time.monotonic()-started,2)}
    (OUT/'independent_completion_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != 'array_sha_checks'},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

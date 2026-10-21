"""Inspect the 72-hour overlap under fixed, explicitly diagnostic price rules.

Only target days are retained from <=65536-row batches. Accepted acquisition
digests are reused with identity/footer guards. A cent-rounded inverse of a
primary dated factor is a hypothesis, not an admitted raw market observation.
"""
from collections import Counter
from . import bounded_tail_pipeline as p
from .probe_kit_historical_minute_overlap_bounded import SOURCE, DEST as ACQUIRED, REMAINING

DEST = SOURCE/'historical_minute_overlap_diagnostic'
PRICE_FIELDS = ['open','high','low','close']


def main():
    lock = p.locked('diagnose_kit_historical_minute_overlap');DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    acquisition_path = ACQUIRED/'receipt.json';acquisition = p.json.loads(acquisition_path.read_text())
    assert acquisition['status'] == 'bounded_three_symbol_probe_complete_no_admission' and acquisition['successful_files'] == 3
    assert acquisition['source_code_sha256'] == p.sha(p.ROOT/'gru_beta_neutral_20261008/probe_kit_historical_minute_overlap_bounded.py')
    remaining = p.pd.read_csv(REMAINING,dtype={'symbol':str,'date':str});assert p.sha(REMAINING) == acquisition['remaining_scope_sha256']
    recheck_path = p.OUT/'historical_quote_candidate_recheck/receipt.json';recheck = p.json.loads(recheck_path.read_text())
    existing = {(d['symbol'],d['date']):d for f in recheck['native_files'] for d in f['days']}
    result = dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='diagnosing_three_symbol_minute_overlap',acquisition_receipt_sha256=p.sha(acquisition_path),
        original_scope_sha256=p.sha(REMAINING),cached_original_donor_recheck_sha256=p.sha(recheck_path),
        fixed_clock_rule='Published09:31..11:30 and13:01..15:00 end labels minus one minute; no auction row invented.',
        fixed_price_hypotheses=['direct_published_prices','inverse_dated_primary_back_factor','inverse_dated_primary_back_factor_float32'],
        factor_inverse_and_cent_rounding_are_diagnostics_only=True,actual_source_adjustment_and_rounding_implementation_certified=False,
        candidate_hours_admitted=0,raw_candidate_modified=False,zero_quantity_minute_quote_or_upstream_fill_policy_certified=False,records=[],counts={})
    counts = Counter();p.atomic_json(output,result);grid = list(range(570,690))+list(range(780,900))
    for record in acquisition['records']:
        symbol = record['symbol'];target = remaining.loc[remaining.symbol.eq(symbol)];dates = sorted(set(target.date));path = p.Path(record['path'])
        assert p.identity(path) == record['verified_identity'] and record['actual_sha256'] == record['published_sha256']
        pf = p.pq.ParquetFile(path);assert pf.metadata.num_rows == record['rows'] and pf.metadata.num_row_groups == record['row_groups']
        selected = [];scanned = 0
        for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
            frame = batch.to_pandas();scanned += len(frame);day = frame.datetime.dt.strftime('%Y-%m-%d');mask = day.isin(dates)
            if mask.any():
                part = frame.loc[mask].copy();part['date'] = day.loc[mask];selected.append(part)
        assert scanned == record['rows'] and p.identity(path) == record['verified_identity']
        frame = p.pd.concat(selected,ignore_index=True) if selected else p.pd.DataFrame();assert len(frame) <= len(dates)*241
        primary_path = p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary = p.json.loads(primary_path.read_text())
        primary_days = {r[0]:dict(zip(primary['fields'],r)) for r in primary['rows']};factors = [dict(zip(primary['factor_fields'],r)) for r in primary['factors']]
        for date in dates:
            slots = sorted(map(int,target.loc[target.date.eq(date),'slot']));rows = frame.loc[frame.date.eq(date)].sort_values('datetime') if not frame.empty else frame
            minutes = (rows.datetime.dt.hour*60+rows.datetime.dt.minute-1).tolist() if not rows.empty else []
            observation = dict(symbol=symbol,date=date,slots=slots,source_minutes=len(rows),fixed_end_minus1_grid_exact=minutes == grid,
                source_path=str(path),source_published_sha256=record['published_sha256'],source_verified_identity=record['verified_identity'],primary_path=str(primary_path),primary_sha256=p.sha(primary_path))
            if rows.empty or minutes != grid:
                observation['status'] = 'source_absent_or_grid_conflict';counts[observation['status']] += len(slots);result['records'].append(observation);continue
            prices = rows[PRICE_FIELDS].to_numpy(dtype='float64');qty = rows.volume.to_numpy(dtype='float64')
            assert p.np.isfinite(prices).all() and p.np.isfinite(qty).all() and (prices>0).all() and (qty>=0).all()
            assert (prices[:,1]>=p.np.maximum(prices[:,0],prices[:,3])).all() and (prices[:,2]<=p.np.minimum(prices[:,0],prices[:,3])).all()
            assert (qty == p.np.rint(qty)).all();day = primary_days[date]
            eligible = [f for f in factors if f['dividOperateDate']<=date];factor = float(max(eligible,key=lambda f:f['dividOperateDate'])['backAdjustFactor']) if eligible else 1.0
            assert p.np.isfinite(factor) and factor>0
            primary_price = p.np.array([float(day[f]) for f in PRICE_FIELDS]);native_day = p.np.array([prices[0,0],prices[:,1].max(),prices[:,2].min(),prices[-1,3]])
            observation.update(status='complete240_source_minutes_diagnostic_only',primary_trade_status=day['tradestatus'],source_volume_sum=int(qty.sum()),primary_volume=int(day['volume']),
                daily_volume_difference=int(qty.sum())-int(day['volume']),source_zero_quantity_minutes=int((qty==0).sum()),dated_primary_back_factor=factor,price_models=[])
            cached = existing.get((symbol,date));native_cached = None
            if cached:
                data = p.Path(cached['selected_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest() == cached['selected_sha256'];native_cached = p.json.loads(data)
                observation['cached_original_donor_selected_path'] = cached['selected_path'];observation['cached_original_donor_selected_sha256'] = cached['selected_sha256']
            for name,scale in [('direct_published_prices',1.0),('inverse_dated_primary_back_factor',factor),('inverse_dated_primary_back_factor_float32',float(p.np.float32(factor)))]:
                restored = prices/scale;rounded = p.np.rint(restored*100)/100
                rounded_day = p.np.array([rounded[0,0],rounded[:,1].max(),rounded[:,2].min(),rounded[-1,3]])
                unrounded_day = native_day/scale;distance = p.np.abs(restored-rounded)
                model = dict(name=name,scale=scale,unrounded_daily_prices=unrounded_day.tolist(),unrounded_daily_abs_differences=p.np.abs(unrounded_day-primary_price).tolist(),
                    cent_rounded_daily_prices=rounded_day.tolist(),cent_rounded_daily_all4_exact=bool(p.np.array_equal(rounded_day,primary_price)),maximum_inverse_price_distance_from_cent=float(distance.max()),
                    source_inverse_to_cent_rounding_not_admitted=True,hours=[])
                for slot in slots:
                    block = rounded[slot*60:(slot+1)*60];volume = qty[slot*60:(slot+1)*60]
                    model['hours'].append(dict(slot=slot,restored_rounded_prices_diagnostic=[float(block[0,0]),float(block[:,1].max()),float(block[:,2].min()),float(block[-1,3])],
                        reported_volume_shares=int(volume.sum()),zero_quantity_minutes=int((volume==0).sum()),minute_raw_prices_not_certified=True))
                observation['price_models'].append(model)
            selected_path = DEST/(symbol+'_'+date+'_published_minutes.parquet');p.pq.write_table(p.pa.Table.from_pandas(rows.drop(columns=['date']),preserve_index=False),selected_path,compression='zstd')
            observation.update(selected_published_minutes_path=str(selected_path),selected_published_minutes_sha256=p.sha(selected_path),selected_verified_identity=p.identity(selected_path))
            counts['complete_grid_diagnostic_hours'] += len(slots)
            if observation['daily_volume_difference'] == 0:counts['direct_reported_daily_volume_exact_hours'] += len(slots)
            if observation['price_models'][1]['cent_rounded_daily_all4_exact']:counts['dated_back_factor_cent_rounded_daily_prices_exact_hours'] += len(slots)
            result['records'].append(observation)
        result.update(updated_utc=p.utc(),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    result.update(status='complete_72_hour_diagnostic_no_admission',finished_utc=p.utc());p.atomic_json(output,result);lock.close()


if __name__ == '__main__':main()

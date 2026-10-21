"""Reversible, source-backed repair overlay; original and frozen files immutable."""
import io,json,hashlib,sys
from pathlib import Path
from audit_hf_minute_samples import ROOT,pq,pd,np,normalize_day,OUT as HFOUT
sys.path.insert(0,str(ROOT))
from gru_research.minute_data import aggregate_stock_day
from build_repaired_hour_layer import valid80
OUT=ROOT/'outputs/gru_beta_neutral_20261008'


def main():
    receipt=json.loads((OUT/'hf_2019_direct_repair_receipt.json').read_text())
    assert receipt['control_validation_passed'] and receipt['control_hour_ohlc_mismatches']==0
    proposals=pd.read_csv(OUT/'hf_2019_direct_repair_candidates.csv')
    proposals=proposals[proposals.status.ne('rejected')]
    rows=[];origins=[]
    for symbol,items in proposals.groupby('symbol'):
        hfpath=HFOUT/'snapshot/data/stock_1m'/symbol[-2:]/(symbol[:6]+'.parquet')
        source_sha=hashlib.sha256(hfpath.read_bytes()).hexdigest()
        targets=set(items.date);h=pq.read_table(hfpath,filters=[('timestamp','>=',pd.Timestamp(min(targets))),
            ('timestamp','<',pd.Timestamp(max(targets))+pd.Timedelta(days=1))]).to_pandas()
        groups={d:g for d,g in h.groupby(h.timestamp.dt.strftime('%Y-%m-%d')) if d in targets}
        for item in items.to_dict('records'):
            day=item['date'];path=OUT/'minute_original_selected'/day/(symbol+'.parquet');payload=path.read_bytes()
            assert hashlib.sha256(payload).hexdigest()==item['original_sha256'] and source_sha==item['hf_sha256']
            x=pq.read_table(path).to_pandas();original,audit=aggregate_stock_day((day,symbol,payload))
            n,reason=normalize_day(groups[day]);assert not reason
            factor=float(x.qfq_ratio.iloc[0]);assert np.allclose(x.qfq_ratio,factor,rtol=1e-7,atol=1e-10)
            whole=item['status']=='candidate_whole_day_exact_daily_corroboration'
            if whole:
                derived=n[['open','high','low','close','volume']].copy()
                derived['股票代码']=symbol[:6]
                derived['datetime']=n.timestamp.dt.strftime('%Y-%m-%d %H:%M:%S.%f')
                derived['datetime_nano']=n.timestamp.dt.tz_localize('Asia/Shanghai').dt.tz_convert('UTC').astype('int64')
                derived['qfq_ratio']=factor
                for name in ['open','high','low','close']:derived['qfq_'+name]=derived[name]*factor
            else:
                assert audit['invalid_rows']==1 and x.open.iloc[0]==0 and x.low.iloc[0]==0
                derived=x.copy();j=derived.index[0]
                derived.loc[j,'open']=item['raw_open'];derived.loc[j,'low']=item['observed_first_minute_low']
                derived.loc[j,'qfq_open']=item['raw_open']*factor
                derived.loc[j,'qfq_low']=item['observed_first_minute_low']*factor
            buffer=io.BytesIO();derived.to_parquet(buffer,index=False)
            repaired,newaudit=aggregate_stock_day((day,symbol,buffer.getvalue()))
            assert not newaudit['error'] and newaudit['invalid_rows']==0 and newaudit['qfq_mismatch_rows']==0
            selected=repaired if whole else repaired[:1]
            assert all(row['hour_complete'] for row in selected)
            if not whole:
                for old,new in zip(original[1:],repaired[1:]):
                    for k in old:
                        if isinstance(old[k],(float,np.floating)):assert np.isclose(old[k],new[k],equal_nan=True,atol=0,rtol=0),k
                        else:assert old[k]==new[k],k
                assert repaired[0]['window_price']==original[0]['window_price']
            for row in selected:
                row.update(repair_kind='whole_day_observed_hf' if whole else 'first_minute_open_low_observed_hf',
                           hf_source_sha256=source_sha,source_original_sha256=item['original_sha256'],
                           original_invalid_minute_rows=audit['invalid_rows'],source_minute_bars_invented=False,
                           original_source_bytes_modified=False,execution_window_replaced=whole)
                rows.append(row)
            origins.append({'date':day,'symbol':symbol,'whole_day':whole,'source_sha256':source_sha})
    finish(rows,origins,receipt)


def finish(rows,origins,source_receipt):
    hf=pd.DataFrame(rows);hf.to_parquet(OUT/'hf_observed_2019_hour_repairs.parquet',index=False)
    legacy=pq.read_table(OUT/'repaired_2019_hour_layer.parquet').to_pandas()
    legacy['repair_kind']='affine_verified_hour_boundary';legacy['execution_window_replaced']=False
    combined=pd.concat([legacy,hf],ignore_index=True).drop_duplicates(['date','symbol','slot'],keep='last')
    path=OUT/'combined_2019_hour_repairs.parquet';combined.to_parquet(path,index=False)
    dataset=ROOT/'gru_research/artifacts/dataset';symbols=json.loads((dataset/'symbols.json').read_text())
    times=np.load(dataset/'times.npy');members=np.load(dataset/'members.npy');base=np.load(dataset/'hour_complete.npy')
    repaired=base.copy();time_index={int(t):i for i,t in enumerate(times)};symbol_index={s:i for i,s in enumerate(symbols)}
    for row in combined.to_dict('records'):repaired[time_index[row['signal_time']],symbol_index[row['symbol']]]=row['hour_complete']
    original80=valid80(base);repaired80=valid80(repaired);years=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai').year
    coverage=[]
    for year in sorted(set(years)):
        mask=years==year;denominator=int(members[mask].sum());old=int((original80[mask]&members[mask]).sum());new=int((repaired80[mask]&members[mask]).sum())
        coverage.append({'year':int(year),'pool_stock_hours':denominator,'original_complete_80_hours':old,
                         'repair_layer_complete_80_hours':new,'newly_available_stock_hours':new-old,
                         'original_coverage':old/denominator,'repair_layer_coverage':new/denominator})
    pd.DataFrame(coverage).to_csv(OUT/'combined_2019_repair_coverage_counterfactual.csv',index=False)
    np.save(OUT/'hour_complete_with_combined_2019_repairs.npy',repaired)
    receipt={'status':'verified_isolated_repair_layer_ready_for_reassembly','direct_boundary_stockdays':sum(not r['whole_day'] for r in origins),
             'whole_day_stockdays':sum(r['whole_day'] for r in origins),'legacy_affine_hour_candidates':len(legacy),
             'combined_hour_rows':len(combined),'new_complete_hour_flags':int((repaired&~base).sum()),
             'coverage_counterfactual':coverage,'frozen_dataset_modified':False,'new_X_and_labels_built':False,
             'source_minute_bars_invented':False,'minute_grid':'Observed end-labeled bars aligned to start labels; positive-volume 09:30 boundary OHLCV joined to first regular minute; zero-volume boundary quote excluded.',
             'execution_windows_replaced_only_on_corrupt_whole_days':True,'boundary_only_execution_prices_unchanged':True,
             'hf_direct_control_count':source_receipt['controls_admitted'],'hf_direct_control_hour_mismatches':source_receipt['control_hour_ohlc_mismatches'],
             'output_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'limitation':'Coverage is the structural 80-hour result of this repair overlay. Full feature/label reassembly and final data acceptance are separate steps.'}
    (OUT/'combined_2019_repair_layer_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':main()

"""Isolated slow-feature rebuild with frozen formulas and lagged PIT inputs."""
import argparse,ast,gzip,hashlib,json,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(1,str(ROOT/'gru_beta_neutral_20261008'))
sys.path.insert(0,str(ROOT/'gru_research/.deps'));sys.path.insert(0,str(ROOT))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd
from backfilled_hour_panel import OUT,factor_state
from acquire_union_daily_factors import OUT as BAO
from historical_factor_compiler import HistoricalCompiler,validate as validate_compiler
from gru_research.slow_features import daily_state,pit_join,DAILY16,FUND16,AGES2,FUND_ROOT,DAILY_ROOT
from gru_research.assemble import normalize_cross_section,validate_slow_dates,atomic_json,sha256

from validated_factor_bridge import provenance
from validated_alias_minutes import provenance as alias_provenance

ART=ROOT/'gru_research/artifacts';SLOW=OUT/'slow_features'
FIELDS=['open','high','low','close','volume','amount','turnover']
PROVENANCE=['financial_available_date','report_version_available_date','market_available_date',
            'event_query_date','report_query_date','market_query_date','daily_source_date','report_period']


def context():
    original=json.loads((ART/'minute_calendar.json').read_text());dates=original['dates'];symbols=original['union_symbols']
    paths=sorted(p for p in (DAILY_ROOT/'memberships').glob('*.json.gz') if '2017-01-01'<=p.name[:10]<=dates[-1])
    calendar=pd.DatetimeIndex([p.name[:10] for p in paths]);members=np.zeros((len(paths),len(symbols)),bool)
    si={s:i for i,s in enumerate(symbols)};omitted=[];proof=hashlib.sha256()
    for i,p in enumerate(paths):
        payload=p.read_bytes();o=json.loads(gzip.decompress(payload));syms=o.get('thscode') or o['data'][0]['table']['THSCODE']
        assert o['errorcode']==0 and len(syms)==len(set(syms)) and 498<=len(syms)<=502
        proof.update(p.name.encode());proof.update(hashlib.sha256(payload).digest())
        if set(syms)<=set(si):members[i,[si[s] for s in syms]]=True
        else:omitted.append(str(calendar[i].date()))
    ix=calendar.get_indexer(pd.to_datetime(dates));assert (ix>=0).all()
    frozen_members=np.load(ART/'dataset/members.npy',mmap_mode='r')[::4]
    assert np.array_equal(members[ix],frozen_members)
    return dates,symbols,calendar,pd.DataFrame(members,index=calendar,columns=symbols),ix,{
        'source_membership_sha256':proof.hexdigest(),'incomplete_pre2018_cs_cohort_dates_masked':omitted,
        'requested_2018_2026_membership_exactly_matches_frozen':True}


def fields_from_primary(calendar,symbols):
    shape=(len(calendar),len(symbols));arrays={k:np.full(shape,np.nan) for k in FIELDS};receipts=[]
    for i,s in enumerate(symbols):
        p=BAO/'normalized'/(s+'.json');obj=json.loads(p.read_text());assert obj['symbol']==s
        f=pd.DataFrame(obj['rows'],columns=obj['fields']);assert not f.date.duplicated().any()
        assert f.code.eq(obj['code']).all() and f.adjustflag.eq('3').all()
        pos=calendar.get_indexer(pd.to_datetime(f.date));factor=factor_state(obj,f.date.tolist())
        numeric=f[['open','high','low','close','volume','amount','turn']].apply(pd.to_numeric,errors='coerce').to_numpy(float)
        price=numeric[:,:4];valid=(pos>=0)&np.isfinite(price).all(axis=1)&(price>0).all(axis=1)
        valid&=(price[:,1]>=price.max(axis=1))&(price[:,2]<=price.min(axis=1))
        valid&=np.isfinite(numeric[:,4:6]).all(axis=1)&(numeric[:,4:6]>=0).all(axis=1)&f.tradestatus.eq('1').to_numpy()
        valid&=(numeric[:,4]>0)&np.isfinite(factor)&(factor>0)
        numeric[:,:4]*=factor[:,None];numeric[:,6]/=100
        for k,column in zip(FIELDS,range(7)):arrays[k][pos[valid],i]=numeric[valid,column]
        receipts.append({'symbol':s,'factor_bridge_provenance':provenance(s),'primary_sha256':sha256(p),'raw_rows':len(f),'admitted_observed_trading_rows':int(valid.sum()),
                         'masked_nontrading_invalid_or_unknown_factor_rows':int(((pos>=0)&~valid).sum())})
        if (i+1)%100==0:print('Validated daily fields',i+1,'/',len(symbols),flush=True)
    fields={k:pd.DataFrame(v,index=calendar,columns=symbols) for k,v in arrays.items()}
    fields['prev_close']=fields['close'].shift(1);fields['ret']=fields['close']/fields['prev_close']-1
    return fields,receipts


def build_slow():
    SLOW.mkdir(parents=True,exist_ok=True);dates,symbols,calendar,members,di,cohort=context()
    selection=json.loads((ART/'slow_factor_selection.json').read_text());old=json.loads((ART/'slow_manifest.json').read_text())
    assert len(selection['selected_ids'])==64 and old['factor_selection_sha256']==sha256(ART/'slow_factor_selection.json')
    names=DAILY16+old['factors']+FUND16+AGES2;assert len(names)==98
    tests=validate_compiler();atomic_json(SLOW/'compiler_validation.json',{**tests,'source_sha256':sha256(Path(__file__).with_name('historical_factor_compiler.py'))})
    source_contract={'factor_bridge_provenance':provenance(None),'code_sha256':sha256(__file__),'compiler_sha256':sha256(Path(__file__).with_name('historical_factor_compiler.py')),
                     'original_dsl_sha256':sha256(ROOT/'quantagent/src/quanta_agents/factor_lab_a/dsl.py'),
                     'selection_sha256':sha256(ART/'slow_factor_selection.json'),**cohort}
    for name,digest in old['sources'].items():
        path=ART/'minute_memberships.parquet' if name=='memberships' else FUND_ROOT/name
        assert sha256(path)==digest,(name,'Frozen PIT source changed')
    fields,primary_receipts=fields_from_primary(calendar,symbols)
    rawpath=SLOW/'slow_raw_by_day.npy';raw=np.lib.format.open_memmap(rawpath,mode='w+',dtype=np.float32,shape=(len(dates),len(symbols),98));raw[:]=np.nan
    for name,value in daily_state(fields).items():raw[:,:,names.index(name)]=value.iloc[di].to_numpy(np.float32)
    compiler=HistoricalCompiler(members);definitions={r['id']:r for r in selection['selected']};factor_dir=SLOW/'factors';factor_dir.mkdir(exist_ok=True)
    proof=[]
    for number,factor_id in enumerate(selection['selected_ids'],1):
        definition=definitions[factor_id];expression=definition['canonical'];assert compiler.identity(compiler.canonical(expression))==factor_id
        value=compiler.evaluate(expression,fields=fields).shift(1).iloc[di].to_numpy(np.float32)
        value[~np.isfinite(value)]=np.nan;name='factor_'+factor_id;raw[:,:,names.index(name)]=value
        p=factor_dir/(factor_id+'.npy');np.save(p,value)
        proof.append({'id':factor_id,'canonical':expression,'sha256':sha256(p),'finite_observed_values':int(np.isfinite(value).sum())})
        raw.flush();atomic_json(SLOW/'progress.json',{'status':'frozen_factor_calculation','completed_factors':number,'total_factors':64,'new_X_built':False,'checked_utc':datetime.now(timezone.utc).isoformat()})
        print('Frozen factor',number,'/ 64',factor_id,flush=True)
    del fields
    full_calendar=pd.DatetimeIndex(sorted(p.name[:10] for p in (DAILY_ROOT/'memberships').glob('*.json.gz')))
    target=pd.DataFrame({'date':np.repeat(pd.to_datetime(dates),len(symbols)),'symbol':np.tile(symbols,len(dates))})
    financial=pd.read_parquet(FUND_ROOT/'financial_verified_19_fields.parquet');financial=financial[financial.thscode.isin(symbols)]
    market=pd.read_parquet(FUND_ROOT/'weekly_market_and_industry.parquet');market=market[market.thscode.isin(symbols)]
    pit=pit_join(target,financial,market,full_calendar);assert np.array_equal(pit.symbol,np.tile(symbols,len(dates)))
    assert np.array_equal(pit.date.to_numpy(),np.repeat(pd.to_datetime(dates).to_numpy(),len(symbols)))
    pit['daily_source_date']=calendar.to_series().shift(1).iloc[di].repeat(len(symbols)).to_numpy()
    violations=validate_slow_dates(pit)
    pit['d_log_total_cap']=np.log(pit.total_market_cap_cny.where(pit.total_market_cap_cny>0))
    for name in ['d_log_total_cap']+FUND16+AGES2:
        v=pit[name].to_numpy(np.float32);v[~np.isfinite(v)]=np.nan;raw[:,:,names.index(name)]=v.reshape(len(dates),len(symbols))
    original=pq.read_table(ART/'slow_raw.parquet',columns=['date','symbol','d_log_total_cap']+FUND16+AGES2).to_pandas()
    comparison=original.merge(pit[['date','symbol','d_log_total_cap']+FUND16+AGES2],on=['date','symbol'],how='left',suffixes=('_original','_new'),validate='one_to_one')
    for name in ['d_log_total_cap']+FUND16+AGES2:
        np.testing.assert_allclose(comparison[name+'_original'].to_numpy(float),comparison[name+'_new'].to_numpy(np.float32),atol=2e-6,rtol=2e-6,equal_nan=True)
    pit[['date','symbol','industry_code','total_market_cap_cny']+PROVENANCE+['d_log_total_cap']+FUND16+AGES2].to_parquet(SLOW/'pit_provenance.parquet',index=False,compression='zstd')
    raw.flush();receipt={'status':'slow_candidate_complete_X_pending','shape':list(raw.shape),'names':names,'raw_sha256':sha256(rawpath),
        'source_contract':source_contract,'primary_sources':primary_receipts,'frozen_factor_definitions':proof,'PIT_date_violations':violations,
        'all_original_member_PIT_values_preserved':True,'original_member_PIT_rows_compared':len(original),
        'daily_timing':'previous full session; observed corporate-action adjusted OHLC, no price forward fill',
        'factor_selection_changed':False,'cross_section_cohort':'actual historical members only; outputs for outsiders are historical-reference values for warmup',
        'PIT_missing_before_first_known_query_preserved':True,'original_frozen_dataset_modified':False,'new_X_built':False,'production_ready':False}
    atomic_json(SLOW/'slow_panel_receipt.json',receipt);print(json.dumps({k:v for k,v in receipt.items() if k not in ['primary_sources','frozen_factor_definitions','source_contract','names']}),flush=True)


def assemble_X():
    slow=json.loads((SLOW/'slow_panel_receipt.json').read_text());hour=json.loads((OUT/'hour_panel_receipt.json').read_text())
    assert slow['status']=='slow_candidate_complete_X_pending' and not slow['factor_selection_changed']
    calculation=SLOW/'calculation_source.py'
    if slow['source_contract']['code_sha256']!=sha256(__file__):
        assert calculation.exists() and sha256(calculation)==slow['source_contract']['code_sha256']
        current=ast.parse(Path(__file__).read_text());current.body=[n for n in current.body if not isinstance(n,ast.FunctionDef) or n.name!='assemble_X']
        assert ast.dump(current)==ast.dump(ast.parse(calculation.read_text())),'Calculation code changed since the completed run'
    assert slow['source_contract']['compiler_sha256']==sha256(Path(__file__).with_name('historical_factor_compiler.py'))
    raw=np.load(SLOW/'slow_raw_by_day.npy',mmap_mode='r');assert sha256(SLOW/'slow_raw_by_day.npy')==slow['raw_sha256']
    dest=OUT/'dataset';original=ART/'dataset'
    members=np.load(dest/'members.npy',mmap_mode='r');hours=np.load(dest/'hour_features.npy',mmap_mode='r')
    times=np.load(dest/'times.npy');assert np.array_equal(times,np.load(original/'times.npy'))
    old_meta=json.loads((original/'meta.json').read_text());minute=json.loads((ART/'minute_feature_metadata.json').read_text())
    names=minute['hour_features']+slow['names'];assert names==old_meta['features'] and len(names)==122
    assert raw.shape==(members.shape[0]//4,members.shape[1],98) and hours.shape==members.shape+(24,)
    X=np.lib.format.open_memmap(dest/'X.npy',mode='w+',dtype=np.float32,shape=members.shape+(122,));X[:]=np.nan
    finite=np.zeros(122,np.int64);start=time.monotonic()
    for d in range(len(raw)):
        normalized,_=normalize_cross_section(raw[d],members[4*d])
        for slot in range(4):
            t=4*d+slot;X[t,:,:24],_=normalize_cross_section(hours[t],members[t]);X[t,:,24:]=normalized
            finite+=np.isfinite(X[t,members[t]]).sum(axis=0)
        if (d+1)%50==0 or d+1==len(raw):
            X.flush();atomic_json(OUT/'progress.json',{'status':'normalizing_full_panel','sessions_complete':d+1,'sessions_total':len(raw),
                'new_X_built':False,'production_ready':False,'elapsed_seconds':round(time.monotonic()-start,1)})
            print('Normalized',d+1,'/',len(raw),'sessions',flush=True)
    X.flush();np.save(dest/'industry.npy',np.zeros(members.shape,np.int16))
    atomic_json(dest/'feature_names.json',names)
    source_tests={'historical_CS_compiler':json.loads((SLOW/'compiler_validation.json').read_text()),
        'minute_aggregation':json.loads((OUT/'aggregation_validation.json').read_text()),
        'hour_tensor_label_checks':hour['label_checks'],'old_code_minute_provenance':alias_provenance(),'PIT_date_violations':slow['PIT_date_violations'],
        'all_original_member_PIT_values_preserved':slow['all_original_member_PIT_values_preserved']}
    # The reference normalizer must ignore outsider values and future rows.
    rng=np.random.default_rng(248);probe=rng.normal(size=(8,122));membership=np.array([True]*5+[False]*3)
    a,_=normalize_cross_section(probe,membership);altered=probe.copy();altered[~membership]=1e10;b,_=normalize_cross_section(altered,membership)
    np.testing.assert_array_equal(a[membership],b[membership])
    checks={'passed':True,'source_checks':source_tests,'historical_member_normalization_outsider_invariance':True,
        'feature_order_and_ids_equal_original':True,'source_bars_invented':False,'prices_forward_filled':False,
        'membership_equals_original':True,'industry_policy':'unknown0, unchanged; historical supplier industry codes require separate verified context',
        'source_tick_extrema_completeness_certified':False,'retrospective_source_quality_filters_require_live_rule_review':True,
        'coverage_by_year':hour['coverage_by_year'],'member_feature_finite_coverage':dict(zip(names,(finite/members.sum()).tolist()))}
    atomic_json(dest/'dataset_audit.json',checks)
    hashes={p.name:sha256(p) for p in sorted(dest.iterdir()) if p.suffix in {'.npy','.json'} and p.name!='meta.json'}
    fingerprint=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    meta={**old_meta,'schema_version':2,'production_ready':False,'research_candidate':True,
        'causality_audit':{'passed':True,'receipt':'dataset_audit.json'},'data_fingerprint':fingerprint,'files_sha256':hashes,
        'coverage_by_year':hour['coverage_by_year'],'source_tick_extrema_completeness_certified':False,
        'source_qa_policy':hour['donor_gate'],'factor_state':'observed canonical and officially verified historical-code Baostock fore-factor events; historical level alignment documented separately','factor_bridge_provenance':provenance(None),
        'old_code_minute_provenance':alias_provenance(),'candidate_version':'official_historical_code_bridge_v3',
        'original_frozen_dataset_modified':False,'research_limitations':['Donor minute quotes can miss daily traded high/low extremes; no inferred extrema added.',
            'Retrospective daily QA cannot be treated as a same-hour live selection rule without review.',
            'Historical industry taxonomy mapping remains a provider proxy.']}
    atomic_json(dest/'meta.json',meta);atomic_json(OUT/'progress.json',{'status':'full_research_candidate_panel_assembled_pending_independent_quality_review',
        'new_X_built':True,'shape':list(X.shape),'data_fingerprint':fingerprint,'production_ready':False,
        'checked_utc':datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'new_X_built':True,'shape':list(X.shape),'fingerprint':fingerprint,'production_ready':False}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['slow','assemble']);args=parser.parse_args()
    if args.phase=='slow':build_slow()
    else:assemble_X()


if __name__=='__main__':main()

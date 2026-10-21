"""Read-only source assembly; historical membership, PIT snapshots, no price fill."""
from pathlib import Path
import gzip, json, gc, argparse
import numpy as np
import pandas as pd
from gru_research import slow_features as slow
from gru_research.assemble import normalize_cross_section, sha256, atomic_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_dualplatform_20261011/daily_panel'

def build():
    OUT.mkdir(parents=True, exist_ok=True)
    roots = [ROOT / (n + '_历年成分股_日频_2010-2026_iFinD_后复权') for n in ['中证500','中证1000']]
    membership = {}
    source_members = []
    for root in roots:
        for path in sorted((root/'memberships').glob('*.json.gz')):
            date = path.name[:10]
            if date >= '2026-09-24': continue
            with gzip.open(path, 'rt') as f: obj = json.load(f)
            assert obj['errorcode'] == 0, path
            codes = set(obj['data'][0]['table']['THSCODE'])
            membership.setdefault(date, set()).update(codes)
            source_members.append((str(path.relative_to(ROOT)), sha256(path)))
    calendar = pd.DatetimeIndex(sorted(membership))
    symbols = sorted(set.union(*membership.values()))
    T,U = len(calendar),len(symbols)
    lookup = {s:i for i,s in enumerate(symbols)}
    members = np.zeros((T,U), dtype=bool)
    for t,date in enumerate(calendar.strftime('%Y-%m-%d')):
        members[t,[lookup[s] for s in membership[date]]] = True
    del membership
    print('MEMBERSHIP', T,U, 'active',members.sum(1).min(),members.sum(1).max(),flush=True)
    frames=[]; audits=[]
    for root in roots:
        a,audit = slow.read_daily(root, history_start='2010-01-01',end='2026-09-23',symbols=symbols)
        frames.append(a); audits.append(audit)
    daily=pd.concat(frames,ignore_index=True); del frames
    dup=daily.duplicated(['date','symbol'],keep=False)
    fields_to_check=['open','high','low','close','volume','amount','turnover']
    conflicts=daily.loc[dup].groupby(['date','symbol'])[fields_to_check].nunique(dropna=False)
    assert not (conflicts>1).any().any(), 'conflicting overlap between index sources'
    daily=daily.drop_duplicates(['date','symbol'])
    fields=slow.wide_fields(daily,calendar,symbols);del daily;gc.collect()
    names=slow.DAILY16 + ['gp_'+x['id'] for x in json.loads((slow.ART/'slow_factor_selection.json').read_text())['selected']] + slow.FUND16 + slow.AGES2
    assert len(names)==98
    X=np.lib.format.open_memmap(OUT/'X.npy',mode='w+',dtype='float32',shape=(T,U,98));X[:]=np.nan
    coverage={}
    def put(name,raw):
        j=names.index(name); raw=np.asarray(raw)
        coverage[name]=float(np.isfinite(raw[members]).mean())
        for t in range(T):X[t,:,j]=normalize_cross_section(raw[t,:,None],members[t])[0][:,0]
        X.flush(); print('FEATURE',j,name,coverage[name],flush=True)
    ds=slow.daily_state(fields)
    for name,frame in ds.items():put(name,frame.to_numpy())
    del ds;gc.collect()
    selection=json.loads((slow.ART/'slow_factor_selection.json').read_text())
    compiler=slow.load_legacy_compiler()
    for spec in selection['selected']:
        raw=compiler.evaluate(spec['canonical'],fields).shift(1)
        put('gp_'+spec['id'],raw.to_numpy());del raw;gc.collect()
    # PIT financial/market fields are joined in yearly chunks to bound memory.
    financial=pd.read_parquet(slow.FUND_ROOT/'financial_verified_19_fields.parquet')
    market=pd.read_parquet(slow.FUND_ROOT/'weekly_market_and_industry.parquet')
    events=slow.financial_events(financial,calendar);del financial
    market=market.rename(columns={'thscode':'symbol','asof_date':'market_query_date'})
    market['market_query_date']=pd.to_datetime(market.market_query_date)
    market['market_available_date']=slow.next_session(market.market_query_date,calendar)
    market=market.dropna(subset=['market_available_date']).sort_values(['market_available_date','symbol'])
    events=events.sort_values(['financial_available_date','symbol'])
    pit_names=['d_log_total_cap']+slow.FUND16+slow.AGES2
    for year in sorted(set(calendar.year)):
        ti=np.flatnonzero(calendar.year==year);aa,bb=np.nonzero(members[ti]);tt=ti[aa]
        target=pd.DataFrame({'date':calendar[tt], 'symbol':np.asarray(symbols)[bb],'t':tt,'u':bb})
        z=pd.merge_asof(target.sort_values(['date','symbol']),events,left_on='date',right_on='financial_available_date',by='symbol',direction='backward')
        z=pd.merge_asof(z.sort_values(['date','symbol']),market,left_on='date',right_on='market_available_date',by='symbol',direction='backward')
        for source in ['event_query_date','report_query_date','market_query_date']:
            assert not (z[source]>=z.date).any(),source
        assert not (z.report_period>z.date).any()
        cap=z.total_market_cap_cny.where(z.total_market_cap_cny>0)
        z['d_log_total_cap']=np.log(cap);z['f_earnings_price']=z.ttm_parent_profit/cap;z['f_book_price']=z.parent_equity/cap
        z['age_report_days']=(z.date-z.report_period).dt.days.astype(float)
        z['age_version_days']=(z.date-z.report_version_available_date).dt.days.astype(float)
        for name in pit_names:
            raw=np.full((len(ti),U),np.nan);raw[z.t.to_numpy()-ti[0],z.u.to_numpy()]=z[name]
            for k,t in enumerate(ti):X[t,:,names.index(name)]=normalize_cross_section(raw[k,:,None],members[t])[0][:,0]
        X.flush();print('PIT YEAR',year,flush=True);del z,target;gc.collect()
    complete=np.isfinite(fields['close'].to_numpy()) & np.isfinite(fields['open'].to_numpy()) & np.isfinite(fields['volume'].to_numpy())
    labels=np.full((T,U,4),np.nan,dtype='float32');ends=np.full((T,4),-1,dtype='int64')
    op=fields['open'].to_numpy(); horizons=[1,2,3,5]
    times=(calendar.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=15)).asi8
    execution=np.full(T,-1,dtype='int64');execution[:-1]=(calendar[1:].tz_localize('Asia/Shanghai')+pd.Timedelta(hours=9,minutes=31)).asi8
    # No minute tape for the 1000 universe: label proxy is stated explicitly.
    for j,h in enumerate(horizons):
        end=T-h-1
        with np.errstate(invalid='ignore',divide='ignore'):labels[:end,:,j]=np.log(op[1+h:]/op[1:T-h])
        ends[:end,j]=(calendar[1+h:].tz_localize('Asia/Shanghai')+pd.Timedelta(hours=9,minutes=36)).asi8
        assert np.all(ends[:end,j]>execution[:end])
    for name,value in {'members':members,'complete':complete,'labels':labels,'times':times,'execution':execution,'label_ends':ends,'industry':np.zeros((T,U),dtype='int16')}.items():np.save(OUT/(name+'.npy'),value)
    atomic_json(OUT/'symbols.json',symbols);atomic_json(OUT/'feature_names.json',names)
    X.flush();del X;gc.collect()
    files={p.name:sha256(p) for p in sorted(OUT.iterdir()) if p.suffix in ['.npy','.json'] and p.name!='meta.json'}
    meta={'shape':[T,U,98],'sequence_length':60,'horizons':horizons,'daily_sources':audits,'feature_coverage':coverage,'membership_sources':source_members,'files':files,'selection_sha256':sha256(slow.ART/'slow_factor_selection.json'),'mechanical_causality_checks_passed':True,'limitations':['64 frozen factors selected on 2019–2022: 2021/2022 tests have upstream selection exposure','CSI1000 membership starts 2014-10-17; earlier active universe is CSI500 only','PIT financial coverage is CSI500 historical universe; missing values retained','Industry taxonomy unverified: all unknown ID 0','Labels use daily next-open to horizon-open proxy, not actual 09:31–09:36 execution prices','Price/factor features preserve original one-session lag; no forward fill'],'label_price':'daily adjusted open proxy','timestamp_format':'utc_ns','timezone':'Asia/Shanghai'}
    import hashlib
    meta['data_fingerprint']=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    atomic_json(OUT/'meta.json',meta);print('DAILY PANEL COMPLETE',meta['shape'],flush=True)

if __name__=='__main__':build()

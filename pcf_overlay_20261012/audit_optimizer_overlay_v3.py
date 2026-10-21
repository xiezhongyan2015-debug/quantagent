"""Independent diagnostics; preserves v3 source, inputs, and account outputs."""
from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from .data import ROOT, OUT, symbol
from . import optimizer_overlay_v3 as O
from . import replay as R

DEST = OUT / 'optimizer_overlay_v3_audit_20261011'
SOURCE = OUT / 'optimizer_overlay_v3'

def save(name, obj):
    (DEST/name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def correlation(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 100: return np.nan
    return float(np.corrcoef(rankdata(a[ok]), rankdata(b[ok]))[0, 1])

def residual(a, b, y):
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(y)
    if ok.sum() < 100: return np.nan
    x, h, yy = [rankdata(v[ok]) for v in (a,b,y)]
    X = np.column_stack([np.ones(len(h)),h])
    e = x-X@np.linalg.lstsq(X,x,rcond=None)[0]
    return float(np.corrcoef(rankdata(e), yy)[0,1])

def metrics(x, y, n=None):
    rx, ry = x[1:]/x[:-1], y[1:]/y[:-1]
    rel = rx/ry; diff=rx-ry
    n = len(rel) if n is None else n
    return dict(relative_cagr=float(np.prod(rel)**(252/n)-1),
        relative_total=float(np.prod(rel)-1),
        te_relative=float(np.std(rel-1,ddof=1)*np.sqrt(252)),
        information_ratio=float(diff.mean()/diff.std(ddof=1)*np.sqrt(252)))

def main():
    DEST.mkdir(exist_ok=True)
    save('diagnostic_protocol.json', dict(
        status='audit_after_historical_results_exposed_not_new_holdout',
        original_strategy_and_results_unchanged=True,
        controls=['P0_exact','V5_exact','excluded_only','fundamental_veto_only',
                  'V5_preserve_signal_day_member_weight','V5_exclusion_requires_current_raw'],
        purpose='attribute exclusions and member reallocation; no choosing a new strategy from these outcomes',
        costs='original 10M fractional adjusted-unit cash account, fee 0.0002/side',
        missing_signal='original event grid retained for exact replication; omitted full-calendar dates reported separately'))
    originals=json.loads((DEST/'original_manifest.json').read_text())
    assert all(hashlib.sha256((ROOT/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in originals)
    base=pd.read_csv(SOURCE/'P0_daily.csv'); dates=base.date.tolist()
    prices=pd.read_parquet(OUT/'daily_prices.parquet')
    symbols=json.loads((OUT/'price_symbols.json').read_text()); lookup={s:i for i,s in enumerate(symbols)}; U=len(symbols)
    all_days=sorted(prices.date.unique()); pos={d:i for i,d in enumerate(all_days)}
    def wide(col):
        return prices.pivot(index='date',columns='symbol',values=col).reindex(index=all_days,columns=symbols).to_numpy(float)
    close, raw, op=wide('close_adjusted'),wide('close'),wide('open_adjusted')
    volume, st, valid=wide('volume'),wide('isST'),wide('valid')
    pc=pd.read_parquet(OUT/'pcf_components.parquet'); groups={d:g[g.stock_replicable] for d,g in pc.groupby('date')}
    meta=pd.read_csv(OUT/'pcf_metadata.csv',dtype={'date':str,'valuation_date':str}).set_index('date')
    c5=pd.read_parquet(O.C5_PATH); c5=c5[c5.segment!='live']; c5groups=dict(tuple(c5.groupby('date')))
    model_symbols=json.loads((R.DATA/'symbols.json').read_text()); mapping=np.array([lookup[s] for s in model_symbols])
    clock=pd.to_datetime(np.load(R.DATA/'times.npy'),utc=True).tz_convert('Asia/Shanghai')
    by15={str(clock[i].date()):i for i in np.flatnonzero(clock.hour==15)}
    membership=np.load(R.DATA/'members.npy',mmap_mode='r')
    arrays={k:[] for k in ['P0_exact','V5_exact','excluded_only','fundamental_veto_only','V5_preserve_signal_day_member_weight','V5_exclusion_requires_current_raw']}
    marks=[]; ex=[]; end=[]; fill=[]; members=[]; observations=[]; priors=[]
    for day in dates:
        j=pos[day]; prior=all_days[j-1]; priors.append(prior)
        g=groups[prior]; m=meta.loc[prior]; ids=np.array([lookup[symbol(x)] for x in g.code]); ref=np.zeros(U)
        prev=raw[pos[m.valuation_date],ids]
        if (~np.isfinite(prev)|(prev<=0)).any(): ref[:]=np.nan
        else:
            ref[ids]=g.quantity.to_numpy()*prev/float(m.nav_per_cu)
            if ref.sum()>.98:ref*=.98/ref.sum()
        cg=c5groups[prior]; cg=cg[cg.code.isin(lookup)]; ix=np.array([lookup[x] for x in cg.code])
        excluded=np.zeros(U,bool); veto=np.zeros(U,bool); current=np.zeros(U,bool)
        excluded[ix]=cg.state.eq('excluded');veto[ix]=cg.state.eq('vetoed');current[ix]=cg.raw_score.notna()
        member=np.zeros(U,bool);member[mapping]=membership[by15[prior]]
        def remove(mask):
            w=ref.copy();w[mask]=0
            return w/w.sum()*ref.sum() if w.sum()>0 else ref.copy()
        arrays['P0_exact'].append(ref);arrays['V5_exact'].append(remove(excluded|veto))
        arrays['excluded_only'].append(remove(excluded));arrays['fundamental_veto_only'].append(remove(veto))
        arrays['V5_exclusion_requires_current_raw'].append(remove((excluded&current)|veto))
        w=ref.copy(); w[excluded|veto]=0
        if w[member].sum()>0:w[member]*=ref[member].sum()/w[member].sum()
        else:w=ref.copy()
        arrays['V5_preserve_signal_day_member_weight'].append(w)
        marks.append(close[j-1]);ex.append(op[j]);end.append(close[j]);fill.append((valid[j-1]==1)&(volume[j-1]>0)&(st[j-1]!=1))
        mm=np.zeros(U,bool);mm[mapping]=membership[by15[day] if day in by15 else by15[prior]];members.append(mm)
        observations.append(dict(signal_date=prior,execution_date=day,pcf_names=len(ids),bad_names=int(((excluded|veto)&(ref>0)).sum()),
            excluded_weight=float(np.nansum(ref[excluded])),vetoed_weight=float(np.nansum(ref[veto])),
            removed_weight=float(np.nansum(ref[excluded|veto])),
            member_target_P0=float(np.nansum(ref[member])),member_target_V5=float(np.nansum(arrays['V5_exact'][-1][member])),
            excluded_no_current_raw_names=int((excluded&~current&(ref>0)).sum()),
            excluded_no_current_raw_weight=float(np.nansum(ref[excluded&~current])),
            veto_no_current_raw_weight=float(np.nansum(ref[veto&~current]))))
    pd.DataFrame(observations).to_csv(DEST/'veto_target_diagnostics.csv',index=False)
    outputs={}; checks={}
    for name,weights in arrays.items():
        d,_=O.simulate('P0',dates,np.asarray(weights),np.zeros((len(dates),U)),np.asarray(marks),np.asarray(ex),np.asarray(fill),np.asarray(end),symbols,None,{}, {}, {}, [np.flatnonzero(w>0) for w in weights], np.asarray(members),[])
        d.to_csv(DEST/(name+'_diagnostic_daily.csv'),index=False);outputs[name]=d
        if name in ['P0_exact','V5_exact']:
            orig=pd.read_csv(SOURCE/('P0_daily.csv' if name=='P0_exact' else 'V5_daily.csv'))
            checks[name]={col:float(np.nanmax(np.abs(d[col].to_numpy()-orig[col].to_numpy()))) for col in ['nav','fee_cny','turnover','member_nav_weight']}
    rec=[]
    for name,d in outputs.items():
        q=metrics(d.nav.to_numpy(),outputs['P0_exact'].nav.to_numpy()) if name!='P0_exact' else {}
        q.update(arm=name,first=dates[0],last=dates[-1],nav_return_periods=len(dates)-1,member_min=float(d.member_nav_weight.min()),member80_days=int((d.member_nav_weight.iloc[3:]<=.8).sum()),turnover=float(d.turnover.iloc[1:].mean()))
        rec.append(q)
    pd.DataFrame(rec).to_csv(DEST/'diagnostic_controls.csv',index=False)
    full_grid=[d for d in all_days if dates[0]<=d<=dates[-1]]
    missing=[d for d in full_grid if d not in set(dates)]
    save('replication_checks.json',dict(exact_replication=checks,missing_calendar_dates=missing,actual_first=dates[0],actual_last=dates[-1],rows=len(dates),returns=len(dates)-1,
        calendar_adjusted_V5_relative_cagr=metrics(outputs['V5_exact'].nav.to_numpy(),outputs['P0_exact'].nav.to_numpy(),len(full_grid)-1)['relative_cagr']))
    # Recompute all paired confidence intervals from saved NAVs, same statistic.
    pairs=pd.read_csv(SOURCE/'paired_comparisons.csv'); pair_checks=[]
    for row in pairs.to_dict('records'):
        a=pd.read_csv(SOURCE/(row['arm']+'_daily.csv')).nav.to_numpy();b=pd.read_csv(SOURCE/(row['versus']+'_daily.csv')).nav.to_numpy()
        q=metrics(a,b); rr=(a[1:]/a[:-1])/(b[1:]/b[:-1]);n=len(rr);rng=np.random.default_rng(20261011)
        ix=(rng.integers(0,n,size=(2000,int(np.ceil(n/20)),1))+np.arange(20))%n;ix=ix.reshape(2000,-1)[:,:n]
        ci=np.quantile(np.prod(rr[ix],axis=1)**(252/n)-1,[.025,.975])
        pair_checks.append(dict(arm=row['arm'],versus=row['versus'],**q,ci_low=float(ci[0]),ci_high=float(ci[1]),cagr_difference=q['relative_cagr']-row['annualized_relative_cagr'],ci_max_difference=float(np.max(np.abs(ci-[row['cagr_boot95_low'],row['cagr_boot95_high']])))))
    pd.DataFrame(pair_checks).to_csv(DEST/'paired_recomputed.csv',index=False)
    # Identify the daily labels and residual definition by independent candidates.
    s15,_=R.signals(15,all_days,symbols); original=pd.read_csv(OUT/'c5_vs_gru_daily.csv').set_index('date')
    stats=[]
    for day in original.index:
        j=pos[day];cg=c5groups[day];cg=cg[cg.code.isin(lookup)]
        a=np.full(U,np.nan);a[[lookup[x] for x in cg.code]]=cg.score_pct.to_numpy();h=s15[day]
        for kind in ['next_open','close']:
            labels={}
            for horizon in [1,5]:
                entry=j+1 if kind=='next_open' else j;exit=j+horizon+1 if kind=='next_open' else j+horizon
                p=op if kind=='next_open' else close
                y=p[exit]/p[entry]-1 if exit<len(all_days) else np.full(U,np.nan)
                ok=np.isfinite(a)&np.isfinite(h)&np.isfinite(y);aa=np.where(ok,a,np.nan);hh=np.where(ok,h,np.nan)
                labels[horizon]=dict(n=int(ok.sum()),c5=correlation(aa,y),gru=correlation(hh,y),resid_c5=residual(a,h,y),resid_gru=residual(h,a,y))
            stats.append(dict(date=day,label_kind=kind,**{f'{k}{hz}':v for hz,d in labels.items() for k,v in d.items()}))
    st=pd.DataFrame(stats);st.to_csv(DEST/'independent_signal_label_candidates.csv',index=False)
    comparison=[]
    columns={'c51':'rankic1_c5','gru1':'rankic1_gru','resid_c51':'resid_c5_on_gru','resid_gru1':'resid_gru_on_c5','c55':'rankic5_c5','gru5':'rankic5_gru','resid_c55':'resid5_c5_on_gru','resid_gru5':'resid5_gru_on_c5'}
    for kind,g in st.groupby('label_kind'):
        g=g.set_index('date'); summary={'label_kind':kind}
        for c,old in columns.items():summary[c+'_max_diff']=float((g[c]-original[old]).abs().max());summary[c+'_mean']=float(g[c].mean())
        comparison.append(summary)
    save('signal_label_verification.json',comparison)
    assert all(hashlib.sha256((ROOT/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in originals)
    print('Exact replication:',checks,flush=True)
    print(pd.DataFrame(rec).to_string(index=False),flush=True)
    print('Label candidates:',json.dumps(comparison),flush=True)
    print('Missing calendar dates:',missing,flush=True)

if __name__=='__main__':main()

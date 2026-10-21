"""Predefined paired analyses run only when each required seed set is complete."""
import json
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from .data import ROOT, write

def residual_ic(daily,hourly,label):
    ok=np.isfinite(daily)&np.isfinite(hourly)&np.isfinite(label)
    if ok.sum()<100:return np.nan,np.nan,int(ok.sum())
    d,h,y=[rankdata(a[ok]) for a in [daily,hourly,label]]
    X=np.column_stack([np.ones(len(h)),h]);r=d-X@np.linalg.lstsq(X,d,rcond=None)[0]
    if r.std()<1e-10:return np.nan,np.nan,int(ok.sum())
    return float(np.corrcoef(rankdata(r),y)[0,1]),float(np.corrcoef(r,y)[0,1]),int(ok.sum())

def collect_comparisons(dest):
    from .collect_results import OLD,BASE,complete,ensemble,corr
    hourly=ROOT/'outputs/gru_walkforward_20261010'
    hs=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    panel=BASE/'daily_panel';ds=json.loads((panel/'symbols.json').read_text());dpos={s:i for i,s in enumerate(ds)}
    mapping=np.array([dpos[s] for s in hs]);labels=np.load(panel/'labels.npy',mmap_mode='r')
    hlabels=np.load(ROOT/'gru_research/artifacts/dataset/labels.npy',mmap_mode='r')
    daily_rows=[];corr_rows=[];a4=[];ready_years=[];a4years=[];family_rows=[]
    for year in range(2021,2027):
        hp=[hourly/f'T{year}/control_seed{s}/test_scores.npz' for s in OLD]
        gp=[BASE/f'B/GRU/T{year}/seed{s}' for s in OLD]
        lp=[BASE/f'B/LightGBM/T{year}/seed{s}' for s in OLD]
        h=None
        for family,paths in [('GRU',gp),('LightGBM',lp)]:
            if not all(complete(p) for p in paths):continue
            if h is None:
                h,hi,ht=ensemble(hp,15);hi_by_time={int(t):i for i,t in enumerate(ht)}
            for horizon,head in [(1,0),(5,3)]:
                ss,ii,tt=ensemble([p/'test_scores.npz' for p in paths],head=head)
                ic=np.array([corr(s,labels[t,:,head]) for s,t in zip(ss,ii)])
                family_rows.append(dict(family=family,year=year,horizon_days=horizon,seeds=5,
                    rank_ic=float(np.nanmean(ic)),valid_dates=int(np.isfinite(ic).sum()),
                    date_rank_ic_std=float(np.nanstd(ic))))
                for j,t in enumerate(tt):
                    if int(t) not in hi_by_time:continue
                    k=hi_by_time[int(t)];label=labels[ii[j],mapping,head];d=ss[j,mapping];hh=h[k]
                    ric,linear,n=residual_ic(d,hh,label)
                    common=np.isfinite(d)&np.isfinite(hh)&np.isfinite(label)
                    label=np.where(common,label,np.nan)
                    daily_rows.append(dict(family=family,year=year,time=int(t),horizon_days=horizon,common_stocks=n,
                        daily_rank_ic=corr(d,label),hourly_rank_ic_same_daily_label=corr(hh,label),
                        residual_rank_ic=ric,partial_rank_correlation=linear,signal_rank_correlation=corr(d,hh)))
        if all(complete(p) for p in gp):
            ready_years.append(year);g,di,dt=ensemble([p/'test_scores.npz' for p in gp])
            if all(complete(p) for p in lp):
                l,li,lt=ensemble([p/'test_scores.npz' for p in lp]);assert np.array_equal(di,li) and np.array_equal(dt,lt)
                for j,t in enumerate(dt):
                    corr_rows.append(dict(year=year,time=int(t),horizon_days=5,
                        gru_lgb_daily_union_rank_correlation=corr(g[j],l[j]),
                        gru_lgb_hourly_universe_rank_correlation=corr(g[j,mapping],l[j,mapping])))
        ap=[BASE/f'A4/T{year}/control_seed{s}' for s in OLD]
        if all(complete(p) for p in ap):
            a4years.append(year);a,ai,at=ensemble([p/'test_scores.npz' for p in ap],14)
            c,ci,ct=ensemble(hp,14);assert np.array_equal(ai,ci) and np.array_equal(at,ct)
            for j,t in enumerate(ai):
                # Both models must have a score: never compare different universes.
                ok=np.isfinite(a[j])&np.isfinite(c[j]);y=np.where(ok,hlabels[t,:,3],np.nan)
                x,y0=corr(a[j],y),corr(c[j],y)
                a4.append(dict(year=year,time=int(at[j]),a4_rank_ic=x,control_rank_ic=y0,paired_delta=x-y0))
    status={'daily_GRU_complete_years':ready_years,'A4_complete_years':a4years,
            'daily_horizon_primary':5,'residual_definition':'OLS of daily score ranks on hourly score ranks plus intercept; RankIC of residual vs same daily future-label ranks, common finite stocks >=100',
            'hourly_signal_head':'y4, approximately one trading day; not called a 5-day forecast',
            'daily_adopt_threshold':.03,'GRU_LGB_no_fusion_threshold':.85,'A4_delta_threshold':.003,
            'all_decisions_deferred_until_six_years_complete':True}
    if daily_rows:
        d=pd.DataFrame(daily_rows);d.to_csv(dest/'daily_vs_hourly_paired_sections.csv',index=False)
        d.groupby(['family','year','horizon_days']).mean(numeric_only=True).drop(columns=['time']).to_csv(dest/'daily_vs_hourly_yearly.csv')
        if len(ready_years)==6:
            r=float(d.loc[(d.family=='GRU')&(d.horizon_days==5),'residual_rank_ic'].mean())
            status['daily_residual_gate_passes']=r>=.03;status['daily_residual_rank_ic']=r
    if corr_rows:
        d=pd.DataFrame(corr_rows);d.to_csv(dest/'GRU_vs_LightGBM_paired_sections.csv',index=False)
        if d.year.nunique()==6:status['GRU_LGB_no_fusion']=bool(d.gru_lgb_daily_union_rank_correlation.mean()>.85)
    if a4:
        d=pd.DataFrame(a4);d.to_csv(dest/'A4_vs_control_paired_sections.csv',index=False)
        y=d.groupby('year').paired_delta.mean();y.to_csv(dest/'A4_vs_control_yearly.csv')
        if len(a4years)==6:status['A4_gate_passes']=bool(d.paired_delta.mean()>=.003 and (y>0).sum()>=4)
    if family_rows:
        pd.DataFrame(family_rows).to_csv(dest/'daily_5seed_ensemble_yearly.csv',index=False)
        status['complete_daily_family_years']={family:sorted({r['year'] for r in family_rows if r['family']==family}) for family in ['GRU','LightGBM']}
    write(dest/'comparison_status.json',status)
    return status

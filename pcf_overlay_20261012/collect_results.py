"""Idempotent local result collector. Does not submit, interrupt or alter jobs."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from .data import ROOT, OUT, write, sha
from .replay import zscore

OLD=[11,22,33,44,55]
NEW=[66,77,88,99,111,122,133,144,155,166,177,188,199,211,222]
BASE=ROOT/'outputs/gru_dualplatform_20261011'

def complete(run):
    path=run/'export.json'
    return path.exists() and json.loads(path.read_text()).get('status')=='complete'

def corr(a,b):
    ok=np.isfinite(a)&np.isfinite(b)
    if ok.sum()<100:return np.nan
    a,b=rankdata(a[ok]),rankdata(b[ok])
    if a.std()<1e-12 or b.std()<1e-12:return np.nan
    return float(np.corrcoef(a,b)[0,1])

def ensemble(paths,slot=None,head=3,return_seed_scores=False):
    arrays=[];reference=None;times=None
    for path in paths:
        a=np.load(path);ix=a['indices'];s=a['scores'][:,:,head].astype(float);ts=a['times']
        if slot is not None:
            keep=pd.to_datetime(ts,utc=True).tz_convert('Asia/Shanghai').hour==slot
            ix,s,ts=ix[keep],s[keep],ts[keep]
        if reference is None:reference=ix;times=ts
        else:assert np.array_equal(ix,reference) and np.array_equal(ts,times)
        arrays.append(np.array([zscore(x) for x in s]))
    result=(np.mean(arrays,axis=0),reference,times)
    return (*result,np.asarray(arrays)) if return_seed_scores else result

def main():
    dest=OUT/'training_followup';dest.mkdir(exist_ok=True)
    inventory=[];rows=[];swa=[];availability={};inputs=[]
    for year in range(2021,2027):
        availability[str(year)]={}
        for seed in OLD+NEW:
            run=(ROOT/'outputs/gru_walkforward_20261010' if seed in OLD else BASE/'A1')/f'T{year}'/f'control_seed{seed}'
            ready=complete(run);availability[str(year)][str(seed)]=ready
            inventory.append(dict(family='A1',year=year,seed=seed,complete=ready,path=str(run)))
            if not ready:continue
            p=run/'test_sections.csv';frame=pd.read_csv(p);inputs.append(dict(path=str(p),sha256=sha(p)))
            for slot in [2,3]:
                a=frame[frame.slot==slot]
                rows.append(dict(year=year,seed=seed,slot=14 if slot==2 else 15,kind='best',rank_ic=float(a.rank_ic.mean()),ic=float(a.ic.mean())))
            sp=run/'test_sections_swa.csv'
            if sp.exists():
                other=pd.read_csv(sp);paired=frame.merge(other,on=['time','date','slot'],suffixes=('_best','_swa'),validate='one_to_one')
                for slot in [2,3]:
                    a=paired[paired.slot==slot]
                    swa.append(dict(year=year,seed=seed,slot=14 if slot==2 else 15,
                                    best=float(a.rank_ic_best.mean()),swa=float(a.rank_ic_swa.mean()),
                                    paired_delta=float((a.rank_ic_swa-a.rank_ic_best).mean()),dates=len(a)))
        for family in ['GRU','LightGBM']:
            for seed in OLD:
                run=BASE/'B'/family/f'T{year}'/f'seed{seed}';ready=complete(run)
                inventory.append(dict(family=family,year=year,seed=seed,complete=ready,path=str(run)))
                if ready:
                    p=run/'test_sections.csv';a=pd.read_csv(p);inputs.append(dict(path=str(p),sha256=sha(p)))
                    for h in [1,5]:rows.append(dict(year=year,seed=seed,slot=15,kind=family,horizon_days=h,rank_ic=float(a[f'rank_ic_{h}'].mean()),ic=float(a[f'ic_{h}'].mean())))
    pd.DataFrame(rows).to_csv(dest/'available_seed_scorecard.csv',index=False)
    pd.DataFrame(swa).to_csv(dest/'SWA_vs_best_available_paired.csv',index=False)
    pd.DataFrame(inventory).to_csv(dest/'completion_inventory.csv',index=False)
    # Full-20 results are withheld until all predefined seeds are present.
    final=[];noise=[];labels=np.load(ROOT/'gru_research/artifacts/dataset/labels.npy',mmap_mode='r')
    for year in range(2021,2027):
        if not all(availability[str(year)].values()):continue
        paths=[((ROOT/'outputs/gru_walkforward_20261010' if s in OLD else BASE/'A1')/f'T{year}'/f'control_seed{s}'/'test_scores.npz') for s in OLD+NEW]
        scores,ix,times,seedscores=ensemble(paths,15,return_seed_scores=True);ics=[corr(s,labels[t,:,3]) for s,t in zip(scores,ix)]
        final.append(dict(year=year,seeds=20,slot=15,rank_ic=float(np.nanmean(ics))))
        for n in [1,2,5,10,20]:
            s=seedscores[:n].mean(axis=0);ic=np.asarray([corr(v,labels[t,:,3]) for v,t in zip(s,ix)])
            groups=seedscores.reshape(20//n,n,*seedscores.shape[1:]).mean(axis=1)
            # Spread of disjoint fixed-seed groups around the full-20 ensemble.
            # At n=20 zero is mechanical, not evidence of zero model error.
            dispersion=float(np.sqrt(np.nanmean((groups-scores[None])**2)))
            noise.append(dict(year=year,seeds=n,rank_ic=float(np.nanmean(ic)),date_rank_ic_std=float(np.nanstd(ic)),
                              fixed_group_score_rms_vs_20seed=dispersion,groups=20//n,seed_order=OLD+NEW))
    if final:pd.DataFrame(final).to_csv(dest/'20seed_yearly_scorecard.csv',index=False)
    if noise:
        pd.DataFrame(noise).to_csv(dest/'seed_noise_fixed_order.csv',index=False)
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(7,4))
        for year,g in pd.DataFrame(noise).groupby('year'):
            ax.plot(g.seeds,g.fixed_group_score_rms_vs_20seed,marker='o',label=str(year))
        ax.set(xlabel='Seeds per fixed disjoint group',ylabel='Score RMS vs full-20 ensemble',xticks=[1,2,5,10,20])
        ax.legend();fig.tight_layout();fig.savefig(dest/'seed_noise_fixed_groups.png',dpi=160);plt.close(fig)
    from .comparisons import collect_comparisons
    comparison_status=collect_comparisons(dest)
    counts={family:sum(x['complete'] for x in inventory if x['family']==family) for family in ['A1','GRU','LightGBM']}
    status=dict(status='waiting_for_predefined_complete_sets' if len(final)<6 else '20seed_scorecard_ready',
                counts=counts,expected={'A1':120,'GRU':30,'LightGBM':30},
                complete_20seed_years=[x['year'] for x in final],
                no_partial_seed_selection=True,no_job_changes=True,
                comparisons=comparison_status,
                remaining=['A3 old-seed SWA exports','A4 five-seed six-year comparison','daily-vs-hourly residual IC once all daily GRU seeds arrive'],inputs=inputs)
    write(dest/'status.json',status)
    print(json.dumps({k:v for k,v in status.items() if k!='inputs'},ensure_ascii=False),flush=True)

if __name__=='__main__':main()

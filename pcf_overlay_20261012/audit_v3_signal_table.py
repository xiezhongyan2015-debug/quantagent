"""Identify original signal-table inputs without modifying any strategy."""
import json
import numpy as np
import pandas as pd
from .data import ROOT,OUT
from .collect_results import ensemble
from .audit_optimizer_overlay_v3 import correlation,residual,DEST

def main():
    original=pd.read_csv(OUT/'c5_vs_gru_daily.csv').set_index('date')
    panel=ROOT/'outputs/gru_dualplatform_20261011/daily_panel'
    hs=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    ds=json.loads((panel/'symbols.json').read_text());dpos={s:i for i,s in enumerate(ds)};mapping=np.array([dpos[s] for s in hs]);lookup={s:i for i,s in enumerate(hs)}
    dt=pd.to_datetime(np.load(panel/'times.npy'),utc=True).tz_convert('Asia/Shanghai');dailypos={str(t.date()):i for i,t in enumerate(dt)}
    dy=np.load(panel/'labels.npy',mmap_mode='r');hy=np.load(ROOT/'gru_research/artifacts/dataset/labels.npy',mmap_mode='r')
    c=pd.read_parquet(ROOT/'给队友_c5分数_20261011/c5_scores_20240601_20260928.parquet');cg=dict(tuple(c.groupby('date')))
    rows=[];stored=[];stored_days=[]
    for year in [2024,2025,2026]:
        paths=[ROOT/f'outputs/gru_walkforward_20261010/T{year}/control_seed{s}/test_scores.npz' for s in [11,22,33,44,55]]
        score,indices,times=ensemble(paths,15,3)
        dates=[str(x.date()) for x in pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai')]
        for h,ix,day in zip(score,indices,dates):
            if day not in original.index:continue
            g=cg[day];g=g[g.code.isin(lookup)];a=np.full(len(hs),np.nan);a[[lookup[x] for x in g.code]]=g.score_pct
            stored.append(h);stored_days.append(day)
            labs={'daily_panel_1':dy[dailypos[day],mapping,0],'daily_panel_5':dy[dailypos[day],mapping,3],'hourly_y4':hy[ix,:,3]}
            for label,y in labs.items():
                ok=np.isfinite(a)&np.isfinite(h)&np.isfinite(y);aa=np.where(ok,a,np.nan);hh=np.where(ok,h,np.nan)
                rows.append(dict(date=day,label=label,n=int(ok.sum()),spearman_allfinite=correlation(a,h),spearman_label_common=correlation(aa,hh),c5=correlation(aa,y),gru=correlation(hh,y),resid_c5=residual(a,h,y),resid_gru=residual(h,a,y)))
        print('Scored input identified for year',year,flush=True)
    np.savez_compressed(DEST/'raw_gru15_table_input.npz',scores=np.asarray(stored),dates=np.asarray(stored_days))
    d=pd.DataFrame(rows);d.to_csv(DEST/'raw_signal_label_candidates.csv',index=False)
    cmp=[]
    for label,g in d.groupby('label'):
        g=g.set_index('date');out={'label':label,'n_max_diff':int((g.n-original.n).abs().max()),'spearman_allfinite_max_diff':float((g.spearman_allfinite-original.spearman).abs().max()),'spearman_label_common_max_diff':float((g.spearman_label_common-original.spearman).abs().max())}
        for horizon in [1,5]:
            columns={'c5':f'rankic{horizon}_c5','gru':f'rankic{horizon}_gru','resid_c5':'resid_c5_on_gru' if horizon==1 else 'resid5_c5_on_gru','resid_gru':'resid_gru_on_c5' if horizon==1 else 'resid5_gru_on_c5'}
            for new,old in columns.items():out[old+'_max_diff']=float((g[new]-original[old]).abs().max())
        out['means']=g.mean(numeric_only=True).to_dict();cmp.append(out)
    (DEST/'raw_signal_table_verification.json').write_text(json.dumps(cmp,ensure_ascii=False,indent=2))
    print(json.dumps(cmp,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()

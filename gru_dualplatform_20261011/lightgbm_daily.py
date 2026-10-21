"""Daily LightGBM comparator; causal last/5/20 mean and slope features."""
from pathlib import Path
import argparse, gc, json, time, os
import numpy as np
import pandas as pd
from .daily import Data, CONFIG, OUT, ranknormal, corr, write, sha

def features(data,t):
    x=np.asarray(data.X[t-19:t+1,data.mask(t)],dtype='float64')
    out=[x[-1]]
    for n in [5,20]:
        a=x[-n:];valid=np.isfinite(a).all(axis=0);s=np.arange(n,dtype=float);s-=s.mean()
        mean=a.mean(0);slope=(a*s[:,None,None]).sum(0)/(s*s).sum()
        out.extend([np.where(valid,mean,np.nan),np.where(valid,slope,np.nan)])
    return np.concatenate(out,axis=1).astype('float32')

def table(data,ix,cache):
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    counts=np.asarray([data.mask(int(t)).sum() for t in ix]);N=int(counts.sum())
    X=np.lib.format.open_memmap(cache/'features.npy',mode='w+',dtype='float32',shape=(N,490))
    y=np.lib.format.open_memmap(cache/'targets.npy',mode='w+',dtype='float32',shape=(N,4))
    offsets=np.concatenate([[0],counts.cumsum()])
    for k,t in enumerate(ix):
        X[offsets[k]:offsets[k+1]]=features(data,int(t))
        y[offsets[k]:offsets[k+1]]=ranknormal(np.asarray(data.labels[t,data.mask(int(t))]))
    X.flush();y.flush();return X,y,offsets

def train(year,seed,pilot=False):
    import lightgbm as lgb
    data=Data();tr=data.split(year,'train');va=data.split(year,'validation');te=data.split(year,'test')
    run=OUT/('LGB_pilot' if pilot else 'LightGBM')/f'T{year}'/f'seed{seed}';run.mkdir(parents=True,exist_ok=True)
    sig={'config':CONFIG,'panel':data.meta['data_fingerprint'],'source':sha(__file__),'year':year,'seed':seed,'pilot':pilot,'target':'per-date Gaussian rank, L2 fit; validation daily RankIC selection'}
    if (run/'export.json').exists():assert json.loads((run/'export.json').read_text())['signature']==sig;return
    # Feature tables shared by seeds; values and membership depend on no targets.
    cache=OUT/'feature_cache'/f'T{year}'
    def get(which,ix):
        p=cache/which
        if not (p/'complete.json').exists():
            a,b,offsets=table(data,ix,p);write(p/'complete.json',{'panel':data.meta['data_fingerprint'],'indices':ix.tolist(),'offsets':offsets.tolist()});del a,b
        receipt=json.loads((p/'complete.json').read_text());assert receipt['panel']==data.meta['data_fingerprint'] and receipt['indices']==ix.tolist()
        return np.load(p/'features.npy',mmap_mode='r'),np.load(p/'targets.npy',mmap_mode='r'),np.asarray(receipt['offsets'])
    X,y,off=get('train',tr);V,v,voff=get('validation',va)
    models=[];elapsed=[]
    for h in ([3] if pilot else range(4)):
        tick=time.monotonic();ok=np.isfinite(y[:,h]);vk=np.isfinite(v[:,h]);positions=np.cumsum(vk)
        # Map each validation section into the filtered label table.
        bounds=np.concatenate([[0],positions[voff[1:]-1]])
        def metric(pred,ds):
            label=ds.get_label();values=[corr(pred[a:b],label[a:b],True) for a,b in zip(bounds[:-1],bounds[1:])]
            values=[x for x in values if x is not None];assert values
            return 'daily_rank_ic',float(np.mean(values)),True
        params={'objective':'regression','metric':'None','num_leaves':31,'learning_rate':.05,'max_bin':63,'verbosity':-1,'num_threads':int(os.environ.get('LGB_THREADS','4')),'seed':seed,'feature_fraction':.8,'bagging_fraction':.8,'bagging_freq':1,'deterministic':True,'force_col_wise':True}
        # Compact one head at a time; file backing bounds memory on the 15 GiB host.
        compact=cache/f'head{h}.npy'
        A=np.lib.format.open_memmap(compact,mode='w+',dtype='float32',shape=(int(ok.sum()),490));o=0
        for st in range(0,len(X),10000):
            block=X[st:st+10000][ok[st:st+10000]];A[o:o+len(block)]=block;o+=len(block)
        A.flush()
        trainset=lgb.Dataset(A,label=np.asarray(y[:,h])[ok],free_raw_data=True)
        validset=lgb.Dataset(np.asarray(V)[vk],label=np.asarray(v[:,h])[vk],reference=trainset)
        booster=lgb.train(params,trainset,num_boost_round=20 if pilot else 500,valid_sets=[validset],feval=metric,callbacks=[lgb.early_stopping(50,first_metric_only=True,verbose=False)])
        booster.save_model(str(run/f'head{h}.txt'));models.append(booster)
        elapsed.append(time.monotonic()-tick);print('LGB HEAD',year,seed,h,elapsed[-1],booster.best_iteration,flush=True)
        del trainset,validset,A;gc.collect()
        compact.unlink()  # Disposable head matrix; saved model is retained.
    if pilot:write(run/'pilot.json',{'signature':sig,'seconds':elapsed,'training_rows':len(X)});return
    rows=[];scores=[]
    for t in te:
        f=features(data,int(t));s=np.column_stack([m.predict(f,num_iteration=m.best_iteration) for m in models]);yraw=np.asarray(data.labels[t,data.mask(int(t))])
        row={'date':data.dates[t],'time':int(data.times[t]),'visible_count':len(f)}
        for h,delta in enumerate(CONFIG['horizons']):row['ic_'+str(delta)]=corr(s[:,h],yraw[:,h]);row['rank_ic_'+str(delta)]=corr(s[:,h],yraw[:,h],True)
        a=np.full((data.U,4),np.nan,dtype='float32');a[data.mask(int(t))]=s;scores.append(a);rows.append(row)
    np.savez_compressed(run/'test_scores.npz',scores=np.asarray(scores),indices=te,times=data.times[te]);pd.DataFrame(rows).to_csv(run/'test_sections.csv',index=False)
    write(run/'export.json',{'signature':sig,'status':'complete','seconds':elapsed,'test':pd.DataFrame(rows).filter(regex='^ic_|^rank_ic_').mean().to_dict(),'limitations':data.meta['limitations']})

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['train','pilot']);ap.add_argument('--year',type=int,default=2026);ap.add_argument('--seed',type=int,default=11);a=ap.parse_args();train(a.year,a.seed,a.command=='pilot')

"""Frozen-panel last-row y4 baseline; five seeds, 2024 checkpoint selection.

The tree baseline uses the same cross-sectional Gaussian-rank y4 target and
prediction participants. L2 is the tree loss; this is not a loss-only or a pure
architecture attribution. The other three heads are not invented or exported
as trained predictions. Fusion weights are fixed before reading test metrics.
"""
import argparse,json,os,time
from pathlib import Path
import numpy as np
import pandas as pd
from .attribution import ROOT,OUT,sha

DEST=ROOT/'outputs/gru_lightgbm_20261009';CACHE=DEST/'cache';SEEDS=[11,22,33,44,55]
CONFIG={'target':'y4 cross-sectional inverse-normal average ranks, identical original control transform',
    'inputs':'last normalized row122 +122 missing indicators + current PIT industry category + signal slot category',
    'training':'2019-2023, same two-session boundary embargo','selection':'2024 slot14 raw-y4 RankIC only',
    'loss':'L2 regression of Gaussian-rank y4; equal total weight per cross-section',
    'maximum_trees':1000,'early_stopping_rounds':80,'learning_rate':.05,'num_leaves':31,'min_data_in_leaf':500,
    'feature_fraction':.8,'bagging_fraction':.8,'bagging_freq':1,'lambda_l2':10,'max_bin':127,
    'fusion_weights':{'control_GRU':.5,'lightgbm':.5},'fusion_transform':'same-timestamp per-model z-score on finite prediction population',
    'historical_test_used_for_selection':False,'seeds':SEEDS,'source_panel':'original frozen panel, single baseline comparison; new backfilled panel remains separate'}


def data():
    from gru_market_attention_20261007.experiment import Data
    return Data()


def matrix(d,t):
    mask=d.mask(int(t));x=np.asarray(d.p.X[t,mask],np.float32).copy();missing=~np.isfinite(x)
    x[missing]=np.nan
    out=np.column_stack([x,missing.astype(np.float32),d.industry[t,mask],np.full(mask.sum(),d.p.slots[t])]).astype(np.float32)
    assert out.shape==(int(mask.sum()),246)
    return out,mask


def metric(pred,raw,counts):
    offset=0;rankic=[]
    for n in counts:
        a=pred[offset:offset+n];b=raw[offset:offset+n];offset+=n;valid=np.isfinite(a)&np.isfinite(b)
        if valid.sum()<100:continue
        ar=pd.Series(a[valid]).rank().to_numpy();br=pd.Series(b[valid]).rank().to_numpy()
        if ar.std()>0 and br.std()>0:rankic.append(float(np.corrcoef(ar,br)[0,1]))
    assert offset==len(pred) and rankic
    return float(np.mean(rankic))


def prepare():
    from gru_market_attention_20261007.experiment import ranknormal
    DEST.mkdir(exist_ok=True);CACHE.mkdir(exist_ok=True);d=data();receipt={'config':CONFIG,'dataset_fingerprint':d.p.fingerprint,
        'context_sha256':d.context_meta['context_sha256'],'source_sha256':sha(__file__),'datasets':{}}
    for split,source in [('train','train'),('validation14','validation')]:
        indices=d.split(source)
        if split=='validation14':indices=indices[d.p.slots[indices]==2]
        indices=np.array([t for t in indices if np.isfinite(d.p.labels[t,d.mask(int(t)),3]).sum()>=100],dtype=np.int64)
        counts=[]
        for t in indices:
            y=np.asarray(d.p.labels[t,d.mask(int(t)),3]);counts.append(int(np.isfinite(y).sum()))
        total=sum(counts);x=np.lib.format.open_memmap(CACHE/(split+'_X.npy'),mode='w+',dtype=np.float32,shape=(total,246))
        y=np.empty(total,np.float32);raw=np.empty(total,np.float32);weight=np.empty(total,np.float32);offset=0
        for i,(t,n) in enumerate(zip(indices,counts)):
            features,mask=matrix(d,int(t));labels=np.asarray(d.p.labels[t,mask]);valid=np.isfinite(labels[:,3])
            target=ranknormal(labels)[:,3];assert np.isfinite(target[valid]).all()
            x[offset:offset+n]=features[valid];y[offset:offset+n]=target[valid];raw[offset:offset+n]=labels[valid,3];weight[offset:offset+n]=1/n;offset+=n
            if (i+1)%250==0:print('Prepared',split,i+1,'/',len(indices),'sections',flush=True)
        weight/=weight.mean()
        x.flush();np.save(CACHE/(split+'_y.npy'),y);np.save(CACHE/(split+'_raw_y4.npy'),raw);np.save(CACHE/(split+'_weights.npy'),weight)
        np.savez(CACHE/(split+'_sections.npz'),indices=indices,times=d.p.times[indices],counts=np.array(counts))
        receipt['datasets'][split]={'rows':total,'sections':len(indices),'files_sha256':{p.name:sha(p) for p in CACHE.glob(split+'_*')}}
    (CACHE/'manifest.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt|{'datasets':{k:{'rows':v['rows'],'sections':v['sections']} for k,v in receipt['datasets'].items()}}),flush=True)


def train(seed):
    import lightgbm as lgb
    run=DEST/f'seed{seed}';run.mkdir(exist_ok=True);receipt=json.loads((CACHE/'manifest.json').read_text())
    assert receipt['config']==CONFIG and receipt['source_sha256']==sha(__file__)
    for entry in receipt['datasets'].values():
        for filename,digest in entry['files_sha256'].items():assert sha(CACHE/filename)==digest
    d=data();assert receipt['dataset_fingerprint']==d.p.fingerprint
    load=lambda name:np.load(CACHE/(name+'.npy'),mmap_mode='r')
    trainset=lgb.Dataset(load('train_X'),label=load('train_y'),weight=load('train_weights'),categorical_feature=[244,245],free_raw_data=True)
    validset=lgb.Dataset(load('validation14_X'),label=load('validation14_y'),reference=trainset,categorical_feature=[244,245],free_raw_data=True)
    counts=np.load(CACHE/'validation14_sections.npz')['counts'];raw=load('validation14_raw_y4')
    def feval(pred,unused):return 'raw_y4_rank_ic_2024_slot14',metric(pred,raw,counts),True
    params={k:CONFIG[k] for k in ['learning_rate','num_leaves','min_data_in_leaf','feature_fraction','bagging_fraction','bagging_freq','lambda_l2','max_bin']}
    params.update(objective='regression',metric='None',verbosity=-1,num_threads=int(os.environ.get('SLURM_CPUS_PER_TASK','4')),
        seed=seed,data_random_seed=seed,feature_fraction_seed=seed,bagging_seed=seed,deterministic=True,force_col_wise=True)
    history={};start=time.monotonic()
    booster=lgb.train(params,trainset,num_boost_round=CONFIG['maximum_trees'],valid_sets=[validset],valid_names=['2024_slot14'],feval=feval,
        callbacks=[lgb.early_stopping(CONFIG['early_stopping_rounds'],first_metric_only=True),lgb.log_evaluation(20),lgb.record_evaluation(history)])
    booster.save_model(str(run/'best_y4.txt'),num_iteration=booster.best_iteration)
    result={'status':'training_complete_export_pending','seed':seed,'config':CONFIG,'params':params,'lightgbm_version':lgb.__version__,
        'best_iteration':booster.best_iteration,'best_validation14_rank_ic':booster.best_score['2024_slot14']['raw_y4_rank_ic_2024_slot14'],
        'seconds':time.monotonic()-start,'dataset_fingerprint':d.p.fingerprint,'source_sha256':sha(__file__),'model_sha256':sha(run/'best_y4.txt'),
        'historical_test_used_for_selection':False,'trained_output_heads':[3],'other_heads_not_fabricated':True}
    (run/'result.json').write_text(json.dumps(result,indent=2));(run/'learning_curve.json').write_text(json.dumps(history,indent=2))
    for split in ['validation','test_2025','recent_2026']:
        indices=d.split(split);scores=np.full((len(indices),d.p.U,4),np.nan,np.float32);sections=[]
        for i,t in enumerate(indices):
            features,mask=matrix(d,int(t));pred=booster.predict(features,num_iteration=booster.best_iteration);scores[i,mask,3]=pred
            y=np.asarray(d.p.labels[t,mask,3]);valid=np.isfinite(y);ic=None
            if valid.sum()>=100:ic=metric(pred[valid],y[valid],[int(valid.sum())])
            sections.append({'time':int(d.p.times[t]),'slot':int(d.p.slots[t]),'rank_ic':ic,'visible_count':int(mask.sum())})
        np.savez_compressed(run/(split+'_scores.npz'),indices=indices,times=d.p.times[indices],scores=scores,trained_heads=np.array([3]))
        pd.DataFrame(sections).to_csv(run/(split+'_sections.csv'),index=False)
    result['status']='complete';(run/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','train']);parser.add_argument('--seed',type=int,choices=SEEDS);a=parser.parse_args()
    if a.phase=='prepare':prepare()
    else:train(a.seed)

"""Daily second tower. Fixed protocol; validation selection, checkpoint resume."""
from pathlib import Path
import argparse, json, os, time, hashlib, random, signal
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from scipy.special import ndtri
import torch
from torch import nn

PANEL=Path(os.environ.get('DAILY_PANEL','outputs/gru_dualplatform_20261011/daily_panel'))
OUT=Path(os.environ.get('DAILY_OUTPUT','outputs/gru_dualplatform_20261011/B'))
CONFIG={'sequence_length':60,'hidden':128,'layers':2,'epochs':40,'patience':8,'learning_rate':3e-4,'weight_decay':1e-4,'batch_sections':8,'microbatch_sections':1,'min_stocks':100,'embargo':2,'selection':'validation mean daily 5-day RankIC','horizons':[1,2,3,5],'dtype':'float32','training_start':'2012-01-01','lgb_num_leaves':31,'lgb_learning_rate':.05,'lgb_rounds':500,'lgb_max_bin':63,'lgb_early_stopping':50}
STOP=False
def interrupted(*_):
    global STOP
    STOP=True

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''):h.update(b)
    return h.hexdigest()
def write(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False));tmp.replace(path)
def save(path,obj):
    tmp=Path(str(path)+'.tmp');torch.save(obj,tmp);tmp.replace(path)
def ranknormal(y):
    out=np.full_like(y,np.nan,dtype=np.float32)
    for h in range(4):
        ok=np.isfinite(y[:,h]);n=ok.sum()
        if n:out[ok,h]=ndtri((rankdata(y[ok,h])-.5)/n)
    return out
def corr(a,b,rank=False):
    ok=np.isfinite(a)&np.isfinite(b);a,b=a[ok],b[ok]
    if len(a)<100 or a.std()<1e-12 or b.std()<1e-12:return None
    if rank:a,b=rankdata(a),rankdata(b)
    return float(np.corrcoef(a,b)[0,1])

def eligible_training_targets(y):
    # Each microbatch is one date and the loss requires all four heads.
    # This condition is applied to training only, never prediction eligibility.
    return bool((np.isfinite(y).sum(axis=0)>=CONFIG['min_stocks']).all()
                and (np.nanstd(y,axis=0)>1e-12).all())

class Data:
    def __init__(self):
        self.meta=json.loads((PANEL/'meta.json').read_text());assert self.meta['mechanical_causality_checks_passed']
        for name in ['X','members','complete','labels','times','execution','label_ends','industry']:
            setattr(self,name,np.load(PANEL/(name+'.npy'),mmap_mode='r'))
        self.T,self.U,self.F=self.X.shape
        self.dates=pd.to_datetime(self.times,utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
        self.prefix=np.concatenate([np.zeros((1,self.U),dtype='int32'),np.cumsum(~self.complete,axis=0,dtype='int32')])
        self.masks={};self.targets={}
    def mask(self,t):
        if t not in self.masks:
            self.masks[t]=(self.members[t]&((self.prefix[t+1]-self.prefix[t-59])==0)) if t>=59 else np.zeros(self.U,dtype=bool)
        return self.masks[t]
    def split(self,year,which):
        bounds={'train':('2012-01-01',f'{year-1}-01-01'),'validation':(f'{year-1}-01-01',f'{year}-01-01'),'test':(f'{year}-01-01',f'{year+1}-01-01' if year<2026 else '2026-09-24')}
        start,end=bounds[which];ix=np.flatnonzero((self.dates>=start)&(self.dates<end))
        if which!='test':
            prior=np.unique(self.dates[self.dates<end]);cut=prior[-2];ns=pd.Timestamp(cut,tz='Asia/Shanghai').value
            ix=ix[(self.dates[ix]<cut)&np.all((self.label_ends[ix]>self.execution[ix,None])&(self.label_ends[ix]<ns),axis=1)]
        ix=np.asarray([t for t in ix if self.mask(int(t)).sum()>=100],dtype='int64')
        if which=='train':
            ix=np.asarray([t for t in ix if eligible_training_targets(np.asarray(self.labels[t,self.mask(int(t))]))],dtype='int64')
        return ix
    def batch(self,ts,device,targets=False):
        xs=[];inds=[];ys=[];counts=[]
        for t in ts:
            t=int(t);mask=self.mask(t);counts.append(int(mask.sum()))
            xs.append(np.asarray(self.X[t-59:t+1,mask]).transpose(1,0,2));inds.append(np.asarray(self.industry[t-59:t+1,mask]).T.astype('int64'))
            if targets:
                if t not in self.targets:self.targets[t]=ranknormal(np.asarray(self.labels[t,mask]))
                ys.append(self.targets[t])
        make=lambda a:torch.from_numpy(np.concatenate(a).copy()).to(device)
        return (make(xs),make(inds)),make(ys) if targets else None,counts

class DailyGRU(nn.Module):
    def __init__(self):
        super().__init__();self.industry=nn.Embedding(1,8)
        self.daily=nn.Sequential(nn.Linear(32,32),nn.GELU())
        self.factors=nn.Sequential(nn.Linear(128,64),nn.GELU())
        self.fund=nn.Sequential(nn.Linear(44,16),nn.GELU())
        self.project=nn.Sequential(nn.LayerNorm(112),nn.Linear(112,64),nn.GELU(),nn.Dropout(.1))
        self.gru=nn.GRU(64,128,2,batch_first=True,dropout=.15)
        self.head=nn.Sequential(nn.LayerNorm(128),nn.Linear(128,64),nn.GELU(),nn.Dropout(.2),nn.Linear(64,4))
    def forward(self,x,industry):
        missing=~torch.isfinite(x);v=torch.where(missing,0.,x);m=missing.float()
        d=self.daily(torch.cat([v[:,:,:16],m[:,:,:16]],-1))
        f=self.factors(torch.cat([v[:,:,16:80],m[:,:,16:80]],-1))
        a=self.fund(torch.cat([v[:,:,80:],m[:,:,80:],self.industry(industry)],-1))
        z=self.project(torch.cat([d,f,a],-1));_,h=self.gru(z);return self.head(h[-1])

def loss_fn(score,y,counts):
    heads=[]
    for h in range(4):
        terms=[];o=0
        for n in counts:
            a,b=score[o:o+n,h],y[o:o+n,h];o+=n;ok=torch.isfinite(b);a,b=a[ok],b[ok]
            if len(b)<100 or b.var(unbiased=False)<=1e-12:continue
            a,b=a-a.mean(),b-b.mean();terms.append(1-(a*b).mean()/(a.square().mean().clamp_min(1e-12)*b.square().mean()).sqrt())
        if not terms:raise ValueError('No target-specific eligible loss')
        heads.append(torch.stack(terms).mean())
    return torch.stack(heads).mean()

def evaluate(model,data,ix,path=None):
    model.eval();rows=[];scores=[]
    with torch.inference_mode():
        for t in ix:
            batch,_,counts=data.batch([t],'cuda');s=model(*batch).cpu().numpy();y=np.asarray(data.labels[t,data.mask(t)])
            row={'date':data.dates[t],'time':int(data.times[t]),'visible_count':counts[0]}
            for j,h in enumerate(CONFIG['horizons']):
                row['ic_'+str(h)]=corr(s[:,j],y[:,j]);row['rank_ic_'+str(h)]=corr(s[:,j],y[:,j],True)
            rows.append(row)
            if path:
                a=np.full((data.U,4),np.nan,dtype='float32');a[data.mask(t)]=s;scores.append(a)
    if path:np.savez_compressed(path,scores=np.asarray(scores),indices=ix,times=data.times[ix])
    means=pd.DataFrame(rows).filter(regex='^ic_|^rank_ic_').mean().to_dict()
    assert all(np.isfinite(x) for x in means.values())
    return means,rows

def train(year,seed,pilot=False):
    torch.set_num_threads(4);assert torch.cuda.is_available()
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    data=Data();tr=data.split(year,'train');va=data.split(year,'validation');te=data.split(year,'test')
    run=OUT/('pilot' if pilot else 'GRU')/f'T{year}'/f'seed{seed}';run.mkdir(parents=True,exist_ok=True)
    sig={'config':CONFIG,'panel':data.meta['data_fingerprint'],'source':sha(__file__),'year':year,'seed':seed,'pilot':pilot}
    if (run/'export.json').exists():assert json.loads((run/'export.json').read_text())['signature']==sig;return
    random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)
    model=DailyGRU().cuda();opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4)
    scheduler=torch.optim.lr_scheduler.ReduceLROnPlateau(opt,mode='max',factor=.5,patience=2,threshold=0.,threshold_mode='abs',min_lr=1e-5)
    start=0;best=-float('inf');bad=0;best_epoch=-1
    if (run/'last.pt').exists():
        ck=torch.load(run/'last.pt',map_location='cuda',weights_only=False);assert ck['signature']==sig
        model.load_state_dict(ck['model']);opt.load_state_dict(ck['optimizer']);scheduler.load_state_dict(ck['scheduler']);start=ck['epoch']+1;best=ck['best'];bad=ck['bad'];best_epoch=ck['best_epoch']
        torch.set_rng_state(ck['torch_rng'].cpu());torch.cuda.set_rng_state_all([x.cpu() for x in ck['cuda_rng']]);random.setstate(ck['python_rng']);np.random.set_state(ck['numpy_rng'])
    write(run/'config.json',sig);print('START',year,seed,len(tr),len(va),len(te),torch.cuda.get_device_name(),flush=True)
    for epoch in range(start,1 if pilot else 40):
        if bad>=8:break
        tick=time.monotonic();model.train();losses=[];order=np.random.default_rng(seed+10000*epoch).permutation(tr)
        for k in range(0,len(order),8):
            ts=order[k:k+8];opt.zero_grad(set_to_none=True)
            for t in ts:
                batch,y,counts=data.batch([t],'cuda',True);loss=loss_fn(model(*batch),y,counts)
                (loss/len(ts)).backward();losses.append(float(loss.detach()))
            nn.utils.clip_grad_norm_(model.parameters(),1.);opt.step()
            if k % 200 == 0:
                print(json.dumps({'training_progress': min(k+len(ts),len(order)),
                                  'training_sections': len(order), 'epoch': epoch+1,
                                  'seconds': time.monotonic()-tick}),flush=True)
        metrics,rows=evaluate(model,data,va);metric=metrics['rank_ic_5'];scheduler.step(metric)
        if metric>best:best=metric;best_epoch=epoch+1;bad=0;save(run/'best.pt',{'signature':sig,'model':model.state_dict()})
        else:bad+=1
        save(run/'last.pt',{'signature':sig,'model':model.state_dict(),'optimizer':opt.state_dict(),'scheduler':scheduler.state_dict(),'epoch':epoch,'best':best,'bad':bad,'best_epoch':best_epoch,'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state_all(),'python_rng':random.getstate(),'numpy_rng':np.random.get_state()})
        event={'epoch':epoch+1,'seconds':time.monotonic()-tick,'validation':metrics,'best_epoch':best_epoch,'train_loss':float(np.mean(losses))}
        with (run/'epochs.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
        print(json.dumps(event),flush=True)
        if STOP:return
    if pilot:write(run/'pilot.json',{'signature':sig,'training_sections':len(tr),'validation_sections':len(va),'gpu':torch.cuda.get_device_name()});return
    ck=torch.load(run/'best.pt',map_location='cuda',weights_only=False);model.load_state_dict(ck['model'])
    validation,rows=evaluate(model,data,va);pd.DataFrame(rows).to_csv(run/'validation_sections.csv',index=False)
    test,rows=evaluate(model,data,te,run/'test_scores.npz');pd.DataFrame(rows).to_csv(run/'test_sections.csv',index=False)
    write(run/'export.json',{'signature':sig,'status':'complete','best_epoch':best_epoch,'checkpoint_sha256':sha(run/'best.pt'),'validation':validation,'test':test,'limitations':data.meta['limitations']})

def test():
    torch.set_num_threads(2);torch.manual_seed(13);m=DailyGRU().eval()
    x=torch.randn(8,60,98);x[0,:,81:]=float('nan');ind=torch.zeros(8,60,dtype=torch.long)
    with torch.no_grad():
        y=m(x,ind);changed=x.clone();changed[4:]*=100;z=m(changed,ind)
        assert torch.equal(y[:4],z[:4]),'cross-stock leakage'
        assert torch.isfinite(y).all(),'missing features unsafe'
    labels=np.random.default_rng(1).normal(size=(130,4)).astype('float32');labels[0,0]=np.nan
    assert np.isnan(ranknormal(labels)[0,0]) and np.isfinite(ranknormal(labels)[1:,0]).all()
    assert eligible_training_targets(labels)
    unavailable=labels.copy();unavailable[:31,2]=np.nan
    assert not eligible_training_targets(unavailable), 'an unavailable head cannot enter a one-date loss'
    m.train();loss_fn(m(torch.randn(130,60,98),torch.zeros(130,60,dtype=torch.long)),torch.tensor(ranknormal(labels)),[130]).backward()
    assert all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)
    print('PASS: daily GRU missingness, stock isolation, target masks and finite gradients')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['train','pilot','test']);ap.add_argument('--year',type=int,default=2026);ap.add_argument('--seed',type=int,default=11);a=ap.parse_args()
    test() if a.command=='test' else train(a.year,a.seed,a.command=='pilot')

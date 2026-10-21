"""Frozen protocol: PIT industry, Gaussian ranks, four heads, 14:00 RankIC.

Control and market-attention models share data/loss/selection/initial backbone.
No labeled eligibility is used for attention keys, queries, or inference.
"""
from pathlib import Path
import argparse,hashlib,json,os,time
import numpy as np,pandas as pd
from scipy.special import ndtri
from scipy.stats import rankdata
import torch
from torch import nn
from .train import PanelDataset,seed_everything,adamw_groups,checkpoint_save,checkpoint_load,rng_state,restore_rng
P=Path(__file__).resolve().parent;ROOT=P.parent;OUT=ROOT/'outputs'/P.name
DATA=Path(os.environ.get('GRU_DATA','/data/users/s1155284034/gru_hourly_20261001/dataset'))
CONFIG={'sequence_length':80,'hidden':128,'gru_layers':2,'attention_layers':1,'attention_heads':4,'attention_ffn':512,'attention_dropout':.2,'film_hidden':32,'heads':4,'output_head':'y4','training_loss':'equal per-head mean Pearson loss on inverse-normal average ranks','label_transform':'ndtri((average_rank - 0.5) / n); separate finite mask per target','batch_cross_sections':8,'microbatch_cross_sections':2,'epochs':40,'patience':8,'lr_patience':3,'learning_rate':3e-4,'min_learning_rate':1e-5,'weight_decay':1e-4,'gradient_clip':1.,'min_stocks':100,'embargo_trading_days':2,'label_variance_floor':1e-12,'seeds':[11,22,33,44,55],'training':'2019-2023','validation':'2024 only','checkpoint_selection':'2024 slot14 y4 RankIC','historical_test':'2025/2026 already exposed; never used for checkpoint selection','dtype':'float32','experiment_comparison':'control versus attention+FiLM bundle; all other new training/input choices identical','retained_upstream_limitation':'Original feature panel retained; historical day-quality anomaly trigger count zero, latent code issue remains. No live certification.'}
def sha(p):
 with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False));tmp.replace(p)
def corr(a,b):
 ok=np.isfinite(a)&np.isfinite(b);a=a[ok];b=b[ok]
 if len(a)<CONFIG['min_stocks'] or np.std(a)<1e-12 or np.std(b)<1e-12:return None
 return float(np.corrcoef(a,b)[0,1])
def rankcorr(a,b):
 ok=np.isfinite(a)&np.isfinite(b)
 return corr(rankdata(a[ok]),rankdata(b[ok]))
def ranknormal(y):
 out=np.full_like(y,np.nan,dtype=np.float32)
 for h in range(4):
  valid=np.isfinite(y[:,h]);n=valid.sum()
  if n:out[valid,h]=ndtri((rankdata(y[valid,h],method='average')-.5)/n)
 return out
class MarketGRU(nn.Module):
 def __init__(self,groups,n_industries,variant):
  super().__init__()
  from .model import PlanGRU
  self.variant=variant;self.backbone=PlanGRU(groups,n_industries,'full');self.backbone.head=nn.Identity()
  self.attention=nn.TransformerEncoderLayer(128,4,dim_feedforward=512,dropout=.2,batch_first=True,norm_first=True,activation='gelu')
  self.film=nn.Sequential(nn.Linear(12,32),nn.GELU(),nn.Linear(32,256))
  self.head=nn.Sequential(nn.LayerNorm(128),nn.Linear(128,64),nn.GELU(),nn.Dropout(.2),nn.Linear(64,4))
 def forward(self,x,industry,slots,market,counts):
  z=self.backbone(x,industry,slots);out=[];start=0
  assert len(market)==len(counts) and sum(counts)==len(z)
  for i,n in enumerate(counts):
   h=z[start:start+n];start+=n
   if self.variant=='market_attention':
    # Separate attention call per timestamp: future dates cannot enter keys.
    h=self.attention(h.unsqueeze(0)).squeeze(0)
    m=market[i];missing=~torch.isfinite(m);m=torch.cat([torch.where(missing,0.,m),missing.float()])
    gamma,beta=self.film(m).chunk(2);h=h*(1+.1*torch.tanh(gamma))+.1*beta
   out.append(self.head(h))
  return torch.cat(out)
class Data:
 def __init__(self,fit=False):
  self.panel=PanelDataset(DATA);self.p=self.panel
  self.context_meta=json.loads((P/'context_manifest.json').read_text());assert self.context_meta['dataset_fingerprint']==self.p.fingerprint
  assert sha(P/'context.npz')==self.context_meta['context_sha256']
  q=np.load(P/'context.npz');assert np.array_equal(q['times'],self.p.times)
  self.industry=q['industry'];self.market=q['market'];self.n_industries=self.context_meta['n_industries'];self.fit=fit
  self.masks={};self.ranks={}
  # Forward participants are independent of every future target's availability.
  self.visible=np.flatnonzero((np.arange(self.p.T)>=79)&(self.p.members.sum(axis=1)>=100))
 def mask(self,t):
  if t not in self.masks:self.masks[t]=self.p.eligible_mask(t,80,False)
  return self.masks[t]
 def split(self,which):
  bounds={'train':('2019-01-01','2024-01-01'),'validation':('2024-01-01','2025-01-01'),'test_2025':('2025-01-01','2026-01-01'),'recent_2026':('2026-01-01','2026-09-24')};start,end=bounds[which]
  dates=pd.to_datetime(self.p.times,utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy();ix=np.flatnonzero((dates>=start)&(dates<end))
  if which in ('train','validation'):
   prior=np.unique(dates[dates<end]);cut=prior[-CONFIG['embargo_trading_days']];cutns=pd.Timestamp(cut,tz='Asia/Shanghai').value
   ix=ix[(dates[ix]<cut)&np.all((self.p.label_end_times[ix]>self.p.earliest_execution_times[ix,None])&(self.p.label_end_times[ix]<cutns),axis=1)]
  return np.array([t for t in ix if self.mask(int(t)).sum()>=100],dtype=np.int64)
 def batch(self,ts,device,targets=False):
  xs=[];inds=[];slots=[];ys=[];counts=[]
  for t in ts:
   t=int(t)
   if self.fit:assert self.p.times[t]<pd.Timestamp('2025-01-01',tz='Asia/Shanghai').value
   mask=self.mask(t);n=int(mask.sum());counts.append(n);a=t-79
   xs.append(np.asarray(self.p.X[a:t+1,mask]).transpose(1,0,2));inds.append(self.industry[a:t+1,mask].T.astype(np.int64));slots.append(np.broadcast_to(self.p.slots[a:t+1],(n,80)))
   if targets:
    if t not in self.ranks:self.ranks[t]=ranknormal(np.asarray(self.p.labels[t,mask]))
    ys.append(self.ranks[t])
  make=lambda a:torch.from_numpy(np.array(np.concatenate(a),copy=True)).to(device)
  x,ind,slot=map(make,(xs,inds,slots));market=torch.from_numpy(np.array(self.market[ts],copy=True)).to(device)
  return (x,ind,slot,market,counts),make(ys) if targets else None

def loss_fn(score,y,counts,min_stocks=100):
 heads=[]
 for h in range(4):
  terms=[];offset=0
  for n in counts:
   a,b=score[offset:offset+n,h],y[offset:offset+n,h];offset+=n;valid=torch.isfinite(b);a,b=a[valid],b[valid]
   if len(a)<min_stocks or b.var(unbiased=False)<=1e-12:continue
   a=a-a.mean();b=b-b.mean();terms.append(1-(a*b).mean()/(a.square().mean().clamp_min(1e-12)*b.square().mean()).sqrt())
  if not terms:raise ValueError('No eligible target-specific loss')
  heads.append(torch.stack(terms).mean())
 return torch.stack(heads).mean()

def evaluate(model,data,indices,device,save_predictions=None):
 model.eval();records=[];allscore=[]
 with torch.inference_mode():
  for st in range(0,len(indices),2):
   ts=indices[st:st+2];batch,_=data.batch(ts,device);scores=model(*batch).cpu().numpy();offset=0
   for t,n in zip(ts,batch[-1]):
    s=scores[offset:offset+n];offset+=n;mask=data.mask(int(t));y=np.asarray(data.p.labels[t,mask,3]);pred=s[:,3]
    # Labels enter metrics after the model forward, never its attention population.
    records.append({'time':int(data.p.times[t]),'date':str(pd.Timestamp(data.p.times[t],tz='UTC').tz_convert('Asia/Shanghai').date()),'slot':int(data.p.slots[t]),'ic':corr(pred,y),'rank_ic':rankcorr(pred,y),'visible_count':int(n),'scored_count':int(np.isfinite(y).sum())})
    if save_predictions is not None:
     a=np.full((data.p.U,4),np.nan,dtype=np.float32);a[mask]=s;allscore.append(a)
 df=pd.DataFrame(records)
 if save_predictions is not None:np.savez_compressed(save_predictions,scores=np.array(allscore),indices=indices,times=data.p.times[indices])
 summary={'overall':df[['ic','rank_ic']].mean().to_dict(),'slot':{str(k):g[['ic','rank_ic']].mean().to_dict() for k,g in df.groupby('slot')},'quarter':{str(k):g[['ic','rank_ic']].mean().to_dict() for k,g in df.groupby(pd.to_datetime(df.date).dt.to_period('Q'))}}
 return summary,records

def signature(data,variant,seed):return {'config':CONFIG,'variant':variant,'seed':seed,'dataset':data.p.fingerprint,'context':data.context_meta['context_sha256'],'source_sha256':sha(__file__),'model_source_sha256':sha(P/'model.py')}
def train(variant,seed):
 torch.set_num_threads(4);assert torch.cuda.is_available();device='cuda';data=Data(fit=True);run=OUT/f'{variant}_seed{seed}';run.mkdir(parents=True,exist_ok=True)
 sig=signature(data,variant,seed)
 if (run/'result.json').exists():assert json.loads((run/'result.json').read_text())['signature']==sig;return
 seed_everything(seed);model=MarketGRU(data.p.groups,data.n_industries,variant).to(device)
 opt=torch.optim.AdamW(adamw_groups(model,CONFIG['weight_decay']),lr=CONFIG['learning_rate']);sched=torch.optim.lr_scheduler.ReduceLROnPlateau(opt,mode='max',factor=.5,patience=2,threshold=0.,threshold_mode='abs',min_lr=1e-5)
 tr=data.split('train');va=data.split('validation');va14=va[data.p.slots[va]==2]
 start=0;best=-float('inf');best_epoch=-1;bad=0
 if (run/'last.pt').exists():
  ck=checkpoint_load(run/'last.pt',device);assert ck['signature']==sig;model.load_state_dict(ck['model']);opt.load_state_dict(ck['optimizer']);sched.load_state_dict(ck['scheduler']);restore_rng(ck['rng']);start=ck['epoch']+1;best=ck['best'];best_epoch=ck['best_epoch'];bad=ck['bad']
 write(run/'config.json',sig)
 print(json.dumps({'event':'start','variant':variant,'seed':seed,'train_sections':len(tr),'validation14_sections':len(va14),'parameters':sum(p.numel() for p in model.parameters()),'gpu':torch.cuda.get_device_name(),'resume_epoch':start}),flush=True)
 last_epoch=start-1
 for epoch in range(start,CONFIG['epochs']):
  if bad>=CONFIG['patience']:break
  started=time.monotonic();model.train();vals=[]
  batches=list(data.p.batches(tr,8,np.random.default_rng(seed+10000*epoch)))
  for k,ts in enumerate(batches):
   opt.zero_grad(set_to_none=True)
   for j in range(0,len(ts),2):
    sub=ts[j:j+2];batch,labels=data.batch(sub,device,targets=True);loss=loss_fn(model(*batch),labels,batch[-1]);assert torch.isfinite(loss);(loss*len(sub)/len(ts)).backward();vals.append(float(loss.detach()))
   norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.);assert torch.isfinite(norm);opt.step()
   if k and k%200==0:print(json.dumps({'event':'batch','epoch':epoch+1,'batch':k,'total':len(batches),'seconds':round(time.monotonic()-started,1)}),flush=True)
  summary,records=evaluate(model,data,va14,device);metric=summary['overall']['rank_ic'];assert np.isfinite(metric)
  improved=metric>best
  if improved:best=metric;best_epoch=epoch+1;bad=0
  else:bad+=1
  sched.step(metric)
  state={'signature':sig,'model':model.state_dict(),'optimizer':opt.state_dict(),'scheduler':sched.state_dict(),'rng':rng_state(),'epoch':epoch,'best':best,'best_epoch':best_epoch,'bad':bad}
  if improved:checkpoint_save(run/'best.pt',state);write(run/'best_validation14.json',summary)
  checkpoint_save(run/'last.pt',state)
  event={'event':'epoch','epoch':epoch+1,'train_loss':float(np.mean(vals)),'validation14_rank_ic':metric,'validation14_ic':summary['overall']['ic'],'best_rank_ic':best,'best_epoch':best_epoch,'bad_epochs':bad,'lr':opt.param_groups[0]['lr'],'seconds':time.monotonic()-started}
  with (run/'epochs.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
  print(json.dumps(event),flush=True);last_epoch=epoch
 # Full validation timing breakdown only after selection is frozen.
 ck=checkpoint_load(run/'best.pt',device);model.load_state_dict(ck['model']);summary,records=evaluate(model,data,va,device)
 write(run/'validation.json',summary);pd.DataFrame(records).to_csv(run/'validation_sections.csv',index=False)
 write(run/'result.json',{'status':'complete','signature':sig,'best_epoch':best_epoch,'epochs_completed':last_epoch+1,'best_validation14_rank_ic':best,'best_checkpoint_sha256':sha(run/'best.pt'),'validation':summary,'historical_test_used_for_selection':False})
 print('TRAINING COMPLETE',variant,seed,flush=True)
def export(variant,seed):
 torch.set_num_threads(4);assert torch.cuda.is_available();data=Data();run=OUT/f'{variant}_seed{seed}';r=json.loads((run/'result.json').read_text());assert r['signature']==signature(data,variant,seed);assert sha(run/'best.pt')==r['best_checkpoint_sha256']
 model=MarketGRU(data.p.groups,data.n_industries,variant).cuda();model.load_state_dict(checkpoint_load(run/'best.pt','cuda')['model'])
 for split in ['test_2025','recent_2026']:
  ix=data.split(split);summary,records=evaluate(model,data,ix,'cuda',run/f'{split}_scores.npz');write(run/f'{split}_metrics.json',summary);pd.DataFrame(records).to_csv(run/f'{split}_sections.csv',index=False)
 write(run/'export.json',{'status':'complete','checkpoint_sha256':sha(run/'best.pt'),'prediction_eligibility_uses_labels':False,'historical_tests_already_exposed':True})
 print('EXPORT COMPLETE',variant,seed,flush=True)
def test():
 torch.set_num_threads(4);seed_everything(123);groups={'hour':list(range(24)),'daily':list(range(24,40)),'factors':list(range(40,104)),'fund':list(range(104,120)),'ages':[120,121]}
 m=MarketGRU(groups,32,'market_attention').eval();x=torch.randn(40,80,122);ind=torch.zeros(40,80,dtype=torch.long);slots=torch.arange(80).remainder(4).repeat(40,1);market=torch.randn(2,6)
 with torch.no_grad():
  y=m(x,ind,slots,market,[20,20]);per=torch.randperm(20);xp=x.clone();xp[:20]=x[per];yp=m(xp,ind,slots,market,[20,20]);assert torch.allclose(yp[:20],y[per],atol=2e-6)
  changed=x.clone();changed[20:]*=100;ym=m(changed,ind,slots,market,[20,20]);assert torch.equal(ym[:20],y[:20]),'cross-date attention leakage'
 yraw=np.random.default_rng(1).normal(size=(40,4)).astype('float32');yraw[0,0]=np.nan;targets=torch.tensor(ranknormal(yraw));m.train();loss=loss_fn(m(x,ind,slots,market,[20,20]),targets,[20,20],10);loss.backward();assert all(torch.isfinite(p.grad).all() for p in m.parameters() if p.grad is not None)
 tied=ranknormal(np.tile(np.array([1,1,2,3,4],dtype='float32')[:,None],(1,4)));assert tied[0,0]==tied[1,0] and np.isfinite(tied).all()
 # A missing label of head1 cannot exclude that stock from head4's target transform.
 assert np.allclose(ranknormal(yraw)[:,3],ranknormal(np.nan_to_num(yraw))[:,3])
 write(OUT/'tests.json',{'status':'passed','checks':['stock permutation equivariance','cross-date context isolation','finite four-head backward with target-specific missingness','Gaussian ranks preserve ties','unrelated target missingness does not alter y4 labels'],'code_sha256':sha(__file__)})
 print('SYNTHETIC TESTS PASSED',flush=True)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['test','train','export']);ap.add_argument('--variant',choices=['control','market_attention']);ap.add_argument('--seed',type=int);a=ap.parse_args()
 if a.command=='test':test()
 elif a.command=='train':train(a.variant,a.seed)
 else:export(a.variant,a.seed)

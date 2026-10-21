"""Automatic matched experiment scorecard after all ten models finish."""
from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd
from . import ledger as L
from .experiment import OUT,P,DATA,CONFIG,sha,write

def order_targets(score,units,marks,nav,buffer=0,n=100):
 ids=np.flatnonzero(np.isfinite(score)&np.isfinite(marks)&(marks>0));order=ids[np.argsort(-score[ids],kind='stable')]
 if not len(order):return units.copy(),np.empty(0,int)
 if buffer:
  keep=[int(u) for u in order[:int(np.ceil(.5*len(order)))] if units[u]>1e-10][:n];have=set(keep)
  chosen=np.array(keep+[int(u) for u in order[:int(np.ceil(.2*len(order))) ] if u not in have][:n-len(keep)],int)
 else:chosen=order[:n]
 desired=np.zeros(len(units))
 if len(chosen):desired[chosen]=min(.98/len(chosen),.09)*nav/marks[chosen]
 delta=desired-units;delta[np.abs(delta)*marks<1000]=0
 return units+delta,chosen

def bootstrap_pair(a,b):
 # Paired moving-block daily-return difference; does not reselect models.
 diff=a-b;n=len(diff);rng=np.random.default_rng(20261007);est=[]
 for _ in range(2000):
  starts=rng.integers(0,max(n-20+1,1),size=int(np.ceil(n/20)));sample=np.concatenate([np.arange(k,min(k+20,n)) for k in starts])[:n];est.append(float(diff[sample].mean()*252))
 return {'metric':'annualized arithmetic daily return difference; not terminal compounded excess','mean':float(diff.mean()*252),'ci95':np.quantile(est,[.025,.975]).tolist(),'block_days':20,'replications':2000}

def main():
 R=OUT/'replay14_fee2_allin';R.mkdir(parents=True,exist_ok=True)
 L.targets=order_targets;L.PROTOCOL['costs'].update(commission_per_side=.0002,commission_minimum_cny=0,stamp_duty_sell_only=0,commission_treatment='user-confirmed simulation all-in 2bp each side, no minimum and no other charges',main_slippage_bps_per_side=0,sensitivity_slippage_bps_per_side=[]);L.PROTOCOL['account']='2,000,000 CNY; independent calendar-year starts; 14:00 signal daily only; T+1'
 L.PROTOCOL['primary_strategy']='14:00 Top100, next 14:01-14:05 mean minute-open execution proxy';L.PROTOCOL['fixed_robustness_strategy']='Top20% enter / outside Top50% exit; up to 100 ranked retained holdings; target98%'
 L.PROTOCOL['limitations']+=['No benchmark industry weights supplied: industry-neutral allocation is NOT implemented or certified.','Provider historical S-code industry embeddings are used, not a certified revision-consistent current taxonomy.']
 write(R/'protocol.json',L.PROTOCOL)
 load=lambda n:np.load(DATA/(n+'.npy'),mmap_mode='r')
 times=load('times');ex=load('earliest_execution_times');members=load('members');slots=((times+8*3600*10**9)//(3600*10**9))%24
 symbols=json.loads((DATA/'symbols.json').read_text());bm=ROOT/'official_csi500_price_2025_2026.csv';assert sha(bm)=='69bc57c78183c4d22978d4213d71a711057e2381836311c289c268aa136e1b20';bench=pd.read_csv(bm).set_index('date').close
 fulltimes=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai');closes={str(fulltimes[i].date()):np.asarray(load('hour_ohlcv')[i,:,3],float) for i in range(3,len(times),4)}
 summaries=[];metricrows=[];dailies={};receipts={}
 for year,split in [(2025,'test_2025'),(2026,'recent_2026')]:
  models={};reference=None
  for variant in ['control','market_attention']:
   z=[]
   for seed in CONFIG['seeds']:
    run=OUT/f'{variant}_seed{seed}';r=json.loads((run/'result.json').read_text());assert json.loads((run/'export.json').read_text())['status']=='complete';assert sha(run/'best.pt')==r['best_checkpoint_sha256'];receipts[run.name]=r['best_checkpoint_sha256']
    a=np.load(run/f'{split}_scores.npz');ix=a['indices'];assert np.array_equal(a['times'],times[ix]);keep=slots[ix]==14;indices=ix[keep];score=a['scores'][keep,:,3]
    if reference is None:reference=indices
    else:assert np.array_equal(reference,indices)
    assert np.all(ex[indices]>times[indices]);models[f'{variant}_seed{seed}']=score
    std=np.nanstd(score,axis=1,keepdims=True);assert np.all(std>1e-10);z.append((score-np.nanmean(score,axis=1,keepdims=True))/std)
    for period in ['validation',split]:
     f=run/(period+'_sections.csv');t=pd.read_csv(f)
     for slot,g in t.groupby('slot'):metricrows.append({'variant':variant,'seed':seed,'period':period,'slot':int(slot),'ic':float(g.ic.mean()),'rank_ic':float(g.rank_ic.mean())})
   models[variant+'_ensemble']=np.mean(z,axis=0)
  ix=reference;prices=np.asarray(load('execution_prices')[ix],float);raw=load('execution_raw_prices')[ix];fill=load('execution_complete')[ix]&load('execution_tradable')[ix]&~load('execution_all_flat')[ix]&np.isfinite(raw)&(raw>0);signal=np.asarray(load('hour_ohlcv')[ix,:,3],float);days=[str(t.date()) for t in pd.to_datetime(ex[ix],utc=True).tz_convert('Asia/Shanghai')];base=bench.loc[bench.index<days[0]].iloc[-1]
  for model,score in models.items():
   for strategy,buffer in [('Top100',0),('Buffer20_50',1)]:
    cases=[('gross',0),('net',0)] if model.endswith('ensemble') else [('net',0)]
    for cost,slip in cases:
     detail=model.endswith('ensemble') and cost=='net' and slip==0
     d,e,tr=L.simulate(prices,fill,score,signal,days,closes,slip,buffer,cost=='gross',detail,n=100,membership=np.asarray(members[ix]))
     bn=bench.loc[d.date].to_numpy()/base;d['benchmark_nav']=bn;d['relative_nav']=d.nav/bn
     tag=f'{year}_{model}_{strategy}_{cost}_slip{slip}';d.to_csv(R/(tag+'_daily.csv'),index=False)
     r={'year':year,'model':model,'strategy':strategy,'cost_case':cost,'slippage_bps':slip,**L.metrics(d,bn)}
     r['member_weight_not_above80_days_after_grace']=int(((d.member_nav_weight<=.8)&d.after_build_grace).sum());r['minimum_member_nav_weight_after_grace']=float(d.loc[d.after_build_grace,'member_nav_weight'].min())
     nr=d.nav.to_numpy()/np.r_[1,d.nav.to_numpy()[:-1]]-1;br=bn/np.r_[1,bn[:-1]]-1
     relative20=(pd.Series((1+nr)/(1+br)).rolling(20).apply(np.prod,raw=True)-1).dropna();r['worst20d_relative_return']=float(relative20.min());r['historical20d_relative_p10']=float(relative20.quantile(.1));r['historical20d_relative_p90']=float(relative20.quantile(.9));r['interval_note']='historical overlapping-window distribution, not calibrated prospective prediction interval'
     summaries.append(r)
     if detail:
      dailies[(model,year,strategy)]=(nr,br);e.to_csv(R/(tag+'_events.csv'),index=False)
      td=pd.DataFrame(tr,columns=['event','symbol_id','signal_order_units','bought_adjusted_units','sold_adjusted_units','mid_adjusted_price','fees_cny']);td['symbol']=[symbols[u] for u in td.symbol_id];td['signal_time']=[str(fulltimes[ix[j]]) for j in td.event];td['execution_time']=[str(pd.Timestamp(ex[ix[j]],tz='UTC').tz_convert('Asia/Shanghai')) for j in td.event];td.to_csv(R/(tag+'_trades.csv.gz'),index=False)
  print('REPLAY YEAR COMPLETE',year,flush=True)
 s=pd.DataFrame(summaries);s.to_csv(R/'summary.csv',index=False);pd.DataFrame(metricrows).drop_duplicates().to_csv(OUT/'slot_metrics.csv',index=False)
 pairs={}
 for y in [2025,2026]:
  for st in ['Top100','Buffer20_50']:
   a,br=dailies[('market_attention_ensemble',y,st)];b,_=dailies[('control_ensemble',y,st)];pairs[f'{y}_{st}']=bootstrap_pair(a,b)
 write(OUT/'paired_bootstrap.json',pairs)
 write(OUT/'completion.json',{'status':'complete','models':10,'checkpoint_hashes':receipts,'replay_cases':len(s),'report_source_sha256':sha(__file__),'ledger_source_sha256':sha(P/'ledger.py'),'train_source_sha256':sha(P/'experiment.py'),'selection_uses_2025_2026':False,'industry_neutral_portfolio_certified':False,'live_execution_certified':False})
 print(s[s.model.str.endswith('ensemble')&(s.cost_case=='net')&(s.slippage_bps==0)][['year','model','strategy','net_return','benchmark_return','excess_pp','max_drawdown']].to_string(index=False))
if __name__=='__main__':
 ROOT=P.parent;main()

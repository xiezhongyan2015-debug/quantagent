"""Five-seed residual-label versus raw-label control on the frozen price tape."""
import argparse,importlib.util,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from .attribution import ROOT,DATA,OUT,sha
from .replay import Targets,holding_metrics,risk_weights,rank,neutralize,test as target_tests

SEEDS=[11,22,33,44,55];DEST=OUT/'residual_paired'


def block_ci(difference,seed=808):
    a=np.asarray(difference,float);rng=np.random.default_rng(seed);n=len(a)
    assert a.ndim==2 and a.shape[1]==5 and np.isfinite(a).all()
    observed=float(np.mean(a.sum(axis=0))*252/n)
    starts=rng.integers(0,n,size=(2000,int(np.ceil(n/20))))
    positions=(starts[:,:,None]+np.arange(20))%n;positions=positions.reshape(2000,-1)[:,:n]
    samples=a[positions].mean(axis=(1,2))*252
    lo,hi=np.quantile(samples,[.025,.975])
    return {'annualized_mean_daily_return_difference':observed,'lower95':float(lo),'upper95':float(hi),
            'resampling':'2000 circular 20-session blocks, same dates across all five paired seeds','interval_crosses_zero':bool(lo<=0<=hi)}


def main(methods):
    DEST.mkdir(exist_ok=True);checks=target_tests()
    spec=importlib.util.spec_from_file_location('paired_frozen_ledger',ROOT/'gru_market_attention_20261007/ledger.py')
    ledger=importlib.util.module_from_spec(spec);spec.loader.exec_module(ledger);checks+=ledger.tests();ledger.CAPITAL=2_000_000
    load=lambda n:np.load(DATA/(n+'.npy'),mmap_mode='r')
    times=load('times');full=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai');execution=load('earliest_execution_times')
    allhour=load('hour_ohlcv');closes={str(full[t].date()):np.asarray(allhour[t,:,3],float) for t in range(3,len(times),4)}
    q=np.load(OUT/'risk_context.npz');assert np.array_equal(q['times'],times)
    bench=pd.read_csv(OUT/'benchmark_sources/H00905_daily.csv').set_index('date').close
    pricebench=pd.read_csv(OUT/'benchmark_sources/000905_daily.csv').set_index('date').close
    records=[];slots=[];sourceproof=[];parity=[];daily={}
    for year,split in [(2025,'test_2025'),(2026,'recent_2026')]:
        models={};reference=None
        for variant in ['control','residual']:
            standardized=[]
            for seed in SEEDS:
                source=OUT/'source_snapshot/outputs/gru_market_attention_20261007'/f'control_seed{seed}' if variant=='control' else ROOT/'outputs/gru_residual_label_20261008'/f'control_seed{seed}'
                p=source/(split+'_scores.npz');a=np.load(p);indices=a['indices'];scores=a['scores'];assert np.array_equal(a['times'],times[indices])
                sourceproof.append({'variant':variant,'seed':seed,'split':split,'sha256':sha(p)})
                keep=full[indices].hour==14;ix=indices[keep]
                if reference is None:reference=ix
                else:assert np.array_equal(reference,ix)
                s=scores[keep,:,3];models[f'{variant}_seed{seed}']=s
                standardized.append((s-np.nanmean(s,axis=1,keepdims=True))/np.nanstd(s,axis=1,keepdims=True))
                for slot in [10,11,14,15]:
                    chosen=full[indices].hour==slot;values=[]
                    for t,pred in zip(indices[chosen],scores[chosen,:,3]):
                        y=load('labels')[t,:,3];ok=np.isfinite(y)&np.isfinite(pred)
                        if ok.sum()>=100:values.append(np.corrcoef(rank(pred[ok]),rank(y[ok]))[0,1])
                    slots.append({'year':year,'variant':variant,'seed':seed,'slot':slot,'rank_ic':float(np.mean(values)),'sections':len(values)})
            models[variant+'_ensemble']=np.mean(standardized,axis=0)
        ix=reference;membership=np.asarray(load('members')[ix]);risk=risk_weights(ix,q,membership)
        signal=np.asarray(allhour[ix,:,3],float);prices=np.asarray(load('execution_prices')[ix],float);raw=load('execution_raw_prices')[ix]
        fill=load('execution_complete')[ix]&load('execution_tradable')[ix]&~load('execution_all_flat')[ix]&np.isfinite(raw)&(raw>0)
        tradable=np.asarray(load('hour_complete')[ix])&np.isfinite(signal)&(signal>0)&(allhour[ix,:,4]>0)
        days=[str(t.date()) for t in pd.to_datetime(execution[ix],utc=True).tz_convert('Asia/Shanghai')]
        assert np.all(execution[ix]>times[ix]);bnav=bench.loc[days].to_numpy()/bench.loc[bench.index<days[0]].iloc[-1]
        pnav=pricebench.loc[days].to_numpy()/pricebench.loc[pricebench.index<days[0]].iloc[-1]
        for method in methods:
            folder=DEST/method;folder.mkdir(exist_ok=True)
            for model,scores in models.items():
                for strategy,buffer in [('Top100',0),('Buffer20_50',1)]:
                    for cost in (['gross','net'] if model.endswith('ensemble') else ['net']):
                        targets=Targets(method,risk,membership,tradable);ledger.targets=targets
                        d,e,tr=ledger.simulate(prices,fill,scores,signal,days,closes,0,buffer,cost=='gross',True,100,membership)
                        holding_metrics(d,tr,risk,signal,prices,closes,days);d['benchmark_nav']=bnav;d['price_benchmark_nav']=pnav
                        tag=f'{year}_{model}_{strategy}_{cost}';d.to_csv(folder/(tag+'_daily.csv'),index=False);e.to_csv(folder/(tag+'_events.csv'),index=False)
                        if method=='baseline' and model.startswith('control_'):
                            old=pd.read_csv(ROOT/'训练回测证据与复核/evidence/replay14_fee2_allin'/(tag+'_slip0_daily.csv'))
                            error=float(np.max(abs(old.nav.to_numpy()-d.nav.to_numpy())));assert error<1e-10;parity.append({'case':tag,'max_nav_error':error})
                        if cost=='net':daily[(year,method,model,strategy)]=d
                        record={'year':year,'method':method,'model':model,'strategy':strategy,'cost_case':cost,**ledger.metrics(d,bnav)}
                        record['total_return_index_excess_pp']=float((d.nav.iloc[-1]-bnav[-1])*100)
                        record['price_index_excess_pp']=float((d.nav.iloc[-1]-pnav[-1])*100)
                        record['max_industry_deviation_proxy']=float(d.max_industry_deviation_proxy.max());after=d.after_build_grace
                        record['beta_pass']=.9<=record['beta']<=1.1
                        for name,column,op,value in [('industry_proxy','max_industry_deviation_proxy','le',.030001),('stock92','cash_weight','le',.080001),('member85','member_nav_weight','ge',.849999),('single5','max_stock_nav_weight','le',.050001)]:
                            record[name+'_pass']=bool(getattr(d.loc[after,column],op)(value).all())
                        record['all_exposure_gates_pass']=all(record[k] for k in ['beta_pass','industry_proxy_pass','stock92_pass','member85_pass','single5_pass'])
                        relative=d.nav.to_numpy()/np.r_[1,d.nav.to_numpy()[:-1]]/(bnav/np.r_[1,bnav[:-1]])
                        record['worst20d_relative_return']=float((pd.Series(relative).rolling(20).apply(np.prod,raw=True)-1).min())
                        record['constraint_fail_days']=sum(a['status']!='optimal' for a in targets.diagnostics)
                        record['sell_minimum_cny']=1000;record['fee_per_side']=.0002;records.append(record)
                        if targets.diagnostics:pd.DataFrame(targets.diagnostics).to_csv(folder/(tag+'_optimizer.csv'),index=False)
            pd.DataFrame(records).to_csv(DEST/'scorecard_running.csv',index=False);print('Paired replay completed',year,method,flush=True)
    pairs=[];bootstrap=[]
    for year in [2025,2026]:
        for method in methods:
            for strategy in ['Top100','Buffer20_50']:
                differences=[]
                for seed in SEEDS:
                    old=daily[(year,method,f'control_seed{seed}',strategy)];new=daily[(year,method,f'residual_seed{seed}',strategy)]
                    assert np.array_equal(old.date,new.date)
                    a=old.nav.to_numpy();b=new.nav.to_numpy();differences.append(b/np.r_[1,b[:-1]]-a/np.r_[1,a[:-1]])
                    pairs.append({'year':year,'method':method,'strategy':strategy,'seed':seed,'control_net_return':float(a[-1]-1),'residual_net_return':float(b[-1]-1),'residual_minus_control_pp':float((b[-1]-a[-1])*100)})
                bootstrap.append({'year':year,'method':method,'strategy':strategy,**block_ci(np.stack(differences,axis=1))})
    for name,value in [('scorecard',records),('seed_pairs',pairs),('block_bootstrap',bootstrap),('rank_ic_by_slot',slots),('control_baseline_parity',parity)]:pd.DataFrame(value).to_csv(DEST/(name+'_'+'_'.join(methods)+'.csv'),index=False)
    receipt={'status':'complete','methods':methods,'cases':len(records),'paired_seeds':SEEDS,'ledger_tests':checks,'source_code_sha256':sha(__file__),
        'source_scores':sourceproof,'fee_per_side':.0002,'independent_initial_capital_each_year':2000000,'frozen_input_panel_unchanged':True,
        'sell_minimum_cny_retained':1000,'original_224_replay_outputs_overwritten':False,'all_exposure_gate_pass_cases':sum(r['all_exposure_gates_pass'] for r in records)}
    (DEST/('completion_'+'_'.join(methods)+'.json')).write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt|{'source_scores':'see receipt'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--methods',nargs='+',choices=['baseline','neutral','bucket','constraint'],default=['baseline','neutral','bucket']);args=parser.parse_args();main(args.methods)

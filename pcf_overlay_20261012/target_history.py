"""Full historical target feasibility, strictly using signal-time information."""
import json
import numpy as np
import pandas as pd
from .data import ROOT,OUT,symbol,write

PROTOCOL={'version':'constructive_integer_targets_v1','capital':2_000_000.,'buy_fee':.0002,
    'minimum_stock_NAV':.90,'maximum_stock_plus_initial_buy_fee_NAV':.98,
    'minimum_member_NAV':.8001,'max_single_stock_NAV':.05,
    'prices':'PCF weights use published prior valuation-date raw close; share allocation uses signal-date raw close. No execution/closing future prices supplied.',
    'membership':'frozen CSI500 membership visible at signal-date 15:00; changes on execution day are separately unresolved',
    'algorithm':'nearest 100 shares with STAR 0-or-200 minimum; remove lots to respect cash cap; add member lots by marginal L1 error per NAV, funding by nonmember lot removals if necessary; then add minimum-error lots toward original cash weight',
    'scope':'P0-only target feasibility on every original execution date; not continuous-account returns, no P1 recomputation or live orders',
    'assumed_tradeable':'positive priced PCF stocks; actual ST, suspensions, price limits and position T+1 availability not certified',
    'failure':'report construction failure, never claim mathematically infeasible without a solver proof',
    'original_results_preserved':True}

def construct(price,weight,member,star,capital=2_000_000.):
    price,weight=np.asarray(price,float),np.asarray(weight,float)
    member,star=np.asarray(member,bool),np.asarray(star,bool)
    if not np.isfinite(price).all() or (price<=0).any() or not np.isfinite(weight).all():raise ValueError('Missing prices/weights')
    v=price*100/capital;cap=.98/(1+.0002);ub=np.floor(.05/v+1e-10).astype(int);ub[weight<=0]=0
    x=np.minimum(np.floor(weight/v+.5).astype(int),ub);x[star&(x==1)]=np.where(weight[star&(x==1)]>v[star&(x==1)],2,0)
    x=np.minimum(x,ub);x[star&(x==1)]=0
    def pick(side,mask,available=np.inf):
        steps=np.where((x==0)&star,2,1) if side==1 else np.where((x==2)&star,2,1)
        candidates=(x+steps<=ub)&(weight>0) if side==1 else x>=steps
        candidates&=mask
        if side==1:candidates&=v*steps<=available+1e-10
        cost=np.abs((x+side*steps)*v-weight)-np.abs(x*v-weight)
        ratio=np.where(candidates,cost/(v*steps),np.inf)
        if not np.isfinite(ratio).any():return None
        i=int(np.argmin(ratio));return i,int(steps[i])
    for _ in range(20000):
        total=float(x@v);mem=float(x@(v*member))
        if total>cap+1e-10:
            chosen=pick(-1,np.ones(len(x),bool))
            if chosen is None:break
            i,k=chosen;x[i]-=k;continue
        if mem<.8001-1e-10:
            chosen=pick(1,member,cap-total)
            if chosen is None:
                chosen=pick(-1,~member)
                if chosen is None:break
                i,k=chosen;x[i]-=k;continue
            i,k=chosen;x[i]+=k;continue
        goal=min(float(weight.sum()),cap)
        chosen=pick(1,np.ones(len(x),bool),cap-total)
        if chosen is None or total>=goal-1e-10:break
        i,k=chosen
        # Above minimum stock exposure, add only if security+cash L1 improves.
        before=abs(x[i]*v[i]-weight[i])+abs(total-weight.sum())
        after=abs((x[i]+k)*v[i]-weight[i])+abs(total+k*v[i]-weight.sum())
        if total>=.90 and after>=before-1e-12:break
        x[i]+=k
    total=float(x@v);mem=float(x@(v*member));feasible=total>=.90-1e-8 and total<=cap+1e-8 and mem>=.8001-1e-8
    feasible=feasible and bool((x<=ub).all() and (x>=0).all() and ((x[star]==0)|(x[star]>=2)).all())
    return x*100,dict(target_feasible=feasible,stock_NAV=total,member_NAV=mem,
        L1_target_error=float(np.abs(x*v-weight).sum()),max_stock_NAV=float((x*v).max()),holdings=int((x>0).sum()))

def main():
    dest=OUT/'integer_target_research';p=dest/'historical_preregistration.json'
    if p.exists():assert json.loads(p.read_text())==PROTOCOL
    else:write(p,PROTOCOL)
    coverage=pd.read_csv(OUT/'daily_input_coverage.csv');meta=pd.read_csv(OUT/'pcf_metadata.csv').set_index('date')
    components=pd.read_parquet(OUT/'pcf_components.parquet');groups={d:g[g.stock_replicable].copy() for d,g in components.groupby('date')}
    prices=pd.read_parquet(OUT/'daily_prices.parquet',columns=['date','symbol','close'])
    byday={d:g.set_index('symbol').close for d,g in prices.groupby('date')}
    times=np.load(ROOT/'gru_research/artifacts/dataset/times.npy');dates=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai')
    timebyday={str(t.date()):i for i,t in enumerate(dates) if t.hour==15}
    sy=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text());pos={s:i for i,s in enumerate(sy)}
    membership=np.load(ROOT/'gru_research/artifacts/dataset/members.npy',mmap_mode='r');rows=[];targets=[]
    for r in coverage.itertuples():
        day=r.pcf_date;g=groups[day];m=meta.loc[day];symbols=[symbol(c) for c in g.code]
        val=byday[m.valuation_date].reindex(symbols).to_numpy();current=byday[day].reindex(symbols).to_numpy()
        if not np.isfinite(val).all() or not np.isfinite(current).all():
            rows.append(dict(signal_date=day,execution_date=r.execution_date,target_feasible=False,reason='missing current price'));continue
        w=g.quantity.to_numpy()*val/float(m.nav_per_cu)
        if w.sum()>.98:w*=.98/w.sum()
        visible=membership[timebyday[day]];member=np.array([bool(visible[pos[s]]) if s in pos else False for s in symbols])
        codes=g.code.astype(str).str.zfill(6).to_numpy();star=np.array([c.startswith('688') for c in codes])
        floor=(2_000_000*w/current//100).astype(int)*100;floor[star&(floor<200)]=0;fw=floor*current/2_000_000
        shares,status=construct(current,w,member,star)
        status.update(signal_date=day,execution_date=r.execution_date,naive_floor_stock_NAV=float(fw.sum()),
                      naive_floor_member_NAV=float(fw[member].sum()),unconstrained_fractional_member_NAV=float(w[member].sum()))
        rows.append(status)
        for i,code in enumerate(codes):targets.append(dict(signal_date=day,execution_date=r.execution_date,code=code,
            pcf_valuation_date=m.valuation_date,pcf_valuation_close=val[i],execution_reference_close=current[i],
            member_at_signal=bool(member[i]),fractional_weight=w[i],target_shares=int(shares[i]),
            target_weight=float(shares[i]*current[i]/2_000_000),construction_feasible=status['target_feasible']))
    d=pd.DataFrame(rows);d.to_csv(dest/'P0_all_dates_target_feasibility.csv',index=False)
    pd.DataFrame(targets).to_parquet(dest/'P0_all_dates_integer_targets.parquet',index=False)
    result=dict(status='target_construction_audited',tested_dates=len(d),feasible_target_dates=int(d.target_feasible.sum()),
        construction_failed_dates=d.loc[~d.target_feasible,'execution_date'].tolist(),
        naive_floor_under90_dates=int((d.naive_floor_stock_NAV<.90).sum()),naive_floor_member80_fail_dates=int((d.naive_floor_member_NAV<=.80).sum()),
        minimum_constructed_stock_NAV=float(d.stock_NAV.min()),minimum_constructed_member_NAV=float(d.member_NAV.min()),
        max_L1_target_error=float(d.L1_target_error.max()),median_L1_target_error=float(d.L1_target_error.median()),
        membership_is_signal_time_only=True,actual_execution_and_NAV_returns_verified=False,
        P1_and_corporate_action_cash_ledger_complete=False,live_activation=False)
    write(dest/'historical_target_feasibility_summary.json',result);print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()

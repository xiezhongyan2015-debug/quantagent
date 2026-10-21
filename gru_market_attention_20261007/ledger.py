"""Causal-order, adjusted-unit diagnostic replay of frozen GRU predictions.

This is a portfolio cash ledger, not a compounding of overlapping label returns.
Execution/CA/round-lot approximations are explicitly retained in the protocol.
"""
from pathlib import Path
import json
import hashlib
import math
import os
import numpy as np
import pandas as pd

ROOT=Path(os.environ.get('GRU_PROJECT_ROOT',Path(__file__).resolve().parents[2]))
OUT=Path(os.environ.get('GRU_REPLAY_OUTPUT',Path(__file__).resolve().parent))
DATA=Path(os.environ.get('GRU_DATA',ROOT/'gru_research/artifacts/dataset'))
SCORES=Path(os.environ.get('GRU_SCORES',OUT/'scores'))
CAPITAL=2_000_000.
PROTOCOL={
 'status':'historical_diagnostic_not_live_certification',
 'models':'latest raw and qlib_rank_linear; each five frozen IC-selected seeds; equal mean of per-signal z scores',
 'primary_strategy':'Top50 at every available hourly signal; target 98% invested',
 'fixed_robustness_strategy':'Buffer50_100: retain holdings ranked within 100, fill remaining slots from signal ranking',
 'selection':'signal scores/current membership/past complete 80 bars only; no future label/price/fill selection or refill',
 'orders':'quantities fixed at signal adjusted close; execution at stored next 5-minute mean open; adverse price slippage',
 'account':'1,000,000 CNY initial cash; independent reset each calendar year; T+1 by execution day; cash-limited purchases; no leverage',
 'constraints':'2% cash target; buy target <=9% NAV; report realized <90% invested after day3 and >10% single-stock/equity violations',
 'minimum_signal_order_cny':1000,
 'costs':{'commission_per_side':0.0002,'commission_minimum_cny':0,'commission_treatment':'user-confirmed simulation: .0002 each buy/sell; all-in; no minimum, tax, transfer or added slippage',
          'stamp_duty_sell_only':0.0,'main_slippage_bps_per_side':0,'sensitivity_slippage_bps_per_side':[]},
 'cost_sources':['user clarification 2026-10-07: each side .0002 all-in'],
 'benchmark':'official CSI500 PRICE index 000905, same dates from prior-year last close; excludes benchmark dividends and trading costs',
 'metrics':'daily close NAV; interval-return difference in percentage points; relative wealth separately; 252-day risk annualization, zero risk-free',
 'limitations':['Adjusted fractional units, not actual raw-share corporate-action or round-lot settlement.',
                'Execution completeness/volume/flat-window mask is a conservative proxy, not authoritative daily price-limit/queue simulation.',
                'No participation/capacity or nonlinear market impact model; slippage is assumed.',
                'Missing valuations carry only last observed mark; never manufacture a tradable price.',
                '2025/2026 historical periods have already been exposed; not a pristine out-of-sample claim.',
                'Original training and model-selection label-availability filtering remains; this replay removes future-label-based prediction/selection.',
                'Official price benchmark is not total return; adjusted-price portfolio includes corporate-action adjustment effects.',
                'End-of-period NAV is marked holdings, not final liquidation.']}

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def targets(score,units,marks,nav,buffer=0,n=50):
    ids=np.flatnonzero(np.isfinite(score)&np.isfinite(marks)&(marks>0))
    order=ids[np.argsort(-score[ids],kind='stable')]
    if not len(order): return units.copy(),np.empty(0,int)
    if buffer:
        chosen=[u for u in order[:buffer] if units[u]>1e-10][:n]
        keep=set(chosen);chosen += [int(u) for u in order if u not in keep][:n-len(chosen)]
        chosen=np.array(chosen,int)
    else:chosen=order[:n]
    desired=np.zeros(len(units))
    desired[chosen]=min(.98/n,.09)*nav/marks[chosen]
    delta=desired-units
    delta[np.abs(delta)*marks<PROTOCOL['minimum_signal_order_cny']]=0
    return units+delta,chosen

def simulate(p,fill,s,c,days,closes,slip_bps=0,buffer=0,gross=False,keep_trades=False,n=50,membership=None):
    U=p.shape[1];units=np.zeros(U);locked=np.zeros(U);marks=np.zeros(U);cash=CAPITAL
    daily=[];events=[];trades=[];day=None;numday=0
    fee_sum=slip_sum=turn_sum=t1_sum=blocked_sum=0.
    slip=0 if gross else slip_bps/10000
    commission=0 if gross else PROTOCOL['costs']['commission_per_side']
    floor=0 if gross else PROTOCOL['costs']['commission_minimum_cny']
    tax=0 if gross else PROTOCOL['costs']['stamp_duty_sell_only']
    transfer=0.0
    for j in range(len(p)):
        if days[j]!=day:
            locked[:]=0;day=days[j];numday+=1
            fee_sum=slip_sum=turn_sum=t1_sum=blocked_sum=0.
        valid=np.isfinite(c[j])&(c[j]>0);marks[valid]=c[j,valid]
        signal_nav=cash+units@marks
        target,chosen=targets(s[j],units,marks,signal_nav,buffer,n)
        delta=target-units  # committed before observing the execution window
        signal_marks=marks.copy()
        valid=np.isfinite(p[j])&(p[j]>0);marks[valid]=p[j,valid]
        before=cash+units@marks;can=fill[j]&valid
        available=np.maximum(units-locked,0);requested=np.maximum(-delta,0)
        t1=float(np.maximum(requested-available,0)@marks/before)
        blocked=float((np.abs(delta)*marks*(~can)).sum()/before)
        sell=np.where(can,np.minimum(requested,available),0)
        sell[sell*signal_marks<PROTOCOL['minimum_signal_order_cny']]=0
        sell_price=marks*(1-slip);sell_value=sell*sell_price
        sell_fee=np.where(sell>1e-10,np.maximum(sell_value*commission,floor)+sell_value*(tax+transfer),0)
        cash+=float((sell_value-sell_fee).sum());units-=sell
        want=np.where(can,np.maximum(delta,0),0)
        # Brokerage cash reservation scales committed quantities; it never selects a replacement stock.
        buy_price=marks*(1+slip)
        def buy_cost(scale):
            v=want*scale*buy_price
            return float((v+np.where(want*scale>1e-10,np.maximum(v*commission,floor)+v*transfer,0)).sum())
        if buy_cost(1)>cash:
            lo,hi=0.,1.
            for _ in range(45):
                mid=(lo+hi)/2
                if buy_cost(mid)<=max(cash,0):lo=mid
                else:hi=mid
            scale=lo
        else:scale=1.
        bought=want*scale;buy_value=bought*buy_price
        buy_fee=np.where(bought>1e-10,np.maximum(buy_value*commission,floor)+buy_value*transfer,0)
        cash-=float((buy_value+buy_fee).sum());units+=bought;locked+=bought
        fee=float(buy_fee.sum()+sell_fee.sum());slippage=float(((bought+sell)*marks*slip).sum())
        after=cash+units@marks
        assert cash>=-1e-6 and units.min()>=-1e-8 and np.all(locked<=units+1e-8), (j,cash,float(units.min()),float((locked-units).max()),float(sell_value.sum()),float(sell_fee.sum()))
        assert abs(after-(before-fee-slippage))<1e-5, (j,after,before,fee,slippage)
        turn=float((buy_value.sum()+sell_value.sum())/before)
        fee_sum+=fee;slip_sum+=slippage;turn_sum+=turn;t1_sum+=t1;blocked_sum+=blocked
        equity=units@marks
        events.append(dict(event=j,execution_date=day,nav=after/CAPITAL,cash_weight=cash/after,
                           turnover=turn,fee_cny=fee,slippage_cny=slippage,t1_blocked=t1,fill_blocked=blocked,
                           max_stock_in_equity=float((units*marks).max()/equity) if equity else 0))
        if keep_trades:
            for u in np.flatnonzero((bought+sell)>1e-10):
                trades.append((j,int(u),float(delta[u]),float(bought[u]),float(sell[u]),float(marks[u]),float(buy_fee[u]+sell_fee[u])))
        if j==len(p)-1 or days[j+1]!=day:
            close=closes[day];known=np.isfinite(close)&(close>0);marks[known]=close[known]
            nav=cash+units@marks;equity=units@marks
            daily.append(dict(date=day,nav=nav/CAPITAL,cash_weight=cash/nav,turnover=turn_sum,fee_cny=fee_sum,
                              slippage_cny=slip_sum,t1_blocked=t1_sum,fill_blocked=blocked_sum,
                              stale_weight=float((units*marks)[~known].sum()/nav),holdings=int((units>1e-10).sum()),
                              max_stock_in_equity=float((units*marks).max()/equity) if equity else 0,
                              after_build_grace=numday>3,member_nav_weight=float((units*marks)[membership[j]].sum()/nav) if membership is not None else None))
    return pd.DataFrame(daily),pd.DataFrame(events),trades

def metrics(d,bnav):
    nav=d.nav.to_numpy();r=nav/np.r_[1,nav[:-1]]-1;br=bnav/np.r_[1,bnav[:-1]]-1
    active=r-br;sd=r.std(ddof=1);te=active.std(ddof=1);mean=r.mean()
    dd=np.r_[1,nav]/np.maximum.accumulate(np.r_[1,nav])-1
    cagr=nav[-1]**(252/len(r))-1;down=np.sqrt(np.mean(np.minimum(r,0)**2))
    var95=-np.quantile(r,.05);var99=-np.quantile(r,.01)
    beta=np.cov(r,br,ddof=1)[0,1]/np.var(br,ddof=1)
    return dict(days=len(r),start=d.date.iloc[0],end=d.date.iloc[-1],net_return=float(nav[-1]-1),
                benchmark_return=float(bnav[-1]-1),excess_pp=float(nav[-1]-bnav[-1]),relative_return=float(nav[-1]/bnav[-1]-1),
                cagr=float(cagr),max_drawdown=float(dd.min()),annual_vol=float(sd*np.sqrt(252)),
                sharpe=float(mean/sd*np.sqrt(252)),sortino=float(mean/down*np.sqrt(252)),
                calmar=float(cagr/abs(dd.min())) if dd.min()<0 else None,
                tracking_error=float(te*np.sqrt(252)),information_ratio=float(active.mean()/te*np.sqrt(252)),
                daily_win_rate=float((active>0).mean()),beta=float(beta),arithmetic_alpha_annual=float((r.mean()-beta*br.mean())*252),
                historical_var95=float(var95),historical_var99=float(var99),
                parametric_var95=float(1.644854*sd-mean),parametric_var99=float(2.326348*sd-mean),
                expected_shortfall95=float(-r[r<=np.quantile(r,.05)].mean()),
                daily_two_sided_turnover=float(d.turnover.mean()),average_exposure=float(1-d.cash_weight.mean()),
                under90_days_after_grace=int(((d.cash_weight>.10000001)&d.after_build_grace).sum()),
                over10_days=int((d.max_stock_in_equity>.10000001).sum()),max_stale_weight=float(d.stale_weight.max()),
                total_fees_cny=float(d.fee_cny.sum()),total_slippage_cny=float(d.slippage_cny.sum()))

def tests():
    p=np.ones((4,20))*10;s=np.tile(np.arange(20.,0,-1),(4,1));s[1:,0]=-1
    c=p.copy();f=np.ones_like(p,bool);days=['a','a','b','b'];cl={'a':p[0],'b':p[0]}
    d,e,tr=simulate(p,f,s,c,days,cl,gross=True,keep_trades=True,n=20)
    assert np.allclose(d.nav,1)
    assert not any(j==1 and sold>0 for j,u,delta,b,sold,price,fee in tr)
    f[:,0]=False
    d,e,tr=simulate(p,f,s,c,days,cl,gross=True,keep_trades=True,n=20)
    assert not any(u==0 for j,u,delta,b,sold,price,fee in tr)
    q,chosen=targets(s[0],np.zeros(20),c[0],CAPITAL,n=20)
    assert np.isclose((q*c[0]).sum(),.98*CAPITAL)
    # Future prices cannot enter target decisions: the target function has no such argument.
    a,e,_=simulate(p,np.ones_like(p,bool),np.tile(s[0],(4,1)),c,days,cl,gross=True,n=20)
    b,e,_=simulate(p,np.ones_like(p,bool),np.tile(s[0],(4,1)),c,days,cl,gross=False,n=20)
    assert b.nav.iloc[-1]<a.nav.iloc[-1]
    # Missing top candidate is not replaced by a lower-ranked stock.
    f=np.ones((4,20),bool);f[:,0]=False
    _,_,tr=simulate(p,f,np.tile(s[0],(4,1)),c,days,cl,gross=True,keep_trades=True,n=10)
    assert all(u<10 for j,u,delta,b,sold,price,fee in tr)
    return ['constant-price accounting','T+1 same-day sale block','failed fills remain unfilled','cash nonnegative at every event',
            'signal-only order function','cost monotonicity at constant prices','no replacement using future fill status']

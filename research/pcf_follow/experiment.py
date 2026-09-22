"""PCF replay accounting extracted from the reviewed local research simulator."""
import numpy as np
import pandas as pd

def metrics(r,b=None):
    r=pd.Series(r).dropna();n=len(r)
    if not n:return {}
    nav=(1+r).cumprod();ann=nav.iloc[-1]**(252/n)-1
    peak=np.maximum.accumulate(np.r_[1.,nav.values])[1:]
    result=dict(days=n,total=float(nav.iloc[-1]-1),annual=float(ann),vol=float(r.std()*np.sqrt(252)),
                sharpe=float(r.mean()/r.std()*np.sqrt(252)) if r.std()>0 else None,
                max_drawdown=float(np.min(nav.values/peak-1)))
    if b is not None:
        b=pd.Series(b).reindex(r.index);ex=r-b;bn=(1+b).prod()
        result.update(excess_annual=float(ann-(bn**(252/n)-1)),
                      relative_annual=float((nav.iloc[-1]/bn)**(252/n)-1),
                      information_ratio=float(ex.mean()/ex.std()*np.sqrt(252)) if ex.std()>0 else None)
    return result

def simulate(o,c,targets,cost,start,limit=None):
    """targets at signal close. NaN row=no instruction, zero row=liquidate.
    Missing open locks units, uses last available close for valuation; no forced sale.
    Fractional adjusted units, no capacity/lot constraints. Limit is a daily approximate band.
    """
    o=o.astype(float);c=c.reindex_like(o).astype(float);targets=targets.reindex_like(o)
    assert (targets.fillna(0)>=-1e-9).all().all()
    assert (targets.fillna(0).sum(axis=1)<=1.0000001).all()
    op=o.to_numpy();cp=c.to_numpy();tw=targets.to_numpy()
    dates=o.index;units=np.zeros(o.shape[1]);cash=1.;last=np.full(o.shape[1],np.nan)
    previous_nav=1.;rows=[]
    for t,d in enumerate(dates):
        known=np.isfinite(cp[t])&(cp[t]>0)
        if d<pd.Timestamp(start):
            last=np.where(known,cp[t],last);continue
        valid=np.isfinite(op[t])&(op[t]>0)
        mark=np.where(valid,op[t],last)
        value=units*np.nan_to_num(mark)
        pre=cash+value.sum();fees=traded=0.;blocked=0.
        if t>0 and np.isfinite(tw[t-1]).any():
            want=np.nan_to_num(tw[t-1])*pre
            delta=want-value
            buyable=valid.copy();sellable=valid.copy()
            if limit is not None:
                gap=op[t]/last-1;band=limit[t]
                buyable &= ~(gap>=band-0.002)
                sellable &= ~(gap<=-band+0.002)
            blocked=float(np.abs(delta[(delta>0)&~buyable]).sum()+np.abs(delta[(delta<0)&~sellable]).sum())/pre
            sell=np.where(sellable,np.minimum(delta,0),0.)
            units+=np.divide(sell,op[t],out=np.zeros_like(sell),where=valid)
            cash-=sell.sum()*(1-cost)
            buy=np.where(buyable,np.maximum(delta,0),0.)
            scale=min(1.,max(cash,0)/(buy.sum()*(1+cost))) if buy.sum()>0 else 0.
            buy*=scale
            units+=np.divide(buy,op[t],out=np.zeros_like(buy),where=valid)
            cash-=buy.sum()*(1+cost)
            traded=buy.sum()-sell.sum();fees=cost*traded
        last=np.where(known,cp[t],last)
        nav=cash+(units*np.nan_to_num(last)).sum()
        assert cash>-1e-9 and nav>0 and (units>=-1e-10).all()
        rows.append(dict(date=d,ret=nav/previous_nav-1,nav=nav,fee=fees,turnover=traded/pre,
                         exposure=1-cash/nav,blocked=blocked))
        previous_nav=nav
    return pd.DataFrame(rows).set_index('date')


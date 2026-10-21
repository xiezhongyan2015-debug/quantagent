"""Offline integer TARGET feasibility; never represents a filled cash account."""
import json
import numpy as np
import pandas as pd
from scipy.optimize import milp, Bounds, LinearConstraint
from scipy.sparse import lil_matrix
from .data import OUT,write

PROTOCOL={'version':'integer_target_feasibility_v1','capital':2_000_000.,
    'minimum_stock_NAV':.90,'maximum_cost_including_buy_fee_NAV':.98,
    'minimum_member_NAV':.8001,'maximum_single_stock_NAV':.05,'buy_fee':.0002,
    'quantity_rules':'100-share multiples; SH688 either zero or at least 200 shares, matching supplied console parser',
    'universe':'same positive optional-stock PCF rows only; no outside replacement stocks',
    'objective':'minimize NAV L1 error to frozen fractional targets plus cash-weight error; 1e-8 per lot tie breaker',
    'solver':'scipy HiGHS MILP, relative gap 0.00001, max 10 seconds per basket',
    'interpretation':'Offline target construction only. No future fill prices, holdings, execution capacity, dividend settlement or live activation.',
    'capital_comparison':[2_000_000.,9_500_000.],
    'sampling':'all historical P0 target days once reconstructed; the existing latest historical JSON sample first',
    'status':'separate follow-up research protocol; fixed original four-account results untouched'}

def allocate(reference,capital=2_000_000.,minimum_member=.8001,max_seconds=10):
    """Prices and membership must be known at signal time. No fill inputs exist."""
    a=reference.copy();a.code=a.code.astype(str).str.zfill(6)
    if a.code.duplicated().any():raise ValueError('Duplicate target code')
    fields=a[['previous_close','target_weight']].to_numpy(float)
    if not np.isfinite(fields).all() or (fields[:,0]<=0).any() or (fields[:,1]<0).any():raise ValueError('Invalid target inputs')
    if not a.member.isin([True,False,0,1]).all():raise ValueError('Membership must be boolean')
    n=len(a);value=a.previous_close.to_numpy(float)*100/capital;w=a.target_weight.to_numpy(float)
    member=a.member.to_numpy(bool);upper=np.floor(.05/value+1e-10)
    upper[w<=0]=0
    lower=np.zeros(2*n+1);ub=np.r_[upper,np.full(n+1,np.inf)];integ=np.zeros(2*n+1,int);integ[:n]=1
    star=a.code.str.startswith('688').to_numpy()&(upper>=2)&(w>0)
    lower[:n][star]=2;integ[:n][star]=3
    ub[:n][a.code.str.startswith('688').to_numpy()&(upper<2)]=0
    A=lil_matrix((2*n+4,2*n+1));lo=np.full(2*n+4,-np.inf);hi=np.full(2*n+4,np.inf)
    for i in range(n):
        A[i,i]=value[i];A[i,n+i]=-1;hi[i]=w[i]
        A[n+i,i]=-value[i];A[n+i,n+i]=-1;hi[n+i]=-w[i]
    A[2*n,:n]=value;A[2*n,-1]=-1;hi[2*n]=w.sum()
    A[2*n+1,:n]=-value;A[2*n+1,-1]=-1;hi[2*n+1]=-w.sum()
    A[2*n+2,:n]=value;lo[2*n+2]=.90;hi[2*n+2]=.98/(1+.0002)
    A[2*n+3,:n]=value*member;lo[2*n+3]=minimum_member
    objective=np.r_[np.full(n,1e-8),np.ones(n+1)]
    res=milp(objective,integrality=integ,bounds=Bounds(lower,ub),
             constraints=LinearConstraint(A.tocsc(),lo,hi),options={'time_limit':max_seconds,'mip_rel_gap':1e-5})
    status={'solver_status':int(res.status),'solver_message':res.message,'capital':capital,
            'target_feasible':False,'optimal':bool(res.success),'filled_or_live_ready':False}
    if res.x is None:return None,status
    shares=np.rint(res.x[:n]).astype(int)*100;weights=shares*a.previous_close.to_numpy()/capital
    is_star=a.code.str.startswith('688').to_numpy()
    if (np.abs(res.x[:n]-shares/100)>1e-5).any():return None,status
    stock=float(weights.sum());mem=float(weights[member].sum())
    feasible=stock>=.90-1e-8 and stock*(1+.0002)<=.98+1e-8 and mem>=minimum_member-1e-8
    feasible=feasible and bool((weights<=.05+1e-8).all()) and bool(((shares[is_star]==0)|(shares[is_star]>=200)).all())
    feasible=feasible and bool((shares[w==0]==0).all())
    if not feasible:return None,status
    a['integer_target_shares']=shares;a['integer_target_weight']=weights
    status.update(target_feasible=True,stock_NAV=stock,member_NAV=mem,cash_NAV=1-stock,
        NAV_L1_target_error=float(np.abs(weights-w).sum()),cash_weight_error=float(abs(stock-w.sum())),
        max_stock_NAV=float(weights.max()),names=int((shares>0).sum()),buy_fee_if_built_from_cash=stock*capital*.0002,
        mip_gap=float(res.mip_gap))
    return a,status

def main():
    dest=OUT/'integer_target_research';dest.mkdir(exist_ok=True)
    p=dest/'preregistration.json'
    if p.exists():assert json.loads(p.read_text())==PROTOCOL
    else:write(p,PROTOCOL)
    a=pd.read_csv(OUT/'interface_example/target_EXAMPLE_2026-09-23.csv',dtype={'code':str,'substitution':str})
    results=[]
    for capital in PROTOCOL['capital_comparison']:
        b,status=allocate(a,capital=capital)
        status.update(signal_date='2026-09-22',execution_date='2026-09-23',source='historical P0 template; no daily-GRU checkpoint')
        results.append(status)
        if b is not None:
            b.to_csv(dest/f'target_{int(capital)}_EXAMPLE.csv',index=False)
            # Shares format avoids re-rounding integer targets via float weights.
            write(dest/f'target_{int(capital)}_EXAMPLE.json',dict(strategy_id='EXAMPLE_INTEGER_P0_OFFLINE',
                signal_date='2026-09-22',execution_date='2026-09-23',mode='shares',research_only=True,
                targets=[dict(code=('SH' if r.code.startswith('6') else 'SZ')+r.code,shares=int(r.integer_target_shares)) for r in b.itertuples() if r.integer_target_shares>0]))
    write(dest/'latest_example_feasibility.json',results)
    print(json.dumps(results,ensure_ascii=False),flush=True)

if __name__=='__main__':main()

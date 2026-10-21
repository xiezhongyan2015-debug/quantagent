"""Independent verification of user-confirmed simulation cost: 2bp each side."""
import numpy as np
from . import ledger as L
from .report import order_targets
from .experiment import write,OUT

def main():
 L.targets=order_targets
 assert L.PROTOCOL['costs']['commission_per_side']==.0002
 assert L.PROTOCOL['costs']['commission_minimum_cny']==0
 assert L.PROTOCOL['costs']['stamp_duty_sell_only']==0
 p=np.full((3,120),10.);s=np.tile(np.arange(120.,0,-1),(3,1));c=p.copy();f=np.ones_like(p,bool);days=['a','a','b'];cl={'a':p[0],'b':p[0]}
 d,e,tr=L.simulate(p,f,s,c,days,cl,0,0,False,True,n=100,membership=np.ones_like(p,bool))
 first=[x for x in tr if x[0]==0];assert len(first)==100
 assert np.isclose(e.fee_cny.iloc[0],.98*L.CAPITAL*.0002)
 assert np.all(e.cash_weight>=0) and np.isclose(d.nav.iloc[-1],1-e.fee_cny.sum()/L.CAPITAL)
 assert e.slippage_cny.sum()==0
 g,_,_=L.simulate(p,f,s,c,days,cl,0,0,True,False,n=100);assert np.allclose(g.nav,1)
 s[1:]=np.arange(120.)
 _,e,tr=L.simulate(p,f,s,c,days,cl,0,0,False,True,n=100)
 assert not any(j==1 and sold>0 for j,u,delta,bought,sold,mid,fee in tr)
 assert any(j==2 and sold>0 for j,u,delta,bought,sold,mid,fee in tr)
 for j,u,delta,bought,sold,mid,fee in tr:assert np.isclose((bought+sold)*mid*.0002,fee,rtol=1e-10,atol=1e-10)
 assert e.slippage_cny.sum()==0
 # 1,000 CNY small orders incur 0.20 CNY, not a 5 CNY minimum.
 d,e,tr=L.simulate(p,f,s,c,days,cl,0,0,False,True,n=100)
 assert all(np.isclose(fee/((bought+sold)*mid),.0002) for j,u,delta,bought,sold,mid,fee in tr)
 score=np.arange(500.,0,-1);units=np.zeros(500);units[150]=100
 _,chosen=order_targets(score,units,np.full(500,10.),2e6,1,100)
 assert 150 in chosen and all(u<100 or u==150 for u in chosen)
 write(OUT/'replay_tests_fee2.json',{'status':'passed','checks':['each executed buy and sell charged exactly notional*.0002','no commission minimum or extra sell tax/transfer','zero added slippage','NAV conservation at constant prices','T+1 actual rank reversal','buffer retention','cash nonnegative'],'roundtrip_example':'buy100000=20 CNY; sell100000=20 CNY; total40 CNY','scope':'simulation fee correction; execution/valuation approximations unchanged'})
 print('ALL-IN 2BP REPLAY TESTS PASSED; 100000 buy+sell costs 40 CNY')
if __name__=='__main__':main()

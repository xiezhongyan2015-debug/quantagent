import unittest
import numpy as np
import pandas as pd
from pcf_backtest import make_targets
from experiment import simulate

class TimingTests(unittest.TestCase):
    def test_prior_price_only_and_zero_quantity(self):
        dates=pd.date_range('2024-01-01',periods=4,freq='B');symbols=['600001.SH','600002.SH']
        prices=pd.DataFrame([[10,20],[100,200],[1000,2000],[10000,20000]],index=dates,columns=symbols)
        c=pd.DataFrame({'date':[dates[1],dates[1]],'symbol':symbols,'L_NUMBER':[5,0],'F_TDJE':[0,0]})
        basic=pd.DataFrame({'NAVPERCU':[100]},index=[dates[1]])
        t,a=make_targets(c,basic,prices,dates,symbols)
        self.assertAlmostEqual(t.loc[dates[1],symbols[0]],.5)
        self.assertEqual(t.loc[dates[1],symbols[1]],0)
        changed=prices.copy();changed.loc[dates[1]:]*=100
        tt,_=make_targets(c,basic,changed,dates,symbols)
        pd.testing.assert_frame_equal(t,tt)

    def test_missing_stock_not_redistributed(self):
        dates=pd.date_range('2024-01-01',periods=3,freq='B');symbols=['600001.SH','600002.SH']
        prices=pd.DataFrame([[10,np.nan]]*3,index=dates,columns=symbols)
        c=pd.DataFrame({'date':[dates[1]]*2,'symbol':symbols,'L_NUMBER':[5,5],'F_TDJE':[0,25]})
        b=pd.DataFrame({'NAVPERCU':[100]},index=[dates[1]])
        t,a=make_targets(c,b,prices,dates,symbols)
        self.assertAlmostEqual(t.loc[dates[1]].sum(),.5)
        self.assertAlmostEqual(a.loc[dates[1],'missing_weight_proxy'],.25)

    def test_signal_cannot_earn_earlier_gap(self):
        dates=pd.date_range('2024-01-01',periods=4,freq='B')
        p=pd.DataFrame({'stock':[10,100,100,110]},index=dates)
        t=pd.DataFrame({'stock':[np.nan,1,np.nan,np.nan]},index=dates)
        r=simulate(p,p,t,0,dates[0])
        self.assertEqual(r.loc[dates[1],'nav'],1)
        self.assertEqual(r.loc[dates[2],'nav'],1)
        self.assertAlmostEqual(r.loc[dates[3],'nav'],1.1)

if __name__=='__main__':unittest.main()

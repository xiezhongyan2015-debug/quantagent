"""Meaningful invariants: missing alpha, causal orders, accounting, CPU identity."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from .replay import account, target_weights
from .daily_inference import external_targets,infer_cpu
from gru_dualplatform_20261011.daily import DailyGRU
from .comparisons import residual_ic
from .lot_targets import allocate
from .target_history import construct

class ResearchTests(unittest.TestCase):
    def test_missing_alpha_does_not_delete_basket(self):
        w=target_weights(np.array([.4,.4]),np.array([np.nan,0.]),.5)
        np.testing.assert_allclose(w,[.4,.4])

    def test_failed_fill_never_changes_target_selection(self):
        dates=['2026-01-05','2026-01-06'];price=np.full((2,2),10.)
        w=np.array([[.4,.4],[.4,.4]]);fill=np.array([[False,True],[False,True]])
        d,tr=account(w,price,price,fill,dates,price,np.ones((2,2),bool))
        self.assertEqual(set(tr.stock_index),{1});self.assertTrue((d.cash_weight>.59).all())
        self.assertAlmostEqual(d.nav.iloc[-1],1-tr.fee.sum()/2_000_000,places=12)

    def test_stale_valuation_is_not_trade_price(self):
        dates=['a','b'];price=np.full((2,1),10.);signal=price.copy();signal[1]=np.nan
        d,tr=account(np.array([[.5],[0.]]),signal,price,np.ones((2,1),bool),dates,price,np.ones((2,1),bool))
        self.assertTrue((tr.date=='a').all());self.assertAlmostEqual(d.nav.iloc[0],d.nav.iloc[1])

    def test_future_prices_do_not_change_committed_quantity(self):
        signal=np.full((1,2),10.);w=np.array([[.3,.3]])
        def run(px):return account(w,signal,np.array([px]),np.ones((1,2),bool),['a'],signal,np.ones((1,2),bool))[1]
        a=run([10,10]);b=run([11,9]);np.testing.assert_allclose(a.requested_adjusted_units,b.requested_adjusted_units)

    def test_external_format_and_mandatory_cash(self):
        ref=pd.DataFrame(dict(code=['000001','600001','688001'],quantity=[500,500,500],
                             pcf_valuation_close=[10.,10.,10.],previous_close=[10.,10.,10.],nav_per_cu=[10000.]*3,member=[True]*3,substitution=['1','3','2']))
        df,obj,checks=external_targets(ref,{},'2026-01-06','2026-01-05')
        self.assertEqual(obj['mode'],'weights');self.assertEqual({r['code'] for r in obj['targets']},{'SZ000001','SH600001'})
        self.assertEqual(df.loc[2,'target_weight'],0.);self.assertFalse(checks['live_ready'])
        with self.assertRaises(ValueError):external_targets(ref,{},'2026-01-05','2026-01-05')

    def test_cpu_model_missingness_and_batch_independence(self):
        torch.manual_seed(11);model=DailyGRU();x=np.random.default_rng(11).normal(size=(110,60,98)).astype('float32');x[0,:,80:]=np.nan
        ind=np.zeros((110,60),dtype='int64')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'checkpoint.pt';torch.save({'model':model.state_dict(),'signature':{'year':2026}},p)
            a,_=infer_cpu(x,ind,[p],batch=32);b,_=infer_cpu(x,ind,[p],batch=110)
            np.testing.assert_allclose(a,b,atol=1e-4);self.assertTrue(np.isfinite(a).all())

    def test_residual_rank_ic_removes_identical_hourly_rank(self):
        h=np.arange(200,dtype=float)
        r,p,n=residual_ic(h.copy(),h,h)
        self.assertEqual(n,200);self.assertTrue(np.isnan(r) and np.isnan(p))
        d=h+np.random.default_rng(11).normal(size=200)*80
        r,p,n=residual_ic(d,h,d)
        self.assertGreater(r,.1);self.assertGreater(p,.1)

    def test_pcf_valuation_is_distinct_from_execution_reference(self):
        ref=pd.DataFrame(dict(code=['000001'],quantity=[500],pcf_valuation_close=[10.],
                             previous_close=[20.],nav_per_cu=[10000.],member=[True],substitution=['1']))
        df,obj,_=external_targets(ref,{},'2026-01-06','2026-01-05')
        self.assertAlmostEqual(df.target_weight.iloc[0],.5)
        self.assertEqual(df.target_shares.iloc[0],50_000)

    def test_integer_construction_respects_cash_member_and_star(self):
        price=np.array([50.]*30);weight=np.array([.98/30]*30);member=np.array([True]*27+[False]*3);star=np.array([True]+[False]*29)
        shares,status=construct(price,weight,member,star)
        self.assertTrue(status['target_feasible']);self.assertTrue((shares%100==0).all())
        self.assertTrue(shares[0]==0 or shares[0]>=200)
        self.assertGreaterEqual(status['stock_NAV'],.9);self.assertGreaterEqual(status['member_NAV'],.8001)
        self.assertLessEqual(status['stock_NAV']*1.0002,.98+1e-8)

    def test_infeasible_integer_case_is_not_called_ready(self):
        ref=pd.DataFrame({'code':['000001'],'previous_close':[50.],'target_weight':[.98],'member':[True]})
        frame,status=allocate(ref,max_seconds=1)
        self.assertIsNone(frame);self.assertFalse(status['target_feasible']);self.assertFalse(status['filled_or_live_ready'])

if __name__=='__main__':unittest.main()

import unittest
import pandas as pd
from quanta_agents.factor_lab_a.pcf import build_target, normalize_code

class TargetTests(unittest.TestCase):
    def fixture(self):
        record={'date':'2026-03-18','baseinfo':{'tDate':'2026-03-18','isMapShow':True,'map':{'RECORDNUM':2,'NAVPERCU':10000,'PRETRADINGDAY':'2026-03-17'}},'stocklist':[{'C_STOCKCODE':c,'BUSI_DATE':'20260318','L_NUMBER':n} for c,n in [('600001',500),('688001',300)]]}
        quotes=pd.DataFrame({'code':['600001','688001'],'date':['2026-03-18']*2,'close':[10.,10.],'reference_date':['2026-03-17']*2,'reference_close':[10.,10.],'member_csi500':[1,1],'member_union':[1,1],'is_st':[0,0],'tradable':[1,1]})
        return dict(record=record,quotes=quotes,holdings=pd.DataFrame(columns=['code','shares']),nav=10000,calendar=['2026-03-17','2026-03-18','2026-03-19'],execution_date='2026-03-19',asof='2026-03-18')
    def test_codes_and_cash(self):
        self.assertEqual(normalize_code('600001'),'SH600001')
        d,s=build_target(**self.fixture())
        self.assertEqual(d.exec_shares.sum(),800)
        self.assertAlmostEqual(s['estimated_fee'],8)
        self.assertAlmostEqual(s['cash_after_estimated_trades'],1992)
        self.assertFalse(s['broker_orders_submitted'])
    def test_no_same_day_or_future_reference(self):
        a=self.fixture();a['execution_date']='2026-03-18'
        with self.assertRaises(ValueError):build_target(**a)
        a=self.fixture();a['quotes']['reference_date']='2026-03-18'
        with self.assertRaises(ValueError):build_target(**a)
    def test_missing_stock_rejected(self):
        a=self.fixture();a['quotes']=a['quotes'].iloc[:1]
        with self.assertRaises(ValueError):build_target(**a)
    def test_star_small_increment_and_odd_lots(self):
        a=self.fixture();a['holdings']=pd.DataFrame({'code':['600001','688001'],'shares':[50,200]})
        d,_=build_target(**a);d=d.set_index('code')
        self.assertEqual(d.loc['SH688001','delta_shares'],0)
        self.assertEqual(d.loc['SH600001','delta_shares'],400)
    def test_frozen_and_cash_scaling(self):
        a=self.fixture();a['quotes'].loc[0,'tradable']=0
        a['holdings']=pd.DataFrame({'code':['600001'],'shares':[900]})
        d,s=build_target(**a)
        self.assertGreaterEqual(s['cash_after_estimated_trades'],0)
        self.assertEqual(d.set_index('code').loc['SH600001','delta_shares'],0)

if __name__=='__main__':unittest.main()

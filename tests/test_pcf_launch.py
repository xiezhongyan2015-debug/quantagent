"""Regression tests for observed receipt case, initial-buy audit and user capital cap."""
import copy
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.pcf_automation import Journal,order_cash_reserve,CN
from quanta_agents.factor_lab_a.choice_quotes import quote
from test_pcf_automation import Session


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{x:'FAKE' for x in ('CHOICE_ACCOUNT_ID','CHOICE_JTOKEN','CHOICE_UTOKEN','CHOICE_CTOKEN','CHOICE_UID')})
        self.env.start()
        self.contract={'initial_buy_only':True,'validated_submission_with_authorized_test':True,
                       'validated_cancel_with_authorized_test':True}
        self.order={'code':'SH600001','side':1,'quantity':100,'price':10.}
    def tearDown(self): self.env.stop()

    def test_observed_uppercase_id_receipt_returns_exact_id(self):
        s=Session();s.obj={'Code':0,'Message':'success','Data':{'uid':'FAKE','accID':'FAKE',
            'secCode':'600001','secMkt':1,'orderID':'262626200000000001'}}
        b=ChoiceBroker(self.contract,True,s)
        self.assertEqual(b.submit(self.order),'262626200000000001')
        self.assertEqual(s.posts,1)
        self.assertEqual(b.contract['order_id'],'Data.orderID')
        self.assertNotIn('validated_with_authorized_test',b.contract)

    def test_initial_adapter_never_sells_or_uses_unverified_sellable(self):
        s=Session();b=ChoiceBroker(self.contract,True,s)
        with self.assertRaises(TradeError): b.submit(dict(self.order,side=2))
        self.assertEqual(s.posts,0)

    def test_initial_fill_requires_order_inventory_and_deals_agreement(self):
        b=ChoiceBroker(self.contract,True,Session())
        row={'orderId':'one','secCode':'600001','drt':1,'orderCount':100,'orderPrice':10.,'tradeCount':100,'status':4}
        hold={'secCode':'600001','count':100}
        deal={'secCode':'600001','drt':1,'tradeCount':100}
        with patch.object(b,'_rows',side_effect=[[row],[row],[hold],[deal]]):
            self.assertTrue(b.progress('one',self.order)['done'])
        with patch.object(b,'_rows',side_effect=[[row],[row],[],[deal]]):
            self.assertFalse(b.progress('one',self.order)['done'])
        with patch.object(b,'_rows',side_effect=[[row],[row,dict(row,orderId='two')]]):
            with self.assertRaises(TradeError): b.progress('one',self.order)

    def test_persistent_cap_counts_unknown_and_cancelled_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.sqlite'; j=Journal(path,'FAKE')
            j.reserve_capital('2026-09-21','SH600001',1000000,2000000)
            j.mark('2026-09-21','SH600001','UNKNOWN')
            j.db.close(); j=Journal(path,'FAKE')
            try:
                j.reserve_capital('2026-09-21','SH600002',1000000,2000000)
                j.mark('2026-09-21','SH600002','CANCELLED')
                with self.assertRaises(TradeError): j.reserve_capital('2026-09-21','SH600003',.01,2000000)
                with self.assertRaises(TradeError): j.reserve_capital('2026-09-21','SH600001',1,3000000)
            finally: j.db.close()

    def test_fee_reserve_rounds_up_without_changing_research_cost(self):
        self.assertEqual(order_cash_reserve({'execution_fee_buffer_per_order':10},815),825.815)
        with tempfile.TemporaryDirectory() as directory:
            j=Journal(Path(directory)/'x.sqlite','fake')
            try:
                with self.assertRaises(TradeError): j.reserve_capital('day','code',2000000.001,2000000)
            finally:j.db.close()

    def test_quote_uses_source_time_and_observed_price_scale(self):
        s=Session();s.obj={'rc':0,'rt':4,'data':{'f57':'600001','f58':'sample','f59':2,'f86':1789719118,
            'f43':907,'f60':906,'f19':907,'f39':908,'f51':997,'f52':815}}
        row=quote('600001',s)
        self.assertEqual(row['ask'],9.08)
        self.assertEqual(row['timestamp'],'2026-09-18T16:11:58+08:00')
        self.assertEqual(row['tradable'],1)


if __name__=='__main__':unittest.main()

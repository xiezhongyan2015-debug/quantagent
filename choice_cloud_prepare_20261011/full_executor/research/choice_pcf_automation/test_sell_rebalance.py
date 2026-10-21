import unittest
from test_console import ExecutionRisk
import console_core as core


class SellProtection(unittest.TestCase):
    def setUp(self):
        self.fixture=ExecutionRisk();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.w=self.fixture.w;self.a=self.fixture.a
        self.w.c['allow_sells']=True;self.w.c['target_fraction']=.9
        self.w.c['position_floor']['enabled']=True
        self.w.state['position_compliance']={'met':True}
        self.w.state['targets']['SH600000']['quantity']=500
        self.a.update(market_value=90000,total_assets=100000)
    def tick(self):return self.fixture.tick()
    def test_at_floor_intraday_full_target_sell_is_allowed(self):
        self.tick();self.assertEqual(self.w.broker.submit.call_args.args[0]['quantity'],500)
    def test_reduction_is_not_split_to_fit_headroom(self):
        self.a['market_value']=92500;self.tick()
        order=self.w.broker.submit.call_args.args[0]
        self.assertEqual(order['side'],2);self.assertEqual(order['quantity'],500)
    def test_pending_other_sell_below_floor_requires_buys_before_more_sells(self):
        self.a['market_value']=92500
        self.a['pending']={'other':dict(code='SH600001',side=2,quantity=300,price=10)}
        self.tick();self.w.broker.submit.assert_not_called()
    def test_late_session_defers_whole_order_instead_of_splitting(self):
        self.fixture.now=self.fixture.now.replace(hour=14,minute=35)
        self.fixture.q['timestamp']=self.fixture.now.isoformat()
        self.a['market_value']=92500;self.tick();self.w.broker.submit.assert_not_called()
        self.a['market_value']=95100;self.tick()
        self.assertEqual(self.w.broker.submit.call_args.args[0]['quantity'],500)
    def test_below_floor_prioritizes_buying(self):
        self.w.state['targets']['SH600000']['quantity']=1200
        self.a['market_value']=89000
        self.tick();self.assertEqual(self.w.broker.submit.call_args.args[0]['side'],1)
    def test_only_verified_fills_credit_budget_and_all_fees_deducted(self):
        attempts=[dict(oid='sell',order=dict(side=2,price=10),filled=999)]
        audit=dict(states={'sell':dict(filled=100)},fees='15')
        self.assertEqual(self.w.verified_sell_credit(attempts,audit),985)
        audit['states']['sell']['filled']=0
        self.assertEqual(self.w.verified_sell_credit(attempts,audit),0)
        self.assertEqual(self.w.verified_sell_credit([dict(order=dict(side=2,price=10))],audit),0)
    def test_verified_sell_credit_allows_reinvestment_without_raising_cap(self):
        self.w.state['targets']['SH600000']['quantity']=1200
        self.w.state['carried_reserve']='5500000';self.w.c['capital_limit']=5500000
        self.w.state['attempts']=[dict(oid='sold',order=dict(code='SH600002',side=2,price=10),filled=300)]
        self.a['states']={'sold':dict(filled=300)};self.a['fees']='5'
        self.tick();self.assertEqual(self.w.broker.submit.call_args.args[0]['side'],1)
        self.assertEqual(self.w.c['capital_limit'],5500000)
    def test_unfilled_sell_gives_no_reinvestment_budget(self):
        self.w.state['targets']['SH600000']['quantity']=1200
        self.w.state['carried_reserve']='5500000';self.w.c['capital_limit']=5500000
        self.w.state['attempts']=[dict(oid='sold',order=dict(code='SH600002',side=2,price=10),filled=300)]
        self.a['states']={'sold':dict(filled=0)}
        self.tick();self.w.broker.submit.assert_not_called()


if __name__=='__main__':unittest.main()

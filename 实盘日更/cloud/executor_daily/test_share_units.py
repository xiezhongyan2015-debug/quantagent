"""Offline quantity contracts and executor write-boundary tests."""
import copy
import unittest
from unittest.mock import Mock, patch
import share_units as u


class Units(unittest.TestCase):
    def test_same_odd_hold_is_valid_target(self):
        self.assertTrue(u.target_allowed('SZ001309', 780, 780))

    def test_odd_target_without_reference_is_rejected(self):
        self.assertFalse(u.target_allowed('SZ001309', 780))

    def test_odd_target_cannot_invent_different_remainder(self):
        self.assertFalse(u.target_allowed('SZ001309', 790, 780))

    def test_odd_target_cannot_reduce_reference_odd_hold(self):
        self.assertFalse(u.target_allowed('SZ001309', 680, 780))

    def test_buy_target_uses_whole_increment_and_never_overspends(self):
        self.assertEqual(u.compatible_target('SZ001309', 900, 780), 880)

    def test_unchanged_hold_and_normal_sell_target_stay_exact(self):
        self.assertEqual(u.compatible_target('SZ001309', 780, 780), 780)
        self.assertEqual(u.compatible_target('SZ001309', 700, 780), 700)

    def test_normal_and_star_targets_unchanged(self):
        self.assertEqual(u.compatible_target('SZ001309', 900, 700), 900)
        self.assertEqual(u.compatible_target('SH688001', 900, 780), 900)
        self.assertFalse(u.target_allowed('SH688001', 780, 780))

    def test_boolean_fraction_negative_target_rejected(self):
        for n in (True, 780.5, -1):
            self.assertFalse(u.target_allowed('SZ001309', n, 780))

    def test_whole_odd_piece_can_be_sold_without_full_liquidation(self):
        self.assertTrue(u.sell_allowed('SZ001309', 80, 780, 780))
        self.assertTrue(u.sell_allowed('SZ001309', 280, 780, 780))

    def test_odd_piece_cannot_be_split(self):
        self.assertFalse(u.sell_allowed('SZ001309', 40, 780, 780))
        self.assertFalse(u.sell_allowed('SZ001309', 180, 800, 800))

    def test_locked_transfer_odd_piece_is_not_assumed_sellable(self):
        self.assertFalse(u.sell_allowed('SZ001309', 80, 780, 500))
        self.assertEqual(u.sell_quantity('SZ001309', 80, 780, 500, 200), 0)
        self.assertEqual(u.sell_quantity('SZ001309', 280, 980, 700, 200), 200)

    def test_fully_sellable_odd_80_is_one_piece(self):
        self.assertEqual(u.sell_quantity('SZ001309', 80, 780, 780, 200), 80)

    def test_cap_preserved_without_splitting_odd_piece(self):
        self.assertEqual(u.sell_quantity('SZ001309', 280, 980, 980, 200), 180)
        self.assertEqual(u.sell_quantity('SZ001309', 80, 780, 780, 0), 0)

    def test_oversell_and_bad_available_rejected(self):
        self.assertFalse(u.sell_allowed('SZ001309', 880, 780, 780))
        self.assertFalse(u.sell_allowed('SZ001309', 80, 780, 880))
        with self.assertRaises(ValueError):
            u.sell_quantity('SZ001309', 80, 780, 880, 200)

    def test_buy_same_remainder_means_sell_whole_lots_only(self):
        self.assertEqual(u.sell_quantity('SZ001309', 200, 980, 980, 200), 200)

    def test_sealed_reference_requires_date_and_hash(self):
        obj=dict(signal_date='2026-10-13', odd_lot_reference=u.odd_reference({'SZ001309':780},'2026-10-13','a'*64))
        self.assertEqual(u.reference_holding(obj,'SZ001309'),780)
        obj['odd_lot_reference']['signal_date']='2026-10-12'
        self.assertEqual(u.reference_holding(obj,'SZ001309'),0)
        obj['odd_lot_reference']['signal_date']='2026-10-13'
        obj['odd_lot_reference']['account_sha256']='not-a-hash'
        self.assertEqual(u.reference_holding(obj,'SZ001309'),0)


class WriteBoundary(unittest.TestCase):
    def setUp(self):
        from test_workflow_rebalance import Workflow
        Workflow.setUp(self)

    def reseal(self):
        from test_workflow_rebalance import Workflow
        return Workflow.reseal(self)

    def fixture(self, quantity=80, target=700, available=780):
        from test_workflow_rebalance import Workflow, NOW
        self.obj['targets'][0]['shares']=target;self.reseal()
        self.audit['positions']['SZ000001']=780;self.audit['sellable']['SZ000001']=available
        self.audit['position_mark_values']['SZ000001']='7800'
        self.audit['market_value']=str(int(self.audit['market_value'])+6800)
        self.audit['total_assets']=str(int(self.audit['total_assets'])+6800)
        self.audits=[dict(copy.deepcopy(self.audit),checked_at=(NOW-__import__('datetime').timedelta(seconds=6)).isoformat()),copy.deepcopy(self.audit)]
        self.state['last_audit']=copy.deepcopy(self.audit)
        return Workflow.formal(self,'SZ000001',2,quantity)

    def test_80_sale_write_guard_passes_only_for_whole_sellable_odd_piece(self):
        from test_workflow_rebalance import w, NOW
        o=self.fixture();w.guard_formal(self.c,self.state,o,NOW)

    def test_locked_80_sale_write_guard_blocks(self):
        from test_workflow_rebalance import w, NOW, TradeError
        o=self.fixture(available=500)
        with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)

    def test_split_odd_40_sale_write_guard_blocks(self):
        from test_workflow_rebalance import w, NOW, TradeError
        o=self.fixture(quantity=40)
        with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)

    def test_80_buy_write_guard_remains_blocked(self):
        from test_workflow_rebalance import w, NOW, TradeError
        o=self.fixture();o['side']=1
        with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)

    def test_odd_sale_price_guard_is_unchanged(self):
        from test_workflow_rebalance import w, NOW, TradeError
        o=self.fixture();o['price']=9.69;self.state['attempts'][-1]['reserve']=str(w.reserved(9.69,80))
        self.state['quotes']['SZ000001']['bid']=9.69
        with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)

    def test_odd_sale_stock_floor_remains_enforced(self):
        from test_workflow_rebalance import w, NOW, TradeError
        o=self.fixture();self.state['last_audit']['total_assets']='180000'
        with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)

    def test_worker_plans_80_instead_of_rounding_to_zero(self):
        from test_workflow_rebalance import rt, w, NOW, quote
        self.fixture();self.state['attempts'].pop()
        worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=w.effective(self.c,self.state);worker.original_config=self.c
        worker.day=w.DAY;worker.audit=Mock(return_value=self.audit);worker.cancel=Mock();worker.check_stop=Mock(return_value=False)
        worker.public=Mock();worker.flush=Mock();worker.http=Mock();worker.broker=Mock();worker.broker.submit.return_value='odd-proof-order'
        worker.last_submit=-1000
        with patch.object(rt,'now_cn',return_value=NOW),patch.object(rt,'quote',return_value=quote('SZ000001')):
            worker.step()
        self.assertEqual(worker.broker.submit.call_args.args[0]['quantity'],80)

    def test_locked_80_worker_preserves_target_and_queues_alarm_no_orders(self):
        from test_workflow_rebalance import rt, w, NOW, quote
        self.fixture(available=500);self.state['attempts'].pop()
        worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=w.effective(self.c,self.state);worker.original_config=self.c
        worker.day=w.DAY;worker.audit=Mock(return_value=self.audit);worker.cancel=Mock();worker.check_stop=Mock(return_value=False)
        worker.public=Mock();worker.flush=Mock();worker.http=Mock();worker.broker=Mock();worker.last_submit=-1000
        with patch.object(rt,'now_cn',return_value=NOW),patch.object(rt,'quote',return_value=quote('SZ000001')),patch.object(rt,'deferred_notice') as alert:
            worker.step();worker.step()
        worker.broker.submit.assert_not_called();alert.assert_called_once()
        self.assertEqual(worker.state['targets']['SZ000001']['quantity'],700)

    def test_retained_odd_target_passes_all_parsers_with_evidence(self):
        from test_workflow_rebalance import w, core, NOW, quote
        self.obj['targets'][0]['shares']=780
        self.obj['odd_lot_reference']=u.odd_reference({'SZ000001':780},w.SIGNAL,'a'*64);self.reseal();w.inputs(self.c)
        obj=dict(execution_date=w.DAY,signal_date=w.SIGNAL,mode='shares',targets=[dict(code='SZ000001',shares=780)],odd_lot_reference=self.obj['odd_lot_reference'])
        c=dict(self.c,target_fraction=.95,max_security_value=20000)
        t=core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,{'SZ000001':quote()},valuation_time=NOW,holdings={'SZ000001':780})
        self.assertEqual(t['SZ000001']['quantity'],780)

    def test_odd_target_without_live_match_blocks(self):
        from test_workflow_rebalance import w, core, NOW, quote, TradeError
        obj=dict(execution_date=w.DAY,signal_date=w.SIGNAL,mode='shares',targets=[dict(code='SZ000001',shares=780)],odd_lot_reference=u.odd_reference({'SZ000001':780},w.SIGNAL,'a'*64))
        for holdings in (None,{'SZ000001':700}):
            with self.assertRaises(TradeError):core.parse_targets(obj,w.DAY,w.SIGNAL,10000,dict(self.c,max_security_value=20000),{'SZ000001':quote()},holdings=holdings)


if __name__ == '__main__':
    unittest.main()

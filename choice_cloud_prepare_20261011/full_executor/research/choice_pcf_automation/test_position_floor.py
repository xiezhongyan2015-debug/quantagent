"""Offline tests for minimum exposure, valid lots, live repairs and deadlines."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import console_core as core
import console_runtime as runtime
import position_floor as floor

CALENDAR=['2026-09-22','2026-09-23','2026-09-24','2026-09-28','2026-09-29','2026-09-30','2026-10-08']


def config():
    return core.config_checked(dict(core.DEFAULTS,target_fraction=.9,capital_limit=9500000,
        position_floor=dict(floor.POLICY_DEFAULTS,enabled=True)))


def audit(market=899000,nav=1000000):
    return dict(market_value=market,total_assets=nav,stock_fraction=market/nav,checked_at='2026-09-23T15:00:00+08:00')


class Rules(unittest.TestCase):
    def test_zero_quantity_pcf_entries_do_not_require_reference_quotes(self):
        record=dict(date='2026-09-22',stocklist=[dict(C_STOCKCODE='600001',L_NUMBER=100),dict(C_STOCKCODE='002025',L_NUMBER=0)])
        quotes={'SH600001':dict(previous_close=10,is_st=False,tradable=True)}
        with patch('quanta_agents.factor_lab_a.pcf.validate_pcf'):
            result=core.pcf_targets(record,'2026-09-23','2026-09-22',100000,config(),quotes,{'SH600001'})
        self.assertNotIn('SZ002025',result)
        self.assertGreaterEqual(result['SH600001']['quantity']*10,90500)

    def test_lots_reach_buffered_target_instead_of_flooring_below_minimum(self):
        c=config();prices={'SH600001':37,'SH600002':57,'SH600003':91};weights=dict.fromkeys(prices,1)
        targets,value=floor.allocate(weights,prices,{},1000000,c)
        self.assertGreaterEqual(value,905000)
        self.assertTrue(all(n%100==0 for n in targets.values()))
        self.assertLessEqual(value,9500000)
        self.assertTrue(all(targets[k]*prices[k]<=500000 for k in targets))

    def test_star_rounding_never_introduces_a_100_share_adjustment(self):
        c=config();prices={'SH688001':31,'SH600002':57,'SH600003':91};weights=dict.fromkeys(prices,1)
        for held in (0,100,200,900,1000,1100,10000):
            targets,value=floor.allocate(weights,prices,{'SH688001':held},1000000,c)
            delta=targets['SH688001']-held
            self.assertTrue(delta==0 or abs(delta)>=200 or targets['SH688001']==0)
            self.assertGreaterEqual(value,905000)

    def test_old_cap_cannot_silently_produce_90_percent_plan(self):
        c=config();c['capital_limit']=5500000
        with self.assertRaises(ValueError):floor.allocate({'SH600001':1},{'SH600001':10},{},10000000,c)

    def test_single_stock_cap_not_relaxed_to_complete_floor(self):
        with self.assertRaises(ValueError):floor.allocate({'SH600001':1},{'SH600001':10},{},1000000,config())

    def test_buy_only_preserves_existing_holdings_and_counts_outside_names(self):
        targets,value=floor.allocate({'SH600001':1,'SH600002':1},
            {'SH600001':10,'SH600002':10,'SH600003':10},{'SH600001':40000,'SH600003':10000},1000000,config())
        self.assertGreaterEqual(targets['SH600001'],40000)
        self.assertNotIn('SH600003',targets)
        self.assertGreaterEqual(value,905000)
        self.assertLess(value,910000)

    def test_existing_star_residual_is_not_silently_removed_when_floor_already_met(self):
        c=config()
        targets,value=floor.allocate({'SH688001':1,'SH600002':1},
            {'SH688001':10,'SH600002':10,'SH600003':10},
            {'SH688001':100,'SH600003':91000},1000000,c)
        self.assertEqual(targets['SH688001'],100)
        self.assertGreaterEqual(value,905000)

    def test_tomorrow_deadline_stays_tomorrow_even_after_intraday_recovery(self):
        c=config();first=floor.observe({},audit(),'2026-09-23',c,CALENDAR)
        self.assertEqual(first['deadline'],'2026-09-23')
        recovered=floor.observe(first,audit(905000),'2026-09-23',c,CALENDAR)
        self.assertTrue(recovered['met'])
        again=floor.observe(recovered,audit(),'2026-09-23',c,CALENDAR)
        self.assertEqual(again['deadline'],'2026-09-23')

    def test_three_trading_days_include_detection_day_and_skip_holidays(self):
        c=config();before=floor.observe({},audit(905000),'2026-09-23',c,CALENDAR)
        breach=floor.observe(before,audit(),'2026-09-24',c,CALENDAR)
        self.assertEqual(breach['deadline'],'2026-09-29')
        later=floor.observe(breach,audit(),'2026-09-28',c,CALENDAR)
        self.assertEqual(later['breach_since'],'2026-09-24')
        self.assertEqual(later['deadline'],'2026-09-29')
        self.assertFalse(later['due_today'])

    def test_recovery_clears_breach_and_next_decline_gets_its_own_deadline(self):
        c=config();breach=floor.observe({},audit(),'2026-09-24',c,CALENDAR)
        good=floor.observe(breach,audit(900000),'2026-09-28',c,CALENDAR)
        self.assertTrue(good['met']);self.assertIsNone(good['breach_since'])
        again=floor.observe(good,audit(),'2026-09-29',c,CALENDAR)
        self.assertEqual(again['deadline'],'2026-10-08')

    def test_insufficient_calendar_blocks_guessing_deadline(self):
        with self.assertRaises(ValueError):floor.observe({},audit(),'2026-09-24',config(),CALENDAR[:3])

    def test_future_rule_does_not_change_today(self):
        self.assertFalse(floor.observe({},audit(),'2026-09-22',config(),[])['enabled'])

    def test_overnight_roll_preserves_unmet_deadline(self):
        compliance=floor.observe({},audit(),'2026-09-23',config(),CALENDAR)
        state=dict(day='2026-09-23',attempts=[],historical={},prior={},position_compliance=compliance,
                   last_audit=dict(pending={},frozen='0',positions={},cash='1000',paid='0'))
        self.assertEqual(core.roll_day(state,'2026-09-24')['position_compliance'],compliance)

    def test_topup_quantity_respects_cash_fee_and_order_caps(self):
        c=config();c['max_order_value']=2000
        self.assertEqual(floor.topup_quantity('SH600001',10,10,1000,50000,10000,c),200)
        self.assertEqual(floor.topup_quantity('SH600001',10,10,1000,50000,1000,c),0)
        self.assertEqual(floor.topup_quantity('SH688001',10,10,1000,100,1000,c),0)
        self.assertEqual(floor.topup_quantity('SH600001',10,10,50000,50000,10000,c),0)


class Execution(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=object.__new__(runtime.Worker);self.w.run=Path(self.tmp.name);self.w.c=config();self.w.day='2026-09-23'
        self.w.state=dict(day=self.w.day,attempts=[],targets={'SH600000':dict(code='SH600000',quantity=10000,reference_price=10)},
            carried_reserve='100000',deferred={},position_compliance=dict(met=False))
        self.a=dict(positions={'SH600000':10000},sellable={'SH600000':10000},pending={},frozen='0',cash='20000',paid='100000',
                    market_value=100000,total_assets=120000,stock_fraction=100000/120000)
        self.w.state['last_audit']=self.a;self.w.audit=Mock(return_value=self.a);self.w.cancel=Mock();self.w.check_stop=lambda:False
        self.w.two_audits=Mock();self.w.public=None;self.w.broker=Mock();self.w.http=SimpleNamespace(armed=None);self.w.last_submit=0
        self.now=dt.datetime(2026,9,23,10,0,tzinfo=core.CN)
        self.q=dict(code='SH600000',timestamp=self.now.isoformat(),ask=10,last=10,bid=10,lower_limit=9,upper_limit=11,is_st=0,tradable=1)

    def plan(self):
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'quote',return_value=self.q):
            return self.w.plan_position_topup(self.a)

    def test_completed_share_targets_below_floor_are_not_success(self):
        self.w.plan_position_topup=Mock(return_value=False)
        with patch.object(runtime,'now_cn',return_value=self.now):self.assertEqual(self.w.step(),'position_floor_unmet')
        self.w.broker.submit.assert_not_called()

    def test_met_floor_remains_running_until_close(self):
        self.w.state['position_compliance']['met']=True
        with patch.object(runtime,'now_cn',return_value=self.now):self.assertEqual(self.w.step(),'maintaining_position_floor')

    def test_topup_updates_target_without_submitting_and_records_reason(self):
        self.assertTrue(self.plan())
        self.assertEqual(self.w.state['targets']['SH600000']['quantity'],10900)
        self.assertEqual(len(self.w.state['position_topups']),1)
        self.w.broker.submit.assert_not_called()
        self.assertTrue((self.w.run/'position_topups.json').exists())

    def test_topup_keeps_price_guard_and_cap(self):
        self.q['ask']=10.4;self.q['last']=10.4;self.assertFalse(self.plan())
        self.q['ask']=10;self.q['last']=10;self.w.c['capital_limit']=100010;self.assertFalse(self.plan())

    def test_only_exact_terminal_cancellations_release_unfilled_reserve(self):
        order=dict(code='SH600000',side=1,quantity=1000,price=10)
        attempt=dict(order=order,reserve='10020',oid='synthetic',terminal=True,state='8',filled=0)
        self.assertEqual(self.w.used_buy_reserves([attempt]),0)
        attempt.update(state='7',filled=200)
        self.assertEqual(self.w.used_buy_reserves([attempt]),runtime.reserve(2000))
        attempt.update(terminal=False,state='6')
        self.assertEqual(self.w.used_buy_reserves([attempt]),10020)
        attempt.update(terminal=True,state='8',filled=0);attempt.pop('oid')
        self.assertEqual(self.w.used_buy_reserves([attempt]),10020)

    def test_topup_does_not_bypass_stop_pending_or_closed_window(self):
        self.w.check_stop=lambda:True;self.assertFalse(self.plan())
        self.w.check_stop=lambda:False;self.a['pending']={'SH600000':{}};self.assertFalse(self.plan())
        self.a['pending']={};self.now=self.now.replace(hour=14,minute=50);self.assertFalse(self.plan())

    def test_topup_never_adds_a_zero_weight_name(self):
        self.w.state['floor_reference_targets']={'SH600000':dict(quantity=0)}
        self.assertFalse(self.plan())

    def test_actual_submit_still_uses_normal_durable_order_path(self):
        self.assertTrue(self.plan());self.w.broker.submit.return_value='synthetic-order'
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'quote',return_value=self.q),patch.object(runtime,'ACCOUNT','synthetic'):
            self.assertEqual(self.w.step(),'watching')
        order=self.w.broker.submit.call_args.args[0]
        self.assertEqual(order['quantity'],900);self.assertEqual(order['side'],1)
        self.assertEqual(self.w.state['attempts'][0]['oid'],'synthetic-order')


if __name__=='__main__':unittest.main()

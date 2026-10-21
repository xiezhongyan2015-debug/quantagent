"""Offline regression coverage for the 2026-09-23 cash drift and read outage."""
import copy
import unittest
from unittest.mock import Mock, patch
import console_core as core
import console_runtime as runtime
from test_console import state, raw
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending, SessionExpired


class CashTolerance(unittest.TestCase):
    def setUp(self):
        self.s=state();self.r=raw()
    def drift(self,amount):
        self.r['balance']['availBalance']=str(core.dec('9000395.66')+core.dec(amount))
    def check(self):return core.reconcile(self.r,self.s,cash_tolerance_cny=500)
    def pending(self):
        order=dict(code='SH600000',quantity=100,price=10,side=1)
        self.s['attempts']=[dict(oid='known',order=order,filled=0)]
        self.r['orders']=[dict(orderId='known',orderDate=self.s['day'],secCode='600000',drt=1,orderCount=100,orderPrice=10,tradeCount=0,status='2')]
        self.r['balance']['frozenMoney']='1000'
        self.r['balance']['availBalance']=str(core.dec(self.r['balance']['availBalance'])-1000)
    def test_observed_surplus_is_not_new_buying_power(self):
        self.drift('460.93');a=self.check()
        self.assertEqual(core.dec(a['cash']),9000000)
        self.assertEqual(core.dec(a['actual_cash_after_quarantine']),core.dec('9000460.93'))
        self.assertEqual(a['cash_drift_cny'],'460.93')
        self.assertTrue(a['cash_drift_requires_confirmation'])
        self.assertEqual(self.s['baseline']['cash'],'9000000')
    def test_shortfall_uses_lower_actual_available_balance(self):
        self.drift(-500);self.assertEqual(core.dec(self.check()['cash']),8999500)
    def test_exact_boundary_and_three_cent_strict_default(self):
        self.drift(500);self.check()
        with self.assertRaises(FillSnapshotPending):core.reconcile(self.r,self.s)
    def test_large_positive_or_negative_delta_still_stops(self):
        for delta in ('500.01','-500.01'):
            self.drift(delta)
            with self.assertRaisesRegex(FillSnapshotPending,'现金差额'):self.check()
    def test_cannot_absorb_repeated_small_deltas_into_baseline(self):
        self.s['cash_variance']=dict(day=self.s['day'],amount_cny='460.93')
        self.drift(600)
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_mismatched_holdings_still_stop(self):
        self.drift(100);self.r['holds'][0]['count']=1100
        with self.assertRaisesRegex(FillSnapshotPending,'持仓数量'):self.check()
    def test_external_orders_still_stop(self):
        self.drift(100);self.r['orders']=[dict(orderId='external',orderDate=self.s['day'])]
        with self.assertRaisesRegex(TradeError,'外部委托'):self.check()
    def test_unknown_write_result_still_stops(self):
        self.drift(100);self.s['attempts']=[dict(order={})]
        with self.assertRaisesRegex(TradeError,'提交结果不明'):self.check()
    def test_frozen_without_pending_never_accepted_as_drift(self):
        self.drift(100);self.r['balance']['frozenMoney']=100
        with self.assertRaisesRegex(FillSnapshotPending,'冻结资金'):self.check()
    def test_new_drift_cannot_be_accepted_while_order_is_pending(self):
        self.drift('460.93');self.pending()
        with self.assertRaisesRegex(FillSnapshotPending,'须待已知挂单'):self.check()
    def test_confirmed_constant_drift_does_not_block_known_order(self):
        self.drift('460.93');self.pending()
        self.s['cash_variance']=dict(day=self.s['day'],amount_cny='460.93')
        a=self.check();self.assertEqual(core.dec(a['cash']),8999000)
        self.assertIn('known',a['pending']);self.assertFalse(a['cash_drift_requires_confirmation'])
    def test_changed_drift_during_pending_still_stops(self):
        self.drift('470.93');self.pending()
        self.s['cash_variance']=dict(day=self.s['day'],amount_cny='460.93')
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_config_cannot_expand_authorized_tolerance(self):
        for value in ('5000.01',-1,float('nan')):
            with self.assertRaises(TradeError):core.config_checked(dict(core.DEFAULTS,cash_drift_tolerance_cny=value))
    def test_authorized_5000_inclusive_and_positive_surplus_not_spendable(self):
        self.assertEqual(core.config_checked(dict(core.DEFAULTS,cash_drift_tolerance_cny=5000))['cash_drift_tolerance_cny'],5000)
        for amount,expected_cash in ((5000,9000000),(-5000,8995000)):
            self.drift(amount);a=core.reconcile(self.r,self.s,cash_tolerance_cny=5000)
            self.assertEqual(core.dec(a['cash']),expected_cash)
            self.assertTrue(a['cash_drift_requires_confirmation'])
            self.assertEqual(self.s['baseline']['cash'],'9000000')
        for amount in ('5000.01','-5000.01'):
            self.drift(amount)
            with self.assertRaises(FillSnapshotPending):core.reconcile(self.r,self.s,cash_tolerance_cny=5000)
    def test_wider_cash_tolerance_never_absorbs_position_mismatch(self):
        self.drift('2301.733');self.r['holds'][0]['count']+=100
        with self.assertRaisesRegex(FillSnapshotPending,'持仓数量'):core.reconcile(self.r,self.s,cash_tolerance_cny=5000)


class ConfirmCash(unittest.TestCase):
    def setUp(self):
        self.w=object.__new__(runtime.Worker);self.w.state=state();self.w.day=self.w.state['day']
        self.w.c=core.config_checked(core.DEFAULTS);self.w.flush=Mock();self.r=raw()
        self.r['balance']['availBalance']='9000856.59';self.w.raw=Mock(return_value=self.r)
    def test_two_stable_snapshots_required_and_recorded(self):
        with self.assertRaisesRegex(FillSnapshotPending,'第二次'):self.w.audit()
        self.assertNotIn('cash_variance',self.w.state)
        a=self.w.audit()
        self.assertEqual(self.w.state['cash_variance']['amount_cny'],'460.93')
        self.assertEqual(len(self.w.state['cash_variance_events']),1)
        self.assertEqual(core.dec(a['cash']),9000000)
        self.w.audit();self.assertEqual(len(self.w.state['cash_variance_events']),1)
    def test_unstable_or_failed_snapshot_resets_confirmation(self):
        with self.assertRaises(FillSnapshotPending):self.w.audit()
        self.r['holds'][0]['count']=1100
        with self.assertRaises(FillSnapshotPending):self.w.audit()
        self.r['holds'][0]['count']=1000
        with self.assertRaisesRegex(FillSnapshotPending,'第二次'):self.w.audit()
        self.r['balance']['availBalance']='9000866.59'
        with self.assertRaisesRegex(FillSnapshotPending,'第二次'):self.w.audit()
        self.w.audit();self.assertEqual(self.w.state['cash_variance']['amount_cny'],'470.93')
    def test_next_day_rechecks_instead_of_carrying_confirmation(self):
        with self.assertRaises(FillSnapshotPending):self.w.audit()
        self.w.audit()
        rolled=core.roll_day(self.w.state,'2026-09-24')
        self.assertNotIn('cash_variance',rolled)
        self.assertEqual(core.dec(rolled['baseline']['cash']),9000000)
    def test_preflight_timeout_preserves_specific_reason(self):
        self.w.audit=Mock(side_effect=FillSnapshotPending('现金差额+600.00元'))
        self.w.check_stop=lambda:False;self.w.publish=Mock()
        with patch.object(runtime.time,'monotonic',side_effect=[0,0,91]):
            with self.assertRaisesRegex(TradeError,'现金差额\\+600.00元'):self.w.two_audits()
        self.assertEqual(self.w.state['last_snapshot_mismatch']['reason'],'现金差额+600.00元')


class ReadRetries(unittest.TestCase):
    def setUp(self):self.b=object.__new__(runtime.Broker)
    def test_transient_read_retries_without_any_write(self):
        with patch.object(runtime.ChoiceBroker,'_request',side_effect=[TradeError('Choice transport/JSON failure; do not retry orders'),{'Data':{}}]) as query,patch.object(runtime.time,'sleep'):
            self.assertEqual(self.b._request('AguTrade/SpoOrders',params={'example':1}),{'Data':{}})
        self.assertEqual(query.call_count,2)
        self.assertTrue(all('payload' not in call.kwargs for call in query.call_args_list))
    def test_three_failed_reads_return_to_snapshot_wait(self):
        with patch.object(runtime.ChoiceBroker,'_request',side_effect=TradeError('Choice HTTP response requires reconciliation')) as query,patch.object(runtime.time,'sleep'):
            with self.assertRaisesRegex(FillSnapshotPending,'连续3次失败'):self.b._request('AguTrade/SpoOrders')
        self.assertEqual(query.call_count,3)
    def test_writes_are_never_retried(self):
        for path in ('AguTrade/SpoOrder','AguTrade/SpoCancel'):
            with patch.object(runtime.ChoiceBroker,'_request',side_effect=TradeError('Choice transport/JSON failure; do not retry orders')) as query:
                with self.assertRaises(TradeError):self.b._request(path,payload={'example':1})
            self.assertEqual(query.call_count,1)
    def test_auth_and_business_errors_are_not_blindly_retried(self):
        for error in (SessionExpired(),TradeError('Choice business failure; not an empty account')):
            with patch.object(runtime.ChoiceBroker,'_request',side_effect=error) as query:
                with self.assertRaises(type(error)):self.b._request('AguTrade/SpoOrders')
            self.assertEqual(query.call_count,1)


class ReferenceQuoteRetries(unittest.TestCase):
    def setUp(self):
        self.w=object.__new__(runtime.Worker);self.w.public=None;self.w.state={}
        self.w.check_stop=lambda:False;self.w.flush=Mock();self.w.publish=Mock()
    def test_reference_timeout_recovers_with_new_request(self):
        expected=dict(code='SH600000',previous_close=10)
        with patch.object(runtime,'quote',side_effect=[runtime.requests.ReadTimeout(),expected]) as query,patch.object(runtime.time,'sleep'):
            self.assertEqual(self.w.reference_quote('SH600000'),expected)
        self.assertEqual(query.call_count,2)
        self.assertEqual(self.w.state['read_retry_events'][0]['error_type'],'ReadTimeout')
    def test_reference_http_error_can_retry(self):
        with patch.object(runtime,'quote',side_effect=[TradeError('Quote HTTP failure'),{'code':'SH600000'}]) as query,patch.object(runtime.time,'sleep'):
            self.w.reference_quote('SH600000')
        self.assertEqual(query.call_count,2)
    def test_persistent_failure_has_bound_and_specific_stock(self):
        with patch.object(runtime,'quote',side_effect=runtime.requests.ReadTimeout()) as query,patch.object(runtime.time,'sleep'):
            with self.assertRaisesRegex(TradeError,'连续3次失败：SH600000'):self.w.reference_quote('SH600000')
        self.assertEqual(query.call_count,3)
    def test_security_identity_error_is_not_overridden(self):
        with patch.object(runtime,'quote',side_effect=TradeError('Quote security mismatch')) as query:
            with self.assertRaisesRegex(TradeError,'security mismatch'):self.w.reference_quote('SH600000')
        self.assertEqual(query.call_count,1)
    def test_stop_prevents_even_reference_requests(self):
        self.w.check_stop=lambda:True
        with patch.object(runtime,'quote') as query:
            with self.assertRaisesRegex(TradeError,'用户已停止'):self.w.reference_quote('SH600000')
        query.assert_not_called()


if __name__=='__main__':unittest.main()

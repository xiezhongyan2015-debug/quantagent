"""Lost acknowledgements must never cause an unverified or duplicate order."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import console_core as core
import console_runtime as runtime
import receipt_recovery as receipts
import test_console
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending


def fixture():
    book=test_console.Accounting();book.setUp();book.order(1,200)
    attempt=book.s['attempts'][0];attempt.pop('oid')
    attempt.update(state='UNKNOWN',submit_failed_at='2026-09-23T10:00:15+08:00')
    book.r['orders'][0].update(orderTime='10:00:01',orderType=1)
    return book.s,book.r


class Candidate(unittest.TestCase):
    def setUp(self):self.s,self.r=fixture();self.c=core.config_checked(core.DEFAULTS)
    def check(self):return receipts.candidate(self.r,self.s,self.c)
    def test_unique_exact_receipt_requires_full_account_reconciliation(self):
        original=copy.deepcopy(self.s);proposed,audit,oid=self.check()
        self.assertEqual(oid,'1');self.assertEqual(proposed['attempts'][0]['filled'],200)
        self.assertEqual(audit['positions']['SH600000'],1200);self.assertEqual(self.s,original)
    def test_absence_never_means_safe_to_resubmit(self):
        self.r['orders']=[]
        with self.assertRaisesRegex(FillSnapshotPending,'不自动重发'):self.check()
        self.assertNotIn('oid',self.s['attempts'][0])
    def test_more_than_one_new_order_is_ambiguous(self):
        self.r['orders'].append(dict(self.r['orders'][0],orderId='another',secCode='600001'))
        with self.assertRaisesRegex(TradeError,'多笔未关联'):self.check()
    def test_duplicate_server_ids_are_not_a_unique_receipt(self):
        self.r['orders']*=2
        with self.assertRaises(TradeError):self.check()
    def test_every_identity_field_must_match(self):
        for key,value in [('secCode','600001'),('drt',2),('orderCount',300),('orderPrice',10.01),('orderType',2),('orderDate','2026-09-24')]:
            with self.subTest(key=key):
                self.s,self.r=fixture();self.r['orders'][0][key]=value
                with self.assertRaises(TradeError):self.check()
    def test_same_stock_and_price_outside_submit_window_is_rejected(self):
        for time in ('09:59:55','10:00:20'):
            self.r['orders'][0]['orderTime']=time
            with self.assertRaisesRegex(TradeError,'提交窗口'):self.check()
    def test_missing_or_invalid_server_time_does_not_match(self):
        self.r['orders'][0]['orderTime']='not-a-time'
        with self.assertRaises(TradeError):self.check()
    def test_multiple_unknown_intents_require_manual_review(self):
        self.s['attempts'].append(copy.deepcopy(self.s['attempts'][0]))
        with self.assertRaisesRegex(TradeError,'多笔结果不明'):self.check()
    def test_account_cash_mismatch_blocks_candidate(self):
        self.r['balance']['availBalance']=str(core.dec(self.r['balance']['availBalance'])-1000)
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_positions_and_fill_links_still_have_to_match(self):
        self.r['holds'][0]['count']=1300
        with self.assertRaises(FillSnapshotPending):self.check()
        self.s,self.r=fixture();self.r['deals']=[]
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_legacy_window_requires_logged_post_and_following_get(self):
        intent=self.s['attempts'][0];intent.pop('submit_failed_at')
        trace=[dict(method='POST',endpoint='AguTrade/SpoOrder',at=intent['submitted_at']),
               dict(method='GET',endpoint='AguTrade/SpoOrders',at='2026-09-23T10:00:16+08:00')]
        start,end=receipts.submission_window(intent,trace)
        self.assertEqual((end-start).total_seconds(),16)
        with self.assertRaises(TradeError):receipts.submission_window(intent,trace[:1])
    def test_overlong_or_missing_timezone_window_is_rejected(self):
        intent=self.s['attempts'][0];intent['submit_failed_at']='2026-09-23T10:02:00+08:00'
        with self.assertRaises(TradeError):self.check()
        intent['submitted_at']='2026-09-23T10:00:00'
        with self.assertRaises(TradeError):self.check()


class ReadOnlyRecovery(unittest.TestCase):
    def setUp(self):
        self.s,self.r=fixture();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=object.__new__(runtime.Worker);self.w.run=Path(self.tmp.name);self.w.state=self.s
        self.w.c=core.config_checked(core.DEFAULTS);self.w.day=self.s['day'];self.w.http=SimpleNamespace(writes_suspended=False)
        self.w.broker=Mock();self.w.check_stop=lambda:False;self.w.publish=Mock()
    def recover(self):
        with patch.object(runtime.time,'sleep'):self.w.recover_submission()
    def test_two_reads_bind_same_server_id_without_a_post(self):
        def read():
            self.assertFalse(self.w.state['attempts'][0].get('oid'))
            self.assertTrue(self.w.http.writes_suspended)
            return copy.deepcopy(self.r)
        self.w.raw=Mock(side_effect=read);self.recover()
        self.assertEqual(self.w.raw.call_count,2)
        self.assertEqual(self.w.state['attempts'][0]['oid'],'1')
        self.assertEqual(self.w.state['attempts'][0]['filled'],200)
        self.assertEqual(self.w.state['receipt_recovery_events'][0]['orders_resent'],0)
        self.assertTrue(self.w.http.writes_suspended)
        self.w.broker.submit.assert_not_called();self.w.broker.cancel.assert_not_called()
        self.assertTrue((self.w.run/'receipt_recovery_confirmed.json').exists())
    def test_absent_after_timeout_stays_unknown_and_no_write(self):
        absent=copy.deepcopy(self.r);absent['orders']=[];self.w.raw=Mock(return_value=absent)
        with patch.object(runtime.time,'monotonic',side_effect=[0,91]),patch.object(runtime.time,'sleep'):
            with self.assertRaisesRegex(TradeError,'仍未确认'):self.w.recover_submission()
        self.assertNotIn('oid',self.w.state['attempts'][0]);self.assertTrue(self.w.http.writes_suspended)
        self.w.broker.submit.assert_not_called()
        self.assertEqual(self.w.state['ack_recovery']['phase'],'unresolved')
    def test_candidate_disappearing_on_second_read_never_commits(self):
        absent=copy.deepcopy(self.r);absent['orders']=[]
        self.w.raw=Mock(side_effect=[self.r,absent])
        with patch.object(runtime.time,'monotonic',side_effect=[0,1,91]),patch.object(runtime.time,'sleep'):
            with self.assertRaises(TradeError):self.w.recover_submission()
        self.assertNotIn('oid',self.w.state['attempts'][0])
    def test_fill_regression_on_second_read_is_rejected(self):
        smaller=copy.deepcopy(self.r);smaller['orders'][0]['tradeCount']=100;smaller['orders'][0]['status']='3'
        smaller['deals'][0]['tradeCount']=100;smaller['holds'][0]['count']=1100
        smaller['balance']['availBalance']=str(core.dec(smaller['balance']['availBalance'])+1000)
        self.w.raw=Mock(side_effect=[self.r,smaller])
        with self.assertRaisesRegex(TradeError,'倒退'):self.recover()
        self.assertNotIn('oid',self.w.state['attempts'][0])
    def test_stop_prevents_all_recovery_requests(self):
        self.w.raw=Mock();self.w.check_stop=lambda:True
        with self.assertRaisesRegex(TradeError,'用户已停止'):self.recover()
        self.w.raw.assert_not_called()
    def test_account_history_is_also_queried_for_missing_receipt(self):
        broker=Mock();broker._rows.side_effect=[[dict(orderId='known')],[],[]];broker._request.return_value={'Data':{}}
        with patch.object(runtime,'historical_rows',side_effect=[[dict(orderId='known'),dict(orderId='lost')],[]]) as history:
            snapshot=runtime.account_snapshot(broker,dict(prior={},attempts=[dict(oid='known'),dict(order={})]))
        self.assertEqual(history.call_count,2);self.assertEqual(len(snapshot['orders']),2)
    def test_normal_audit_resumes_only_after_recovered_id_and_fresh_audit(self):
        self.w.raw=Mock(return_value=self.r)
        with patch.object(runtime.time,'sleep'):audit=self.w.audit()
        self.assertEqual(self.w.raw.call_count,3)
        self.assertFalse(self.w.http.writes_suspended)
        self.assertEqual(audit['positions']['SH600000'],1200)
        self.assertEqual(self.w.state['attempts'][0]['state'],'4')
    def test_failed_post_recovery_audit_keeps_writes_suspended(self):
        bad=copy.deepcopy(self.r);bad['balance']['availBalance']=str(core.dec(bad['balance']['availBalance'])-1000)
        self.w.raw=Mock(side_effect=[self.r,self.r,bad])
        with patch.object(runtime.time,'sleep'):
            with self.assertRaises(FillSnapshotPending):self.w.audit()
        self.assertTrue(self.w.http.writes_suspended)
        self.w.broker.submit.assert_not_called()


if __name__=='__main__':unittest.main()

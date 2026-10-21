"""Cross-day closure requires account evidence; elapsed time alone never suffices."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import console_core as core
import console_runtime as runtime
import receipt_recovery as receipts
import reconcile_previous_receipt as recovery
from test_uncertain_isolation import fixture
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import TradeError


class Expiry(unittest.TestCase):
    def setUp(self):
        s,self.raw,self.config=fixture()
        old=dt.datetime(2026,9,23,13,5,tzinfo=core.CN)
        audit,_=receipts.isolation_candidate(self.raw,s,self.config,old)
        self.state=receipts.isolate(s,audit,'first','second',old.isoformat())
        self.state['last_audit']=core.reconcile(self.raw,self.state,cash_tolerance_cny=500,allow_quarantined=True)
        self.raw['history_complete']=True
        self.now=dt.datetime(2026,9,24,8,tzinfo=core.CN)
        self.observations=[dict(raw=copy.deepcopy(self.raw),
            started_at=(self.now-dt.timedelta(seconds=age+1)).isoformat(),
            finished_at=(self.now-dt.timedelta(seconds=age)).isoformat()) for age in (7,1)]
    def propose(self):return receipts.expire_previous_day(self.state,self.config,self.observations,self.now)
    def mutate_raw(self,callback):
        for x in self.observations:callback(x['raw'])
    def test_closure_keeps_original_unknown_without_fake_id_and_releases_reserve(self):
        before=copy.deepcopy(self.state);proposed=self.propose()
        self.assertEqual(self.state,before);self.assertEqual(proposed['baseline'],before['baseline'])
        self.assertEqual(proposed['attempts'],[])
        entry=proposed['expired_unacknowledged'][0]
        self.assertEqual(entry['classification'],'EXPIRED_UNOBSERVED')
        self.assertEqual(entry['intent'],before['attempts'][0]);self.assertNotIn('oid',entry['intent'])
        self.assertEqual(entry['intent']['state'],'UNKNOWN')
        self.assertEqual(core.dec(proposed['last_audit']['cash'])-core.dec(before['last_audit']['cash']),core.dec('1011'))
        self.assertEqual(proposed['last_audit']['uncertain_buy_reserve'],'0')
        self.assertEqual(core.submission_count(proposed),1)
    def test_archive_survives_day_roll_and_new_day_has_zero_submissions(self):
        proposed=self.propose();rolled=core.roll_day(proposed,'2026-09-24')
        self.assertEqual(rolled['expired_unacknowledged'],proposed['expired_unacknowledged'])
        self.assertEqual(core.submission_count(rolled),0)
        self.assertEqual(core.reconcile(self.raw,rolled)['positions'],self.state['baseline']['positions'])
    def test_old_unresolved_still_cannot_roll_without_evidence(self):
        with self.assertRaisesRegex(TradeError,'未确认委托'):core.roll_day(self.state,'2026-09-24')
    def test_same_day_cannot_expire(self):
        self.now=self.now-dt.timedelta(days=1)
        with self.assertRaisesRegex(TradeError,'尚未跨过'):self.propose()
    def test_missing_isolation_or_multiple_unknowns_or_sell_cannot_expire(self):
        for mode in ('isolation','multiple','sell'):
            with self.subTest(mode=mode):
                original=copy.deepcopy(self.state)
                if mode=='isolation':self.state['attempts'][0].pop('isolation')
                elif mode=='multiple':self.state['attempts']*=2
                else:self.state['attempts'][0]['order']['side']=2
                with self.assertRaises(TradeError):self.propose()
                self.state=original
    def test_opt_out_rejects(self):
        self.config['isolate_unknown_orders']=False
        with self.assertRaises(TradeError):self.propose()
    def test_one_snapshot_incomplete_history_or_stale_reads_reject(self):
        original=copy.deepcopy(self.observations)
        for mode in ('one','history','stale','interval'):
            with self.subTest(mode=mode):
                self.observations=copy.deepcopy(original)
                if mode=='one':self.observations=self.observations[:1]
                elif mode=='history':self.observations[1]['raw']['history_complete']=False
                elif mode=='stale':self.observations[0]['started_at']=(self.now-dt.timedelta(seconds=121)).isoformat()
                else:self.observations[0]['finished_at']=(self.now-dt.timedelta(seconds=2)).isoformat()
                with self.assertRaises(TradeError):self.propose()
    def test_changed_positions_and_unrecognized_deals_reject(self):
        self.observations[1]['raw']['holds'][0]['count']+=100
        with self.assertRaises(TradeError):self.propose()
        self.setUp();self.observations[1]['raw']['deals']=[dict(orderId='unknown')]
        with self.assertRaises(TradeError):self.propose()
    def test_frozen_cash_pending_or_unconfirmed_cash_drift_reject(self):
        for field,value in (('frozenMoney',100),('availBalance','9000495.66')):
            with self.subTest(field=field):
                self.setUp();self.mutate_raw(lambda r:r['balance'].update({field:value}))
                with self.assertRaises(TradeError):self.propose()
        self.setUp()
        self.state['attempts'].append(dict(oid='pending',order=dict(code='SH600000',side=1,quantity=100,price=10),filled=0))
        self.mutate_raw(lambda r:r['orders'].append(dict(orderId='pending',orderDate='2026-09-23',secCode='600000',drt=1,orderCount=100,orderPrice=10,tradeCount=0,status='2')))
        with self.assertRaises(TradeError):self.propose()
    def test_existing_matching_receipt_cannot_be_discarded_even_if_cancelled(self):
        self.mutate_raw(lambda r:r['orders'].append(dict(orderId='late',orderDate='2026-09-23',secCode='600001',drt=1,
            orderCount=100,orderPrice=10,orderType=1,orderTime='10:00:01',tradeCount=0,status='8')))
        with self.assertRaisesRegex(TradeError,'候选委托'):self.propose()
    def test_two_accepted_small_cash_drifts_must_still_match(self):
        self.state['cash_variance']=dict(day=self.state['day'],amount_cny='100')
        self.observations[0]['raw']['balance']['availBalance']='9000495.66'
        self.observations[1]['raw']['balance']['availBalance']='9000495.67'
        with self.assertRaisesRegex(TradeError,'两轮跨日账户核验不一致'):self.propose()
    def test_late_old_cancelled_order_trips_guard_after_day_roll(self):
        rolled=core.roll_day(self.propose(),'2026-09-24')
        raw=copy.deepcopy(self.raw)
        raw['orders']=[dict(orderId='late',orderDate='2026-09-23',secCode='600001',tradeCount=0,status='8')]
        with self.assertRaisesRegex(TradeError,'迟到回执'):core.reconcile(raw,rolled)
    def test_tampered_archive_blocks_roll_and_reconciliation(self):
        proposed=self.propose();proposed['expired_unacknowledged'][0]['intent']['order']['quantity']=200
        with self.assertRaises(TradeError):core.roll_day(proposed,'2026-09-24')
        with self.assertRaises(TradeError):core.reconcile(self.raw,proposed)
    def test_history_is_always_queried_with_archive_even_when_current_orders_present(self):
        rolled=core.roll_day(self.propose(),'2026-09-24');broker=Mock()
        broker._rows.side_effect=[[dict(orderId='today')],[],self.raw['holds']]
        broker._request.return_value={'Data':self.raw['balance']}
        with patch.object(runtime,'historical_rows',return_value=[]) as history:
            snapshot=runtime.account_snapshot(broker,rolled)
        self.assertTrue(snapshot['history_complete']);self.assertEqual(history.call_count,2)
        broker.submit.assert_not_called();broker.cancel.assert_not_called()
    def test_latest_session_does_not_fall_back_to_older_unresolved_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            old=Path(folder)/'old';new=Path(folder)/'new'
            save(old/'private_state.json',self.state);save(new/'private_state.json',self.propose())
            import os
            os.utime(old/'private_state.json',(100,100));os.utime(new/'private_state.json',(200,200))
            with patch.object(runtime,'session_dirs',return_value=[old,new]):self.assertEqual(runtime.latest_session(),new)
    def test_source_race_or_running_worker_cannot_overwrite_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';evidence=Path(folder)/'evidence';proposed=self.propose()
            changed=dict(self.state,deferred={'change':'concurrent edit'})
            save(source/'private_state.json',changed);before=(source/'private_state.json').read_bytes()
            with patch.object(recovery,'active_session',return_value=None):
                with self.assertRaisesRegex(TradeError,'来源账本发生变化'):recovery.commit_source(source,self.state,proposed,evidence)
            self.assertEqual((source/'private_state.json').read_bytes(),before)
            save(source/'private_state.json',self.state);before=(source/'private_state.json').read_bytes()
            with patch.object(recovery,'active_session',return_value=source):
                with self.assertRaisesRegex(TradeError,'交易进程'):recovery.commit_source(source,self.state,proposed,evidence)
            self.assertEqual((source/'private_state.json').read_bytes(),before)
    def test_startup_routes_old_unknown_through_readonly_resolution_before_roll(self):
        class ReachedReadOnly(Exception):pass
        import contextlib
        with patch.object(runtime,'mutex',side_effect=lambda _:contextlib.nullcontext()), \
                patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'active_session',return_value=None), \
                patch.object(runtime,'days_for'),patch.object(runtime,'latest_session',return_value=Path('source')), \
                patch.object(runtime,'read_json',return_value=self.state), \
                patch.object(recovery,'resolve_source',side_effect=ReachedReadOnly) as resolve, \
                patch.object(runtime.subprocess,'Popen') as process:
            with self.assertRaises(ReachedReadOnly):runtime.start_console(dict(self.config,prefer_sealed_plan=False))
            resolve.assert_called_once();process.assert_not_called()


if __name__=='__main__':unittest.main()

"""Offline recovery, concurrency and conservative valuation regressions."""
import copy, datetime as dt, json, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import test_workflow_rebalance as fixtures
import console_runtime as rt
import workflow_rebalance as w
from choice_auth import IdentityMismatch, LoginUnavailable

class Efficiency(fixtures.Workflow):
    # Inherit the setup only; the base tests remain in their own test module.
    def test_small_mark_timing_gap_has_conservative_bounds(self):
        a=dict(copy.deepcopy(self.audit),market_value='143100')
        b=w.valuation_bounds(a)
        self.assertEqual(b['lower_stock'],143000)
        self.assertEqual(b['upper_stock'],143100)
        self.assertEqual(b['lower_nav'],154900)
        self.assertEqual(b['upper_nav'],155000)
        self.assertEqual(b['uncertainty'],100)
        self.assertEqual(a['cash'],self.audit['cash'])

    def test_large_gap_remains_readonly(self):
        with self.assertRaises(rt.FillSnapshotPending):
            w.valuation_bounds(dict(self.audit,market_value='144000'))

    def test_buy_cap_uses_upper_stock(self):
        order=self.formal()
        a=self.state['last_audit'];a['market_value']='146200'
        a['position_mark_values']['SZ000001']='4350'
        # Reported stock alone would permit this order; upper stock disallows it.
        self.assertLessEqual(w.dec(a['market_value'])+w.reserved(10,100),w.dec(a['total_assets'])*w.dec('.95'))
        with self.assertRaises(rt.TradeError):w.guard_formal(self.c,self.state,order,fixtures.NOW)

    def test_sell_cap_uses_lower_stock(self):
        a=dict(positions={'SZ000001':100,'SZ001800':100},position_mark_values={'SZ000001':'80200','SZ001800':'10550'},market_value='90800',total_assets='100000')
        # CSI500 equality using raw marks is insufficient after the uncertainty haircut.
        self.assertFalse(w.sell_room(self.c,a,self.members,'SZ000001',200,1))

    def test_parallel_snapshot_reads_all_four_endpoints(self):
        barrier=threading.Barrier(4)
        class Reads:
            def _rows(self,path):
                barrier.wait(timeout=2)
                return [{'orderId':'existing'}] if path.endswith('SpoOrders') else []
            def _request(self,path,params):
                barrier.wait(timeout=2);return {'Data':{'totalAssets':1}}
        result=rt.account_snapshot(Reads(),{'attempts':[{'oid':'existing'}]})
        self.assertEqual(result['orders'],[{'orderId':'existing'}])
        self.assertEqual(result['balance'],{'totalAssets':1})
        self.assertFalse(result['history_complete'])

    def test_read_failure_produces_no_partial_account(self):
        broker=Mock();broker._rows.side_effect=rt.FillSnapshotPending('temporary GET failure')
        with self.assertRaises(rt.FillSnapshotPending):rt.account_snapshot(broker,{'attempts':[]})
        broker.submit.assert_not_called()

    def test_viewer_reads_small_status_without_loading_private_ledger(self):
        (self.root/'status_redacted.json').write_text(json.dumps(dict(status='watching',verified_at='fresh',cash='123',frozen='0',remaining_count=5,unresolved_intents=0)))
        (self.root/'private_state.json').write_text('invalid large ledger must not be read by viewer')
        with patch.object(rt,'alive',return_value=True):result=rt.status_view(self.root)
        self.assertEqual(result['remaining_count'],5);self.assertTrue(result['running'])

    def worker(self):
        worker=rt.Worker.__new__(rt.Worker)
        worker.state=copy.deepcopy(self.state);worker.c=self.c;worker.original_config=self.c
        worker.day=w.DAY;worker.run=self.root;worker.http=Mock();worker.http.writes_suspended=False
        worker.login=Mock();worker.two_audits=Mock();worker.audit=Mock(return_value=self.audit)
        worker.cancel=Mock();worker.check_stop=Mock(return_value=False);worker.flush=Mock();worker.publish=Mock()
        worker.broker=Mock();worker.auth=Mock();worker.public=Mock();worker.fail_since=0;worker.last_success=0
        (self.root/'status_redacted.json').write_text('{}')
        return worker

    def test_transient_failure_resumes_same_ledger_without_new_order(self):
        worker=self.worker();before=copy.deepcopy(worker.state['attempts'])
        worker.step=Mock(side_effect=[rt.FillSnapshotPending('delayed GET'), 'completed_targets'])
        with patch.object(rt,'now_cn',return_value=fixtures.NOW),patch.object(rt.time,'monotonic',return_value=1000),patch.object(rt.time,'sleep'):
            self.assertEqual(worker.execute(),'completed_targets')
        self.assertEqual(worker.state['attempts'],before)
        self.assertEqual(worker.state['automatic_recovery']['phase'],'ready')
        self.assertEqual(worker.step.call_count,2);worker.broker.submit.assert_not_called()

    def test_identity_failure_never_auto_resumes(self):
        worker=self.worker();worker.step=Mock(side_effect=IdentityMismatch('wrong account'))
        with patch.object(rt,'now_cn',return_value=fixtures.NOW),patch.object(rt.time,'sleep'):
            self.assertEqual(worker.execute(),'stopped_review_required')
        self.assertEqual(worker.step.call_count,1);self.assertNotIn('automatic_recovery',worker.state)
        worker.broker.submit.assert_not_called()

    def test_memory_error_recovers_without_discarding_intent(self):
        worker=self.worker();before=copy.deepcopy(worker.state['attempts']);worker.step=Mock(side_effect=[MemoryError(), 'completed_targets'])
        with patch.object(rt,'now_cn',return_value=fixtures.NOW),patch.object(rt.time,'sleep'),patch.object(rt.gc,'collect') as collect:
            self.assertEqual(worker.execute(),'completed_targets')
        collect.assert_called_once();self.assertEqual(worker.state['attempts'],before)
        worker.broker.submit.assert_not_called()

    def test_transient_login_refresh_does_not_exit(self):
        worker=self.worker();worker.login=Mock(side_effect=[LoginUnavailable('temporary SSO'),None]);worker.step=Mock(return_value='completed_targets')
        with patch.object(rt,'now_cn',return_value=fixtures.NOW),patch.object(rt.time,'sleep'):
            self.assertEqual(worker.execute(),'completed_targets')
        self.assertEqual(worker.login.call_count,2);worker.broker.submit.assert_not_called()

    def test_stop_interrupts_backoff(self):
        worker=self.worker();worker.check_stop=Mock(return_value=True)
        with patch.object(rt,'now_cn',return_value=fixtures.NOW),patch.object(rt.time,'sleep') as sleep:
            worker.transient_pause(rt.FillSnapshotPending('slow'))
        sleep.assert_not_called();self.assertTrue(worker.http.writes_suspended)

def load_tests(loader,tests,pattern):
    # Avoid running inherited base tests twice.
    names=[name for name in Efficiency.__dict__ if name.startswith('test_')]
    return unittest.TestSuite(Efficiency(name) for name in names)

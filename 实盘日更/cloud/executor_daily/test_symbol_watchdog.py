"""Offline watchdog safety and delivery-state regression tests."""
import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import symbol_watchdog as w

NOW = dt.datetime(2026, 10, 13, 10, 0, tzinfo=w.CN)


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.s = dict(day='2026-10-13', targets={'SZ300919':dict(quantity=100, reference_price=20)},
                      deferred={'SZ300919':'不利方向超过3%价格保护或价格精度'}, attempts=[],
                      last_audit=dict(checked_at=NOW.isoformat(),positions={'SZ300919':900},
                        sellable={'SZ300919':900},position_mark_values={'SZ300919':'18000'},pending={}))
        self.st = dict(execution_date=self.s['day'],status='watching',at=NOW.isoformat(),deferred={})
        self.previous = {}

    def tick(self, minute, *, second=0):
        now = NOW+dt.timedelta(minutes=minute, seconds=second)
        self.s['last_audit']['checked_at']=now.isoformat();self.st['at']=now.isoformat()
        self.previous, events = w.observe(self.previous,self.s,self.st,now)
        return events

    def wait(self, stop):
        events=[]
        for m in range(stop+1):events+=self.tick(m)
        return events

    def test_no_alert_until_ten_minutes(self):
        self.assertEqual(self.wait(9),[])
        events=self.tick(10);self.assertEqual(len(events),1)
        self.assertIn('800股',events[0]['text']);self.assertIn('16000.00元',events[0]['text'])

    def test_reminder_deduplicates_until_two_more_hours(self):
        self.assertEqual(len(self.wait(10)),1)
        for m in range(11,60):self.assertEqual(self.tick(m),[])   # 30 more minutes no longer re-alert
        self.previous['entries']['SZ300919']['seconds']=600+7200-90
        self.assertEqual(self.tick(60),[])                          # 7770 s < 10 min + 2 h
        self.assertEqual(len(self.tick(61)),1)                      # 7830 s: one reminder

    def _real_tick(self, mutate, nows):
        import corporate_action_runtime as ca
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);run=folder/'run';run.mkdir();calendar=folder/'calendar.json'
            calendar.write_text(json.dumps([self.s['day']]))
            s=copy.deepcopy(self.s);st=copy.deepcopy(self.st);mutate(s,st)
            (run/'private_state.json').write_text(json.dumps(s));(run/'status_redacted.json').write_text(json.dumps(st))
            results=[]
            for now in nows:
                with patch.object(w,'CALENDAR',calendar),patch.object(ca,'root',return_value=folder),patch.object(ca,'clocks',return_value=now),patch('console_runtime.active_session',return_value=run):
                    results.append(w.run_once(now))
            return results,[json.loads(p.read_text())['kind'] for p in (folder/'outbox').glob('*.json')] if (folder/'outbox').exists() else []

    def test_finished_executor_is_silent(self):
        def done(s,st):st['status']='stopped_review_required';s['last_audit']['checked_at']=(NOW-dt.timedelta(hours=1)).isoformat();st['at']=s['last_audit']['checked_at']
        res,kinds=self._real_tick(done,[NOW,NOW+dt.timedelta(minutes=30)])
        self.assertEqual(res,[0,0]);self.assertEqual(kinds,[])

    def test_session_start_without_audit_is_silent(self):
        res,kinds=self._real_tick(lambda s,st:s.pop('last_audit'),[NOW])
        self.assertEqual(res,[0]);self.assertEqual(kinds,[])

    def test_brief_stale_audit_is_silent_but_persistent_stale_alerts_once(self):
        def old(s,st):s['last_audit']['checked_at']=(NOW-dt.timedelta(seconds=300)).isoformat()
        res,kinds=self._real_tick(old,[NOW,NOW+dt.timedelta(minutes=5)])
        self.assertEqual(res,[0,0]);self.assertEqual(kinds,[])
        res,kinds=self._real_tick(old,[NOW,NOW+dt.timedelta(minutes=5),NOW+dt.timedelta(minutes=11),NOW+dt.timedelta(minutes=12)])
        self.assertEqual(res[:2],[0,0]);self.assertEqual(kinds,['symbol_watchdog_failed'])

    def test_old_deferred_without_real_gap_not_alerted(self):
        self.s['targets']['SZ300919']['quantity']=900
        self.assertEqual(self.wait(15),[])

    def test_progress_resets_clock_and_announces_progress(self):
        self.wait(10);self.s['last_audit']['positions']['SZ300919']=800
        events=self.tick(11);self.assertEqual(len(events),1);self.assertIn('已有执行进展',events[0]['text'])
        self.assertEqual(self.previous['entries']['SZ300919']['seconds'],0)
        for m in range(12,21):self.assertEqual(self.tick(m),[])
        self.assertIn('700股',self.tick(21)[0]['text'])

    def test_completed_gap_sends_one_resolution(self):
        self.wait(10);self.s['last_audit']['positions']['SZ300919']=100
        self.assertIn('目标差额已完成',self.tick(11)[0]['text']);self.assertEqual(self.tick(12),[])

    def test_pending_order_is_not_declared_completed(self):
        self.wait(10);self.s['last_audit']['pending']={'p':{'code':'SZ300919'}}
        text=self.tick(11)[0]['text'];self.assertNotIn('差额已完成',text)
        self.assertIn('仍需核验',text)

    def test_reason_flapping_does_not_erase_wait(self):
        self.wait(9);self.s['deferred']['SZ300919']='卖出将低于90%股票或80%中证500比例'
        self.assertIn('低于90%',self.tick(10)[0]['text'])

    def test_unsubmitted_normal_gap_not_marked_blocked(self):
        self.s['deferred']={};self.assertEqual(self.wait(15),[])

    def test_zero_cancel_without_deferred_is_visible(self):
        self.s['deferred']={};self.s['attempts']=[dict(order={'code':'SZ300919'},state='8',filled=0,terminal=True,oid='old')]
        self.assertIn('零成交撤单',self.wait(10)[0]['text'])

    def test_rejection_stays_notification_only(self):
        self.s['deferred']={};self.s['attempts']=[dict(order={'code':'SZ300919'},state='9',filled=0,terminal=True,oid='old')]
        self.assertIn('不自动重报',self.wait(10)[0]['text'])

    def test_odd_lot_on_running_old_worker_is_visible(self):
        self.s['deferred']={};self.s['targets']={'SZ001309':dict(quantity=700,reference_price=235)}
        self.s['last_audit'].update(positions={'SZ001309':780},sellable={'SZ001309':500},position_mark_values={'SZ001309':'183300'})
        event=self.wait(10)[0]['text'];self.assertIn('80股',event);self.assertIn('零股',event)

    def test_t1_shortfall_is_visible(self):
        self.s['deferred']={};self.s['last_audit']['sellable']['SZ300919']=0
        self.assertIn('T+1',self.wait(10)[0]['text'])

    def test_lunch_is_excluded(self):
        a=NOW.replace(hour=11,minute=25);b=NOW.replace(hour=13,minute=5)
        self.assertEqual(w.trading_seconds(a,b),600)
        self.assertFalse(w.in_session(NOW.replace(hour=12)))

    def test_polling_outage_not_counted_as_confirmed_wait(self):
        self.tick(0);self.assertEqual(self.tick(30),[])
        self.assertEqual(self.previous['entries']['SZ300919']['seconds'],0)

    def test_global_pause_not_counted(self):
        self.wait(5);self.st['status']='stopped_review_required'
        for m in range(6,20):self.assertEqual(self.tick(m),[])
        self.st['status']='watching';self.assertEqual(self.tick(20),[])
        self.assertEqual(self.previous['entries']['SZ300919']['seconds'],300)

    def test_other_stock_progress_does_not_reset_blocked_stock(self):
        self.wait(9);self.s['targets']['SZ000001']=dict(quantity=100,reference_price=10)
        self.s['last_audit']['positions']['SZ000001']=100
        self.assertIn('SZ300919',self.tick(10)[0]['text'])

    def test_snapshot_day_staleness_future_and_integer_validation(self):
        for change in ['old','future','wrongday','fractional']:
            s=copy.deepcopy(self.s);st=copy.deepcopy(self.st)
            if change=='old':s['last_audit']['checked_at']=(NOW-dt.timedelta(seconds=121)).isoformat()
            elif change=='future':st['at']=(NOW+dt.timedelta(seconds=1)).isoformat()
            elif change=='wrongday':s['day']='2026-10-12'
            else:s['last_audit']['positions']['SZ300919']=900.5
            with self.subTest(change=change),self.assertRaises(ValueError):w.observe({},s,st,NOW)

    def test_inputs_are_not_mutated(self):
        s=copy.deepcopy(self.s);st=copy.deepcopy(self.st);old=copy.deepcopy(self.previous)
        w.observe(self.previous,self.s,self.st,NOW)
        self.assertEqual(self.s,s);self.assertEqual(self.st,st);self.assertEqual(self.previous,old)

    def test_monitor_state_survives_restart(self):
        self.wait(9);self.previous=json.loads(json.dumps(self.previous))
        self.assertEqual(len(self.tick(10)),1)

    def test_target_change_starts_new_episode(self):
        self.wait(9);self.s['targets']['SZ300919']['quantity']=200
        self.assertEqual(self.tick(10),[]);self.assertEqual(self.previous['entries']['SZ300919']['seconds'],0)

    def test_catching_monitor_failure_does_not_stop_execution(self):
        import corporate_action_runtime as ca
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);calendar=folder/'calendar.json';calendar.write_text(json.dumps([self.s['day']]))
            with patch.object(w,'CALENDAR',calendar),patch.object(ca,'root',return_value=folder),patch('console_runtime.active_session',return_value=folder/'nonexistent'),patch.object(ca,'queue_notice') as q,patch.object(ca,'save'):
                self.assertEqual(w.run_once(NOW),1)
                self.assertEqual(q.call_args.args[0],'symbol_watchdog_failed')

    def test_dispatch_failure_keeps_original_outbox_pending(self):
        import corporate_action_runtime as ca
        # Execute the unchanged production dispatcher against a temporary outbox.
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);out=folder/'outbox';out.mkdir()
            path=out/'notice.json';path.write_text(json.dumps(dict(delivered=False,attempts=0,text='offline alert',at=NOW.isoformat())))
            module=Mock();module.send.return_value=False;spec=Mock();spec.loader.exec_module=Mock()
            with patch.object(ca,'root',return_value=folder),patch.object(ca,'clocks',return_value=NOW),patch.object(ca.importlib.util,'spec_from_file_location',return_value=spec),patch.object(ca.importlib.util,'module_from_spec',return_value=module):
                self.assertEqual(ca.dispatch()['failed'],1)
            entry=json.loads(path.read_text());self.assertFalse(entry['delivered']);self.assertEqual(entry['attempts'],1)

    def test_real_file_tick_only_writes_watchdog_and_outbox(self):
        import corporate_action_runtime as ca
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);run=folder/'run';run.mkdir();calendar=folder/'calendar.json'
            calendar.write_text(json.dumps([self.s['day']]))
            ledger=run/'private_state.json';status=run/'status_redacted.json'
            ledger.write_text(json.dumps(self.s));status.write_text(json.dumps(self.st))
            source=[ledger.read_bytes(),status.read_bytes()]
            with patch.object(w,'CALENDAR',calendar),patch.object(ca,'root',return_value=folder),patch.object(ca,'clocks',return_value=NOW),patch('console_runtime.active_session',return_value=run):
                self.assertEqual(w.run_once(NOW),0)
                p=folder/'symbol_watchdog/state.json';state=json.loads(p.read_text())
                state['entries']['SZ300919'].update(seconds=600,last_seen=NOW.isoformat())
                p.write_text(json.dumps(state))
                self.assertEqual(w.run_once(NOW),1)
                self.assertEqual(w.run_once(NOW),0)
            self.assertEqual([ledger.read_bytes(),status.read_bytes()],source)
            pending=list((folder/'outbox').glob('*.json'));self.assertEqual(len(pending),1)
            entry=json.loads(pending[0].read_text());self.assertFalse(entry['delivered']);self.assertEqual(entry['kind'],'symbol_stall')

    def test_failed_queue_does_not_commit_alerted_state(self):
        import corporate_action_runtime as ca
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);run=folder/'run';run.mkdir();calendar=folder/'calendar.json'
            calendar.write_text(json.dumps([self.s['day']]))
            (run/'private_state.json').write_text(json.dumps(self.s));(run/'status_redacted.json').write_text(json.dumps(self.st))
            with patch.object(w,'CALENDAR',calendar),patch.object(ca,'root',return_value=folder),patch('console_runtime.active_session',return_value=run):
                w.run_once(NOW);p=folder/'symbol_watchdog/state.json';state=json.loads(p.read_text())
                state['entries']['SZ300919'].update(seconds=600,last_seen=NOW.isoformat());p.write_text(json.dumps(state))
                before=p.read_bytes()
                with patch.object(ca,'queue_notice',side_effect=[OSError('disk error'),Mock()]):
                    self.assertEqual(w.run_once(NOW),1)
                self.assertEqual(p.read_bytes(),before)

    def test_original_health_hook_calls_watchdog_without_trading(self):
        import corporate_action_runtime as ca
        with tempfile.TemporaryDirectory() as tmp,patch.object(ca,'root',return_value=Path(tmp)),patch.object(w,'run_once',return_value=0) as tick:
            ca.health();tick.assert_called_once()

    def test_watchdog_crash_does_not_prevent_existing_dispatch(self):
        import corporate_action_runtime as ca
        with tempfile.TemporaryDirectory() as tmp,patch.object(ca,'root',return_value=Path(tmp)),patch.object(w,'run_once',side_effect=RuntimeError('offline injected crash')),patch.object(ca,'queue_notice') as queue,patch.object(ca,'dispatch',return_value={'sent':1,'failed':0}) as dispatch,patch('sys.argv',['corporate_action_runtime.py','--dispatch']):
            self.assertEqual(ca.main(),0);dispatch.assert_called_once()
            self.assertEqual(queue.call_args.args[0],'symbol_watchdog_failed')

    def test_cold_health_start_bootstraps_reference_modules(self):
        import subprocess,sys
        stage=str(Path(__file__).parent)
        code = "import sys;sys.path[:0]="+repr([stage,str(w.BASE)])+";import corporate_action_runtime as ca,symbol_watchdog as sw;from unittest.mock import Mock;" 
        code += "ca.save=Mock();ca.queue_notice=Mock();sw.run_once=Mock(return_value=0);"
        code += "ca.read=lambda p: {'first_close_date':'2099-01-01','first_preopen_date':'2099-01-01'} if p.name=='installation.json' else ['2026-10-13'];"
        code += "assert ca.health()==0;sw.run_once.assert_called_once();ca.queue_notice.assert_not_called()"
        result=subprocess.run([sys.executable,'-B','-I','-c',code],capture_output=True,text=True,encoding='utf8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)


if __name__=='__main__':unittest.main()

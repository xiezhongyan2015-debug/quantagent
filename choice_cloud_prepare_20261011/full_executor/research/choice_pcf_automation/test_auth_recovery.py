"""Offline authentication-loss drills: no real login, credentials, or orders."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_watch_engine import Fake
from watch_engine import Watcher,save
from choice_auth import ChoiceAuthenticator,IdentityMismatch,HumanLoginRequired,LoginUnavailable,uid_digest
from auth_recovery import RecoveryController,RecoveryStopped,native_login_bridge
from quanta_agents.factor_lab_a.choice_pcf import TradeError,SessionExpired
from quanta_agents.factor_lab_a.pcf_automation import CN


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=dt.datetime(2026,9,21,13,30,tzinfo=CN);self.b=Fake()
        self.state=dict(day='2026-09-21',prior={},targets={'SH600001':dict(code='SH600001',quantity=300,reference_price=10)},
                    carried_reserve=0,attempts=[],deferred={},quotes={},blocked_codes={},fill_timeout_seconds=60,retry_cooldown_seconds=60,max_attempts_per_code=3)
        self.w=Watcher(Path(self.tmp.name)/'state.json',self.state,self.b,self.quote,lambda:self.now,self.advance)
        self.transport=SimpleNamespace(writes_suspended=False);self.logins=0;self.installs=0
        self.controller=RecoveryController(self.w,self.transport,self.login,self.install,lambda:self.now,self.advance)
        self.net=patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden'));self.net.start()
    def tearDown(self):self.net.stop();self.tmp.cleanup()
    def advance(self,n):self.now+=dt.timedelta(seconds=n)
    def quote(self,c):return dict(code=c,timestamp=self.now.isoformat(),ask=10.,last=10.,bid=9.99,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
    def login(self):self.assertTrue(self.transport.writes_suspended);self.logins+=1;return {'dummy':'unit-test'}
    def install(self,t):self.installs+=1
    def test_five_minute_wait_then_two_reconciliations(self):
        before=self.now
        with patch.object(self.w,'audit',wraps=self.w.audit) as audit:self.controller.recover();self.assertEqual(audit.call_count,2)
        self.assertEqual((self.now-before).total_seconds(),305);self.assertFalse(self.transport.writes_suspended)
    def test_initial_exchange_does_not_consume_intraday_attempt_budget(self):
        self.controller.recover(initial=True) if self.now.time()<dt.time(9,50) else None
        self.now=self.now.replace(hour=9,minute=40)
        self.controller.recover(initial=True);before=self.now;self.controller.recover()
        self.assertEqual((self.now-before).total_seconds(),305);self.assertEqual(self.controller.info['attempts'],1)
    def test_fill_during_disconnect_is_not_bought_twice(self):
        self.w.tick();original=self.controller.sleep
        def advance(n):
            if not self.b.deals:self.b.fill('1',300)
            original(n)
        self.controller.sleep=advance;self.controller.recover();self.w.tick()
        self.assertEqual(len(self.b.orders),1);self.assertEqual(self.w.audit()['positions'],{'SH600001':300})
    def test_partial_fill_replacement_only_after_cancel_and_cooldown(self):
        self.w.tick();self.b.fill('1',100);self.controller.recover();self.w.tick();self.w.tick();self.advance(61);self.w.tick()
        self.assertEqual([x['orderCount'] for x in self.b.orders],[300,200])
    def test_unknown_post_auth_response_never_replayed(self):
        original=self.b.submit
        def lost(o):original(o);raise SessionExpired(request_was_write=True)
        self.b.submit=lost
        with self.assertRaises(SessionExpired):self.w.tick()
        self.assertEqual(self.state['attempts'][0]['state'],'UNKNOWN')
        with self.assertRaises(TradeError):self.controller.recover()
        self.assertTrue(self.transport.writes_suspended);self.assertEqual(len(self.b.orders),1)
    def test_other_persons_order_blocks_resume(self):
        self.b.submit(dict(code='SH600999',quantity=100,price=10))
        with self.assertRaises(TradeError):self.controller.recover()
        self.assertTrue(self.transport.writes_suspended)
    def test_cash_mismatch_never_rebases_baseline(self):
        original=self.b.read
        def changed():
            raw=original();raw['balance']['availBalance']-=1;return raw
        self.b.read=changed
        with self.assertRaises(TradeError):self.controller.recover()
        self.assertTrue(self.transport.writes_suspended);self.assertFalse(self.b.orders)
    def test_cancel_get_auth_failure_can_be_sent_after_reconciliation(self):
        self.w.tick();self.advance(61);original=self.b.cancel
        self.b.cancel=lambda *args:(_ for _ in ()).throw(SessionExpired(False))
        with self.assertRaises(SessionExpired):self.w.tick()
        self.assertNotIn('cancel_requested_at',self.state['attempts'][0])
        self.b.cancel=original;self.controller.recover();self.w.tick()
        self.assertEqual(sum(x[0]=='cancel' for x in self.b.log),1)
    def test_cancel_post_uncertainty_is_not_repeated(self):
        self.w.tick();self.advance(61)
        self.b.cancel=lambda *args:(_ for _ in ()).throw(SessionExpired(True))
        with self.assertRaises(SessionExpired):self.w.tick()
        with self.assertRaises(TradeError):self.controller.recover()
        self.assertTrue(self.transport.writes_suspended)
    def test_bounded_5_10_15_minute_backoff(self):
        self.controller.login=lambda:(_ for _ in ()).throw(LoginUnavailable('unit-test'))
        before=self.now
        with self.assertRaises(RecoveryStopped):self.controller.recover()
        self.assertEqual(self.controller.info['attempts'],3);self.assertEqual((self.now-before).total_seconds(),1800)
    def test_stop_during_backoff_prevents_login(self):
        original=self.controller.sleep
        def stop(n):original(n);self.w.path.with_name('STOP').touch()
        self.controller.sleep=stop
        with self.assertRaises(RecoveryStopped):self.controller.recover()
        self.assertEqual(self.logins,0)
    def test_close_during_backoff_prevents_login(self):
        self.now=self.now.replace(hour=14,minute=58)
        with self.assertRaises(RecoveryStopped):self.controller.recover()
        self.assertEqual(self.logins,0)
    def test_recovery_after_1450_does_not_enable_new_buys(self):
        self.now=self.now.replace(hour=14,minute=49);self.controller.recover();self.w.tick()
        self.assertFalse(self.b.orders)
    def test_native_reply_is_not_account_verification(self):
        before=self.controller.login
        self.controller.login=lambda:(_ for _ in ()).throw(HumanLoginRequired('unit-test'))
        def ui(c,initial):c.login=before
        self.controller.native_login=ui;self.controller.recover();self.assertEqual(self.installs,1)
    def test_native_bridge_correlates_exact_request(self):
        self.controller.update('waiting_native_login');original=self.controller.sleep
        def reply(n):
            original(n);p=self.w.path.parent
            req=json.loads((p/'native_login_request.json').read_text())
            save(p/'native_login_reply.json',dict(request_id=req['request_id'],status='completed'))
        self.controller.sleep=reply;native_login_bridge(self.controller)
        req=json.loads((self.w.path.parent/'native_login_request.json').read_text());self.assertEqual(req['status'],'closed')
    def test_captcha_reply_stops_trading(self):
        self.controller.login=lambda:(_ for _ in ()).throw(HumanLoginRequired('unit-test'))
        def requires_human(*args):raise HumanLoginRequired('captcha')
        self.controller.native_login=requires_human
        with self.assertRaises(HumanLoginRequired):self.controller.recover()
        self.assertTrue(self.transport.writes_suspended)


class AuthClientTests(unittest.TestCase):
    def setUp(self):
        self.tokens=dict(JToken='old',ctoken='ct-test',utoken='ut-test',UID='unit-test-uid')
        self.auth=ChoiceAuthenticator(identity=dict(username='unit-test-user',uid_sha256=uid_digest('unit-test-uid')),cookie_reader=lambda names:dict(self.tokens))
        self.net=patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden'));self.net.start()
    def tearDown(self):self.net.stop()
    def test_no_password_submission_without_real_verification(self):
        with patch.object(self.auth,'_post') as post:
            with self.assertRaises(HumanLoginRequired):self.auth.password_login(lambda:{},None)
            post.assert_not_called()
    def test_wrong_cookie_identity_rejected_before_network(self):
        self.tokens['UID']='other'
        with self.assertRaises(IdentityMismatch):self.auth.login()
    def test_wrong_sso_identity_rejected(self):
        with patch.object(self.auth,'_post',return_value=dict(UID='other')):
            with self.assertRaises(IdentityMismatch):self.auth.login()
    def test_token_exchange_only_uses_official_schema(self):
        with patch.object(self.auth,'_post',return_value=dict(UID='unit-test-uid',JToken='new',UToken='new-ut',CToken='new-ct')) as post:
            result=self.auth.login();self.assertEqual(result['JToken'],'new')
            post.assert_called_once_with('/Crm/GetUserAfterLogin',{'ct':'ct-test','ut':'ut-test','userAgent':3})
    def test_trading_endpoint_not_allowed_on_auth_client(self):
        with self.assertRaises(TradeError):self.auth._post('/AguTrade/SpoOrder',{})
    def test_redirect_cannot_forward_tokens(self):
        class Session:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def post(self,url,**kw):
                assert url=='https://choicelabapi.eastmoney.com/emlab-api/Crm/GetUserAfterLogin'
                assert kw['allow_redirects'] is False
                return SimpleNamespace(status_code=302)
        self.auth.http_factory=Session
        with self.assertRaises(LoginUnavailable):self.auth.login()

if __name__=='__main__':unittest.main()

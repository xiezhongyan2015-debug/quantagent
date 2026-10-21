"""Offline verification of the local one-shot probe. No session extraction/network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import verify_simulation_submission as p


class Reply:
    status_code=200
    def __init__(self,data): self.obj={'Code':0,'Data':data}
    def json(self): return self.obj


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.stack=[]
        for name,value in [('OUTPUT',self.root/'report.json'),('INTENT',self.root/'intent.json')]:
            patcher=patch.object(p,name,value); patcher.start(); self.stack.append(patcher)
        self.rows=[]; self.posts=[]; self.bad=False

    def tearDown(self):
        for patcher in reversed(self.stack): patcher.stop()
        self.temp.cleanup()

    def request(self,method,url,**kwargs):
        endpoint=url.rsplit('/',1)[-1]
        if method.upper()=='POST':
            self.posts.append(endpoint)
            if endpoint=='SpoOrder':
                self.rows.append({'orderId':'REDACTED_PRIVATE_ID','secCode':'600000','orderCount':100,
                    'orderPrice':8.15,'tradeCount':0,'drt':1,'status':1})
                return Reply({} if self.bad else 'REDACTED_PRIVATE_ID')
            if endpoint=='SpoCancel': self.rows[0]['status']=8; return Reply(None)
            raise AssertionError('Unexpected POST')
        if endpoint=='SpoBalInfo':
            return Reply({'availBalance':'10000000','totalAssets':'10000000','frozenMoney':'0'})
        return Reply({'data':self.rows if endpoint=='SpoOrders' else []})

    def run_probe(self):
        with patch.object(p,'session_tokens',return_value={x:'FAKE' for x in ('JToken','utoken','ctoken','UID')}), \
             patch.object(p,'today',return_value=p.DAY), patch.object(p.sys,'argv',['probe','--authorized-contest-simulation']), \
             patch('requests.sessions.Session.request',side_effect=self.request), patch('builtins.print'):
            p.main()
        return json.loads(p.OUTPUT.read_text())

    def test_same_submit_function_success_cancel_and_persistent_no_repeat(self):
        self.assertIs(p.Probe.submit,p.ChoiceBroker.submit)
        report=self.run_probe()
        self.assertTrue(report['submission_verified'])
        self.assertTrue(report['cancellation_verified'])
        self.assertEqual(self.posts,['SpoOrder','SpoCancel'])
        self.assertNotIn('FAKE',p.OUTPUT.read_text())
        self.assertNotIn('REDACTED_PRIVATE_ID',p.OUTPUT.read_text())
        with self.assertRaises(p.TradeError): self.run_probe()
        self.assertEqual(len(self.posts),2)

    def test_missing_receipt_id_never_retries_or_cancels_an_old_order(self):
        self.bad=True
        report=self.run_probe()
        self.assertFalse(report['submission_verified'])
        self.assertEqual(report['status'],'review_required')
        self.assertEqual(self.posts,['SpoOrder'])

    def test_out_of_scope_order_blocked_before_network(self):
        session=p.BoundedSession({x:'FAKE' for x in ('JToken','utoken','ctoken','UID')})
        with patch('requests.sessions.Session.request') as network:
            with self.assertRaises(p.TradeError):
                session.post(p.BASE+'AguTrade/SpoOrder',json={'volume':200},allow_redirects=False)
            with self.assertRaises(p.TradeError):
                session.post(p.BASE+'AguTrade/SpoCancel',json={'orderId':'old'},allow_redirects=False)
            network.assert_not_called()


if __name__=='__main__': unittest.main()

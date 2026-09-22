"""All tests are offline: fake orders, fake credentials, temporary paper accounts."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, TradeError, order_payload
from quanta_agents.factor_lab_a.pcf_automation import (
    CN, Journal, PaperBroker, close_job, execute_job, execute_orders, live_quote, write_json, settle_unfilled,
)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.no_network = patch("requests.sessions.Session.request", side_effect=AssertionError("No network in tests"))
        self.no_network.start()
        self.now = dt.datetime(2026,9,22,9,35,tzinfo=CN)
        self.close = dt.datetime(2026,9,21,16,tzinfo=CN)
        self.codes = [f"600{x:03d}" for x in range(1,13)]
        self.record = {"date":"2026-09-21", "baseinfo":{"tDate":"2026-09-21","isMapShow":True,
            "map":{"NAVPERCU":10000,"RECORDNUM":12,"PRETRADINGDAY":"2026-09-18"}},
            "stocklist":[{"C_STOCKCODE":x,"BUSI_DATE":"20260921","L_NUMBER":80} for x in self.codes]}
        self.cfg = {"state_dir":str(self.root / "state"), "calendar":str(self.root / "calendar.json"),
            "close_quotes":str(self.root / "quotes.csv"),"live_quotes":str(self.root / "live.json"),
            "paper_account":str(self.root / "paper.json"),"max_orders":50,"max_order_value":20000,
            "max_batch_value":100000,"order_spacing_seconds":0,"fill_timeout_seconds":0}
        write_json(self.cfg["calendar"],["2026-09-17","2026-09-18","2026-09-21","2026-09-22"])
        write_json(self.cfg["paper_account"],{"cash":100000,"nav":100000,"frozen":0,"positions":{},"asof":"2026-09-21"})
        quotes = pd.DataFrame({"code":self.codes,"date":"2026-09-21","close":10.,"reference_date":"2026-09-18",
                              "reference_close":10.,"member_csi500":1,"member_union":1,"is_st":0,"tradable":1})
        quotes.to_csv(self.cfg["close_quotes"],index=False)
        write_json(self.cfg["live_quotes"], {"SH"+x:{"timestamp":self.now.isoformat(),"last":10.,"ask":10.,"bid":10.,
                   "lower_limit":9.,"upper_limit":11.,"tradable":1,"is_st":0} for x in self.codes})

    def tearDown(self):
        self.no_network.stop()
        self.temp.cleanup()

    def fetch(self, fund, day, path):
        write_json(path,self.record)
        return copy.deepcopy(self.record)

    def archive(self):
        return close_job(self.cfg, self.close, self.fetch)

    def test_archive_exact_date_immutable_and_revision_block(self):
        one = self.archive()
        self.assertEqual(one["execution_date"],"2026-09-22")
        self.assertEqual(one,self.archive())
        self.record["stocklist"][0]["L_NUMBER"] += 1
        with self.assertRaises(TradeError): self.archive()
        self.assertTrue((self.root / "state/archive/2026-09-21/REVISION_BLOCK.json").exists())
        with self.assertRaises(TradeError): execute_job(self.cfg,now=self.now,clock=lambda:self.now)

    def test_archive_weekend_and_intraday(self):
        self.assertEqual(close_job(self.cfg,dt.datetime(2026,9,20,16,tzinfo=CN),self.fetch)["status"],"non_trading_day")
        with self.assertRaises(TradeError): close_job(self.cfg,self.close.replace(hour=14),self.fetch)

    def test_full_paper_pipeline_and_repeat_is_idempotent(self):
        self.archive()
        result = execute_job(self.cfg,now=self.now,clock=lambda:self.now)
        self.assertEqual(result,{"status":"complete","orders":12})
        state = json.loads(Path(self.cfg["paper_account"]).read_text())
        self.assertAlmostEqual(state["cash"],3904.)
        self.assertEqual(sum(x["shares"] for x in state["positions"].values()),9600)
        self.assertTrue(all(x["sellable"]==0 for x in state["positions"].values()))
        again = execute_job(self.cfg,now=self.now,clock=lambda:self.now)
        self.assertEqual(again["status"],"already_complete")
        self.assertEqual(json.loads(Path(self.cfg["paper_account"]).read_text()),state)

    def test_stale_quote_and_unarchived_pcf_stop(self):
        with self.assertRaises(FileNotFoundError): execute_job(self.cfg,now=self.now,clock=lambda:self.now)
        self.archive()
        with self.assertRaises(TradeError):
            live_quote(self.cfg,"SH600001",self.now+dt.timedelta(seconds=31))
        with self.assertRaises(TradeError): execute_job(self.cfg,"choice",now=self.now,clock=lambda:self.now)

    def test_submit_timeout_never_retried(self):
        Path(self.cfg["state_dir"]).mkdir()
        broker = PaperBroker(self.cfg["paper_account"],"2026-09-22")
        plan = {"execution_date":"2026-09-22","initial_positions":{},
                "orders":[{"code":"SH600001","side":1,"quantity":100,"reference_price":10.}]}
        journal = Journal(self.root / "test.sqlite","paper")
        try:
            with patch.object(broker,"submit",side_effect=TradeError("lost response")) as submit:
                with self.assertRaises(TradeError): execute_orders(self.cfg,plan,broker,journal,clock=lambda:self.now)
                with self.assertRaises(TradeError): execute_orders(self.cfg,plan,broker,journal,clock=lambda:self.now)
                self.assertEqual(submit.call_count,1)
                self.assertEqual(journal.db.execute("SELECT state FROM orders").fetchone()[0],"UNKNOWN")
        finally: journal.db.close()

    def test_partial_fill_stops_later_orders(self):
        Path(self.cfg["state_dir"]).mkdir()
        broker = PaperBroker(self.cfg["paper_account"],"2026-09-22")
        plan = {"execution_date":"2026-09-22","initial_positions":{},"orders":[
            {"code":"SH"+x,"side":1,"quantity":100,"reference_price":10.} for x in self.codes[:2]]}
        journal = Journal(self.root / "test.sqlite","paper")
        try:
            with patch.object(broker,"progress",return_value={"filled":50,"done":False,"terminal_failure":False}):
                with self.assertRaises(TradeError): execute_orders(self.cfg,plan,broker,journal,clock=lambda:self.now)
            self.assertEqual(journal.db.execute("SELECT filled FROM orders").fetchall(),[(50,)])
        finally: journal.db.close()

    def test_timeout_cancels_once_then_stops_entire_batch(self):
        Path(self.cfg["state_dir"]).mkdir()
        self.cfg.update(cancel_on_timeout=True,cancel_timeout_seconds=0)
        broker=Mock(account="fake")
        broker.snapshot.return_value={"cash":100000.,"frozen":0,"positions":{}}
        broker.submit.return_value="fake-1"
        broker.progress.side_effect=[
            {"filled":30,"done":False,"state":"3","terminal_failure":False},
            {"filled":50,"done":False,"state":"7","deals_complete":True}]
        plan={"execution_date":"2026-09-22","initial_positions":{},"orders":[
            {"code":"SH"+x,"side":1,"quantity":100,"reference_price":10.} for x in self.codes[:2]]}
        journal=Journal(self.root/"cancel.sqlite","fake")
        try:
            with self.assertRaisesRegex(TradeError,"cancelled"):
                execute_orders(self.cfg,plan,broker,journal,clock=lambda:self.now)
            self.assertEqual(journal.db.execute("SELECT state,filled FROM orders").fetchall(),[("CANCELLED",50)])
            broker.cancel.assert_called_once()
            broker.submit.assert_called_once()
            with self.assertRaises(TradeError):
                execute_orders(self.cfg,plan,broker,journal,clock=lambda:self.now)
            broker.submit.assert_called_once()
        finally: journal.db.close()


class Response:
    status_code = 200
    def __init__(self,obj): self.obj=obj
    def json(self): return self.obj


class Session:
    def __init__(self): self.headers={}; self.posts=0; self.obj={"Code":0,"Data":{"orderId":"fake-1"}}
    def get(self,*args,**kwargs): return Response(self.obj)
    def post(self,*args,**kwargs): self.posts+=1; return Response(self.obj)


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{x:"FAKE" for x in ("CHOICE_ACCOUNT_ID","CHOICE_JTOKEN","CHOICE_UTOKEN","CHOICE_CTOKEN","CHOICE_UID")})
        self.env.start()
        self.contract={"validated_with_authorized_test":True,"order_id":"Data.orderId","sellable":"canSell",
                       "deal_order_id":"orderId","deal_id":"dealId"}
    def tearDown(self): self.env.stop()

    def test_payload_and_default_block(self):
        o=order_payload("fake","600001",1,100,10)
        self.assertEqual((o["mktCode"],o["orderDrt"],o["orderType"]),("1",1,1))
        s=Session(); b=ChoiceBroker(self.contract,session=s)
        with self.assertRaises(TradeError): b.submit({"code":"SH600001","side":1,"quantity":100,"price":10})
        self.assertEqual(s.posts,0)
        with self.assertRaises(TradeError): order_payload("fake","688001",1,100,10)

    def test_login_failure_is_not_empty_account(self):
        s=Session();s.obj={"Code":207,"Data":[]}
        with self.assertRaisesRegex(TradeError,"expired"): ChoiceBroker(self.contract,session=s).snapshot()

    def test_order_http_not_retried(self):
        s=Session();b=ChoiceBroker(self.contract,True,s)
        with patch.object(s,"post",side_effect=requests.Timeout()) as request:
            with self.assertRaises(TradeError): b.submit({"code":"SH600001","side":1,"quantity":100,"price":10})
            self.assertEqual(request.call_count,1)

    def test_full_fill_requires_linked_deals(self):
        b=ChoiceBroker(self.contract,True,Session())
        order={"code":"SH600001","side":1,"quantity":100}
        row={"orderId":"fake-1","secCode":"600001","drt":1,"orderCount":100,"tradeCount":100,"status":"4"}
        deal={"orderId":"fake-1","dealId":"deal-1","secCode":"600001","drt":1,"tradeCount":100}
        with patch.object(b,"_rows",side_effect=[[row],[]]): self.assertFalse(b.progress("fake-1",order)["done"])
        with patch.object(b,"_rows",side_effect=[[row],[deal]]): self.assertTrue(b.progress("fake-1",order)["done"])
        with patch.object(b,"_rows",side_effect=[[row],[deal,deal]]):
            with self.assertRaises(TradeError): b.progress("fake-1",order)

    def test_cancel_default_disabled_and_identity_checked(self):
        s=Session(); b=ChoiceBroker(self.contract,True,s)
        order={"code":"SH600001","side":1,"quantity":100,"price":10.}
        with self.assertRaises(TradeError): b.cancel("fake-1",order)
        b.contract["validated_cancel_with_authorized_test"]=True
        wrong={"orderId":"fake-1","secCode":"600002","drt":1,"orderCount":100,
               "orderPrice":10.,"tradeCount":0,"status":"2"}
        with patch.object(b,"_rows",return_value=[wrong]):
            with self.assertRaises(TradeError): b.cancel("fake-1",order)
        self.assertEqual(s.posts,0)

    def test_unvalidated_cancellation_policy_stops_before_submission(self):
        b=ChoiceBroker(self.contract,True,Session())
        with patch.object(b,"submit") as submit:
            with self.assertRaisesRegex(TradeError,"Validate cancellation"):
                execute_orders({"cancel_on_timeout":True},{},b,None)
            submit.assert_not_called()

    def test_cancel_only_pending_owned_order_and_ack_is_not_terminal(self):
        self.contract["validated_cancel_with_authorized_test"]=True
        s=Session(); b=ChoiceBroker(self.contract,True,s)
        order={"code":"SH600001","side":1,"quantity":100,"price":10.}
        row={"orderId":"fake-1","secCode":"600001","drt":1,"orderCount":100,
             "orderPrice":10.,"tradeCount":0,"status":"2"}
        with patch.object(b,"_rows",return_value=[row]), patch.object(s,"post",wraps=s.post) as post:
            result=b.cancel("fake-1",order)
            self.assertEqual(result["reason"],"acknowledged_not_confirmed")
            self.assertEqual(post.call_args.kwargs["json"]["orderId"],"fake-1")
            self.assertTrue(post.call_args.args[0].endswith("AguTrade/SpoCancel"))
            row["status"]="4"; row["tradeCount"]=100
            self.assertFalse(b.cancel("fake-1",order)["request_sent"])
            self.assertEqual(post.call_count,1)


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.journal=Journal(Path(self.temp.name)/"test.sqlite","fake")
        self.order={"code":"SH600001","side":1,"quantity":100,"price":10.}
        self.cfg={"cancel_timeout_seconds":0}
        self.broker=Mock()

    def tearDown(self):
        self.journal.db.close(); self.temp.cleanup()

    def run_settle(self, filled=0):
        return settle_unfilled(self.cfg,"2026-09-22",self.order,"fake-1",self.broker,self.journal,filled)

    def saved(self):
        return self.journal.db.execute("SELECT state,filled FROM orders").fetchone()

    def test_cancel_ack_without_terminal_confirmation_does_not_pass(self):
        self.broker.progress.return_value={"filled":0,"done":False,"state":"6","deals_complete":True}
        with self.assertRaises(TradeError): self.run_settle()
        self.assertEqual(self.saved(),("REVIEW_REQUIRED",0))
        self.broker.cancel.assert_called_once()
        self.broker.submit.assert_not_called()

    def test_partial_cancel_tracks_actual_fill_without_replacement(self):
        self.broker.progress.return_value={"filled":50,"done":False,"state":"7","deals_complete":True}
        self.assertEqual(self.run_settle(30),"cancelled")
        self.assertEqual(self.saved(),("CANCELLED",50))
        self.broker.submit.assert_not_called()

    def test_fill_during_cancel_is_never_bought_again(self):
        self.broker.progress.return_value={"filled":100,"done":True,"state":"4","deals_complete":True}
        self.assertEqual(self.run_settle(30),"filled_during_cancel")
        self.assertEqual(self.saved(),("FILLED",100))
        self.broker.submit.assert_not_called()

    def test_cancel_timeout_is_queried_without_second_post(self):
        self.broker.cancel.side_effect=requests.Timeout()
        self.broker.progress.return_value={"filled":0,"done":False,"state":"8","deals_complete":True}
        self.assertEqual(self.run_settle(),"cancelled")
        self.broker.cancel.assert_called_once()
        self.broker.submit.assert_not_called()

    def test_lagging_deals_and_decreasing_fills_require_review(self):
        self.broker.progress.return_value={"filled":50,"done":False,"state":"7","deals_complete":False}
        with self.assertRaises(TradeError): self.run_settle(30)
        self.assertEqual(self.saved(),("REVIEW_REQUIRED",50))
        self.broker.progress.return_value={"filled":20,"done":False,"state":"7","deals_complete":True}
        with self.assertRaises(TradeError): self.run_settle(50)
        self.assertEqual(self.saved(),("REVIEW_REQUIRED",50))


if __name__ == "__main__": unittest.main()

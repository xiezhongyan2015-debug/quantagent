"""Startup safety boundaries without reading credentials or making network requests."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import run_session_20260922 as session
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import CN

class Tests(unittest.TestCase):
    def test_immediate_authorization_is_bounded_and_opt_in(self):
        now=dt.datetime(2026,9,22,10,5,tzinfo=CN)
        approval=dict(user_instruction='马上启动',execution_date='2026-09-22',authorized_at=now.isoformat(),expires_at=(now+dt.timedelta(minutes=15)).isoformat())
        with patch.object(session,'read_json',return_value=approval):
            self.assertEqual(session.launch_deadline(now,True),now+dt.timedelta(minutes=15))
            for when in (now-dt.timedelta(seconds=1),now+dt.timedelta(minutes=16),now+dt.timedelta(days=1)):
                with self.assertRaises(TradeError):session.launch_deadline(when,True)
            with self.assertRaises(TradeError):session.launch_deadline(now,False)
            approval['expires_at']=(now+dt.timedelta(minutes=16)).isoformat()
            with self.assertRaises(TradeError):session.launch_deadline(now,True)
    def test_wrong_date_and_late_start_never_authenticate(self):
        for when in [dt.datetime(2026,9,21,9,40,tzinfo=CN),dt.datetime(2026,9,22,9,34,tzinfo=CN),dt.datetime(2026,9,22,9,51,tzinfo=CN),dt.datetime(2026,9,23,9,40,tzinfo=CN)]:
            with patch.object(session,'now_cn',return_value=when),patch.object(session,'session_tokens',side_effect=AssertionError('Credentials forbidden')):
                with self.assertRaises(TradeError):session.execute()
    def test_existing_marker_is_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp);(dest/'launch_attempt.json').write_text('{}')
            with patch.object(session,'DEST',dest),patch.object(session,'now_cn',return_value=dt.datetime(2026,9,22,9,40,tzinfo=CN)),patch.object(session,'initial_state',return_value={}),patch.object(session,'read_json',return_value={}),patch.object(session,'session_tokens',side_effect=AssertionError('Credentials forbidden')):
                with self.assertRaises(FileExistsError):session.execute()
            self.assertEqual((dest/'launch_attempt.json').read_text(),'{}')
    def test_tampered_plan_rejected(self):
        with patch.object(session,'read_json',side_effect=[{'capital_limit':99999999},{'plan_sha256':'wrong'}, {}, {}]):
            with self.assertRaisesRegex(TradeError,'Sealed plan'):session.initial_state()

if __name__=='__main__':unittest.main()

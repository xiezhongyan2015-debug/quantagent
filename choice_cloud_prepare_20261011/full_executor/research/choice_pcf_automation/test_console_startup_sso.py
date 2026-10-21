import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import console_runtime as runtime


class StartupSession(unittest.TestCase):
    def test_authorized_restart_exchanges_sso_without_resetting_recovery_history(self):
        w=object.__new__(runtime.Worker)
        w.state={'auth_recovery':{'attempts':3,'phase':'ready','events':[]}}
        w.http=SimpleNamespace(writes_suspended=True);w.auth=Mock();w.bind=Mock()
        w.two_audits=Mock(side_effect=[runtime.SessionExpired(),None])
        w.login(initial=True)
        w.auth.login.assert_called_once();self.assertFalse(w.http.writes_suspended)
        self.assertEqual(w.state['auth_recovery']['attempts'],3)
    def test_failed_startup_exchange_does_not_bypass_exhausted_recovery_budget(self):
        w=object.__new__(runtime.Worker)
        w.state={'auth_recovery':{'attempts':3,'phase':'ready','events':[]}}
        w.http=SimpleNamespace(writes_suspended=True);w.auth=Mock();w.bind=Mock()
        w.two_audits=Mock(side_effect=runtime.SessionExpired())
        w.auth.login.side_effect=runtime.HumanLoginRequired()
        with self.assertRaises(runtime.TradeError):w.login(initial=True)
        w.auth.login.assert_called_once();self.assertTrue(w.http.writes_suspended)


if __name__=='__main__':unittest.main()

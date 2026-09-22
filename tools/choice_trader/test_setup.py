"""Synthetic-only enrollment and identity isolation tests. No network access."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import local_access
import setup_account as setup
import console_core as core
import console_runtime as runtime
from choice_auth import ChoiceAuthenticator, IdentityMismatch, uid_digest
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import read_json


def empty():
    return dict(orders=[], deals=[], holds=[], balance=dict(
        availBalance=1000000, totalAssets=1000000, frozenMoney=0, totalPosValue=0))


class Enrollment(unittest.TestCase):
    def test_empty_baseline_carries_account_binding_overnight(self):
        state = setup.initial_state(empty(), '2026-09-22')
        state['account_binding_sha256'] = 'synthetic-binding'
        result = core.roll_day(state, '2026-09-23')
        self.assertEqual(result['baseline']['positions'], {})
        self.assertEqual(result['baseline']['cash'], '1000000.0')
        self.assertEqual(result['account_binding_sha256'], 'synthetic-binding')

    def test_nonempty_or_unexplained_account_never_becomes_baseline(self):
        cases = [dict(deals=[{}]), dict(holds=[dict(count=100)]),
                 dict(orders=[dict(status='2', tradeCount=0, orderDate='20260921')]),
                 dict(orders=[dict(status='8', tradeCount=0, orderDate='20260922')])]
        for change in cases:
            raw = empty(); raw.update(change)
            with self.subTest(change=change), self.assertRaises(TradeError):
                setup.initial_state(raw, '2026-09-22')
        for field in ('frozenMoney', 'totalPosValue', 'totalAssets'):
            raw = empty(); raw['balance'][field] += 1
            with self.subTest(field=field), self.assertRaises(TradeError):
                setup.initial_state(raw, '2026-09-22')

    def test_historical_query_uses_supplied_account_not_import_time_default(self):
        broker = Mock(); broker.account = 'synthetic-account'
        broker._request.return_value = {'Data': {'data': []}}
        self.assertEqual(runtime.historical_rows(broker, 'AguTrade/SpoOrders'), [])
        self.assertEqual(broker._request.call_args.kwargs['params']['accId'], 'synthetic-account')

    def test_always_queries_history_at_enrollment(self):
        broker = Mock(); broker._rows.side_effect = [[], [], []]
        broker._request.return_value = {'Data': empty()['balance']}
        with patch.object(setup, 'historical_rows', side_effect=[[], [dict(orderId='old')]]) as history:
            raw = setup.snapshot(broker)
        self.assertEqual(history.call_count, 2)
        with self.assertRaises(TradeError): setup.initial_state(raw, '2026-09-22')

    def test_enrollment_writes_only_local_identity_and_verified_baseline(self):
        auth = Mock(); auth.sso.side_effect = lambda tokens: dict(tokens)
        tokens = dict(JToken='synthetic', utoken='synthetic', ctoken='synthetic', UID='synthetic-user')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with patch.object(setup, 'APP_DIR', path), patch.object(setup, 'mutex'), \
                 patch.object(setup, 'session_tokens', return_value=dict(tokens)), \
                 patch.object(setup, 'ChoiceAuthenticator', return_value=auth), \
                 patch.object(setup, 'GetOnlySession') as transport, \
                 patch.object(setup, 'ChoiceBroker') as broker, \
                 patch.object(setup, 'snapshot', side_effect=[empty(), empty()]), \
                 patch.object(setup.time, 'sleep'):
                setup.enroll('synthetic-account', path)
            self.assertFalse(broker.call_args.kwargs['allow_orders'])
            transport.return_value.post.assert_not_called()
            identity = read_json(path / 'account.json')
            self.assertNotIn('JToken', identity)
            self.assertEqual(identity['uid_sha256'], uid_digest('synthetic-user'))
            self.assertEqual(len(list(path.glob('runs/*/*/private_state.json'))), 1)

    def test_existing_binding_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); (path / 'account.json').write_text('{}')
            with patch.object(setup, 'APP_DIR', path), patch.object(setup, 'mutex'), \
                 patch.object(setup, 'session_tokens') as cookies:
                with self.assertRaises(TradeError): setup.enroll('synthetic', path)
                cookies.assert_not_called()

    def test_uninitialized_machine_import_is_allowed_but_binding_is_required(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(local_access, 'APP_DIR', Path(tmp)):
            self.assertEqual(local_access.account_id(required=False), '')
            with self.assertRaises(RuntimeError): local_access.binding()

    def test_login_identity_change_rejected(self):
        auth = ChoiceAuthenticator(identity={'uid_sha256': uid_digest('synthetic-user')})
        with self.assertRaises(IdentityMismatch): auth.check_uid('other-user')


if __name__ == '__main__':
    unittest.main()

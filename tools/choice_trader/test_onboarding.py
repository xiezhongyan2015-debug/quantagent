"""Synthetic onboarding tests; no cookies, live accounts or network calls."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import onboarding
import bootstrap
import local_access
import console_core as core
import console_runtime as runtime
import choice_auth
from setup_account import initial_state
from quanta_agents.factor_lab_a.pcf_automation import digest, read_json
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker


class Installation(unittest.TestCase):
    def test_existing_environment_reused_without_install_or_account_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); python = root / '.venv/Scripts/python.exe'
            python.parent.mkdir(parents=True); python.touch()
            with patch.object(bootstrap, 'REPO', root), patch.object(bootstrap, 'run', side_effect=[0, 0, 0, 2]) as command, patch('builtins.print'):
                self.assertEqual(bootstrap.main(['--skip-install']), 0)
            commands = [call.args[0] for call in command.call_args_list]
            self.assertFalse(any('pip' in args or 'verify-account' in args or '--start' in args for args in commands))
            self.assertEqual(commands[-1][-1], 'inspect')

    def test_missing_environment_cannot_be_silently_recreated_in_check_mode(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bootstrap, 'REPO', Path(tmp)), \
             patch.object(bootstrap, 'run') as command, patch('builtins.print'):
            self.assertEqual(bootstrap.main(['--skip-install']), 1)
            command.assert_not_called()


class Configuration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'calendar.json').write_text(json.dumps(['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-28']))
        (self.root / 'strategy.py').write_text('# synthetic; never executed\n')
        self.patch = dict(strategy='local_script', external_script='strategy.py',
                          calendar_path='calendar.json', calendar_valid_through='2026-09-28')

    def config(self, value=None):
        return onboarding.normalized_config(value or self.patch, self.root)

    def test_relative_paths_are_relative_to_input_file(self):
        config = self.config()
        self.assertEqual(config['external_script'], str(self.root / 'strategy.py'))
        self.assertEqual(onboarding.input_checks(config, '2026-09-23')['signal_date'], '2026-09-22')

    def test_existing_limits_are_not_replaced_by_defaults(self):
        prior = self.config(); prior['capital_limit'] = 1200000
        result = onboarding.normalized_config({'price_guard': .02}, self.root, prior)
        self.assertEqual(result['capital_limit'], 1200000)
        self.assertEqual(result['price_guard'], .02)

    def test_unknown_setting_and_invalid_risk_are_rejected(self):
        with self.assertRaises(ValueError): self.config(dict(self.patch, start=True))
        with self.assertRaises(core.TradeError): self.config(dict(self.patch, spacing_seconds=1))

    def test_expired_and_malformed_calendars_block(self):
        with self.assertRaises(ValueError): onboarding.input_checks(self.config(), '2026-09-29')
        (self.root / 'calendar.json').write_text('["2026-09-22", "wrong"]')
        with self.assertRaises(ValueError): onboarding.input_checks(self.config(), '2026-09-23')

    def test_weekend_install_does_not_authorize_weekend_execution(self):
        result = onboarding.input_checks(self.config(), '2026-09-26')
        self.assertFalse(result['trading_day'])
        with self.assertRaises(core.TradeError): core.days_for(self.config(), '2026-09-26')

    def test_stale_target_file_blocks(self):
        target = self.root / 'target.json'
        target.write_text(json.dumps(dict(execution_date='2026-09-22', signal_date='2026-09-21',
                                         mode='weights', targets=[dict(code='SH600000', weight=.01)])))
        config = self.config(dict(self.patch, strategy='target_file', external_file='target.json'))
        with self.assertRaises(ValueError): onboarding.input_checks(config, '2026-09-23')

    def test_configure_backs_up_existing_settings_without_account_access(self):
        prior = self.config(); runtime.save(self.root / 'settings.json', prior)
        proposal = self.root / 'proposal.json'; proposal.write_text('{"price_guard": 0.02}')
        with patch.object(onboarding, 'RUNTIME', self.root), patch.object(runtime, 'mutex'), \
             patch.object(runtime, 'active_session', return_value=None), \
             patch.object(choice_auth.ChoiceAuthenticator, 'current_tokens', side_effect=AssertionError('no cookies')), \
             patch.object(runtime.requests.Session, 'request', side_effect=AssertionError('no network')):
            result = onboarding.configure(proposal, '2026-09-23')
        self.assertFalse(result['account_accessed']); self.assertFalse(result['trading_started'])
        self.assertEqual(read_json(self.root / 'settings.json')['price_guard'], .02)
        self.assertEqual(read_json(self.root / 'config_history' / (digest(prior) + '.json')), prior)

    def test_configure_does_not_overwrite_running_account_settings(self):
        proposal = self.root / 'proposal.json'; proposal.write_text(json.dumps(self.patch))
        with patch.object(onboarding, 'RUNTIME', self.root), patch.object(runtime, 'mutex'), \
             patch.object(runtime, 'active_session', return_value=self.root):
            with self.assertRaises(ValueError): onboarding.configure(proposal, '2026-09-23')
        self.assertFalse((self.root / 'settings.json').exists())

    def test_inspect_missing_setup_is_read_only(self):
        with patch.object(onboarding, 'RUNTIME', self.root), patch.object(local_access, 'APP_DIR', self.root), \
             patch.object(runtime, 'APP_DIR', self.root), patch.object(onboarding, 'candidate_profiles', return_value=[]), \
             patch.object(choice_auth.ChoiceAuthenticator, 'current_tokens', side_effect=AssertionError('no cookies')), \
             patch.object(runtime.requests.Session, 'request', side_effect=AssertionError('no network')):
            result = onboarding.inspect('2026-09-23')
        self.assertEqual(result['status'], 'needs_input')
        self.assertFalse(result['account_accessed'])
        self.assertFalse((self.root / 'account.json').exists())


class ReadOnlyVerification(unittest.TestCase):
    def test_two_snapshots_without_writes_or_journal_mutation(self):
        identity = dict(account_mode='competition_simulation_only', account_id='synthetic-account',
                        uid_sha256=choice_auth.uid_digest('synthetic-user'), choice_profile='synthetic-profile')
        raw = dict(orders=[], deals=[], holds=[], balance=dict(availBalance=1000000,
                   totalAssets=1000000, frozenMoney=0, totalPosValue=0))
        day = runtime.now_cn().date().isoformat()
        state = initial_state(raw, day); state['account_binding_sha256'] = digest(identity)
        tokens = dict(JToken='synthetic', utoken='synthetic', ctoken='synthetic', UID='synthetic-user')
        auth = Mock(); auth.current_tokens.return_value = dict(tokens)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); runtime.save(root / 'private_state.json', state)
            original = (root / 'private_state.json').read_bytes()
            before = {k: os.environ.get(k) for k in (*choice_auth.ENV_KEYS.values(), 'CHOICE_ACCOUNT_ID')}
            with patch.object(onboarding, 'RUNTIME', root), patch.object(runtime, 'mutex'), \
                 patch.object(local_access, 'binding', return_value=identity), \
                 patch.object(runtime, 'latest_session', return_value=root), \
                 patch.object(runtime, 'active_session', return_value=None), \
                 patch.object(choice_auth, 'ChoiceAuthenticator', return_value=auth), \
                 patch.object(runtime, 'account_snapshot', side_effect=[copy.deepcopy(raw), copy.deepcopy(raw)]) as snapshots, \
                 patch.object(runtime.requests.Session, 'request', side_effect=AssertionError('no network')), \
                 patch.object(ChoiceBroker, 'submit', side_effect=AssertionError('no submit')), \
                 patch.object(ChoiceBroker, 'cancel', side_effect=AssertionError('no cancel')), \
                 patch('time.sleep'):
                result = onboarding.verify_account()
            self.assertEqual(result['status'], 'account_verified')
            self.assertEqual(snapshots.call_count, 2)
            self.assertFalse(result['pcf_checked']); self.assertFalse(result['trading_started'])
            self.assertEqual((root / 'private_state.json').read_bytes(), original)
            self.assertEqual(before, {k: os.environ.get(k) for k in before})


if __name__ == '__main__':
    unittest.main()

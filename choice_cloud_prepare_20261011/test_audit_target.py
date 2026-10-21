import copy
import unittest
from audit_target import audit


class TargetAuditTests(unittest.TestCase):
    def setUp(self):
        self.codes = [f'SH{600000 + i:06d}' for i in range(100)]
        self.target = dict(execution_date='2026-10-12', signal_date='2026-10-09', mode='weights',
                           generated_from=dict(pcf_trading_day='20261009'),
                           targets=[dict(code=c, weight=.0098) for c in self.codes])
        self.account = dict(nav=10000000, positions={c:9800 for c in self.codes},
                            prices_now={c:10 for c in self.codes})
        self.config = dict(target_fraction=.98, capital_limit=10000000, max_security_value=500000)
        self.quotes = {c:10 for c in self.codes}
        self.membership = dict(as_of='2026-10-09', csi500=self.codes, allowed=self.codes)

    def run_audit(self, target=None, config=None, membership=None, quotes=None):
        return audit(target or self.target, '2026-10-12', '2026-10-09', self.account,
                     config or self.config, quotes or self.quotes, membership or self.membership)

    def test_valid_complete_inputs(self):
        self.assertTrue(self.run_audit()['ready_for_console_dry_run'])

    def test_saved_ninety_percent_config_rejects_ninetyeight_percent_targets(self):
        config = dict(self.config, target_fraction=.9, capital_limit=9500000)
        result = self.run_audit(config=config)
        self.assertFalse(result['ready_for_console_dry_run'])
        self.assertTrue(any('target_fraction' in f for f in result['failures']))

    def test_nonfinite_and_duplicate_rows_block(self):
        target = copy.deepcopy(self.target)
        target['targets'].append(dict(code=self.codes[0], weight=.0098))
        target['targets'][1]['weight'] = float('nan')
        result = self.run_audit(target=target)
        self.assertFalse(result['ready_for_console_dry_run'])
        self.assertIn('Invalid target row', result['failures'])
        self.assertTrue(any('Duplicate' in f for f in result['failures']))

    def test_stale_file_and_missing_evidence_block(self):
        target = copy.deepcopy(self.target)
        target['signal_date'] = '2026-10-08'
        self.assertFalse(self.run_audit(target=target)['ready_for_console_dry_run'])
        result = audit(self.target, '2026-10-12', '2026-10-09')
        self.assertFalse(result['ready_for_console_dry_run'])
        self.assertEqual(len(result['pending']), 3)

    def test_member_constraint_and_lot_rounding(self):
        members = dict(self.membership, csi500=self.codes[:70])
        self.assertFalse(self.run_audit(membership=members)['ready_for_console_dry_run'])
        quotes = {c:100000 for c in self.codes}
        result = self.run_audit(quotes=quotes)
        self.assertEqual(result['metrics']['stock_weight_after_console_rounding'], 0)
        self.assertFalse(result['ready_for_console_dry_run'])


if __name__ == '__main__':
    unittest.main()

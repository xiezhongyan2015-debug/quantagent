"""Tiny synthetic input/target checks. These are not training or backtest results."""
import copy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
try:
    from . import backtest_inputs as b
except ImportError:
    import backtest_inputs as b


def signal_fixture():
    dates = np.array(['2025-01-02', '2025-01-03', '2025-01-06'], dtype='U10')
    return {'dates': dates, 'symbols': np.array(['000001.SZ', '000002.SZ', '000003.SZ'], dtype='U9'),
            'signal_time_ns': (dates.astype('datetime64[ns]') + np.timedelta64(7, 'h')).astype(np.int64),
            'membership': np.tile([True, True, False], (3, 1)), 'roles': np.full(3, 'primary2025', dtype='U11'),
            'scores': np.array([[1., np.nan, np.nan], [2., -2., np.nan], [0., 0., np.nan]], dtype=np.float64)}


def pcf_fixture(date='2025-01-02'):
    return [{'date': date, 'symbol': s, 'weight': w} for s, w in
            [('000001.SZ', .4), ('000002.SZ', .25), ('000003.SZ', .1), ('999999.SZ', .15)]]


class BacktestInputChecks(unittest.TestCase):
    def test_six_fields_clock_finite_population_and_export(self):
        pack = signal_fixture(); b.validate_signal_pack(pack); rows = list(b.export_rows(pack))
        self.assertEqual(len(rows), 5); self.assertEqual(rows[0]['score'], 1.)
        self.assertEqual(rows[0]['signal_time_ns'], int(pack['signal_time_ns'][0]))
        self.assertTrue(all(r['symbol'] != '000003.SZ' for r in rows))
        for kind in ('clock', 'outside_member', 'objects', 'roles', 'duplicates', 'field'):
            p = copy.deepcopy(pack)
            if kind == 'clock': p['signal_time_ns'][0] -= 1
            elif kind == 'outside_member': p['scores'][0, 2] = 1.
            elif kind == 'objects': p['symbols'] = p['symbols'].astype(object)
            elif kind == 'roles': p['roles'][0] = 'carry2026'
            elif kind == 'duplicates': p['dates'][1] = p['dates'][0]
            else: p['future_returns'] = np.zeros(3)
            with self.subTest(kind=kind), self.assertRaises(ValueError): b.validate_signal_pack(p)

    def test_hand_tilt_preserves_cash_budget_and_all_unknown_stocks(self):
        pack = signal_fixture(); mapping = [{'signal_date': '2025-01-02', 'execution_date': '2025-01-03'}]
        rows, checks = b.build_targets(pack, pcf_fixture(), mapping, 'signal_date')
        self.assertEqual(len(rows), 4); self.assertEqual([r['multiplier'] for r in rows], [1.5, 1., 1., 1.])
        expected = np.array([.6, .25, .1, .15]) / 1.1 * .9
        np.testing.assert_allclose([r['target_weight'] for r in rows], expected, rtol=0, atol=1e-16)
        self.assertAlmostEqual(sum(r['target_weight'] for r in rows), .9)
        self.assertEqual(rows[1]['score_status'], 'missing_score_neutral')
        self.assertEqual(rows[2]['score_status'], 'not_signal_member_neutral')
        self.assertEqual(rows[3]['score_status'], 'outside_model_universe_neutral')
        self.assertTrue(checks[0]['next_session_checked_against_signal_calendar'])
        self.assertEqual(rows[0]['weight_date'], '2025-01-02')

    def test_signal_date_PCF_uses_only_prior_signal_and_future_mutation(self):
        pack = signal_fixture(); mapping = [{'signal_date': '2025-01-02', 'execution_date': '2025-01-03'}]
        rows, _ = b.build_targets(pack, pcf_fixture(), mapping, 'signal_date')
        changed = copy.deepcopy(pack); changed['scores'][1:, :2] = 1e9; changed['membership'][1:, 2] = True
        other, _ = b.build_targets(changed, pcf_fixture(), mapping, 'signal_date')
        self.assertEqual(rows, other); self.assertEqual(rows[0]['signal_date'], '2025-01-02')
        self.assertEqual(rows[0]['execution_date'], '2025-01-03'); self.assertEqual(rows[0]['weight_date'], '2025-01-02')
        with self.assertRaises(ValueError):
            b.build_targets(pack, pcf_fixture('2025-01-03'), mapping, 'execution_date')

    def test_same_day_skipped_session_missing_mapping_and_future_boundary(self):
        pack = signal_fixture()
        for mapping in ([], [{'signal_date': '2025-01-02', 'execution_date': '2025-01-02'}],
                         [{'signal_date': '2025-01-02', 'execution_date': '2025-01-06'}]):
            with self.assertRaises(ValueError): b.build_targets(pack, pcf_fixture(), mapping, 'signal_date')
        rows, checks = b.build_targets(pack, pcf_fixture('2025-01-06'),
            [{'signal_date': '2025-01-06', 'execution_date': '2025-01-07'}], 'signal_date')
        self.assertEqual(len(rows), 4); self.assertFalse(checks[0]['next_session_checked_against_signal_calendar'])

    def test_k_clip_zero_reference_and_impossible_longonly_are_explicit(self):
        target, mult = b.tilt_weights([.4, .3], [9., -9.])
        np.testing.assert_array_equal(mult, [2., 0.]); np.testing.assert_array_equal(target, [.7, 0.])
        target, _ = b.tilt_weights([.4, .3], [2., -2.], k=0.)
        np.testing.assert_allclose(target, [.4, .3], rtol=0, atol=1e-16)
        np.testing.assert_array_equal(b.tilt_weights([0., 0.], [0., np.nan])[0], [0., 0.])
        for weights, scores, k in (([.4, .3], [-2., -2.], .5), ([.4, .3], [-2., 1.], 1.),
                                  ([.9, .2], [0., 0.], .5), ([.4, .3], [0., 0.], -1.)):
            with self.assertRaises(ValueError): b.tilt_weights(weights, scores, k)

    def test_csv_schema_duplicate_rows_mapping_and_explicit_file_hash(self):
        with tempfile.TemporaryDirectory() as name:
            p = Path(name); signals = p / 'synthetic.npz'; np.savez_compressed(signals, **signal_fixture())
            with self.assertRaises(ValueError): b.load_signals(signals)
            pack, digest = b.load_signals(signals, b.sha256(signals)); self.assertFalse(pack['scores'].flags.writeable)
            self.assertEqual(digest, b.sha256(signals))
            f = p / 'pcf.csv'; f.write_text('date,symbol,weight\n2025-01-02,000001.SZ,0.4\n2025-01-02,000001.SZ,0.3\n')
            with self.assertRaises(ValueError): b.read_pcf(f)
            f.write_text('signal_date,execution_date\n2025-01-02,2025-01-02\n')
            with self.assertRaises(ValueError): b.read_mapping(f)

    def test_cli_export_and_tilt_only_outputs_no_account_claim_or_overwrite(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); signals = root / 'synthetic.npz'; np.savez_compressed(signals, **signal_fixture())
            common = ['--signals', str(signals), '--signals-sha256', b.sha256(signals)]
            pcf = root / 'pcf.csv'
            with pcf.open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=('date', 'symbol', 'weight')); w.writeheader(); w.writerows(pcf_fixture())
            mapping = root / 'mapping.csv'; mapping.write_text('signal_date,execution_date\n2025-01-02,2025-01-03\n')
            for operation in ('export', 'tilt'):
                out = root / (operation + '.csv'); args = [sys.executable, '-B', b.__file__, operation, *common, '--out', str(out)]
                if operation == 'tilt': args += ['--pcf', str(pcf), '--mapping', str(mapping), '--weight-date', 'signal_date']
                process = subprocess.run(args, capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                meta = json.loads(out.with_suffix('.csv.meta.json').read_bytes())
                self.assertFalse(meta['account_or_return_calculation']); self.assertFalse(meta['is_frozen_OLD_RANK_e402'])
                self.assertIsNone(meta['source_model_if_frozen']); self.assertFalse(meta['orders_or_trades_generated'])
                self.assertFalse(meta['additional_score_transform_applied'])
                if operation == 'tilt': self.assertEqual(meta['output_rows'], 4)
                before = out.read_bytes(); again = subprocess.run(args, capture_output=True, text=True)
                self.assertNotEqual(again.returncode, 0); self.assertEqual(out.read_bytes(), before)


if __name__ == '__main__': unittest.main()

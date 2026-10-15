"""Small CPU regressions; no real model checkpoint, labels or returns are read."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
import torch
from torch import nn

from . import model, predict, verify

STUDY = Path(__file__).resolve().parents[1]
SOURCE = STUDY.parent / 'gru_minute_vwap_20261014/source_reference/gru_market_attention_20261007'


def original_classes():
    expected = {
        'model.py': 'b22e45fcb9814a0d088daa979217e34af97e2263856c62de13ee317a9c0aea49',
        'experiment.py': '83982c9aa668888501d464dfc0208422e9a15d98fdc50cf6385ca838549d7bba',
    }
    ns = {'torch': torch, 'nn': nn}
    if not all((SOURCE / name).is_file() for name in expected):
        return None  # Public package tests remain runnable without the private research checkout.
    for name, cls in (('model.py', 'PlanGRU'), ('experiment.py', 'MarketGRU')):
        path = SOURCE / name
        assert verify.sha256(path) == expected[name]
        node = copy.deepcopy(next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                                  if isinstance(n, ast.ClassDef) and n.name == cls))
        for f in node.body:
            if isinstance(f, ast.FunctionDef):
                f.body = [n for n in f.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<original-model-only>', 'exec'), ns)
    return ns


class PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_original_architecture_state_forward_and_subset_exact(self):
        ns = original_classes()
        torch.manual_seed(66)
        original = (model.MarketGRU() if ns is None else ns['MarketGRU'](model.GROUPS, 39, 'control')).eval()
        torch.manual_seed(66); portable = model.MarketGRU().eval()
        self.assertEqual(sum(p.numel() for p in portable.parameters()), 410116)
        self.assertEqual(set(original.state_dict()), set(portable.state_dict()))
        self.assertTrue(all(torch.equal(v, portable.state_dict()[k]) for k, v in original.state_dict().items()))
        rng = np.random.default_rng(71)
        x = rng.normal(size=(6, 80, 122)).astype('float32'); x[0, 5, 41] = np.nan
        ind = rng.integers(0, 39, (6, 80), dtype=np.int64); slots = np.arange(80, dtype=np.int64) % 4
        with torch.inference_mode():
            expected = original(torch.from_numpy(x), torch.from_numpy(ind), torch.from_numpy(slots),
                                torch.full((1, 6), float('nan')), [6]).numpy()
        actual = predict.predict_windows([portable] * 5, x, ind, slots, predict.FACTOR_INPUT_STATE)
        self.assertTrue(np.array_equal(actual[0], expected))
        subset = predict.predict_windows([portable] * 5, x[[1, 4]], ind[[1, 4]], slots, predict.FACTOR_INPUT_STATE)
        np.testing.assert_allclose(subset[0], expected[[1, 4]], rtol=0, atol=2e-7)
        with self.assertRaises(ValueError):
            model.MarketGRU(n_industries=128)
        with self.assertRaises(ValueError):
            predict.predict_windows([portable] * 5, x, ind, slots, 'raw_unranked')

    def test_factor_rank_matches_original_and_ignores_other_cohorts(self):
        path = STUDY.parent / 'gru_factor_rank_ablation_20261013/factor_rank.py'
        ns = {'np': np}
        if path.is_file():
            nodes = [n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef)
                     and n.name in ('_numeric_array', '_membership_array', 'rank_factor_day')]
            exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<original-ranks>', 'exec'), ns)
        rng = np.random.default_rng(9); x = np.round(rng.normal(size=(120, 64)), 1).astype('float32')
        mem = np.ones(120, bool); mem[-3:] = False
        x[3, 2] = np.nan; x[2, 4] = np.inf; x[:, 0] = 1; x[:, 1] = np.nan; x[0, 1] = 2
        actual = predict.rank_factor_day(x, mem)
        if 'rank_factor_day' in ns:
            self.assertTrue(np.array_equal(actual.view('uint32'), ns['rank_factor_day'](x, mem).view('uint32')))
        for c in range(64):
            valid = mem & np.isfinite(x[:, c]); n = valid.sum()
            if n >= 2:
                expected = np.sqrt(12.) * ((pd.Series(x[valid, c]).rank(method='average').to_numpy() - .5) / n - .5)
                np.testing.assert_array_equal(actual[valid, c], expected.astype('float32'))
        changed = x.copy(); changed[-3:] = 1e6
        self.assertTrue(np.array_equal(actual.view('uint32'), predict.rank_factor_day(changed, mem).view('uint32')))
        self.assertTrue(np.isnan(actual[:, 1]).all())

    def test_original_mask_window_copy_and_missing_bitmap_gate(self):
        rng = np.random.default_rng(4); h, u = 84, 104
        x = rng.normal(size=(h, u, 122)).astype('float32')
        daily = rng.normal(size=(21, u, 64)).astype('float32')
        for d in range(21): x[d*4:d*4+4, :, 40:104] = daily[d]
        ind = np.zeros((h, u), np.int64); slots = np.arange(h, dtype=np.int64) % 4
        mem = np.ones((h, u), bool); complete = mem.copy(); complete[10, 0] = False
        ranks = np.stack([predict.rank_factor_day(d, mem[i*4]) for i, d in enumerate(daily)])
        a, ai, sl, ids = predict.make_windows(x, ind, slots, mem, complete, 79, ranks)
        self.assertEqual(a.shape, (103, 80, 122)); self.assertNotIn(0, ids)
        self.assertTrue(np.array_equal(a[..., :40], x[:80, ids, :40].transpose(1, 0, 2)))
        self.assertTrue(np.array_equal(a[:, :, 40:104], ranks[(np.arange(80)//4)[:, None], ids].transpose(1, 0, 2)))
        a[:] = 0; self.assertFalse(np.all(x[:80] == 0))
        bad = ranks.copy(); bad[0, 1, 0] = np.nan
        with self.assertRaises(ValueError): predict.make_windows(x, ind, slots, mem, complete, 79, bad)
        future = x.copy(); future[80:] = np.nan
        b = predict.make_windows(future, ind, slots, mem, complete, 79, ranks)[0]
        np.testing.assert_array_equal(b, predict.make_windows(x, ind, slots, mem, complete, 79, ranks)[0])

    def test_postprocessing_matches_original_pandas_math_and_stream_state(self):
        rng = np.random.default_rng(85); t, u = 4, 112
        raw = rng.normal(size=(5, t, u, 4)).astype('float32')
        mem = np.ones((t, u), bool); mem[1, 0] = False; raw[:, 1, 0] = np.nan
        exp = rng.normal(size=(t, u, 4)); exp[:, :, :2] = np.round(exp[:, :, :2], 1)
        exp[2, 1, 2] = np.nan
        def old_neutral(s, exposure):
            out = np.full_like(s, np.nan, dtype=float); ok = np.isfinite(s) & np.isfinite(exposure).all(axis=1)
            xx = exposure[ok].copy()
            for c in (0, 1): xx[:, c] = pd.Series(xx[:, c]).rank(method='average', pct=True).to_numpy()
            scale = xx.std(axis=0); xx = (xx - xx.mean(axis=0)) / np.where(scale > 1e-12, scale, 1)
            xx = np.c_[np.ones(len(xx)), xx]; out[ok] = s[ok] - xx @ np.linalg.lstsq(xx, s[ok], rcond=None)[0]
            return out
        per = [np.asarray([predict.zscore(s) for s in raw[k, :, :, 3].astype(float)]) for k in range(5)]
        old_rows = []; last = None
        for row, m, expo in zip(np.mean(per, axis=0), mem, exp):
            row = old_neutral(np.where(m, row, np.nan), expo)
            if last is not None: row = np.where(np.isfinite(row) & np.isfinite(last), .2 * row + .8 * last, row)
            last = row.copy(); old_rows.append(predict.zscore(np.where(m, row, np.nan)))
        actual, end = predict.processed_head4(raw, mem, exp)
        self.assertTrue(np.array_equal(actual, np.asarray(old_rows), equal_nan=True))
        first, state = predict.processed_head4(raw[:, :2], mem[:2], exp[:2])
        tail, end2 = predict.processed_head4(raw[:, 2:], mem[2:], exp[2:], state)
        self.assertTrue(np.array_equal(np.r_[first, tail], actual, equal_nan=True))
        self.assertTrue(np.array_equal(end, end2, equal_nan=True))
        changed = raw.copy(); changed[:, 2:] *= 100
        self.assertTrue(np.array_equal(predict.processed_head4(changed, mem, exp)[0][:2], actual[:2], equal_nan=True))
        bad = raw.copy(); bad[1, 0, 0, 3] = np.nan
        with self.assertRaises(ValueError): predict.processed_head4(bad, mem, exp)

    def test_safe_tensor_state_and_unknown_pickle_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'state.pt'; torch.save(model.MarketGRU().state_dict(), p)
            state = predict.safe_state(p, verify.sha256(p), False)
            model.MarketGRU().load_state_dict(state, strict=True)
            with self.assertRaises(ValueError): predict.safe_state(p, '0'*64, False)
            torch.save({'unexpected': Path('not-a-tensor')}, p)
            with self.assertRaises(ValueError): predict.safe_state(p, verify.sha256(p), False)

    def test_manifest_identity_relative_path_and_signals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); models = {}
            for seed in verify.SEEDS:
                p = root / f'seed{seed}.pt'; p.write_bytes(b'NONEXECUTABLE_FIXTURE_' + str(seed).encode())
                models[str(seed)] = {'path': p.name, 'sha256': verify.sha256(p), 'bytes': p.stat().st_size,
                    'source_checkpoint_sha256': verify.SOURCE_CHECKPOINT_SHA256[seed], 'format': 'state_dict'}
            m = {'schema': 'gru_old_rank_portable_weights_v1', 'seeds': list(verify.SEEDS),
                 'variant': 'control', 'input_features': 122, 'sequence_length': 80, 'n_industries': 39,
                 'n_parameters': 410116, 'heads': 4, 'factor_input_state': predict.FACTOR_INPUT_STATE, 'models': models}
            p = root / 'weights.json'; p.write_text(json.dumps(m), encoding='utf-8')
            self.assertEqual(len(verify.verify_weights_manifest(p, verify.sha256(p))[1]), 5)
            m['models']['66']['source_checkpoint_sha256'] = '0' * 64; p.write_text(json.dumps(m), encoding='utf-8')
            with self.assertRaises(ValueError): verify.verify_weights_manifest(p, verify.sha256(p))
            with self.assertRaises(ValueError): verify.package_file(root, '../escape.pt')
            p = root / 'signals.npz'; ts = np.asarray([np.datetime64('2025-01-02T07:00:00', 'ns').astype('int64')])
            a = {'scores': np.zeros((1, 2), np.float64), 'membership': np.ones((1, 2), bool),
                 'symbols': np.array(['000001.SZ', '600000.SH']), 'dates': np.array(['2025-01-02']),
                 'signal_time_ns': ts, 'roles': np.array(['primary2025'])}
            np.savez(p, **a); self.assertEqual(verify.verify_signal_pack(p, verify.sha256(p))['scores'].shape, (1, 2))
            a['signal_time_ns'] = ts + 1; np.savez(p, **a)
            with self.assertRaises(ValueError): verify.verify_signal_pack(p, verify.sha256(p))


if __name__ == '__main__':
    unittest.main()

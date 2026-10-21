"""constraint85_draft.py 的单元测试（草案，未接入实盘）。
运行：.pcf-research-venv/bin/python -I outputs/pre_gru_training_20261019/construction_replay/test_constraint85_draft.py
（只用标准库 unittest；装了 pytest 也可以直接 pytest 这个文件。）"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
from constraint85_draft import enforce_csi500_floor, post_lot_member_fraction


def book(**kw):
    return pd.Series(kw, dtype=float)


class TestFloor(unittest.TestCase):
    def setUp(self):
        # stock weight 0.95; CSI500 names m1..m4 (0.76) + non-members n1, n2 (0.19) -> share 0.76
        self.w = book(m1=0.30, m2=0.20, m3=0.16, m4=0.10, n1=0.12, n2=0.07)
        self.c500 = {'m1', 'm2', 'm3', 'm4'}
        self.ind = {'m1': 'S11', 'm2': 'S11', 'm3': 'S22', 'm4': 'S33', 'n1': 'S11', 'n2': 'S22'}

    def test_noop_when_above_floor(self):
        w = book(m1=0.5, m2=0.4, n1=0.05)
        out, info = enforce_csi500_floor(w, {'m1', 'm2'}, {})
        self.assertFalse(info['applied']); pd.testing.assert_series_equal(out, w)

    def test_exactly_at_floor_is_noop(self):
        w = book(m1=0.85, n1=0.10)
        out, info = enforce_csi500_floor(w, {'m1'}, {})
        self.assertFalse(info['applied'])

    def test_reaches_target_and_preserves_total(self):
        out, info = enforce_csi500_floor(self.w, self.c500, self.ind, max_single=1.0)
        self.assertTrue(info['applied']); self.assertTrue(info['feasible'])
        self.assertAlmostEqual(out[list(self.c500)].sum(), 0.86, places=12)
        self.assertAlmostEqual(out.sum(), self.w.sum(), places=12)
        self.assertAlmostEqual(info['moved'], 0.10, places=12)

    def test_non_members_cut_pro_rata(self):
        out, _ = enforce_csi500_floor(self.w, self.c500, self.ind, max_single=1.0)
        r1, r2 = out['n1'] / self.w['n1'], out['n2'] / self.w['n2']
        self.assertAlmostEqual(r1, r2, places=12); self.assertAlmostEqual(r1, 1 - 0.10 / 0.19, places=12)

    def test_same_industry_routing(self):
        out, info = enforce_csi500_floor(self.w, self.c500, self.ind, max_single=1.0)
        cut1 = 0.12 * 0.10 / 0.19; cut2 = 0.07 * 0.10 / 0.19
        # n1 (S11) -> m1, m2 pro-rata 0.30:0.20 ; n2 (S22) -> m3 only ; m4 (S33) receives nothing
        self.assertAlmostEqual(out['m1'] - 0.30, cut1 * 0.6, places=12)
        self.assertAlmostEqual(out['m2'] - 0.20, cut1 * 0.4, places=12)
        self.assertAlmostEqual(out['m3'] - 0.16, cut2, places=12)
        self.assertAlmostEqual(out['m4'], 0.10, places=12)
        self.assertAlmostEqual(info['moved_global'], 0.0, places=12)
        self.assertAlmostEqual(info['moved_same_industry'], 0.10, places=12)

    def test_global_fallback_when_industry_has_no_member(self):
        ind = dict(self.ind, n2='S99')   # nobody from S99 is a CSI500 name -> global pool, pro-rata over m1..m4
        out, info = enforce_csi500_floor(self.w, self.c500, ind, max_single=1.0)
        cut2 = 0.07 * 0.10 / 0.19
        self.assertAlmostEqual(info['moved_global'], cut2, places=12)
        self.assertAlmostEqual(out['m4'] - 0.10, cut2 * 0.10 / 0.76, places=12)
        self.assertAlmostEqual(out[list(self.c500)].sum(), 0.86, places=12)

    def test_unknown_industry_goes_global(self):
        out, info = enforce_csi500_floor(self.w, self.c500, None, max_single=1.0)
        self.assertAlmostEqual(info['moved_global'], 0.10, places=12)
        m = self.w[list(self.c500)]
        np.testing.assert_allclose((out[m.index] - m) / m, 0.10 / 0.76, rtol=0, atol=1e-12)

    def test_member_without_industry_only_gets_global_share(self):
        ind = dict(self.ind); del ind['m3']   # n2 (S22) has no member peer with a known industry any more
        out, info = enforce_csi500_floor(self.w, self.c500, ind, max_single=1.0)
        self.assertGreater(info['moved_global'], 0)
        self.assertAlmostEqual(out[list(self.c500)].sum(), 0.86, places=12)

    def test_infeasible_moves_everything(self):
        w = book(m1=0.50, n1=0.30, n2=0.15)   # share 0.50, non-members 0.45 < gap 0.36? no: gap .36 < .45 -> feasible
        out, info = enforce_csi500_floor(w, {'m1'}, {}, max_single=1.0)
        self.assertTrue(info['feasible']); self.assertAlmostEqual(out['m1'], 0.86, places=12)
        w = book(m1=0.30, n1=0.20)            # total only 0.50 < floor -> cannot be met
        out, info = enforce_csi500_floor(w, {'m1'}, {}, max_single=1.0)
        self.assertFalse(info['feasible']); self.assertNotIn('n1', out.index); self.assertAlmostEqual(out['m1'], 0.50, places=12)

    def test_single_name_cap(self):
        mem = [f'm{i}' for i in range(20)]
        w = pd.Series({**{c: 0.034 for c in mem}, 'n1': 0.27}, dtype=float)   # share 0.68
        ind = {**{c: ('S11' if i < 2 else 'S22') for i, c in enumerate(mem)}, 'n1': 'S11'}   # n1's cut (0.18) would all go to m0, m1
        out, info = enforce_csi500_floor(w, set(mem), ind, max_single=0.05)
        self.assertLessEqual(out[mem].max(), 0.05 + 1e-12); self.assertGreater(info['capped_names'], 0); self.assertTrue(info['feasible'])
        self.assertAlmostEqual(out.sum(), w.sum(), places=12)
        self.assertAlmostEqual(out[mem].sum(), 0.86, places=12)

    def test_cap_infeasible_returns_excess_to_non_members(self):
        mem = [f'm{i}' for i in range(10)]
        w = pd.Series({**{c: 0.04 for c in mem}, 'n1': 0.55}, dtype=float)   # 10 names x 5% cap = 0.50 < 0.86
        out, info = enforce_csi500_floor(w, set(mem), {}, max_single=0.05)
        self.assertFalse(info['feasible']); self.assertLessEqual(out[mem].max(), 0.05 + 1e-12)
        self.assertAlmostEqual(out.sum(), w.sum(), places=12); self.assertAlmostEqual(out[mem].sum(), 0.50, places=12)

    def test_never_adds_names_or_negative_weights_and_input_untouched(self):
        w0 = self.w.copy()
        out, _ = enforce_csi500_floor(self.w, self.c500 | {'zz'}, self.ind, max_single=1.0)   # 'zz' is a member not in the target
        self.assertTrue(set(out.index) <= set(self.w.index)); self.assertTrue((out >= 0).all())
        pd.testing.assert_series_equal(self.w, w0)

    def test_zero_weight_member_does_not_receive(self):
        w = self.w.copy(); w['m5'] = 0.0; ind = dict(self.ind, m5='S11')
        out, _ = enforce_csi500_floor(w, self.c500 | {'m5'}, ind, max_single=1.0)
        self.assertNotIn('m5', out.index)

    def test_no_members_returns_unchanged(self):
        w = book(n1=0.5, n2=0.45)
        out, info = enforce_csi500_floor(w, set(), {})
        self.assertFalse(info['feasible']); pd.testing.assert_series_equal(out, w)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError): enforce_csi500_floor(book(m1=-0.1, n1=0.5), {'m1'})
        with self.assertRaises(ValueError): enforce_csi500_floor(book(m1=np.nan), {'m1'})
        with self.assertRaises(ValueError): enforce_csi500_floor(self.w, self.c500, floor=0.86, target=0.85)

    def test_idempotent(self):
        out, _ = enforce_csi500_floor(self.w, self.c500, self.ind, max_single=1.0)
        out2, info2 = enforce_csi500_floor(out, self.c500, self.ind, max_single=1.0)
        self.assertFalse(info2['applied']); pd.testing.assert_series_equal(out, out2)

    def test_post_lot_member_fraction(self):
        qty = pd.Series({'m1': 1000, 'n1': 500}); ref = pd.Series({'m1': 10.0, 'n1': 20.0})
        self.assertAlmostEqual(post_lot_member_fraction(qty, ref, {'m1'}, 20000.0), 0.5)

    def test_realistic_random_books(self):
        rng = np.random.default_rng(20261019)
        for _ in range(200):
            n = 110; codes = [f'c{i}' for i in range(n)]; w = pd.Series(rng.dirichlet(np.ones(n)) * 0.949, index=codes)
            mem = set(rng.choice(codes, size=int(rng.integers(60, 100)), replace=False)); ind = {c: f'S{rng.integers(0, 28)}' for c in codes if rng.random() > 0.05}
            out, info = enforce_csi500_floor(w, mem, ind)
            self.assertAlmostEqual(out.sum(), w.sum(), places=10); self.assertTrue((out >= 0).all())
            share = out[out.index.isin(list(mem))].sum()
            self.assertGreaterEqual(share, min(0.85, w[w.index.isin(list(mem))].sum()) - 1e-10)
            if info['applied'] and info['feasible']: self.assertAlmostEqual(share, 0.86, places=10)
            self.assertLessEqual(out[out.index.isin(list(mem))].max(), max(0.05, w.max()) + 1e-12)

    def test_name_already_above_cap_receives_nothing(self):
        mem = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6']
        w = book(m1=0.08, m2=0.02, m3=0.02, m4=0.02, m5=0.02, m6=0.02, n1=0.77)   # m1 above the 5% cap before the layer; share 0.18
        out, info = enforce_csi500_floor(w, set(mem), {}, floor=0.20, target=0.22)
        self.assertAlmostEqual(out['m1'], 0.08, places=12); self.assertAlmostEqual(out[mem].sum(), 0.22, places=12)
        self.assertLessEqual(out[mem[1:]].max(), 0.05)


if __name__ == '__main__':
    unittest.main(verbosity=1)

"""Synthetic correctness tests. They are not market-data or compute-performance evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from gru_research.evaluate import correlation, ranks, paired_block_bootstrap, breakdown

TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None
if TORCH_AVAILABLE:
    import torch
    from gru_research.model import PlanGRU, ic_losses, adamw_groups, unrolled_step
    from gru_research.train import (PanelDataset, day_number, local_days, CHINA_OFFSET, NS_DAY,
                                    seed_everything, checkpoint_load, fit_fixed)


GROUPS = {"hour": list(range(24)), "daily": list(range(24, 40)), "factors": [40, 41],
          "fund": list(range(42, 58)), "ages": [58, 59]}


class MetricTests(unittest.TestCase):
    def test_tied_rank_and_constant_prediction(self):
        np.testing.assert_array_equal(ranks(np.array([3., 1., 1., 7.])), [3., 1.5, 1.5, 4.])
        self.assertTrue(np.isnan(correlation(np.ones(120), np.arange(120))))
        self.assertAlmostEqual(correlation(np.arange(120), np.arange(120)), 1.0)
        self.assertTrue(np.isnan(correlation(np.arange(99), np.arange(99))))

    def test_block_bootstrap_pairs_timestamps_and_preserves_days(self):
        a, b = [], []
        for d in range(60):
            date = str(np.datetime64("2025-01-01") + np.timedelta64(d, "D"))
            for slot in range(4):
                common = {"signal_time_ns": d * 4 + slot, "date": date, "slot": slot,
                          "pool_count": 500, "scored_count": 450, "rank_ic": 0.1}
                a.append({**common, "ic": float(slot) + 0.01})
                b.append({**common, "ic": float(slot)})
        result = paired_block_bootstrap(a, b, 20, 100)
        self.assertEqual(result["trading_days"], 60)
        self.assertEqual(result["paired_timestamp_count"], 240)
        np.testing.assert_allclose(result["ci_95"], [0.01, 0.01], atol=1e-12)
        self.assertTrue(result["improvement_established"])
        report = breakdown(a)
        self.assertAlmostEqual(report["overall"]["coverage"], 0.9)
        self.assertEqual(set(report["slot"]), {"0", "1", "2", "3"})


@unittest.skipUnless(TORCH_AVAILABLE, "Run model tests on the cluster's existing PyTorch; no installation")
class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        seed_everything(11)

    def batch(self, sections=2, n=12, length=5):
        x = torch.randn(sections * n, length, 60)
        x[0, 0, 2] = float("nan")
        industry = torch.randint(0, 8, (sections * n, length))
        slots = torch.arange(length) % 4
        y = torch.randn(sections * n, 4)
        return x, industry, slots, y, [n] * sections

    def test_architecture_masks_and_window_independence(self):
        model = PlanGRU(GROUPS, 8).eval()
        self.assertEqual(model.gru.num_layers, 2)
        self.assertEqual(model.gru.hidden_size, 128)
        self.assertFalse(model.gru.bidirectional)
        self.assertEqual(model.project[1].in_features, 116)
        batch = self.batch(sections=1)
        with torch.no_grad():
            together = model(*batch[:3])
            alone = model(batch[0][:1], batch[1][:1], batch[2])
        self.assertTrue(torch.isfinite(together).all())
        torch.testing.assert_close(together[:1], alone, rtol=2e-5, atol=2e-6)
        encoded = PlanGRU.with_missing(torch.tensor([float("nan"), 0., float("inf")]))
        torch.testing.assert_close(encoded, torch.tensor([0., 0., 0., 1., 0., 1.]))

    def test_cross_sections_are_equal_weight_not_pooled(self):
        s = torch.tensor([0., 1., 2., 100., 101., 102., 103., 104.], requires_grad=True)
        y = torch.tensor([0., 1., 2., 4., 3., 2., 1., 0.])[:, None].repeat(1, 4)
        loss, skip = ic_losses(s, y, [3, 5], min_stocks=3)
        torch.testing.assert_close(loss, torch.ones(4))
        self.assertEqual(skip, 0)
        loss.sum().backward()
        self.assertTrue(torch.isfinite(s.grad).all())
        pooled, _ = ic_losses(s.detach(), y, [8], min_stocks=3)
        self.assertGreater(float((pooled - loss.detach()).abs().max()), 0.1)
        short, skipped = ic_losses(s, y, [3, 5], min_stocks=4)
        self.assertEqual(skipped, 1)
        torch.testing.assert_close(short, torch.full((4,), 2.))

    def test_blo_predictive_gradient_and_one_hot_inner_equivalence(self):
        model = PlanGRU(GROUPS, 8).eval()  # Remove dropout to compare exact updates.
        inner, outer = self.batch(), self.batch()
        logits = torch.zeros(4, requires_grad=True)
        updated, gradient, diagnostics = unrolled_step(model, logits, inner, outer, min_stocks=8)
        self.assertTrue(torch.isfinite(gradient).all())
        self.assertGreater(np.linalg.norm(diagnostics["predictive_hypergradient"]), 0.)
        self.assertTrue(all(torch.isfinite(p).all() for p in updated.values()))
        one_hot = torch.tensor([-1000., -1000., -1000., 0.], requires_grad=True)
        updated, _, _ = unrolled_step(model, one_hot, inner, outer, min_stocks=8)
        params = dict(model.named_parameters())
        direct, _ = ic_losses(model(*inner[:3]), inner[3], inner[4], min_stocks=8)
        grads = torch.autograd.grad(direct[3], tuple(params.values()))
        for (name, p), g in zip(params.items(), grads):
            expected = p - 1e-3 * (g + (1e-4 * p if p.ndim >= 2 else 0.))
            torch.testing.assert_close(updated[name], expected, rtol=1e-5, atol=1e-7)

    def test_ablations_do_not_use_excluded_groups(self):
        batch = self.batch(sections=1)
        changed = batch[0].clone()
        changed[..., GROUPS["factors"] + GROUPS["fund"] + GROUPS["ages"]] = 40.
        industry = (batch[1] + 1) % 8
        model = PlanGRU(GROUPS, 8, "pv").eval()
        with torch.no_grad():
            before = model(*batch[:3])
            after = model(changed, industry, batch[2])
        torch.testing.assert_close(before, after)
        groups = adamw_groups(model, 1e-4)
        nodecay = {id(p) for p in groups[1]["params"]}
        for name, parameter in model.named_parameters():
            if name.endswith("bias") or parameter.ndim < 2:
                self.assertIn(id(parameter), nodecay)


@unittest.skipUnless(TORCH_AVAILABLE, "Panel/memory-map tests use the cluster's existing PyTorch")
class PanelTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)

    def make_fixture(self, root: Path):
        dates = np.busday_offset("2022-11-01", np.arange(70), roll="forward")
        dates = np.r_[dates, np.busday_offset("2023-12-01", np.arange(50), roll="forward"),
                      np.busday_offset("2024-12-02", np.arange(50), roll="forward"),
                      np.busday_offset("2026-01-02", np.arange(30), roll="forward")]
        dates = np.unique(dates)
        ns = []
        for day in dates:
            for hour, minute in [(10, 30), (11, 30), (14, 0), (15, 0)]:
                ns.append(int(day.astype("datetime64[ns]").astype(np.int64)) + (hour * 60 + minute) * 60_000_000_000 - CHINA_OFFSET)
        times = np.array(ns, dtype=np.int64)
        n, u = len(times), 120
        rng = np.random.default_rng(11)
        execution = times + 60_000_000_000
        ends = np.zeros((n, 4), dtype=np.int64)
        for delta in range(1, 5):
            ends[:-delta, delta - 1] = execution[delta:]
        arrays = {"X": rng.normal(size=(n, u, 60)).astype(np.float32), "hour_complete": np.ones((n, u), dtype=bool),
                  "members": np.ones((n, u), dtype=bool), "industry": np.ones((n, u), dtype=np.int16),
                  "labels": rng.normal(size=(n, u, 4)).astype(np.float32), "times": times,
                  "earliest_execution_times": execution, "label_end_times": ends}
        for name, array in arrays.items():
            np.save(root / (name + ".npy"), array)
        (root / "symbols.json").write_text(json.dumps([str(i) for i in range(u)]))
        (root / "meta.json").write_text(json.dumps({"groups": GROUPS, "n_industries": 2,
                    "timestamp_format": "utc_ns", "timezone": "Asia/Shanghai", "synthetic_fixture": True}))

    def test_purge_embargo_complete_window_and_historical_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            panel = PanelDataset(root, enforce_audit=False)
            config = json.loads(Path(__file__).parents[1].joinpath("config.json").read_text())
            inner = panel.split_indices("inner", config)
            boundary = day_number("2023-01-01")
            cutoff = panel.trading_days[panel.trading_days < boundary][-2]
            self.assertTrue(np.all(panel.days[inner] < cutoff))
            self.assertTrue(np.all(local_days(panel.label_end_times[inner]) < cutoff))
            self.assertTrue(np.all(inner >= 79))
            # Membership at historical rows does not make an otherwise complete window invalid.
            members = np.load(root / "members.npy")
            members[:50, 0] = False
            np.save(root / "members.npy", members)
            panel = PanelDataset(root, enforce_audit=False)
            self.assertTrue(panel.eligible_mask(100)[0])
            complete = np.load(root / "hour_complete.npy")
            complete[90, 1] = False
            np.save(root / "hour_complete.npy", complete)
            panel = PanelDataset(root, enforce_audit=False)
            self.assertFalse(panel.eligible_mask(100)[1])
            self.assertTrue(panel.eligible_mask(170)[1])
            with self.assertRaisesRegex(ValueError, "production_ready"):
                PanelDataset(root)

    def test_future_data_does_not_change_past_window_or_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            panel = PanelDataset(root, enforce_audit=False)
            config = json.loads(Path(__file__).parents[1].joinpath("config.json").read_text())
            before = panel.batch([100], "cpu", config)
            values = np.load(root / "X.npy")
            values[101:] = 999.
            # A not-yet-effective future financial row changes only future inputs.
            values[101:, :, GROUPS["fund"]] = -777.
            np.save(root / "X.npy", values)
            future = PanelDataset(root, enforce_audit=False)
            after = future.batch([100], "cpu", config)
            torch.testing.assert_close(before[0], after[0])
            seed_everything(11)
            model = PlanGRU(GROUPS, 2).eval()
            with torch.no_grad():
                torch.testing.assert_close(model(*before[:3]), model(*after[:3]))

    def test_checkpoint_resume_matches_uninterrupted_training(self):
        import gru_research.train as training
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            self.make_fixture(data)
            panel = PanelDataset(data, enforce_audit=False)
            config = json.loads(Path(__file__).parents[1].joinpath("config.json").read_text())
            config.update({"epochs": 2, "sequence_length": 3})
            original_split = panel.split_indices
            selected = {kind: original_split(kind, config)[:8] for kind in ("train", "validation")}
            panel.split_indices = lambda kind, _config, require_labels=True: selected[kind]
            uninterrupted = root / "uninterrupted"
            resumed = root / "resumed"
            fit_fixed(panel, uninterrupted, 4, 11, "full", config, "cpu")
            original_save = training.checkpoint_save
            def stop_after_persisting_first_epoch(path, state):
                original_save(path, state)
                if path.name == "last.pt" and state["epoch"] == 0:
                    raise InterruptedError("Synthetic interruption after an atomic checkpoint")
            with patch.object(training, "checkpoint_save", side_effect=stop_after_persisting_first_epoch):
                with self.assertRaises(InterruptedError):
                    fit_fixed(panel, resumed, 4, 11, "full", config, "cpu")
            resumed_result = fit_fixed(panel, resumed, 4, 11, "full", config, "cpu")
            direct_state = checkpoint_load(uninterrupted / "last.pt", "cpu")
            resumed_state = checkpoint_load(resumed / "last.pt", "cpu")
            self.assertEqual(resumed_result["epochs_completed"], 2)
            self.assertEqual(direct_state["best_epoch"], resumed_state["best_epoch"])
            for name, parameter in direct_state["model"].items():
                torch.testing.assert_close(parameter, resumed_state["model"][name], rtol=0., atol=0.)


if __name__ == "__main__":
    unittest.main()

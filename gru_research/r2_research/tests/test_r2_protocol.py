"""Pure numerical and structural tests; no Torch, old tests, data or model runs."""
import ast
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import numpy as np

from gru_research.r2_research.protocol import (ARMS, FOLDS, SEEDS, PROTOCOL_SHA256,
    final_budget, load_protocol, plans, require_completed_diagnostics, select_lr)
from gru_research.r2_research.folds import period_indices, day, NS_DAY, CHINA_OFFSET
from gru_research.r2_research.causal_gate import safe_child
from gru_research.r2_research.runner import Runtime, prepare_contract


def outer_runs(differences=None, counts=(20, 23, 26)):
    out = []
    for fold, n in zip(FOLDS, counts):
        for arm in ARMS:
            for seed in SEEDS:
                rows = []
                for i in range(n):
                    date = datetime.fromisoformat(fold["outer"][0]) + timedelta(days=i)
                    for slot, hour in enumerate(("10:30", "11:30", "14:00", "15:00")):
                        t = datetime.fromisoformat(date.date().isoformat() + "T" + hour).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                        delta = differences[fold["id"]] if differences else 0.0
                        rows.append({"signal_time_ns": int(t.timestamp() * 1e9), "date": date.date().isoformat(),
                                     "slot": slot, "ic": .02 + (delta if arm == "lower_lr" else 0),
                                     "pool_count": 150, "scored_count": 100})
                out.append({"stage": "diagnostic_outer", "complete": True, "protocol_sha256": PROTOCOL_SHA256,
                    "fold": fold["id"], "arm": arm, "seed": seed, "checkpoint_role": "inner_best",
                    "checkpoint_sha256": "a" * 64, "eligible_sections_expected": len(rows), "records": rows})
    return out


def inner_results(arm="control"):
    return [{"stage": "diagnostic", "complete": True, "protocol_sha256": PROTOCOL_SHA256,
             "fold": f["id"], "arm": arm, "seed": s, "best_inner_epoch": i + 1,
             "best_inner_checkpoint_sha256": "a" * 64, "inner_records_sha256": "c" * 64}
            for i, (f, s) in enumerate((f, s) for f in FOLDS for s in SEEDS)]


class Chronology(unittest.TestCase):
    def panel(self):
        class SmallPanel:
            def eligible_mask(self, t, length, require_labels):
                self.lengths.append(length)
                return np.ones(100, dtype=bool) if t >= 79 else np.zeros(100, dtype=bool)
        p = SmallPanel()
        # Complete observed dates cross the boundary; eighty-hour eligibility
        # comes from the original history, never newly padded interval windows.
        p.trading_days = np.arange(day("2021-05-01"), day("2021-07-05"))
        p.days = np.repeat(p.trading_days, 4)
        p.times = p.days * NS_DAY - CHINA_OFFSET + np.tile(np.array([10.5, 11.5, 14, 15]) * 3_600_000_000_000, len(p.trading_days)).astype(np.int64)
        p.earliest_execution_times = p.times + 60_000_000_000
        p.label_end_times = p.times[:, None] + np.array([2, 3, 4, 5])[None, :] * 3_600_000_000_000
        p.labels = np.tile(np.arange(100, dtype=np.float32)[None, :, None], (len(p.times), 1, 4))
        p.lengths = []
        return p

    def test_real_end_times_strict_purge_and_two_calendar_dates(self):
        p = self.panel()
        cutoff = day("2021-06-29") * NS_DAY - CHINA_OFFSET
        first = int(np.flatnonzero(p.days == day("2021-06-01"))[0])
        p.label_end_times[first, 3] = cutoff  # Equality must be purged.
        p.label_end_times[first + 1, 0] = p.earliest_execution_times[first + 1]  # Entry equality is immature.
        ix, report = period_indices(p, "2021-06-01", "2021-07-01", {"embargo_trading_days": 2, "min_stocks": 100, "label_variance_floor": 1e-12})
        self.assertNotIn(first, ix)
        self.assertNotIn(first + 1, ix)
        self.assertTrue(np.all(p.label_end_times[ix] < cutoff))
        self.assertEqual(report["last_two_complete_calendar_dates"], ["2021-06-29", "2021-06-30"])
        self.assertTrue(np.all(p.days[ix] < day("2021-06-29")))

    def test_first_inner_window_uses_prior_fit_hours_not_inner_padding(self):
        p = self.panel()
        ix, _ = period_indices(p, "2021-06-01", "2021-07-01", {"embargo_trading_days": 2, "min_stocks": 100, "label_variance_floor": 1e-12})
        first = int(np.flatnonzero(p.days == day("2021-06-01"))[0])
        self.assertIn(first, ix)
        self.assertLess(p.days[first - 79], day("2021-06-01"))
        self.assertEqual(set(p.lengths), {80})


class Selection(unittest.TestCase):
    def test_equal_fold_weights_do_not_pool_unequal_fold_dates(self):
        result = select_lr(outer_runs({"2021": .001, "2022": .003, "2023": .005}))
        self.assertEqual(result["selected_arm"], "lower_lr")
        self.assertAlmostEqual(result["mean_difference"], .003, places=12)
        self.assertEqual(result["comparisons"], 1)

    def test_interval_contains_zero_keeps_control(self):
        result = select_lr(outer_runs())
        self.assertEqual(result["selected_arm"], "control")
        self.assertEqual(result["paired_difference_ci95"], [0.0, 0.0])

    def test_missing_or_duplicate_fit_retains_control(self):
        rows = outer_runs()
        for broken in (rows[:-1], rows[:-1] + [copy.deepcopy(rows[0])]):
            with self.subTest(count=len(broken)):
                self.assertIn("missing_or_invalid_evidence", select_lr(broken))

    def test_no_slot_drop_test_record_or_timestamp_masquerade(self):
        for kind in ("slot", "date", "clock", "population", "nan"):
            rows = outer_runs()
            with self.subTest(kind=kind):
                r = rows[0]
                if kind == "slot":
                    r["records"] = [x for x in r["records"] if x["slot"] != 3]
                    r["eligible_sections_expected"] = len(r["records"])
                elif kind == "date":
                    r["records"][0]["date"] = "2025-07-01"
                elif kind == "clock":
                    r["records"][0]["signal_time_ns"] += 60 * 1_000_000_000
                elif kind == "population":
                    r["records"][0]["scored_count"] = 101
                else:
                    r["records"][0]["ic"] = float("nan")
                result = select_lr(rows)
                self.assertEqual(result["selected_arm"], "control")
                self.assertIn("missing_or_invalid_evidence", result)

    def test_seed_outlier_cannot_replace_five_seed_average(self):
        rows = outer_runs()
        for r in rows:
            if r["arm"] == "lower_lr":
                for x in r["records"]:
                    x["ic"] += .04 if r["seed"] == 11 else -.02
        result = select_lr(rows)
        self.assertAlmostEqual(result["mean_difference"], -.008, places=12)
        self.assertEqual(result["selected_arm"], "control")


class BudgetAndExecution(unittest.TestCase):
    def test_partial_select_publishes_nothing_and_full_completion_remains_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime.__new__(Runtime)  # No constructor, Torch, gate or real panel.
            runtime.output = Path(tmp)
            runtime.binding = {"protocol_sha256": PROTOCOL_SHA256}
            runtime.collect_diagnostic_inputs = lambda: (inner_results(), [], {})
            with self.assertRaises(ValueError):
                runtime.select()
            self.assertFalse((runtime.output / "selection.json").exists())
            runtime.collect_diagnostic_inputs = lambda: (inner_results() + inner_results("lower_lr"),
                                                        outer_runs(counts=(10, 10, 10)), {})
            decision, _ = runtime.select()
            self.assertEqual(decision["selected_arm"], "control")
            original = (runtime.output / "selection.json").read_bytes()
            runtime.select()
            self.assertEqual((runtime.output / "selection.json").read_bytes(), original)

    def test_missing_other_arm_blocks_refit_but_complete_weak_statistics_keep_control(self):
        with self.assertRaises(ValueError):
            require_completed_diagnostics(inner_results(), [])
        inner = inner_results() + inner_results("lower_lr")
        outer = outer_runs(counts=(10, 10, 10))
        self.assertTrue(require_completed_diagnostics(inner, outer)["final_execution_prerequisites_complete"])
        self.assertEqual(select_lr(outer)["selected_arm"], "control")
        for kind in ("missing_record", "wrong_checkpoint", "duplicate_timestamp", "missing_slot", "invalid_clock"):
            altered = copy.deepcopy(outer)
            if kind == "missing_record":
                altered[0]["records"].pop()
            elif kind == "wrong_checkpoint":
                altered[0]["checkpoint_sha256"] = "d" * 64
            elif kind == "duplicate_timestamp":
                altered[0]["records"][1] = copy.deepcopy(altered[0]["records"][0])
            elif kind == "missing_slot":
                altered[0]["records"] = [r for r in altered[0]["records"] if r["slot"] != 3]
                altered[0]["eligible_sections_expected"] = len(altered[0]["records"])
            else:
                altered[0]["records"][0]["signal_time_ns"] += 1_000_000_000
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                require_completed_diagnostics(inner, altered)

    def test_median_fifteen_inner_epochs_independent_of_outer_ic(self):
        rows = inner_results()
        rows[0]["outer_IC"] = .99
        rows[1]["2025_IC"] = .8
        self.assertEqual(final_budget(rows, "control")["epochs"], 8)
        self.assertFalse(final_budget(rows, "control")["outer_or_2024_2025_2026_used_for_budget"])

    def test_missing_duplicate_bool_and_outside_epoch_block_final(self):
        rows = inner_results()
        variants = [rows[:-1], rows[:-1] + [copy.deepcopy(rows[0])]]
        for e in (True, 0, 41):
            altered = copy.deepcopy(rows)
            altered[0]["best_inner_epoch"] = e
            variants.append(altered)
        for broken in variants:
            with self.subTest(count=len(broken)):
                with self.assertRaises(ValueError):
                    final_budget(broken, "control")

    def test_final_function_has_no_validation_scheduler_or_metric_stop(self):
        source = Path(__file__).resolve().parents[1] / "runner.py"
        tree = ast.parse(source.read_text())
        functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        text = ast.unparse(functions["fit_final"])
        self.assertNotIn("validate", text)
        self.assertNotIn("scheduler.step", text)
        self.assertNotIn("inner_y4_IC", text)
        self.assertNotIn("0.07", text)
        self.assertIn("budget['epochs']", text)
        self.assertIn("checkpoint_state", text)

    def test_torch_import_follows_actual_causal_and_source_gates(self):
        source = Path(__file__).resolve().parents[1] / "runner.py"
        tree = ast.parse(source.read_text())
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("torch" in ast.unparse(n) for n in imports))
        runtime = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Runtime")
        init = next(n for n in runtime.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        text = ast.unparse(init)
        self.assertLess(text.index("verify_gate("), text.index("import torch"))
        self.assertLess(text.index("source_seal("), text.index("import torch"))
        self.assertLess(text.index("verify_gate("), text.index("PanelDataset("))

    def test_closed_mapping_refuses_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            (root / "escape").symlink_to(Path(other), target_is_directory=True)
            for p in ("../secret", "/tmp/secret", "escape/data"):
                with self.subTest(path=p), self.assertRaises(ValueError):
                    safe_child(root, p)

    def test_fixed_plan_and_default_preparation_do_not_claim_execution(self):
        self.assertEqual(len(plans()), 30)
        self.assertEqual(load_protocol()["fit_cap"]["total"], 35)
        contract = prepare_contract()
        self.assertFalse(contract["actual_training_started"])
        self.assertFalse(contract["IC07_guaranteed"])
        self.assertEqual(contract["new_future_ledger_records"], 0)


if __name__ == "__main__":
    unittest.main()

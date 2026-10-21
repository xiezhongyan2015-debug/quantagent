"""Read-only PLAN acceptance audit; script existence never proves completion.

Reads concrete runs, immutable receipts, checkpoints, per-section predictions and
score CSVs. A completed historical experiment and 60 future sessions are separate
requirements. Optional paired comparisons are written as a separate artifact and
never change models, selection, frozen scores, or original evaluation summaries.

python -m gru_research.experiment_acceptance --run /path/outputs/formal \
  --dataset /path/dataset --artifacts /path/artifacts --output audit.json \
  --derive-comparisons-dir /path/independent_comparisons
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import math
import re
from zoneinfo import ZoneInfo
import numpy as np

from .evaluate import breakdown, correlation, ranks, file_hash, paired_block_bootstrap, write_json

SEEDS = [11, 22, 33, 44, 55]
MODELS = ["fixed_label_1", "fixed_label_2", "fixed_label_3", "fixed_label_4",
          "blo_selected", "selected_full", "pv", "pv_factors", "pv_fund"]
EXPECTED_SCORE_COLUMNS = ["symbol", "signal_time", "earliest_execution_time", "score", "percentile", "model_version", "data_quality_flag"]
DEFAULT_STATISTICS_PROTOCOL = Path(__file__).parent / "artifacts" / "experiment_acceptance_statistics_protocol_20261001.json"
FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256 = "15d3b0f9e31671b5926caca9842b40de51fe4bfe2924bb6289ef661e19d85ea1"


class EvidencePending(Exception):
    """A declared future/in-progress artifact is honest but does not prove completion."""


def audited_epoch_count(reported_count: int, epochs: dict, config: dict, source_hash: str) -> dict:
    """Use actual continuous epoch logs; allow one narrowly identified metadata defect.

    The frozen runner increments its loop variable once before an early-stop break.
    It reports actual+1, while no training step occurs in that extra iteration. This
    exception never repairs missing logs, altered code, model state or selection.
    """
    assert epochs, "No actual epoch records"
    actual = max(epochs)
    assert set(epochs) == set(range(1, actual + 1)), "Missing actual epoch records"
    assert 1 <= actual <= config["epochs"]
    result = {"actual_completed_epochs": actual, "reported_completed_epochs": reported_count,
              "allowed_frozen_metadata_exception": False}
    if reported_count != actual:
        assert source_hash == FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256, "Unexpected source cannot use frozen count exception"
        assert reported_count == actual + 1, "Count discrepancy is not the known plus-one defect"
        assert actual < config["epochs"] and epochs[actual]["bad_epochs"] >= config["patience"], "Count exception requires actual early stopping"
        result.update(allowed_frozen_metadata_exception=True, source_sha256=source_hash,
            defect="Frozen fit_fixed loop enters one additional iteration before early-stop break; result count is metadata only",
            original_result_preserved=True, weights_labels_selection_changed=False)
    return result


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def near(a, b, tolerance=1e-10):
    return (a is None and b is None) or (finite(a) and finite(b) and abs(a - b) <= tolerance)


def load_json(path):
    return json.loads(Path(path).read_text())


def model_reference(run: Path, path: str) -> Path:
    """Keep original evidence identity, allowing a downloaded run directory."""
    candidate = Path(path)
    if candidate.exists():
        return candidate
    for part in ("fixed", "blo_final", "ablations", "forward_validation"):
        marker = "/" + part + "/"
        if marker in path:
            return run / part / path.split(marker, 1)[1]
    return candidate


def canonical_payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def verify_forecast_exports(run: Path, ledger: Path, data: dict, records: dict) -> dict:
    """Bind actual exported CSV bytes and manifest receipts to original records."""
    receipts = data["forecast_artifacts"]
    assert len(receipts) == len(records), "Every actual forecast requires its CSV and JSON receipt"
    by_signal = {r["signal_time_ns"]: r for r in receipts}
    assert set(by_signal) == set(records), "CSV receipts contain duplicates, missing or extra signals"
    for signal, record in records.items():
        payload, receipt = record["payload"], by_signal[signal]
        csv_path = ledger / "forecasts" / f"{signal:020d}.csv"
        record_path = ledger / "forecasts" / f"{signal:020d}.json"
        assert csv_path.is_file() and record_path.is_file(), "Actual CSV and original forecast JSON required"
        assert model_reference(run, receipt["path"]).resolve() == csv_path.resolve()
        assert model_reference(run, receipt["record_path"]).resolve() == record_path.resolve()
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=EXPECTED_SCORE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(payload["forecasts"])
        expected = output.getvalue().encode()
        assert csv_path.read_bytes() == expected, "Exported CSV differs from its original immutable forecast"
        assert file_hash(csv_path) == payload["forecast_csv_sha256"] == receipt["sha256"]
        assert file_hash(record_path) == receipt["record_file_sha256"]
        assert record["sha256"] == receipt["record_payload_sha256"]
        assert receipt["date"] == datetime.fromtimestamp(signal / 1e9, ZoneInfo("Asia/Shanghai")).date().isoformat()
        for key in ("signal_time_ns", "recorded_at_ns", "earliest_execution_time_ns", "pool_count", "scored_count"):
            assert receipt[key] == payload[key], "CSV receipt metadata differs from original forecast"
    return by_signal


def required_comparisons(selection):
    best_fixed = max(("fixed_" + str(i) for i in (1, 2, 3, 4)),
                     key=lambda k: selection["alternatives"][k]["mean_ic"])
    pairs = {f"label_{i}_minus_direct_y4": (f"fixed_label_{i}", "fixed_label_4") for i in (1, 2, 3)}
    pairs.update({"blo_minus_best_fixed": ("blo_selected", "fixed_label_" + best_fixed[-1]),
        "factor_increment_over_pv": ("pv_factors", "pv"),
        "fundamental_increment_over_pv": ("pv_fund", "pv"),
        "full_increment_over_pv": ("selected_full", "pv"),
        "fundamental_increment_given_factors": ("selected_full", "pv_factors"),
        "factor_increment_given_fundamentals": ("selected_full", "pv_fund")})
    return pairs


class AcceptanceAudit:
    def __init__(self, run: Path, dataset: Path | None, artifacts: Path | None,
                 derive_dir: Path | None = None, verify_scores: bool = True,
                 statistics_protocol: Path = DEFAULT_STATISTICS_PROTOCOL,
                 expected_protocol_sha256: str | None = None):
        self.run = Path(run)
        self.dataset = Path(dataset) if dataset else self.run / "dataset"
        self.artifacts = Path(artifacts) if artifacts else self.run / "validation_receipts"
        self.derive_dir, self.verify_scores = derive_dir, verify_scores
        self.statistics_protocol = Path(statistics_protocol)
        self.expected_protocol_sha256 = expected_protocol_sha256
        self.items, self.observations, self._hashes = [], [], {}
        self.config = self._optional(self.run / "run_config.json") or load_json(Path(__file__).with_name("config.json"))
        self.meta = self._optional(self.dataset / "meta.json")
        self.freeze = self._optional(self.run / "freeze.json")
        self.selection = self._optional(self.run / "selection.json")
        canonical = json.dumps(self.meta, sort_keys=True, separators=(",", ":")) if self.meta else None
        self.dataset_fingerprint = hashlib.sha256(canonical.encode()).hexdigest() if canonical else None

    @staticmethod
    def _optional(path):
        return load_json(path) if path.exists() else None

    def sha(self, path):
        path = Path(path)
        if path not in self._hashes:
            self._hashes[path] = file_hash(path)
        return self._hashes[path]

    def evidence(self, paths):
        return [{"path": str(Path(p)), "exists": Path(p).exists(),
                 "sha256": self.sha(p) if Path(p).is_file() else None} for p in paths]

    def check(self, identity, requirement, paths, predicate, scope="historical_experiment"):
        missing = [str(p) for p in paths if not Path(p).is_file()]
        item = {"id": identity, "requirement": requirement, "scope": scope,
                "status": "missing" if missing else "complete", "evidence": self.evidence(paths)}
        if missing:
            item["detail"] = "Required evidence is not readable at these paths in this audit environment: " + ", ".join(missing) + ". Unsynchronized remote output is unverified here, not evidence that a remote artifact or live job is missing."
        else:
            try:
                detail = predicate()
                item["detail"] = detail or "Direct artifacts satisfy the stated requirement"
            except EvidencePending as exc:
                item.update(status="missing", detail=str(exc))
            except Exception as exc:
                item.update(status="failed", detail=type(exc).__name__ + ": " + str(exc))
        self.items.append(item)
        return item

    def sources(self):
        if not self.dataset:
            self.check("audited_real_panel", "Audited real minute/PIT panel and all four label definitions", [],
                       lambda: (_ for _ in ()).throw(ValueError("No dataset directory supplied")))
            return
        meta_path = Path(self.dataset) / "meta.json"
        def panel_metadata():
            meta = self.meta
            assert meta["production_ready"] is True and meta["causality_audit"]["passed"] is True
            assert meta.get("raw_market_data_is_synthetic") is False
            assert meta["timestamp_format"] == "utc_ns" and meta["timezone"] == "Asia/Shanghai"
            assert meta["history"] == {"start": "2018-01-02", "end": "2026-09-23"}
            expected = [("hour", 24), ("daily", 16), ("fund", 16), ("ages", 2)]
            assert all(len(meta["groups"][k]) == n for k, n in expected)
            assert len(meta["groups"]["factors"]) <= 64 and len(meta["features"]) == 58 + len(meta["groups"]["factors"])
            return {"metadata_fingerprint": self.dataset_fingerprint, "source_data_fingerprint": meta["data_fingerprint"]}
        self.check("audited_real_panel", "Real 2018 warmup to 2026-09-23 panel, historical membership normalization, PIT audits, four labels", [meta_path], panel_metadata)
        if self.meta:
            files = [Path(self.dataset) / name for name in self.meta["files_sha256"]]
            def hashes():
                bad = [name for name, digest in self.meta["files_sha256"].items() if self.sha(Path(self.dataset) / name) != digest]
                assert not bad, "Dataset bytes changed: " + ", ".join(bad)
                reconstructed = hashlib.sha256(json.dumps(self.meta["files_sha256"], sort_keys=True).encode()).hexdigest()
                assert reconstructed == self.meta["data_fingerprint"]
                return {"verified_files": len(files), "reconstructed_source_fingerprint": reconstructed}
            self.check("dataset_content_hashes", "Actual panel and data audit bytes still match the frozen source fingerprint", files, hashes)
        if self.artifacts:
            receipt = Path(self.artifacts) / "source_tests_receipt.json"
            remote_log = Path(self.artifacts) / "remote_training_tests.log"
            local_log = Path(self.artifacts) / "local_source_tests.log"
            def tests():
                data = load_json(receipt)
                assert data["passed"] is True and data["local_returncode"] == data["remote_returncode"] == 0
                assert data["remote_model_tests"] == 9
                assert self.sha(remote_log) == data["remote_test_log_sha256"]
                assert self.sha(local_log) == data["local_test_log_sha256"]
                assert "Ran 9 tests" in remote_log.read_text() and "REMOTE_TEST_EXIT=0" in remote_log.read_text()
                assert not re.search(r"\.\.\.\s+skipped|OK\s*\(skipped=", remote_log.read_text()), "A required unittest was skipped"
                changed = [name for name, h in data["source_code_sha256"].items()
                           if not Path(__file__).parent.joinpath(name).exists() or self.sha(Path(__file__).parent / name) != h]
                assert not changed, "Validated source changed: " + ", ".join(changed)
                return {"local_tests": data["local_tests"], "remote_model_tests": 9,
                    "covered_invariants": ["future input/report perturbation", "common entry and ordered labels",
                        "independent timestamp IC", "complete80 historical windows", "purge2sessions",
                        "BLO predictive hypergradient", "onehot SGD equivalence", "atomic optimizer/RNG resume"]}
            self.check("actual_causality_and_model_tests", "Actual passed independent data/model tests, including perturbation, BLO gradient and deterministic continuation", [receipt, remote_log, local_log], tests)
            launch_log = Path(self.artifacts) / "remote_formal_progress.log"
            def launched():
                rows = [json.loads(line) for line in launch_log.read_text().splitlines() if line.startswith("{")]
                env = next(r for r in rows if r.get("event") == "remote_launch_verified")
                preflight = next(r for r in rows if r.get("event") == "gpu_preflight_passed")
                assert env["passed"] and preflight["passed"] and preflight["real_market_data"]
                assert len(preflight["inner_section_sizes"]) == len(preflight["outer_section_sizes"]) == 4
                assert np.linalg.norm(preflight["diagnostics"]["predictive_hypergradient"]) > 0
                epochs = [r for r in rows if r.get("event") == "epoch"]
                assert epochs and all(finite(r["validation_y4_ic"]) for r in epochs)
                return {"job_id": env["slurm_job_id"], "observed_formal_epochs": len(epochs),
                    "last_observed_epoch": epochs[-1], "live_job_state": "not established by a saved log; query Slurm separately"}
            self.check("actual_gpu_training_started", "Real data CUDA preflight followed by actual formal gradient training and validation", [launch_log], launched, scope="training_launch")

    def fixed_run(self, relative, label, seed, variant, identity):
        folder = self.run / relative
        result, best, log, sections = [folder / name for name in ("result.json", "best.pt", "epochs.jsonl", "best_validation_sections.json")]
        def validate():
            data = load_json(result)
            assert data["complete"] is True and data["real_data"] is True
            signature = data["signature"]
            assert signature["label"] == label and signature["seed"] == seed and signature["variant"] == variant
            assert signature["config"] == self.config
            if self.dataset_fingerprint:
                assert signature["dataset_fingerprint"] == self.dataset_fingerprint
            assert data["best_checkpoint_sha256"] == self.sha(best)
            epochs = [json.loads(s) for s in log.read_text().splitlines() if s.strip()]
            epochs = {e["epoch"]: e for e in epochs if e.get("event") == "epoch"}
            counts = audited_epoch_count(data["epochs_completed"], epochs, self.config,
                                         self.sha(Path(__file__).with_name("train.py")))
            assert 1 <= data["best_epoch"] <= counts["actual_completed_epochs"]
            if counts["allowed_frozen_metadata_exception"]:
                self.observations.append({"status": "known_metadata_defect", "item_id": identity,
                    "result_path": str(result), "original_result_sha256": self.sha(result),
                    "epochs_log_path": str(log), "epochs_log_sha256": self.sha(log), **counts})
            assert all(e["seed"] == seed and e["label"] == label and e["variant"] == variant for e in epochs.values())
            best_ic = max(e["validation_y4_ic"] for e in epochs.values())
            assert near(best_ic, data["best_validation_ic"])
            assert near(epochs[data["best_epoch"]]["validation_y4_ic"], best_ic)
            records = load_json(sections)["records"]
            assert records and all(r["date"].startswith("2024-") for r in records)
            assert near(breakdown(records)["overall"]["ic"], best_ic)
            return {"seed": seed, "label": label, "variant": variant, **counts,
                    "best_epoch": data["best_epoch"], "validation_y4_ic": best_ic}
        return self.check(identity, f"Completed from-scratch {variant} label {label}, seed {seed}; actual best checkpoint, per-epoch history and 2024 y4 selection", [result, best, log, sections], validate)

    def training(self):
        for label in (1, 2, 3, 4):
            for seed in SEEDS:
                self.fixed_run(Path("fixed") / f"label_{label}" / f"seed_{seed}", label, seed, "full", f"fixed_label_{label}_seed_{seed}")
        for seed in SEEDS:
            folder = self.run / "blo" / f"seed_{seed}"
            paths = [folder / name for name in ("result.json", "epochs.jsonl", "steps.jsonl", "last.pt")]
            def validate_blo(folder=folder, seed=seed):
                data = load_json(folder / "result.json")
                assert data["complete"] and data["steps"] == 2000 and data["warmup_epochs"] == 3 and data["tail_steps"] == 200
                assert data["signature"]["seed"] == seed and data["signature"]["config"] == self.config
                if self.dataset_fingerprint:
                    assert data["signature"]["dataset_fingerprint"] == self.dataset_fingerprint
                warm = [json.loads(s) for s in (folder / "epochs.jsonl").read_text().splitlines() if s.strip()]
                assert {e["epoch"] for e in warm if e["event"] == "blo_warmup"} == {1, 2, 3}
                steps = [json.loads(s) for s in (folder / "steps.jsonl").read_text().splitlines() if s.strip()]
                steps = {e["step"]: e for e in steps if e["event"] == "blo_step"}
                assert set(steps) == set(range(1, 2001)), "All 2000 actual BLO steps required"
                weights = np.asarray([steps[s]["weights"] for s in range(1801, 2001)])
                assert weights.shape == (200, 4) and np.isfinite(weights).all() and np.all(weights >= 0)
                np.testing.assert_allclose(weights.sum(axis=1), 1., atol=1e-6)
                np.testing.assert_allclose(weights.mean(axis=0), data["mean_tail_weights"], atol=1e-10)
                nonzero = sum(np.linalg.norm(e["predictive_hypergradient"]) > 0 for e in steps.values())
                assert nonzero > 0, "Entropy gradient alone cannot prove BLO learned labels"
                return {"seed": seed, "actual_steps": 2000, "predictive_hypergradient_nonzero_steps": nonzero,
                        "last200_mean_weights": data["mean_tail_weights"]}
            self.check(f"blo_seed_{seed}", f"Actual 3-epoch equal-label warmup, 2000 differentiable inner2019-22/outer2023 updates, last200 average for seed {seed}", paths, validate_blo)
        weights_path = self.run / "blo_weights.json"
        def average():
            runs = [load_json(self.run / "blo" / f"seed_{s}" / "result.json") for s in SEEDS]
            averaged = np.mean([r["mean_tail_weights"] for r in runs], axis=0)
            weights = load_json(weights_path)
            np.testing.assert_allclose(weights["mean_weights"], averaged, atol=1e-10)
            assert weights["selected_label"] == int(np.argmax(averaged) + 1) and weights["seeds"] == SEEDS
            return {"mean_weights": averaged.tolist(), "selected_label": weights["selected_label"]}
        self.check("blo_five_seed_selection", "Select max mean weights after averaging last200 steps within each of five seeds", [weights_path] + [self.run / "blo" / f"seed_{s}" / "result.json" for s in SEEDS], average)
        if weights_path.exists():
            label = load_json(weights_path)["selected_label"]
            for seed in SEEDS:
                self.fixed_run(Path("blo_final") / f"label_{label}" / f"seed_{seed}", label, seed, "full", f"blo_retrain_seed_{seed}")
        else:
            self.check("blo_from_scratch_retraining", "Five selected-horizon BLO final models trained afresh on2019-23/val2024", [weights_path], lambda: None)
        selection_path = self.run / "selection.json"
        def select():
            selection = self.selection
            means = {"fixed_" + str(label): np.mean([load_json(self.run / "fixed" / f"label_{label}" / f"seed_{s}" / "result.json")["best_validation_ic"] for s in SEEDS]) for label in (1, 2, 3, 4)}
            blo_label = load_json(weights_path)["selected_label"]
            means["blo"] = np.mean([load_json(self.run / "blo_final" / f"label_{blo_label}" / f"seed_{s}" / "result.json")["best_validation_ic"] for s in SEEDS])
            assert all(near(selection["alternatives"][k]["mean_ic"], float(v)) for k, v in means.items())
            best = max(means, key=means.get)
            choice = "fixed_4" if means[best] - means["fixed_4"] < 0.001 else best
            assert selection["selected_method"] == choice and selection["validation_year"] == 2024
            assert selection["selected_label"] == (blo_label if choice == "blo" else int(choice[-1]))
            return {"selected_method": choice, "selected_label": selection["selected_label"], "2024_five_seed_means": means}
        self.check("fixed_validation_choice", "2024 five-seed mean y4IC selects label method; <0.001 gain retains directy4; test results are not an input", [selection_path, weights_path], select)
        for variant in ("pv", "pv_factors", "pv_fund"):
            if self.selection:
                for seed in SEEDS:
                    self.fixed_run(Path("ablations") / variant / f"seed_{seed}", self.selection["selected_label"], seed, variant, f"ablation_{variant}_seed_{seed}")
            else:
                self.check(f"ablation_{variant}", f"Five {variant} models with the globally validation-selected label", [selection_path], lambda: None)

    def frozen(self):
        path = self.run / "freeze.json"
        def verify():
            freeze = self.freeze
            assert freeze["config"] == self.config and freeze["selection"] == self.selection
            assert freeze["config"]["seeds"] == SEEDS and freeze["no_test_driven_tuning"] is True
            if self.dataset_fingerprint:
                assert freeze["dataset_fingerprint"] == self.dataset_fingerprint
            assert set(freeze["evaluation_models"]) == set(MODELS)
            for name, checkpoints in freeze["evaluation_models"].items():
                assert len(checkpoints) == 5
                for checkpoint in checkpoints:
                    actual = model_reference(self.run, checkpoint)
                    assert actual.is_file(), "Actual frozen checkpoint absent: " + str(actual)
                    assert self.sha(actual) == freeze["checkpoint_hashes"][checkpoint]
            digest = hashlib.sha256(json.dumps({"data": freeze["dataset_fingerprint"], "config": freeze["config"],
                    "selection": freeze["selection"], "hashes": freeze["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
            assert digest == freeze["freeze_id"]
            return {"freeze_id": digest, "checkpoint_identities": len(freeze["checkpoint_hashes"]),
                    "frozen_at_utc": freeze["frozen_at_utc"], "historical_test_is_virgin_holdout": False}
        self.check("actual_model_freeze", "Actual checkpoint byte hashes, selected label, architecture/config/data frozen before historical evaluations", [path], verify)

    @staticmethod
    def compare_metrics(stored, recomputed):
        for key in ("overall", "year", "quarter", "slot"):
            assert key in stored and key in recomputed
        for group in ("year", "quarter", "slot"):
            assert set(stored[group]) == set(recomputed[group])
        for left, right in [(stored["overall"], recomputed["overall"])] + [(stored[g][k], recomputed[g][k]) for g in ("year", "quarter", "slot") for k in recomputed[g]]:
            for field in ("ic", "rank_ic", "ic_ir", "rank_ic_ir", "coverage"):
                assert near(left[field], right[field]), "Reported metric disagrees with actual section records: " + field
            assert left["cross_sections"] == right["cross_sections"]

    def score_csv(self, path, records, version):
        section = {r["signal_time_ns"]: r for r in records}
        rows, seen, current_time = 0, set(), None
        scored_counts, total_counts = {}, {}
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == EXPECTED_SCORE_COLUMNS
            for row in reader:
                rows += 1
                t, execution = int(row["signal_time"]), int(row["earliest_execution_time"])
                assert t in section and execution > t and row["model_version"] == version
                if current_time != t:
                    assert current_time is None or t > current_time, "CSV timestamps must be in chronological section order"
                    seen, current_time = set(), t
                assert row["symbol"] not in seen, "Duplicate time/security score"
                seen.add(row["symbol"])
                total_counts[t] = total_counts.get(t, 0) + 1
                if row["score"]:
                    assert finite(float(row["score"])) and 0 <= float(row["percentile"]) <= 1
                    scored_counts[t] = scored_counts.get(t, 0) + 1
                else:
                    assert row["percentile"] == "" and "incomplete_hour_window" in row["data_quality_flag"]
        assert set(total_counts) == set(section)
        assert all(total_counts[t] == r["pool_count"] and scored_counts.get(t, 0) == r["scored_count"] for t, r in section.items())
        return {"csv_rows_verified": rows, "unique_sections": len(section)}

    def historical_evaluation(self):
        for split, year in (("test_2025", "2025"), ("recent_2026", "2026")):
            directory = self.run / "frozen_evaluation" / split
            summary_path = directory / "summary.json"
            summary = self._optional(summary_path)
            records_by_model = {}
            for model in MODELS:
                sections_path, scores_path = directory / (model + "_sections.json"), directory / (model + "_scores.csv")
                def metrics_check(model=model, sections_path=sections_path, year=year, summary=summary):
                    assert self.freeze is not None and summary["freeze_id"] == self.freeze["freeze_id"]
                    assert summary["dataset_fingerprint"] == self.freeze["dataset_fingerprint"]
                    source = load_json(sections_path)
                    records, seeds = source["records"], source["seed_records"]
                    assert records and all(r["date"].startswith(year + "-") for r in records)
                    assert len({r["signal_time_ns"] for r in records}) == len(records)
                    assert {str(r["slot"]) for r in records} == {"0", "1", "2", "3"}
                    assert set(seeds) == {str(s) for s in SEEDS}
                    stored = summary["models"][model]
                    self.compare_metrics(stored, breakdown(records))
                    for seed, values in seeds.items():
                        assert [r["signal_time_ns"] for r in values] == [r["signal_time_ns"] for r in records]
                        self.compare_metrics(stored["per_seed"][seed], breakdown(values))
                    means = np.array([breakdown(values)["overall"]["ic"] for values in seeds.values()], dtype=float)
                    means = means[np.isfinite(means)]
                    assert len(means) == 5, "Five valid seed IC means required"
                    distribution = stored["five_seed_ic_distribution"]
                    assert near(distribution["mean"], float(means.mean())) and near(distribution["std"], float(means.std(ddof=1)))
                    assert near(distribution["min"], float(means.min())) and near(distribution["max"], float(means.max()))
                    unmatured = [r for r in records if not r["label_mature"]]
                    assert all(r["mature_label_count"] == 0 and r["ic"] is None and r["rank_ic"] is None for r in unmatured)
                    records_by_model[model] = records
                    return {"cross_sections": len(records), "unmatured_sections_excluded": len(unmatured), "actual_seed_count": 5}
                self.check(f"{split}_{model}_metrics", f"Actual frozen {year} y4IC/RankIC/ICIR, all four slots, annual/quarter/coverage and all five seeds for {model}", [summary_path, sections_path], metrics_check)
                if self.verify_scores:
                    self.check(f"{split}_{model}_score_interface", f"Actual per-stock scores and explicit exclusions for {model}, including executable timestamps, percentiles and version", [scores_path, sections_path, self.run / "freeze.json"],
                               lambda p=scores_path, s=sections_path, m=model: self.score_csv(p, load_json(s)["records"], self.freeze["freeze_id"] + ":" + m))
            derived_path = self.derive_dir / (split + "_paired_comparisons.json") if self.derive_dir else None
            if self.selection and self.freeze and len(records_by_model) == len(MODELS) and derived_path and self.statistics_protocol.exists():
                protocol = self.verify_statistics_protocol()
                pairs = required_comparisons(self.selection)
                comparisons = {name: {"model_a": a, "model_b": b,
                    **paired_block_bootstrap(records_by_model[a], records_by_model[b], 20, 2000, seed=20261001)}
                    for name, (a, b) in pairs.items()}
                artifact = {"freeze_id": self.freeze["freeze_id"], "dataset_fingerprint": self.freeze["dataset_fingerprint"],
                    "split": split, "derived_from_unchanged_frozen_scores": True,
                    "selection_or_training_changed": False, "pair_definitions_fixed_by_PLAN": True,
                    "statistics_protocol_sha256": self.sha(self.statistics_protocol),
                    "statistics_protocol_id": protocol["protocol_id"],
                    "historical_evidence_status": "descriptive frozen evaluation; prior project exposure not excluded",
                    "source_section_sha256": {m: self.sha(directory / (m + "_sections.json")) for m in MODELS},
                    "comparisons": comparisons, "multiple_comparison_correction": "none; no significance-selected model replacement",
                    "unestablished_improvements": [k for k, c in comparisons.items() if not c.get("improvement_established", False)]}
                if derived_path.exists() and load_json(derived_path) != artifact:
                    self.observations.append({"status": "failed", "detail": "Independent comparison output changed; preserve existing artifact", "path": str(derived_path)})
                else:
                    write_json(derived_path, artifact)
            expected_path = derived_path or self.run / "independent_comparisons" / (split + "_paired_comparisons.json")
            def comparisons_check(path=expected_path, directory=directory):
                self.verify_statistics_protocol()
                result = load_json(path)
                assert self.freeze and self.selection and result["freeze_id"] == self.freeze["freeze_id"]
                assert result["selection_or_training_changed"] is False
                assert result["statistics_protocol_sha256"] == self.sha(self.statistics_protocol)
                expected = required_comparisons(self.selection)
                assert set(result["comparisons"]) == set(expected)
                for name, (a, b) in expected.items():
                    report = result["comparisons"][name]
                    assert report["model_a"] == a and report["model_b"] == b
                    assert report["valid"] is True and report["block_trading_days"] == 20 and report["trading_days"] >= 20
                    assert report["repetitions"] == 2000 and report["seed"] == 20261001
                    assert report["improvement_established"] == bool(report["ci_95"][0] > 0)
                    assert result["source_section_sha256"][a] == self.sha(directory / (a + "_sections.json"))
                    assert result["source_section_sha256"][b] == self.sha(directory / (b + "_sections.json"))
                    recalculated = paired_block_bootstrap(load_json(directory / (a + "_sections.json"))["records"],
                        load_json(directory / (b + "_sections.json"))["records"], 20, 2000, seed=20261001)
                    assert near(report["mean_ic_difference"], recalculated["mean_ic_difference"])
                    np.testing.assert_allclose(report["ci_95"], recalculated["ci_95"], atol=1e-10)
                return {"paired_20day_contrasts": len(expected), "unestablished_improvements": result["unestablished_improvements"]}
            self.check(f"{split}_required_paired_comparisons", "Actual preregistered paired20-trading-day/2000rep CI for each short label/directy4, BLO/best fixed, factor and fundamental increments; zero-crossing means unestablished", [expected_path, self.statistics_protocol], comparisons_check)

    def verify_statistics_protocol(self):
        protocol = load_json(self.statistics_protocol)
        if self.expected_protocol_sha256:
            assert self.sha(self.statistics_protocol) == self.expected_protocol_sha256, "Preregistered protocol content hash changed"
        assert protocol["status"] == "FROZEN_BEFORE_2025_2026_EVALUATION"
        if self.freeze:
            registered = datetime.fromisoformat(protocol["registered_at_utc"].replace("Z", "+00:00"))
            frozen = datetime.fromisoformat(self.freeze["frozen_at_utc"].replace("Z", "+00:00"))
            assert registered <= frozen, "Statistics must be preregistered before the final model freeze and test evaluation"
        assert protocol["bootstrap"] == {"block_trading_days": 20, "repetitions": 2000, "seed": 20261001,
            "confidence_level": .95, "method": "paired_non_circular_moving_trading_day_blocks",
            "observation_weighting": "equal_timestamp_keep_all_four_slots_in_each_sampled_day"}
        assert protocol["evaluation_source_sha256"] == self.sha(Path(__file__).with_name("evaluate.py"))
        definitions = protocol["contrasts"]
        assert len(definitions) == 9
        expected = required_comparisons({"alternatives": {"fixed_" + str(i): {"mean_ic": float(i)} for i in (1, 2, 3, 4)}})
        assert set(definitions) == set(expected)
        for name, (a, b) in expected.items():
            assert definitions[name]["model_a"] == a
            if name == "blo_minus_best_fixed":
                assert definitions[name]["model_b_resolver"] == "max_2024_five_seed_mean_ic_among_four_fixed_labels"
                assert definitions[name]["resolver_tie_break"] == "smallest_label_horizon"
            else:
                assert definitions[name]["model_b"] == b
        assert protocol["test_results_used_to_choose_pairs"] is False
        assert protocol["training_or_model_selection_changed"] is False
        return protocol

    def external_deliverables(self):
        trading_manifest = self.run / "trading_evaluation" / "manifest.json"
        def trading():
            data = load_json(trading_manifest)
            assert self.freeze and data["freeze_id"] == self.freeze["freeze_id"]
            assert data["only_prediction_scores_changed"] is True and data["status"] == "complete"
            assert set(data["models"]) == set(MODELS)
            identities = {(r["portfolio_rules_sha256"], r["cost_rules_sha256"], r["execution_rules_sha256"]) for r in data["models"].values()}
            assert len(identities) == 1, "All models must use exactly the same fixed rules"
            assert data["execution_price_policy"] == "five_complete_minute_raw_open_TWAP"
            assert data["cash_and_holdings_preserved_on_failed_execution"] is True
            assert data["corporate_actions_and_t_plus_1_accounted"] is True
            for model in data["models"].values():
                for split in ("test_2025", "recent_2026"):
                    r = model[split]
                    assert r["status"] == "complete"
                    assert all(finite(r[k]) for k in ("net_excess_return", "maximum_drawdown", "turnover", "costs"))
                    assert split != "recent_2026" or r["reported_as_realized_full_year_return"] is False
                assert model["ledger_artifacts"], "Numerical summary alone does not prove actual fills or cash accounting"
                for receipt in model["ledger_artifacts"]:
                    path = model_reference(self.run, receipt["path"])
                    assert path.is_file() and self.sha(path) == receipt["sha256"]
            return {"models_with_same_execution_and_cost_rules": len(data["models"]), "net_nav_basis": data["net_nav_basis"]}
        self.check("actual_same_rule_portfolio_evaluation", "Actual same-rule portfolio/cost ledger, raw five-minute TWAP, failed fills retained, T+1/actions, net excess/drawdown/turnover/cost and partial2026 label", [trading_manifest], trading)
        forward = self.run / "forward_validation" / "manifest.json"
        def actual_forecasts(data):
            ledger = forward.parent
            header_path = ledger / "header.json"
            assert header_path.is_file(), "Positive forecast count requires an actual immutable ledger header"
            header = load_json(header_path)
            binding = canonical_payload_hash(header)
            assert binding == data["ledger_binding_sha256"]
            assert header["mode"] == "genuine_prospective_no_trading"
            assert header["freeze_sha256"] == self.sha(self.run / "freeze.json")
            assert header["model_version"] == self.freeze["freeze_id"] + ":selected_full"
            assert header["prospective_code_sha256"] == self.sha(Path(__file__).with_name("prospective.py"))
            from .prospective import ExchangeCalendar
            calendars = {calendar.fingerprint: calendar
                         for p in (ledger / "calendars").glob("*.json")
                         for calendar in [ExchangeCalendar.from_dict(load_json(p))]}
            assert calendars and all(not c.synthetic for c in calendars.values())
            assert all(c.first_full_day(header["frozen_at_ns"]) == data["first_session"] for c in calendars.values())
            files = sorted((ledger / "forecasts").glob("*.json"))
            assert len(files) == data["actual_forecast_records"] > 0
            previous, last_time, records = binding, -1, {}
            for path in files:
                record = load_json(path)
                payload = record["payload"]
                assert record["sha256"] == canonical_payload_hash(payload)
                assert payload["binding_sha256"] == binding and payload["previous_record_sha256"] == previous
                t = int(payload["signal_time_ns"])
                assert t > last_time and t > header["frozen_at_ns"]
                assert t <= payload["recorded_at_ns"] < payload["earliest_execution_time_ns"]
                calendar = calendars[payload["calendar_sha256"]]
                assert payload["earliest_execution_time_ns"] == calendar.execution_time(t)
                snapshot = payload["snapshot_manifest"]
                assert snapshot["raw_market_data_is_synthetic"] is False and snapshot["point_in_time_audit_passed"] is True
                assert snapshot["source_hashes"]
                captured = int(datetime.fromisoformat(snapshot["captured_at_utc"].replace("Z", "+00:00")).timestamp() * 1e9)
                assert t <= captured <= payload["recorded_at_ns"]
                rows = payload["forecasts"]
                assert len(rows) == payload["pool_count"] and len({r["symbol"] for r in rows}) == len(rows)
                assert all(r["signal_time"] == t and r["earliest_execution_time"] == payload["earliest_execution_time_ns"]
                           and r["model_version"] == header["model_version"] for r in rows)
                actual_scored = sum(r["score"] is not None for r in rows)
                assert actual_scored == payload["scored_count"]
                assert all((finite(r["score"]) and 0 <= r["percentile"] <= 1) if r["score"] is not None
                           else (r["percentile"] is None and r["data_quality_flag"]) for r in rows)
                records[t] = record
                previous, last_time = record["sha256"], t
            assert any(r["payload"]["scored_count"] >= self.config["min_stocks"] for r in records.values())
            verify_forecast_exports(self.run, ledger, data, records)
            return records
        def forward_started():
            data = load_json(forward)
            assert self.freeze and data["freeze_id"] == self.freeze["freeze_id"]
            assert data["target_complete_trading_sessions"] == 60 and data["frozen_model_and_rules"] is True
            assert data["first_session_is_first_full_trading_day_after_freeze"] is True
            assert data["historical_2025_2026_are_forward_evidence"] is False
            assert data["official_calendar_evidence"], "Explicit next official session evidence required"
            if (data.get("forecast_collection_started") is not True or data.get("research_evidence") is not True
                    or data.get("actual_forecast_records", 0) <= 0):
                raise EvidencePending("Freeze/calendar binding exists, but no actual forecasts have been collected; forward validation has not started")
            records = actual_forecasts(data)
            return {"first_session": data["first_session"], "completed_sessions": data["completed_sessions"],
                    "target_sessions": 60, "actual_forecast_records_verified": len(records),
                    "historical_and_forward_evidence_separate": True}
        self.check("forward_validation_started", "Begin fixed-rule collection on first full official trading day after final freeze, separate from historical evaluation", [forward], forward_started, scope="future_validation")
        def forward_complete():
            forward_started()
            data = load_json(forward)
            if data["completed_sessions"] < 60:
                raise EvidencePending(f"Only {data['completed_sessions']} actual future sessions are complete; 60 are required")
            assert len(set(data["completed_official_sessions"])) >= 60
            assert data["daily_artifacts"] and len(data["daily_artifacts"]) >= 60
            records = actual_forecasts(data)
            from .prospective import local_ns, ExchangeCalendar
            calendars = [ExchangeCalendar.from_dict(load_json(p)) for p in (forward.parent / "calendars").glob("*.json")]
            calendar = max(calendars, key=lambda c: c.coverage_end)
            expected_days = [d for d in calendar.dates if d >= data["first_session"]][:60]
            assert len(expected_days) == 60 and set(expected_days) == set(data["completed_official_sessions"])
            verified_days = set()
            for receipt in data["daily_artifacts"]:
                path = model_reference(self.run, receipt["path"])
                assert path.is_file() and self.sha(path) == receipt["sha256"]
                day = load_json(path)
                assert day["date"] == receipt["date"] and day["date"] in data["completed_official_sessions"]
                assert day["freeze_id"] == self.freeze["freeze_id"] and day["mode"] == "genuine_prospective_no_trading"
                assert day["model_version"] == self.freeze["freeze_id"] + ":selected_full"
                assert len(day["slots"]) == 4
                expected_forecast_hashes, expected_outcome_hashes = [], []
                for slot, (hour, minute) in enumerate(((10, 30), (11, 30), (14, 0), (15, 0))):
                    signal = local_ns(day["date"], hour, minute)
                    assert signal in records
                    forecast = records[signal]
                    outcome_path = forward.parent / "outcomes" / f"{signal:020d}.json"
                    assert outcome_path.is_file()
                    slot_receipt = day["slots"][slot]
                    assert slot_receipt["signal_time_ns"] == signal
                    assert slot_receipt["forecast"] == next(a for a in data["forecast_artifacts"] if a["signal_time_ns"] == signal)
                    assert model_reference(self.run, slot_receipt["outcome"]["path"]).resolve() == outcome_path.resolve()
                    assert slot_receipt["outcome"]["sha256"] == self.sha(outcome_path)
                    outcome = load_json(outcome_path)
                    payload = outcome["payload"]
                    assert outcome["sha256"] == canonical_payload_hash(payload)
                    assert payload["forecast_sha256"] == forecast["sha256"] and payload["signal_time_ns"] == signal
                    assert payload["label_end_time_ns"] > forecast["payload"]["earliest_execution_time_ns"]
                    assert payload["label_end_time_ns"] == calendar.y4_end(signal)
                    assert payload["label_end_time_ns"] <= payload["source_available_at_ns"] <= payload["observed_at_ns"]
                    assert payload["source"]["price_policy"] == "five_complete_minute_qfq_open_TWAP" and payload["source"]["hashes"]
                    scores = {r["symbol"]: r["score"] for r in forecast["payload"]["forecasts"]}
                    assert len({r["symbol"] for r in payload["labels"]}) == len(payload["labels"])
                    pairs = [(scores[r["symbol"]], r["y4"]) for r in payload["labels"]
                             if scores.get(r["symbol"]) is not None and r["y4"] is not None]
                    assert len(pairs) == payload["paired_count"] >= self.config["min_stocks"]
                    x, y = np.array(pairs, dtype=float).T
                    ic, rank_ic = correlation(x, y, self.config["min_stocks"]), correlation(ranks(x), ranks(y), self.config["min_stocks"])
                    assert finite(ic) and finite(rank_ic), "Constant or invalid future cross sections cannot count as completed sessions"
                    assert near(payload["ic"], ic if finite(ic) else None) and near(payload["rank_ic"], rank_ic if finite(rank_ic) else None)
                    expected_forecast_hashes.append(forecast["sha256"])
                    expected_outcome_hashes.append(self.sha(outcome_path))
                assert day["forecast_record_hashes"] == expected_forecast_hashes and day["outcome_file_hashes"] == expected_outcome_hashes
                verified_days.add(day["date"])
            assert len(verified_days) >= 60
            return {"completed_sessions": data["completed_sessions"], "actual_daily_artifacts": len(verified_days),
                    "four_slot_forecasts_and_mature_outcomes_verified": True}
        self.check("forward_validation_60_sessions", "Observe and verify actual 60 complete future trading sessions after freeze; elapsed time or historical tests are insufficient", [forward], forward_complete, scope="future_validation")

    def report(self):
        self.sources()
        self.training()
        self.frozen()
        self.check("preregistered_independent_statistics", "Nine fixed contrast definitions and paired20-day/2000rep bootstrap protocol frozen while baselines train, before historical test metrics exist", [self.statistics_protocol],
                   lambda: {"protocol_id": self.verify_statistics_protocol()["protocol_id"], "sha256": self.sha(self.statistics_protocol)}, scope="statistical_preregistration")
        self.historical_evaluation()
        self.external_deliverables()
        counts = {status: sum(i["status"] == status for i in self.items) for status in ("complete", "missing", "failed")}
        historical = [i for i in self.items if i["scope"] == "historical_experiment"]
        future = [i for i in self.items if i["scope"] == "future_validation"]
        return {"schema_version": 1, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_directory": str(self.run), "dataset_directory": str(self.dataset) if self.dataset else None,
            "historical_experiment_complete": bool(historical) and all(i["status"] == "complete" for i in historical),
            "future_60_sessions_complete": bool(future) and all(i["status"] == "complete" for i in future),
            "full_PLAN_complete": bool(self.items) and all(i["status"] == "complete" for i in self.items)
                                 and not any(o["status"] == "failed" for o in self.observations),
            "status_counts": counts, "items": self.items, "observations": self.observations,
            "result_count_audit": [o for o in self.observations if o["status"] == "known_metadata_defect"],
            "interpretation": "Missing means evidence unreadable in this audit environment; remote outputs may not be synchronized. It does not establish absent remote artifacts or a failed/stopped live job. This report never substitutes for Slurm status.",
            "statistical_review": {"original_evaluator": "per-seed and ensemble year/quarter/four-slot metrics are present; CI compares each model minus selected_full",
                "required_additional_pairs": "short labels vs directy4; BLO vs best fixed; factor/fundamental conditional and unconditional increments",
                "pairing_unit": "same timestamp differences, 20-day blocks retaining all within-day timestamps",
                "historical_tests_never_called_true_forward_validation": True,
                "bootstrap_multiple_comparison_correction": "none; comparisons descriptive and not used to select a replacement model"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--derive-comparisons-dir", type=Path)
    parser.add_argument("--protocol", "--statistics-protocol", dest="statistics_protocol", type=Path, default=DEFAULT_STATISTICS_PROTOCOL)
    parser.add_argument("--expected-protocol-sha256", help="Externally recorded preregistration hash; rejects modified protocol bytes")
    parser.add_argument("--skip-score-csv-validation", action="store_true", help="CSV interface remains unverified; do not use for final acceptance")
    args = parser.parse_args()
    audit = AcceptanceAudit(args.run, args.dataset, args.artifacts, args.derive_comparisons_dir,
                            not args.skip_score_csv_validation, args.statistics_protocol, args.expected_protocol_sha256)
    report = audit.report()
    if args.skip_score_csv_validation:
        report["full_PLAN_complete"] = report["historical_experiment_complete"] = False
        report["observations"].append({"status": "missing", "detail": "Actual score CSV interface verification explicitly skipped"})
    write_json(args.output, report)
    print(json.dumps({"output": str(args.output), "status_counts": report["status_counts"],
                      "full_PLAN_complete": report["full_PLAN_complete"]}), flush=True)


if __name__ == "__main__":
    main()

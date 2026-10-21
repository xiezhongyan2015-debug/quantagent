"""Collect actual training/evaluation evidence after the allocated GPU job ends.

Runs on a dependent CPU job. It never fits, selects or modifies a checkpoint.
Missing portfolio data and future sessions stay explicitly pending in the audit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

from .evaluate import file_hash, write_json
from .experiment_acceptance import AcceptanceAudit, MODELS


def corrected_score_exports(run, dataset):
    """Produce separately identified metadata/coverage evidence, never fit again."""
    run, dataset = Path(run), Path(dataset)
    destination = run / "corrected_score_exports"
    manifest_path = destination / "manifest.json"
    if not (run / "freeze.json").is_file():
        return {"status": "pending", "reason": "actual_final_model_freeze_missing"}
    raw = [run / "frozen_evaluation" / split / (name + "_scores.csv")
           for split in ("test_2025", "recent_2026") for name in MODELS]
    missing = [str(p) for p in raw if not p.is_file()]
    if missing:
        return {"status": "pending", "reason": "actual_frozen_raw_scores_missing", "missing": missing}
    try:
        if not manifest_path.is_file():
            log = destination / "export_command.log"
            if log.exists():
                match = re.search(r"EXPORT_EXIT=(-?\d+)\s*$", log.read_text())
                return {"status": "failed", "reason": "previous_export_attempt_has_no_manifest",
                    "exit_code": int(match.group(1)) if match else None,
                    "log": str(log), "log_sha256": file_hash(log),
                    "retry_performed": False, "original_scores_and_models_unchanged": True}
            destination.mkdir(parents=True, exist_ok=True)
            # Reserve this attempt's log before running: a second call must not
            # launch another export whose diagnostics could then be discarded.
            with log.open("x") as stream:
                result = subprocess.run([sys.executable, "-m", "gru_research.score_export_audit",
                    "--run", str(run), "--dataset", str(dataset), "--output-dir", str(destination)],
                    capture_output=True, text=True, check=False)
                stream.write(result.stdout + result.stderr + f"\nEXPORT_EXIT={result.returncode}\n")
            if result.returncode != 0:
                return {"status": "failed", "reason": "readonly_export_failed", "exit_code": result.returncode,
                    "original_scores_and_models_unchanged": True, "log": str(log), "log_sha256": file_hash(log)}
        manifest = json.loads(manifest_path.read_text())
        frozen_path = run / "freeze.json"
        frozen = json.loads(frozen_path.read_text())
        metadata = json.loads((dataset / "meta.json").read_text())
        fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        assert fingerprint == frozen["dataset_fingerprint"], "Actual panel metadata differs from freeze"
        assert manifest["freeze_id"] == frozen["freeze_id"]
        assert manifest["freeze_sha256"] == file_hash(frozen_path)
        assert manifest["export_code_sha256"] == file_hash(Path(__file__).with_name("score_export_audit.py"))
        expected = {split + "/" + name for split in ("test_2025", "recent_2026") for name in MODELS}
        assert set(manifest["reports"]) == expected, "All eighteen actual exports required"
        for key, report in manifest["reports"].items():
            split, name = key.split("/")
            expected_raw = (run / "frozen_evaluation" / split / (name + "_scores.csv")).resolve()
            expected_corrected = (destination / split / (name + "_scores.csv")).resolve()
            assert Path(report["raw_csv"]) == expected_raw, "Wrong original file for model/split"
            assert Path(report["corrected_csv"]) == expected_corrected, "Wrong corrected file for model/split"
            assert report["model_version"] == frozen["freeze_id"] + ":" + name
            assert report["dataset_fingerprint"] == fingerprint
            assert report["research_evidence"] is True
            assert report["raw_preserved"] is True and report["scores_percentiles_versions_changed"] is False
            assert report["inference_performed"] is False and report["labels_or_IC_metrics_read"] is False
            assert file_hash(Path(report["raw_csv"])) == report["raw_sha256"]
            assert file_hash(Path(report["corrected_csv"])) == report["corrected_sha256"]
        assert set(manifest["verified_input_hashes"]) == {
            "X.npy", "hour_complete.npy", "members.npy", "industry.npy", "times.npy",
            "earliest_execution_times.npy", "symbols.json"}, "All seven exact panel inputs required"
        for name, sha in manifest["verified_input_hashes"].items():
            assert metadata["files_sha256"][name] == sha, "Input receipt differs from frozen panel hash"
            assert file_hash(dataset / name) == sha
        complete = manifest["status"] == "complete_export" and all(
            r["status"] == "complete_export" and not r["unresolved_original_score_sections"]
            for r in manifest["reports"].values())
        return {"status": "complete" if complete else "incomplete", "manifest": str(manifest_path),
            "manifest_sha256": file_hash(manifest_path), "actual_model_split_exports": len(expected),
            "original_scores_percentiles_versions_and_metrics_preserved": True,
            "full_calendar_pool_coverage_and_80_hour_missing_flags": True,
            "model_config_or_selection_changed": False}
    except Exception as exc:
        return {"status": "failed", "reason": "derived_reporting_integrity_failed",
            "error_type": type(exc).__name__, "detail": str(exc), "original_models_and_scores_unchanged": True}


def collect(run, dataset, receipts, calendar, destination, training_job):
    run, dataset, receipts, destination = map(Path, (run, dataset, receipts, destination))
    if training_job <= 0:
        raise ValueError("An actual positive Slurm training job ID is required")
    destination.mkdir(parents=True, exist_ok=True)
    receipts.mkdir(parents=True, exist_ok=True)
    original_log = run.parent / f"slurm-{training_job}.log"
    if original_log.is_file():
        shutil.copyfile(original_log, receipts / "remote_formal_progress.log")
    exit_path = run / f"job_exit_{training_job}.txt"
    exit_text = exit_path.read_text() if exit_path.is_file() else ""
    match = re.fullmatch(r"JOB_EXIT=(\d+)\s*", exit_text)
    training_exit = int(match.group(1)) if match else None

    prospective = {"status": "pending", "reason": "final_model_freeze_not_yet_available"}
    if (run / "freeze.json").is_file():
        # Actual future observations cannot be created here. Bind the real model
        # and calendar, and publish the current empty/observed ledger status.
        from .prospective import ExchangeCalendar, FrozenContract, ForecastLedger
        contract = FrozenContract.read(run / "freeze.json", dataset / "meta.json")
        exchange = ExchangeCalendar.read(calendar)
        ledger = ForecastLedger(run / "forward_validation", contract, exchange)
        prospective = ledger.status()
        if hasattr(ledger, "manifest"):
            write_json(run / "forward_validation" / "manifest.json", ledger.manifest())
        write_json(run / "forward_validation" / "current_status.json", prospective)

    protocol = receipts / "experiment_acceptance_statistics_protocol_20261001.json"
    report = AcceptanceAudit(run, dataset, receipts, run / "independent_comparisons",
        statistics_protocol=protocol,
        expected_protocol_sha256="e5e0f80329256694c7d26021e8fcb01b537ce60536f6321d2e164874c8d838ae").report()
    export_status = corrected_score_exports(run, dataset)
    report["independent_score_metadata_and_coverage_correction"] = export_status
    report["training_job_exit_evidence"] = {"job_id": training_job, "exit_file": str(exit_path),
        "exit_code": training_exit, "verified_successful_exit": training_exit == 0}
    if training_exit != 0 or export_status["status"] != "complete":
        report["full_PLAN_complete"] = report["historical_experiment_complete"] = False
    write_json(run / "acceptance.json", report)
    training_items = [i for i in report["items"] if i["scope"] != "future_validation"
        and i["id"] != "actual_same_rule_portfolio_evaluation"]
    prediction_complete = training_exit == 0 and all(i["status"] == "complete" for i in training_items) \
        and not any(o["status"] == "failed" for o in report["observations"]) and export_status["status"] == "complete"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_archive = destination / f"gru_results_job{training_job}_{stamp}.tar.gz"
    score_archive = destination / f"gru_scores_job{training_job}_{stamp}.tar.gz"
    report_files, score_files = [], []
    for path in sorted(run.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.endswith("_scores.csv"):
            score_files.append(path)
        elif path.suffix in {".json", ".jsonl", ".txt", ".log"} or path.name == "best.pt":
            report_files.append(path)
    for target, paths in ((report_archive, report_files), (score_archive, score_files)):
        with tarfile.open(target, "x:gz", compresslevel=1) as handle:
            for path in paths:
                handle.add(path, arcname=str(Path("formal") / path.relative_to(run)), recursive=False)
            if target == report_archive:
                for path in sorted(receipts.iterdir()):
                    if path.is_file() and not path.is_symlink():
                        handle.add(path, arcname=str(Path("validation_receipts") / path.name), recursive=False)
    result = {"collected_at_utc": datetime.now(timezone.utc).isoformat(), "training_job_id": training_job,
        "training_exit_code": training_exit, "training_and_prediction_complete": prediction_complete,
        "full_PLAN_complete": report["full_PLAN_complete"], "acceptance_status_counts": report["status_counts"],
        "prospective_status": prospective,
        "independent_score_metadata_and_coverage_correction": export_status,
        "archives": [{"path": str(p), "bytes": p.stat().st_size, "sha256": file_hash(p)}
                     for p in (report_archive, score_archive)],
        "no_training_selection_checkpoint_or_score_changes": True}
    write_json(destination / f"gru_delivery_job{training_job}.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "dataset", "receipts", "calendar", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--training-job", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.run, args.dataset, args.receipts, args.calendar,
                             args.destination, args.training_job)), flush=True)


if __name__ == "__main__":
    main()

"""Separate score metadata/coverage correction; original models and CSVs stay raw.

This CPU-only export reads no labels or IC metrics and performs no inference.
Scores, percentiles and model versions already in the raw CSV are preserved as
strings. Entire unscored sections get blank scores, never zero replacements.
Eligible sections lacking original scores remain explicitly incomplete.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from itertools import groupby
import json
import math
import os
from pathlib import Path

import numpy as np

from .evaluate import file_hash

COLUMNS = ["symbol", "signal_time", "earliest_execution_time", "score", "percentile", "model_version", "data_quality_flag"]
NS_DAY = 86_400_000_000_000
CHINA_OFFSET = 8 * 3_600_000_000_000
SPLITS = {"test_2025": ("2025-01-01", "2026-01-01"), "recent_2026": ("2026-01-01", "2026-09-24")}


def exclusive_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


class ScorePanel:
    def __init__(self, path, config, expected_fingerprint=None, fixture=False):
        self.path, self.config, self.fixture = Path(path), config, fixture
        self.meta = json.loads((self.path / "meta.json").read_text())
        self.fingerprint = hashlib.sha256(json.dumps(self.meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if expected_fingerprint and self.fingerprint != expected_fingerprint:
            raise ValueError("Panel metadata differs from frozen model")
        if self.meta.get("raw_market_data_is_synthetic") is not bool(fixture):
            raise ValueError("Synthetic fixtures cannot be exported as real evidence")
        if self.meta.get("production_ready") is not True or self.meta.get("causality_audit", {}).get("passed") is not True:
            raise ValueError("An audited frozen panel is required")
        if (config["sequence_length"], config["min_stocks"], config["embargo_trading_days"]) != (80, 100, 2):
            raise ValueError("Export preserves the frozen80/min100/embargo2 protocol")
        self.verified_input_hashes = {}
        for name in ("X", "hour_complete", "members", "industry", "times", "earliest_execution_times"):
            filename = name + ".npy"
            digest = file_hash(self.path / filename)
            if self.meta["files_sha256"].get(filename) != digest:
                raise ValueError("Actual panel bytes changed: " + filename)
            self.verified_input_hashes[filename] = digest
            setattr(self, name, np.load(self.path / filename, mmap_mode="r", allow_pickle=False))
        symbol_hash = file_hash(self.path / "symbols.json")
        if self.meta["files_sha256"].get("symbols.json") != symbol_hash:
            raise ValueError("Original symbol schema changed")
        self.verified_input_hashes["symbols.json"] = symbol_hash
        self.symbols = json.loads((self.path / "symbols.json").read_text())
        self.T, self.U, _ = self.X.shape
        if self.X.dtype != np.float32 or len(self.symbols) != self.U or len(set(self.symbols)) != self.U:
            raise ValueError("Unverified panel dimensions/symbols")
        self.days = (self.times + CHINA_OFFSET) // NS_DAY
        self._complete_prefix = np.zeros((self.T + 1, self.U), np.int32)
        self._complete_prefix[1:] = np.cumsum(~self.hour_complete, axis=0, dtype=np.int32)
        self._missing_prefix = np.zeros_like(self._complete_prefix)
        # One sequential X scan is shared across all eighteen exports; no future
        # row can affect a preceding window's difference of cumulative counts.
        for start in range(0, self.T, 128):
            end = min(start + 128, self.T)
            flags = (~np.isfinite(self.X[start:end])).any(axis=2)
            self._missing_prefix[start + 1:end + 1] = np.cumsum(flags, axis=0, dtype=np.int32) + self._missing_prefix[start]

    def sections(self, split):
        start, end = [int(np.datetime64(x, "D").astype(np.int64)) for x in SPLITS[split]]
        indices = np.flatnonzero((self.days >= start) & (self.days < end))
        if not len(indices):
            raise ValueError("No panel calendar rows for requested split")
        boundary = np.zeros(self.T, bool)
        if split != "recent_2026":
            prior = np.unique(self.days[self.days < end])
            if len(prior) < 2:
                raise ValueError("Insufficient frozen calendar for embargo")
            boundary = self.days >= prior[-2]
        return indices, boundary

    def window_flags(self, t):
        start = max(0, t + 1 - 80)
        complete = self._complete_prefix[t + 1] == self._complete_prefix[start]
        if t < 79:
            complete[:] = False
        missing = self._missing_prefix[t + 1] != self._missing_prefix[start]
        return complete, missing


def export_scores(panel, split, raw_csv, output_csv, model_version):
    raw_csv, output_csv = Path(raw_csv), Path(output_csv)
    if output_csv.exists() or output_csv.resolve() == raw_csv.resolve():
        raise FileExistsError("Raw and previously derived evidence cannot be overwritten")
    raw_hash = file_hash(raw_csv)
    indices, boundary = panel.sections(split)
    sections, unresolved, rows_written, preserved = [], [], 0, 0
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_csv.with_suffix(output_csv.suffix + ".partial")
    temporary_created = False
    try:
        with raw_csv.open(newline="") as original, temporary.open("x", newline="") as output:
            temporary_created = True
            reader = csv.DictReader(original)
            if reader.fieldnames != COLUMNS:
                raise ValueError("Raw score interface changed")
            groups = iter(groupby(reader, key=lambda row: int(row["signal_time"])))
            pending = next(groups, None)
            last_raw_time = -1
            writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            for t in indices:
                signal, entry = int(panel.times[t]), int(panel.earliest_execution_times[t])
                present = pending is not None and pending[0] == signal
                if pending is not None and pending[0] < signal:
                    raise ValueError("Raw CSV contains unordered/out-of-scope timestamp")
                current = {}
                if present:
                    if signal <= last_raw_time:
                        raise ValueError("Repeated/nonchronological raw section")
                    for row in pending[1]:
                        if row["symbol"] in current:
                            raise ValueError("Duplicate original stock score")
                        current[row["symbol"]] = row
                    last_raw_time = signal
                    pending = next(groups, None)
                pool = np.flatnonzero(panel.members[t])
                complete, missing = panel.window_flags(t)
                eligible = complete & panel.members[t]
                eligible_count = int(eligible.sum())
                below_min = eligible_count < 100
                expected_scored = 0 if boundary[t] or below_min else eligible_count
                if present and set(current) != {panel.symbols[u] for u in pool}:
                    raise ValueError("Raw rows differ from actual historical pool")
                missing_original = not present and expected_scored > 0
                if missing_original:
                    unresolved.append(signal)
                actual_scored, reasons = 0, {}
                for u in pool:
                    symbol = panel.symbols[u]
                    row = dict(current.get(symbol) or {"symbol": symbol, "signal_time": str(signal),
                        "earliest_execution_time": str(entry), "score": "", "percentile": "", "model_version": model_version,
                        "data_quality_flag": ""})
                    if int(row["signal_time"]) != signal or int(row["earliest_execution_time"]) != entry or row["model_version"] != model_version:
                        raise ValueError("Original forecast clock/model identity changed")
                    if bool(row["score"]) != bool(row["percentile"]):
                        raise ValueError("Score/percentile missingness differs")
                    scored = bool(row["score"])
                    if scored:
                        if not eligible[u] or boundary[t] or below_min or not math.isfinite(float(row["score"])):
                            raise ValueError("Original score violates frozen eligibility")
                        if not math.isfinite(float(row["percentile"])) or not 0 <= float(row["percentile"]) <= 1:
                            raise ValueError("Invalid original percentile")
                        actual_scored += 1
                        preserved += 1
                    flags = [f for f in row["data_quality_flag"].split("|") if f and f not in ("ok", "feature_missing")]
                    additions = []
                    if missing[u]:
                        additions.append("feature_missing")
                    if not complete[u]:
                        additions.append("incomplete_hour_window")
                    if panel.industry[t, u] == 0:
                        additions.append("industry_unknown")
                    if boundary[t]:
                        additions.append("frozen_boundary_embargo")
                    elif below_min:
                        additions.append("below_min_cross_section")
                    elif missing_original:
                        additions.append("original_score_section_missing")
                    flags = list(dict.fromkeys(flags + additions))
                    row["data_quality_flag"] = "|".join(flags) or "ok"
                    writer.writerow(row)
                    rows_written += 1
                    if not scored:
                        for reason in flags:
                            reasons[reason] = reasons.get(reason, 0) + 1
                if present and actual_scored != expected_scored:
                    raise ValueError("Original valid section lacks required eligible scores")
                sections.append({"signal_time_ns": signal, "date": str(np.datetime64(int(panel.days[t]), "D")),
                    "slot": {10: 0, 11: 1, 14: 2, 15: 3}[int(((signal + CHINA_OFFSET) // 3_600_000_000_000) % 24)],
                    "pool_count": len(pool), "window_eligible_count": eligible_count, "scored_count": actual_scored,
                    "raw_section_present": present, "boundary_embargo": bool(boundary[t]),
                    "below_min_cross_section": below_min, "unresolved_original_score_section": missing_original,
                    "window_feature_missing_count": int((missing & panel.members[t]).sum()), "unscored_reason_counts": reasons})
            if pending is not None:
                raise ValueError("Raw CSV has trailing out-of-scope sections")
        if file_hash(raw_csv) != raw_hash:
            raise ValueError("Raw score bytes changed during export")
        # Atomic exclusive publication: a concurrent/existing artifact is never
        # replaced, including on a failed audit or repeat invocation.
        os.link(temporary, output_csv)
    finally:
        if temporary_created and temporary.exists():
            temporary.unlink()
    pool_hours = sum(s["pool_count"] for s in sections)
    scored_hours = sum(s["scored_count"] for s in sections)
    def coverage(values):
        pool = sum(s["pool_count"] for s in values)
        scored = sum(s["scored_count"] for s in values)
        return {"cross_sections": len(values), "pool_stock_hours": pool, "scored_stock_hours": scored,
            "coverage": scored / pool if pool else None,
            "below_min_sections": sum(s["below_min_cross_section"] for s in values),
            "boundary_embargo_sections": sum(s["boundary_embargo"] for s in values),
            "unresolved_original_score_sections": sum(s["unresolved_original_score_section"] for s in values)}
    breakdown = {"overall": coverage(sections), "year": {}, "quarter": {}, "slot": {}}
    for kind in ("year", "quarter", "slot"):
        grouped = {}
        for s in sections:
            key = s["date"][:4] if kind == "year" else (s["date"][:4] + "Q" + str((int(s["date"][5:7]) - 1) // 3 + 1)
                if kind == "quarter" else str(s["slot"]))
            grouped.setdefault(key, []).append(s)
        breakdown[kind] = {key: coverage(values) for key, values in grouped.items()}
    return {"status": "incomplete" if unresolved else "complete_export", "research_evidence": not panel.fixture,
        "full_PLAN_complete": False, "scope": "all historical calendar rows in split, with frozen boundary exclusions explicit",
        "raw_csv": str(raw_csv.resolve()), "raw_sha256": raw_hash, "raw_preserved": True,
        "corrected_csv": str(output_csv.resolve()), "corrected_sha256": file_hash(output_csv),
        "model_version": model_version, "dataset_fingerprint": panel.fingerprint,
        "scores_percentiles_versions_changed": False, "inference_performed": False,
        "labels_or_IC_metrics_read": False, "csv_rows": rows_written, "original_scores_preserved": preserved,
        "pool_stock_hours": pool_hours, "scored_stock_hours": scored_hours,
        "coverage_all_split_calendar_members": scored_hours / pool_hours if pool_hours else None,
        "unresolved_original_score_sections": unresolved, "coverage_breakdown": breakdown, "sections": sections}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split", choices=list(SPLITS) + ["all"], default="all")
    args = parser.parse_args()
    frozen_path = args.run / "freeze.json"
    frozen = json.loads(frozen_path.read_text())
    identity = hashlib.sha256(json.dumps({"data": frozen["dataset_fingerprint"], "config": frozen["config"],
        "selection": frozen["selection"], "hashes": frozen["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
    if frozen["freeze_id"] != identity or frozen.get("no_test_driven_tuning") is not True:
        raise ValueError("Original model/config/selection freeze identity failed")
    if frozen["config"]["seeds"] != [11, 22, 33, 44, 55]:
        raise ValueError("All five originally frozen seeds are required")
    panel = ScorePanel(args.dataset, frozen["config"], frozen["dataset_fingerprint"])
    reports = {}
    for split in SPLITS if args.split == "all" else [args.split]:
        for name in frozen["evaluation_models"]:
            raw = args.run / "frozen_evaluation" / split / (name + "_scores.csv")
            output = args.output_dir / split / (name + "_scores.csv")
            report = export_scores(panel, split, raw, output, frozen["freeze_id"] + ":" + name)
            exclusive_json(output.with_suffix(".receipt.json"), report)
            reports[split + "/" + name] = {k: v for k, v in report.items() if k != "sections"}
    manifest = {"freeze_id": frozen["freeze_id"], "freeze_sha256": file_hash(frozen_path),
        "export_code_sha256": file_hash(__file__), "verified_input_hashes": panel.verified_input_hashes,
        "status": "incomplete" if any(r["status"] == "incomplete" for r in reports.values()) else "complete_export",
        "original_models_config_selection_and_metrics_unchanged": True, "full_PLAN_complete": False,
        "reports": reports}
    exclusive_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps({"output_dir": str(args.output_dir), "status": manifest["status"], "reports": len(reports)}))


if __name__ == "__main__":
    main()

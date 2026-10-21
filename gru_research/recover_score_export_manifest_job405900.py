"""Recover only job405900's unpublished score-export manifest.

Default invocation prints a contract without reading any files. Explicit execution
requires the reviewed helper identity and the actual native 36-CSV transfer proof.
CSV bytes are hashed, never parsed; no original exporter, model, or panel is run.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid


DELIVERY_SHA = "d1377de248a4a160f74dd2295d811c08fad7887eea33d3eeb675eeee967a85e5"
REPORT_TRANSFER_SHA = "e694e13b1371caa431d2ad9a34a6bc011ffadaf8a8228f7d78ec221130a85378"
PARTS_MANIFEST_SHA = "3b2f4f78375a80abede7d6537571d7db03f8ca715c4e413f7c1b30175f50b7c4"
SCORE_ARCHIVE_SHA = "4328a8a548e6fba86fbd311a87e7f038a2f0d7a5482c441ad2d50d54d4acace1"
REPORT_ARCHIVE_SHA = "434d4efd448cac83b0d0ff4b8807ff0a793cd11250156ff519c1144c54afa36b"
EXPORT_SOURCE_SHA = "8ad1c98b87aa1f39e6a474b101483a633bea0d6d8aa4c9858565d7c0fe2bee18"
EVALUATE_SOURCE_SHA = "b6409f7b918bbd787d5212e510c4ba8a35f6a6bb2cfab94325d66718fd2a72ac"
EXPORT_LOG_SHA = "eda65af670c04feb16ec8bafab818327386920db760436d6097fcdd4e189e166"
FREEZE_SHA = "363eb9cb6ccdb613e8f5907c8e7331c550998df9acb708665d64e1e14233484d"
META_SHA = "2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849"
JOINT40_SHA = "6efcb51ab30f2e5851f4f40fc9ed930e2103f2fc1f63c5e4064ba107a1ed6b05"
FINGERPRINT = "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4"
FREEZE_ID = "d142cc97070bc81034a6"
REMOTE_RUN = "/home/users/s1155284034/gru_hourly_20261001/outputs/formal"
MODELS = ("fixed_label_1", "fixed_label_2", "fixed_label_3", "fixed_label_4",
          "blo_selected", "selected_full", "pv", "pv_factors", "pv_fund")
SPLITS = ("test_2025", "recent_2026")
SEEDS = (11, 22, 33, 44, 55)
INPUT_NAMES = ("X.npy", "hour_complete.npy", "members.npy", "industry.npy",
               "times.npy", "earliest_execution_times.npy", "symbols.json")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REPORT_KEYS = frozenset(("status", "research_evidence", "full_PLAN_complete", "scope",
    "raw_csv", "raw_sha256", "raw_preserved", "corrected_csv", "corrected_sha256",
    "model_version", "dataset_fingerprint", "scores_percentiles_versions_changed",
    "inference_performed", "labels_or_IC_metrics_read", "csv_rows", "original_scores_preserved",
    "pool_stock_hours", "scored_stock_hours", "coverage_all_split_calendar_members",
    "unresolved_original_score_sections", "coverage_breakdown", "sections"))
SECTION_KEYS = frozenset(("signal_time_ns", "date", "slot", "pool_count", "window_eligible_count",
    "scored_count", "raw_section_present", "boundary_embargo", "below_min_cross_section",
    "unresolved_original_score_section", "window_feature_missing_count", "unscored_reason_counts"))
REPORT_SHA_BY_KEY = {
    "test_2025/blo_selected": "31b901e00224cf00a0a57c64f3a92689146647a11000513f72bc29d718a4bea6",
    "test_2025/fixed_label_1": "9cfeb54a7cd19ed933d18af04728e811fe65b7d772d3220c222b00ccf5b9f433",
    "test_2025/fixed_label_2": "8bf71cd21f2badd53f02cee9890749ec442a4070f7d70021107b2d6261cd3019",
    "test_2025/fixed_label_3": "2f3aab242738e899f8e762e911a114c9bdd7f0ea8f7fad6822585faa17074de2",
    "test_2025/fixed_label_4": "8d5e9ec4cf8bb31ca8a5d870bcddd59ed5fb0743cf323416861bf71675d437b2",
    "test_2025/pv_factors": "87f4f0692f9d4a2e42ff4a7631b26200d4b1361d222210fdb4169d2b9631a55a",
    "test_2025/pv_fund": "2bc16464d4068eb2b8e2dedf65cc3ec1da6e496b25f830bf65246e43a44c8327",
    "test_2025/pv": "fbcc14ac95d12e6bfec1b503b1d8f10d198973b2ae43c0ae1cb9152bc9340ab4",
    "test_2025/selected_full": "fbd75de53ecc3d65a0c225ecf6b72be064b9078cb5e818f30d702ea5db667fe8",
    "recent_2026/blo_selected": "fd63262874e9ca1c256601d7691366229fb42d05b1861506d9714e0678831a5b",
    "recent_2026/fixed_label_1": "4dea11540d563ced3150f188e6d59efa60d41da8315ba3d8a926e71cefd6a811",
    "recent_2026/fixed_label_2": "35d9ca03161a6bd36f36c618f306f082388884d429245becd6d6c0bf99bda789",
    "recent_2026/fixed_label_3": "5697efcc296fe277de2a144f0858230926695a2dbcd37910e76e830922c694fe",
    "recent_2026/fixed_label_4": "c3a7a87ae1c88e52b5dbda4de6b0b777bf7f1efb6a8d79a9289a2ad14198804d",
    "recent_2026/pv_factors": "6bb080fce28eea4bd42c68c689a184e3d1899d77461204aab12437420e42e7d4",
    "recent_2026/pv_fund": "88164c8b40e7b6bcb7e8594774ed9b3097530bf4e1e1f2b649093dd1dd810561",
    "recent_2026/pv": "1be115d8c0932e95809d57f735b1f732b07e8e3844c54d98b35ef0024729fc12",
    "recent_2026/selected_full": "224a8264af8b3840c7a6cb75ee83082bae6fca45334ab8f43662ec8acea3403e",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    require(math.isfinite(result), "Non-finite JSON number")
    return result


def strict_json(raw):
    def bad_constant(value):
        raise ValueError("Non-finite JSON constant: " + value)
    return json.loads(raw, object_pairs_hook=_pairs, parse_float=_finite_float,
                      parse_constant=bad_constant)


def integer(value, name, minimum=0):
    require(type(value) is int and value >= minimum, "Invalid integer: " + name)
    return value


def sha_value(value):
    require(type(value) is str and SHA_RE.fullmatch(value) is not None, "Invalid SHA256")
    return value


def _regular(path):
    path = Path(path)
    require(path.is_absolute(), "Explicit absolute paths required")
    for candidate in (path, *path.parents):
        require(not candidate.is_symlink(), "Symlink evidence paths are forbidden")
    require(stat.S_ISREG(path.stat().st_mode), "Missing regular evidence file: " + str(path))
    return path


def _stamp(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def file_identity(path):
    path = _regular(path)
    initial = _stamp(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    require(_stamp(path) == initial, "Evidence changed while hashing: " + str(path))
    return {"path": str(path), "bytes": initial[2], "sha256": digest.hexdigest()}, initial


def descriptor(value, path, expected_sha, expected_bytes=None):
    require(type(value) is dict and set(value) == {"path", "bytes", "sha256"}, "Invalid artifact descriptor")
    require(value["path"] == str(path) and value["sha256"] == sha_value(expected_sha), "Artifact identity mismatch")
    integer(value["bytes"], "artifact bytes", 1)
    if expected_bytes is not None:
        require(value["bytes"] == expected_bytes, "Artifact byte count mismatch")


def coverage(sections):
    pool = sum(s["pool_count"] for s in sections)
    scored = sum(s["scored_count"] for s in sections)
    return {"cross_sections": len(sections), "pool_stock_hours": pool, "scored_stock_hours": scored,
        "coverage": scored / pool if pool else None,
        "below_min_sections": sum(s["below_min_cross_section"] for s in sections),
        "boundary_embargo_sections": sum(s["boundary_embargo"] for s in sections),
        "unresolved_original_score_sections": sum(s["unresolved_original_score_section"] for s in sections)}


def check_report(report, split, model):
    require(type(report) is dict and set(report) == REPORT_KEYS, "Original report schema mismatch")
    require(report["status"] == "complete_export", "Incomplete original export")
    for key in ("research_evidence", "raw_preserved"):
        require(report[key] is True, "Original real-data preservation unproved")
    for key in ("full_PLAN_complete", "scores_percentiles_versions_changed", "inference_performed", "labels_or_IC_metrics_read"):
        require(report[key] is False, "Unsupported original report claim: " + key)
    require(report["unresolved_original_score_sections"] == [] and type(report["unresolved_original_score_sections"]) is list,
            "Unresolved original score sections")
    require(report["dataset_fingerprint"] == FINGERPRINT and report["model_version"] == FREEZE_ID + ":" + model,
            "Original dataset/model version mismatch")
    require(report["scope"] == "all historical calendar rows in split, with frozen boundary exclusions explicit", "Original scope mismatch")
    for kind, folder in (("raw", "frozen_evaluation"), ("corrected", "corrected_score_exports")):
        require(report[kind + "_csv"] == REMOTE_RUN + "/" + folder + "/" + split + "/" + model + "_scores.csv",
                "Unsafe or changed original remote CSV path")
        sha_value(report[kind + "_sha256"])
    sections = report["sections"]
    require(type(sections) is list and sections, "Actual section receipts missing")
    groups = {"year": defaultdict(list), "quarter": defaultdict(list), "slot": defaultdict(list)}
    times, daily_slots = [], defaultdict(list)
    for s in sections:
        require(type(s) is dict and set(s) == SECTION_KEYS, "Original section schema mismatch")
        for key in ("signal_time_ns", "slot", "pool_count", "window_eligible_count", "scored_count", "window_feature_missing_count"):
            integer(s[key], key)
        for key in ("raw_section_present", "boundary_embargo", "below_min_cross_section", "unresolved_original_score_section"):
            require(type(s[key]) is bool, "Section flag is not Boolean")
        require(s["unresolved_original_score_section"] is False, "Unresolved section flag")
        require(0 <= s["scored_count"] <= s["window_eligible_count"] <= s["pool_count"] and
                s["window_feature_missing_count"] <= s["pool_count"], "Invalid section counts")
        require(s["below_min_cross_section"] == (s["window_eligible_count"] < 100), "Below-minimum flag mismatch")
        expected = 0 if s["boundary_embargo"] or s["below_min_cross_section"] else s["window_eligible_count"]
        require(s["scored_count"] == expected and (s["raw_section_present"] or expected == 0), "Original eligible scores missing")
        require(type(s["date"]) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["date"]) is not None, "Invalid source date")
        day = datetime.strptime(s["date"], "%Y-%m-%d")
        require((split == "test_2025" and day.year == 2025) or
                (split == "recent_2026" and "2026-01-01" <= s["date"] <= "2026-09-23"), "Section outside frozen split")
        require(s["slot"] in range(4), "Invalid source hour slot")
        hour, minute = ((10, 30), (11, 30), (14, 0), (15, 0))[s["slot"]]
        expected_ns = int(day.replace(hour=hour, minute=minute, tzinfo=timezone.utc).timestamp()) * 10**9 - 8 * 3600 * 10**9
        require(s["signal_time_ns"] == expected_ns, "Section date/clock mismatch")
        times.append(s["signal_time_ns"]); daily_slots[s["date"]].append(s["slot"])
        require(type(s["unscored_reason_counts"]) is dict, "Invalid exclusion reasons")
        for reason, count in s["unscored_reason_counts"].items():
            require(type(reason) is str and reason, "Invalid exclusion reason")
            integer(count, "reason count")
            require(count <= s["pool_count"] - s["scored_count"], "Impossible exclusion reason count")
        groups["year"][s["date"][:4]].append(s)
        groups["quarter"][s["date"][:4] + "Q" + str((day.month - 1) // 3 + 1)].append(s)
        groups["slot"][str(s["slot"])].append(s)
    require(times == sorted(set(times)), "Duplicate or unordered original sections")
    require(all(slots == [0, 1, 2, 3] for slots in daily_slots.values()), "Missing original daily hour receipts")
    breakdown = {"overall": coverage(sections), **{k: {name: coverage(rows) for name, rows in v.items()} for k, v in groups.items()}}
    require(report["coverage_breakdown"] == breakdown, "Original coverage receipt is internally inconsistent")
    overall = breakdown["overall"]
    for key, expected in (("csv_rows", overall["pool_stock_hours"]), ("pool_stock_hours", overall["pool_stock_hours"]),
                          ("scored_stock_hours", overall["scored_stock_hours"]), ("original_scores_preserved", overall["scored_stock_hours"])):
        integer(report[key], key)
        require(report[key] == expected, "Original aggregate count mismatch")
    require(report["coverage_all_split_calendar_members"] == overall["coverage"], "Original coverage ratio mismatch")
    return {key: value for key, value in report.items() if key != "sections"}


def check_freeze(frozen, joint, meta):
    require(frozen["freeze_id"] == FREEZE_ID and frozen["dataset_fingerprint"] == FINGERPRINT and
            joint["metadata_fingerprint"] == FINGERPRINT, "Frozen identity mismatch")
    require(frozen["no_test_driven_tuning"] is True and frozen["config"]["seeds"] == list(SEEDS), "Frozen experiment changed")
    require((frozen["config"]["sequence_length"], frozen["config"]["min_stocks"], frozen["config"]["embargo_trading_days"]) == (80, 100, 2), "Frozen eligibility changed")
    digest = hashlib.sha256(json.dumps({"data": frozen["dataset_fingerprint"], "config": frozen["config"],
        "selection": frozen["selection"], "hashes": frozen["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
    require(digest == FREEZE_ID, "Freeze content identity failed")
    require(meta["production_ready"] is True and meta["causality_audit"]["passed"] is True and
            meta["raw_market_data_is_synthetic"] is False, "Non-real or unaudited original metadata")
    require(hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == FINGERPRINT,
            "Original panel metadata fingerprint mismatch")
    require(type(meta["files_sha256"]) is dict and len(meta["files_sha256"]) == 26, "Original 26 file metadata missing")
    for value in meta["files_sha256"].values():
        sha_value(value)
    require(joint["all40_final_models_complete"] is True and joint["accepted_final_model_count"] == 40 and
            joint["accepted_final_models_by_stage"] == {"fixed": 20, "blo_final": 5, "ablation": 15}, "Accepted 40-model evidence missing")
    rows = joint["models"]
    require(type(rows) is list and len(rows) == 40, "Exact accepted checkpoint roster required")
    expected = {}
    for row in rows:
        path = REMOTE_RUN + "/" + row["model"] + "/best.pt"
        require(path not in expected, "Duplicate accepted model")
        expected[path] = sha_value(row["best_sha256"])
    require(frozen["checkpoint_hashes"] == expected and len(expected) == 40, "Frozen checkpoint IDs differ from accepted40")
    model_paths = {"fixed_label_" + str(label): [REMOTE_RUN + "/fixed/label_" + str(label) + "/seed_" + str(seed) + "/best.pt" for seed in SEEDS] for label in (1, 2, 3, 4)}
    model_paths["blo_selected"] = [REMOTE_RUN + "/blo_final/label_2/seed_" + str(seed) + "/best.pt" for seed in SEEDS]
    model_paths["selected_full"] = model_paths["fixed_label_3"]
    for variant in ("pv", "pv_factors", "pv_fund"):
        model_paths[variant] = [REMOTE_RUN + "/ablations/" + variant + "/seed_" + str(seed) + "/best.pt" for seed in SEEDS]
    require(frozen["evaluation_models"] == model_paths and set(model_paths) == set(MODELS), "Frozen nine ensembles differ")
    require(frozen["selection"]["selected_method"] == "fixed_3" and frozen["selection"]["selected_label"] == 3 and frozen["selection"]["blo_label"] == 2,
            "Original label selection changed")
    return {name: meta["files_sha256"][name] for name in INPUT_NAMES}


def contract():
    return {"operation": "job405900_manifest_only_recovery", "default": "contract_only_no_file_access",
        "execute_requires": ["explicit delivery root, original sources, dataset metadata, accepted joint40",
            "reviewed helper SHA256", "native36CSV transfer proof and approved exact proof SHA256", "new exclusive recovery receipt path"],
        "original_export_exit": 1, "original_manifest_published": False,
        "CSV_handling": "hash exact bytes only; no score parsing, inference, export, labels, or IC calculation",
        "seven_panel_hashes": "inherited original ScorePanel verification; no new array read/hash",
        "publication": "all gates before exclusive manifest and provenance receipt; no overwrite",
        "original_delivery_flags_modified": False, "full_PLAN_complete": False}


def recover(args):
    started = datetime.now(timezone.utc).isoformat()
    for key in ("delivery_root", "dataset_meta", "accepted_joint", "original_export_source",
                "original_evaluation_source", "scores_transfer_proof", "receipt"):
        require(Path(getattr(args, key)).is_absolute(), "Explicit absolute path required: " + key)
    base = Path(args.delivery_root).absolute()
    require(base.is_dir() and not base.is_symlink(), "Explicit existing delivery root required")
    run = base / "formal"; destination = run / "corrected_score_exports/manifest.json"
    receipt_path = Path(args.receipt).absolute()
    require(not destination.exists() and not destination.is_symlink(), "Original manifest already exists")
    require(not receipt_path.exists() and not receipt_path.is_symlink() and receipt_path.parent.is_dir(), "New receipt in an existing explicit directory required")
    require(all(not parent.is_symlink() for parent in receipt_path.parents), "Symlink publication directory forbidden")
    inputs = {
        "helper": (Path(__file__).absolute(), args.approved_recovery_source_sha256),
        "delivery": (base / "original_transfer/gru_delivery_job405900.json", DELIVERY_SHA),
        "report_transfer": (base / "original_transfer/report_archive_actual_transfer_receipt_v1.json", REPORT_TRANSFER_SHA),
        "parts_manifest": (base / "original_transfer/original_delivery_parts_manifest.json", PARTS_MANIFEST_SHA),
        "freeze": (run / "freeze.json", FREEZE_SHA),
        "export_log": (run / "corrected_score_exports/export_command.log", EXPORT_LOG_SHA),
        "export_source": (Path(args.original_export_source).absolute(), EXPORT_SOURCE_SHA),
        "evaluate_source": (Path(args.original_evaluation_source).absolute(), EVALUATE_SOURCE_SHA),
        "dataset_meta": (Path(args.dataset_meta).absolute(), META_SHA),
        "joint40": (Path(args.accepted_joint).absolute(), JOINT40_SHA),
        "score_transfer": (Path(args.scores_transfer_proof).absolute(), args.scores_transfer_proof_sha256)}
    tracked, documents = {}, {}
    for key, (path, digest) in inputs.items():
        ident, _ = file_identity(path)
        require(ident["sha256"] == sha_value(digest), "Pinned source/proof changed: " + key)
        require(path != receipt_path and path != destination, "Publication aliases evidence")
        tracked[str(path)] = ident
        if key not in ("helper", "export_source", "evaluate_source", "export_log"):
            documents[key] = strict_json(path.read_bytes())
    delivery, transfer, score_transfer = (documents[k] for k in ("delivery", "report_transfer", "score_transfer"))
    require(delivery["training_job_id"] == 405900 and type(delivery["training_exit_code"]) is int and delivery["training_exit_code"] == 0,
            "Original successful training identity missing")
    require(delivery["training_and_prediction_complete"] is False and delivery["full_PLAN_complete"] is False and
            delivery["no_training_selection_checkpoint_or_score_changes"] is True, "Original failed delivery state changed")
    correction = delivery["independent_score_metadata_and_coverage_correction"]
    require(correction["status"] == "failed" and correction["reason"] == "readonly_export_failed" and
            type(correction["exit_code"]) is int and correction["exit_code"] == 1 and correction["log_sha256"] == EXPORT_LOG_SHA and
            correction["original_scores_and_models_unchanged"] is True and
            correction["log"] == REMOTE_RUN + "/corrected_score_exports/export_command.log", "Original export failure evidence changed")
    log = inputs["export_log"][0].read_text()
    require("file_hash(__file__)" in log and "AttributeError: 'str' object has no attribute 'open'" in log and log.rstrip().endswith("EXPORT_EXIT=1"),
            "Exact original manifest-construction failure unproved")
    require(transfer["status"] == "PASS_ACTUAL_ORIGINAL_REPORT_ARCHIVE_REASSEMBLED_SAFE_EXTRACTED_AND40_ACCEPTED_CHECKPOINT_HASHES_MATCH" and
            transfer["accepted40checkpoint_byte_hashes_verified"] is True and transfer["originals_overwritten"] is False and
            transfer["original_export_failed_status_preserved"] is True and transfer["full_PLAN_complete"] is False,
            "Native original report/checkpoint transfer proof missing")
    descriptor(transfer["actual_delivery_receipt"], inputs["delivery"][0], DELIVERY_SHA, 33866)
    descriptor(transfer["actual_freeze"], inputs["freeze"][0], FREEZE_SHA, 18929)
    descriptor(transfer["original_report_archive"], Path(transfer["original_report_archive"]["path"]), REPORT_ARCHIVE_SHA, 99041729)
    require(score_transfer["status"] == "PASS_ACTUAL_ORIGINAL_SCORE_ARCHIVE_REASSEMBLED_SAFE_EXTRACTED36" and
            score_transfer["extraction_root"] == str(base), "Actual native36 transfer proof missing")
    for key, expected in (("original_export_failed_status_preserved", True), ("score_strings_parsed", False),
                          ("raw_score_export_or_inference_performed", False), ("full_PLAN_complete", False)):
        require(score_transfer[key] is expected, "Unsupported transfer claim: " + key)
    descriptor(score_transfer["actual_delivery_receipt"], inputs["delivery"][0], DELIVERY_SHA, 33866)
    descriptor(score_transfer["original_binary_parts_manifest"], inputs["parts_manifest"][0], PARTS_MANIFEST_SHA, 7986)
    descriptor(score_transfer["original_report_transfer"], inputs["report_transfer"][0], REPORT_TRANSFER_SHA, 3014)
    archive = score_transfer["original_score_archive"]
    descriptor(archive, Path(archive["path"]), SCORE_ARCHIVE_SHA, 356445761)
    require(Path(archive["path"]).name == "gru_scores_job405900_20261002T095500Z.tar.gz", "Different native score archive")
    safe = score_transfer["safe_extraction"]
    require(safe["completed"] is True and type(safe["regular_file_count"]) is int and safe["regular_file_count"] == 36 and
            safe["unsafe_or_duplicate_paths"] is False and safe["overwrite_performed"] is False, "Unsafe or incomplete native extraction")
    verified_inputs = check_freeze(documents["freeze"], documents["joint40"], documents["dataset_meta"])
    descriptors = score_transfer["actual_csv_files"]
    require(type(descriptors) is list and len(descriptors) == 36, "Exactly36 native CSV descriptors required")
    descriptor_map = {}
    for d in descriptors:
        require(type(d) is dict and set(d) == {"path", "archive_relative_path", "bytes", "sha256"}, "Invalid CSV descriptor")
        relative = d["archive_relative_path"]
        require(type(relative) is str and relative not in descriptor_map, "Duplicate native CSV path")
        descriptor_map[relative] = d
    expected_relative = {"formal/" + folder + "/" + split + "/" + model + "_scores.csv"
        for folder in ("frozen_evaluation", "corrected_score_exports") for split in SPLITS for model in MODELS}
    require(set(descriptor_map) == expected_relative, "Missing/extra/untrusted native CSV paths")
    reports, report_identities, csv_identities, csv_stamps = {}, {}, {}, {}
    section_reference = {}
    for split in SPLITS:
        for model in documents["freeze"]["evaluation_models"]:
            key = split + "/" + model
            path = run / "corrected_score_exports" / split / (model + "_scores.receipt.json")
            ident, _ = file_identity(path)
            require(ident["sha256"] == REPORT_SHA_BY_KEY[key], "Original archived report bytes changed: " + key)
            tracked[str(path)] = ident; report_identities[key] = ident
            report = strict_json(path.read_bytes()); reports[key] = check_report(report, split, model)
            signature = [(s["signal_time_ns"], s["pool_count"], s["window_eligible_count"], s["scored_count"]) for s in report["sections"]]
            if split in section_reference:
                require(section_reference[split] == signature, "Different actual population across original18 exports")
            else:
                section_reference[split] = signature
            for kind, folder in (("raw", "frozen_evaluation"), ("corrected", "corrected_score_exports")):
                relative = "formal/" + folder + "/" + split + "/" + model + "_scores.csv"
                local = base / relative; d = descriptor_map[relative]
                require(d["path"] == str(local), "Native local CSV relocation mismatch")
                sha_value(d["sha256"]); integer(d["bytes"], "CSV bytes", 1)
                ident, stamp = file_identity(local)
                require(ident["sha256"] == d["sha256"] == report[kind + "_sha256"] and ident["bytes"] == d["bytes"],
                        "Actual CSV bytes do not match original receipt/native transfer: " + relative)
                csv_identities[relative] = ident; csv_stamps[str(local)] = stamp
    require(sum(d["bytes"] for d in csv_identities.values()) == safe["total_uncompressed_bytes"], "Native total CSV byte count mismatch")
    require(type(safe["total_uncompressed_bytes"]) is int, "Native total byte count is not integer")
    for path, before in tracked.items():
        after, _ = file_identity(Path(path))
        require(after == before, "Original source/report/freeze/log changed during recovery")
    for path, stamp in csv_stamps.items():
        require(_stamp(_regular(path)) == stamp, "Native CSV changed after byte verification")
    require(not destination.exists() and not destination.is_symlink() and not receipt_path.exists() and not receipt_path.is_symlink(),
            "Concurrent/existing publication must not be overwritten")
    provenance = {"kind": "manifest_only_recovery", "job": 405900, "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
        "original_export_exit_code": 1, "original_manifest_was_published": False,
        "original_delivery_training_and_prediction_complete": False, "original_delivery_full_PLAN_complete": False,
        "original_failure": "Manifest construction called evaluate.file_hash(__file__) with str; Path.open required.",
        "original_export_source_sha256": EXPORT_SOURCE_SHA, "original_evaluate_source_sha256": EVALUATE_SOURCE_SHA,
        "original_export_log_sha256": EXPORT_LOG_SHA, "recovery_program": tracked[str(inputs["helper"][0])],
        "actual_score_transfer_proof": tracked[str(inputs["score_transfer"][0])], "original_delivery": tracked[str(inputs["delivery"][0])],
        "seven_input_hash_evidence": {"level": "inherited original execution, source-bound control-flow inference",
            "explanation": "Original main completed ScorePanel.__init__ actual seven-file hash comparisons before18 exports. All18 successful exclusive receipts precede the exact failed manifest expression. Metadata fingerprint/26 declarations are byte-bound here; original seven verified values are inherited, not a new4.9GB array audit.",
            "current_array_files_read_or_rehashed": False, "original_individual_hash_map_separately_published": False},
        "current_36_CSV_byte_hashes_verified": True, "CSV_score_strings_parsed": False,
        "inference_or_score_reexport_or_metric_calculation": False, "originals_modified": False,
        "recovery_receipt_path": str(receipt_path), "full_PLAN_complete": False}
    manifest = {"freeze_id": FREEZE_ID, "freeze_sha256": FREEZE_SHA, "export_code_sha256": EXPORT_SOURCE_SHA,
        "verified_input_hashes": verified_inputs, "status": "complete_export",
        "original_models_config_selection_and_metrics_unchanged": True, "full_PLAN_complete": False,
        "reports": reports, "manifest_only_recovery": provenance}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    recovered_identity = {"path": str(destination), "bytes": len(manifest_bytes), "sha256": hashlib.sha256(manifest_bytes).hexdigest()}
    receipt = {"status": "MANIFEST_ONLY_RECOVERED_ORIGINAL_EXPORT_EXIT1_PRESERVED", "started_utc": started,
        "manifest": recovered_identity, "provenance": provenance, "original_small_inputs_actual_before_after": tracked,
        "original18_report_identities": report_identities, "actual36_CSV_identities": csv_identities,
        "accepted40_checkpoint_ids": documents["freeze"]["checkpoint_hashes"],
        "checkpoint_byte_hash_scope": "Inherited actual native report-transfer40 hashes, exact IDs compared against accepted joint40; no checkpoint load/current inference.",
        "original26_meta_declarations": documents["dataset_meta"]["files_sha256"], "inherited_seven_verified_input_hashes": verified_inputs,
        "all_checks_passed_before_publication": True, "original_small_files_bytes_unchanged": True,
        "current_CSV_bytes_hashed_not_parsed": True, "original_delivery_flags_changed": False, "full_PLAN_complete": False}
    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    publish_pair(destination, manifest_bytes, receipt_path, receipt_bytes)
    return {"status": receipt["status"], "manifest": recovered_identity, "recovery_receipt": file_identity(receipt_path)[0],
            "original_export_exit_code": 1, "full_PLAN_complete": False}


def publish_pair(manifest_path, manifest_bytes, receipt_path, receipt_bytes):
    """Stage both files before exclusive hard links; never replace a final file.

    The two links are not a filesystem transaction. If the second publication
    fails, the manifest remains with inline provenance and the exception is
    reported; do not claim receipt publication or retry/overwrite the manifest.
    """
    temporary = []
    try:
        for final, payload in ((manifest_path, manifest_bytes), (receipt_path, receipt_bytes)):
            tmp = final.with_name("." + final.name + ".recovery-" + uuid.uuid4().hex + ".partial")
            with tmp.open("xb") as stream:
                temporary.append(tmp)
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary[0], manifest_path)
        os.link(temporary[1], receipt_path)
    finally:
        for path in temporary:
            if path.exists():
                path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    for option in ("delivery-root", "dataset-meta", "accepted-joint", "original-export-source", "original-evaluation-source",
                   "scores-transfer-proof", "scores-transfer-proof-sha256", "approved-recovery-source-sha256", "receipt"):
        parser.add_argument("--" + option)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(contract(), ensure_ascii=False, indent=2)); return 0
    require(all(value is not None for key, value in vars(args).items() if key != "execute"), "Explicit execution requires every contract argument")
    print(json.dumps(recover(args), ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

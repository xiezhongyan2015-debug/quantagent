"""One fixed issuer source-feature join to an inherited frozen input and scores.

Prepared by default. Explicit execution requires both actual upstream receipt
SHA pins and this reviewed source SHA. No Torch, old pipeline, training, or new
forward is imported or called. This is conditional on saved historical500
statistics; it does not reconstruct those500 issuers' upstream sources.
V2 adapts only the new slow98 upstream and this bridge's own namespace/version.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import zipfile

ROOT = Path(__file__).absolute().parents[2]
GRU = ROOT / "gru_research"
PREP = GRU / "trading_data/fixed_000009_full80_source_bridge_preparation_20261002"
OUT = GRU / "trading_data/fixed_000009_compact_source_input_bridge_v2_20261002"
H24 = GRU / "trading_data/fixed_000009_original_h24_reconstruction_v1_20261002/attempt1"
SLOW = GRU / "trading_data/slow98_fixed_window_raw_reconstruction_v2_20261002/attempt1"
WINDOW = GRU / "trading_data/window_normalization_audit_v2_20261002/attempt1"
FORWARD = GRU / "artifacts/top5_actual_forward_causality_20261002T113238Z"
MASK = GRU / "trading_data/fixed_000009_original_h24_preparation_v1_20261002/fixed_000009_existing_mask_witness_v1.json"
H24_STATUS = "PASS_FIXED000009_ORIGINAL_H24_80_EXACT_QUALIFIED"
SLOW_STATUS = "PASS_FIXED_ISSUER_SOURCE98x21_RAW_FP32_AND_FUTURE_PREFIX_QUERY_CONVENTION_ONLY"
PINS = {
    PREP / "preparation_contract_compact_v1.json": "ced37a16d7ffa329e58bd4cc580d36882aa79d919c91abd0a7152ffc78b41313",
    PREP / "preparation_contract_v1.json": "4c801a4d9ff248fe42715b4a27f75f86d986def078a9a0d9179eede1e6d53b25",
    WINDOW / "actual_window_normalization_receipt_v2.json": "b185a9c5cc70acaa56b3b3d1f8dccb6fd193408147d89dd7aef47ceb05ff90f6",
    FORWARD / "audit_output/actual_forward_causality_receipt_v1.json": "9ea51de4249289a8396e21c7f66059400ed275499e9f9e69476267c04152a803",
    FORWARD / "root_independent_actual_forward_acceptance_v1.json": "fcab6c2f744dec28818d8d0b27cabb21dd65580954905910699dda4725bdeb9b",
    MASK: "260f3430922d6b489a5798a8808c3c113ce6e5638a4e9ac3562fe236ff172dd4",
    GRU / "artifacts/dataset/meta.json": "2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849",
    GRU / "artifacts/dataset/feature_names.json": "7e185048a53239dc70c24694b80da59a719a00987199d66cb97ccef512e13009",
}
BODY_SHA = "3073e5dd18a480a611f0c3aa637ed181998bde01cde1000bbca5074831434202"
SMALL_CAP = 4 * 1024**2
TOTAL_READ_CAP = 16 * 1024**2


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def exact_stat(path):
    s = Path(path).lstat()
    require(stat.S_ISREG(s.st_mode), "input must be a regular file, not symlink: " + str(path))
    return {"bytes": s.st_size, "mtime_ns": s.st_mtime_ns, "device": s.st_dev, "inode": s.st_ino}


class Reads:
    def __init__(self):
        self.files = []
        self.npz_members = []
        self.slices = []
        self.file_bytes = 0

    def read(self, path, expected_sha, cap=SMALL_CAP, expected_bytes=None):
        path = Path(path)
        before = exact_stat(path)
        require(before["bytes"] <= cap, "small input exceeds explicit cap")
        require(self.file_bytes + before["bytes"] <= TOTAL_READ_CAP, "aggregate small file read budget exceeded")
        with path.open("rb") as handle:
            body = handle.read(cap + 1)
        self.file_bytes += len(body)
        require(len(body) <= cap and len(body) == before["bytes"], "small input changed or exceeded cap while reading")
        require(expected_bytes is None or len(body) == expected_bytes, "declared file bytes differ")
        require(digest(body) == expected_sha, "input SHA differs: " + str(path))
        require(exact_stat(path) == before, "small input STAT changed while reading")
        self.files.append({"path": str(path), "bytes_read": len(body), "sha256": expected_sha, "STAT": before})
        return body


def json_input(reads, path, sha):
    return json.loads(reads.read(path, sha))


def require_sha(value):
    require(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value), "explicit out-of-band SHA required")


def artifact(receipt, path, role=None):
    items = receipt.get("artifacts")
    require(isinstance(items, list), "upstream actual receipt needs closed artifact list")
    chosen = [v for v in items if isinstance(v, dict) and v.get("path") == str(path)]
    require(len(chosen) == 1, "required fixed artifact identity absent or duplicated: " + str(path))
    value = chosen[0]
    require(type(value.get("bytes")) is int and value["bytes"] > 0, "artifact byte count malformed")
    require_sha(value.get("sha256"))
    require(role is None or value.get("role") == role, "fixed H24 artifact role differs")
    return value


def load_npy(np, body, shape, dtype):
    array = np.load(io.BytesIO(body), allow_pickle=False)
    require(isinstance(array, np.ndarray) and tuple(array.shape) == tuple(shape), "NPY array shape differs")
    require(array.dtype.str == np.dtype(dtype).str and array.flags.c_contiguous, "NPY dtype or C order differs")
    return array


def source_array(np, reads, receipt, path, shape, dtype, role=None):
    identity = artifact(receipt, path, role)
    body = reads.read(path, identity["sha256"], expected_bytes=identity["bytes"])
    return load_npy(np, body, shape, dtype)


def npz_arrays(np, reads, path, identity, members):
    body = reads.read(path, identity["sha256"], expected_bytes=identity["bytes"])
    arrays = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        infos = archive.infolist()
        require(len(infos) == len(members) and {v.filename for v in infos} == set(members), "NPZ member set differs")
        for info in infos:
            shape, dtype = members[info.filename]
            payload = 1
            for n in shape:
                payload *= n
            cap = payload * np.dtype(dtype).itemsize + 4096
            require(0 < info.file_size <= cap and not info.is_dir(), "NPZ inflated member exceeds fixed array budget")
            with archive.open(info) as stream:
                decoded = stream.read(cap + 1)
            require(len(decoded) == info.file_size and len(decoded) <= cap, "NPZ member size changed")
            arrays[info.filename[:-4]] = load_npy(np, decoded, shape, dtype)
            reads.npz_members.append({"file": str(path), "member": info.filename,
                                      "container_compressed_member_bytes": info.compress_size,
                                      "inflated_member_bytes_including_header": len(decoded),
                                      "array_payload_bytes": payload * np.dtype(dtype).itemsize})
    return arrays


def write_json(path, data):
    body = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    require(len(body) <= SMALL_CAP, "new receipt exceeds metadata cap")
    with Path(path).open("xb") as stream:
        stream.write(body)
    return {"path": str(path), "bytes": len(body), "sha256": digest(body)}


def compare(np, actual, expected):
    require(actual.shape == expected.shape and actual.dtype == expected.dtype, "comparison shape/dtype differs")
    finite = np.isfinite(expected)
    mask_bad = np.isnan(actual) != np.isnan(expected)
    finite_bad = finite & (actual.view("<u4") != expected.view("<u4"))
    bad = mask_bad | finite_bad
    return {"shape": list(actual.shape), "NaN_mask_mismatches": int(mask_bad.sum()),
            "finite_reference_cells": int(finite.sum()), "finite_FP32_bit_mismatches": int(finite_bad.sum()),
            "nonfinite_not_nan": int((~np.isfinite(actual) & ~np.isnan(actual)).sum()),
            "first_mismatch_coordinates": np.argwhere(bad)[:20].tolist(),
            "complete_body_bytes_equal_including_NaN_payloads": actual.tobytes(order="C") == expected.tobytes(order="C")}


def pure_contract():
    return {"schema_version": 2, "status": "PREPARED_ONLY_UPSTREAM_ACTUAL_RECEIPTS_REQUIRED",
            "fixed_symbol": "000009.SZ", "union_index": 2, "batch_index": 0,
            "time_indices_inclusive": [6181, 6260], "raw_join_shape": [80, 122],
            "h24_actual_receipt": str(H24 / "actual_H24_original_source_reconstruction_receipt_v1.json"),
            "slow_actual_receipt": str(SLOW / "actual_source_reconstruction_receipt_v1.json"),
            "source_input_contract": str(PREP / "preparation_contract_compact_v1.json"),
            "source_input_contract_sha256": PINS[PREP / "preparation_contract_compact_v1.json"],
            "requires": ["--execute", "--source-sha", "--h24-receipt-sha", "--slow-receipt-sha"],
            "default_body_reads": 0, "new_forward_or_training": False,
            "saved_historical_normalizer_population": 500, "original_scoring_population": 449,
            "new_source_population": "oneissuer plus author-declared original factor context; not500 upstream issuers",
            "mmap_reference_selected_bytes": 39040, "score_selected_bytes": 40,
            "NPZ_members_inflated_bytes_recorded_separately": True,
            "original_large_reference_STAT_plus_one_slice_no_full_SHA": True, "full_PLAN_complete": False}


def execute(args):
    for value in (args.source_sha, args.h24_receipt_sha, args.slow_receipt_sha):
        require_sha(value)
    reads = Reads()
    reads.read(Path(__file__).absolute(), args.source_sha)
    OUT.mkdir(exist_ok=False)
    marker = write_json(OUT / "EXECUTION_STARTED_v2.json", {"started_utc": utc(), "source_sha256": args.source_sha,
            "h24_receipt_sha256": args.h24_receipt_sha, "slow_receipt_sha256": args.slow_receipt_sha,
            "automatic_retry": False})
    attempt = OUT / "attempt1"
    attempt.mkdir(exist_ok=False)
    result = {"schema_version": 2, "status": "RUNNING", "started_utc": utc(), "execution_marker": marker,
              "source_sha256": args.source_sha, "full_PLAN_complete": False, "economic_comparison_eligible": False,
              "new_forward_or_training": False, "new_scores_computed": False}
    failure = None
    stats_guard = {}
    try:
        import numpy as np  # Only this new numerical bridge; no original module imports.
        original = {p: json_input(reads, p, sha) for p, sha in PINS.items()}
        spec = original[PREP / "preparation_contract_v1.json"]
        win = original[WINDOW / "actual_window_normalization_receipt_v2.json"]
        fw = original[FORWARD / "audit_output/actual_forward_causality_receipt_v1.json"]
        mask = original[MASK]
        names = original[GRU / "artifacts/dataset/feature_names.json"]
        require(len(names) == 122 and mask["fixed_index2_before_and_after"] is True and
                mask["first_two_mask_entries_before_and_after"] == [False, False] and
                mask["target_index_within_ordered_449_batch"] == 0, "original fixed issuer/cohort differs")
        meta = original[GRU / "artifacts/dataset/meta.json"]
        require(digest(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()) ==
                "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4", "original metadata fingerprint differs")
        rows = win["row_proofs"]
        require([r["time_index"] for r in rows] == list(range(6181, 6261)) and
                all(r["full_historical_member_count"] == 500 for r in rows) and
                win["baseline_batch_body_SHA"] == fw["X_before_tensor_sha256"] == BODY_SHA,
                "original80 population/input chain differs")
        hdoc = json_input(reads, H24 / "actual_H24_original_source_reconstruction_receipt_v1.json", args.h24_receipt_sha)
        sdoc = json_input(reads, SLOW / "actual_source_reconstruction_receipt_v1.json", args.slow_receipt_sha)
        require(hdoc.get("status") == H24_STATUS and sdoc.get("status") == SLOW_STATUS,
                "actual upstream PASS receipts absent; prepared/failed work cannot be consumed")
        require([hdoc.get(k) for k in ("fixed_symbol", "symbol_universe_index", "fixed_time_index", "window_start_index", "window_end_index", "window_length")] ==
                ["000009.SZ", 2, 6260, 6181, 6260, 80], "H24 fixed identity differs")
        mapping_id = artifact(sdoc, SLOW / "mapping.json")
        mapping = json.loads(reads.read(SLOW / "mapping.json", mapping_id["sha256"], expected_bytes=mapping_id["bytes"]))
        require(mapping == sdoc.get("mapping"), "slow mapping internal/external bytes-semantic identity differs")
        day_rows = [r["day_index"] - 1545 for r in rows]
        dates = [datetime.fromtimestamp(rows[day_rows.index(i)]["signal_time_ns"] / 1e9, timezone.utc).date().isoformat() for i in range(21)]
        require(mapping == {"symbol": "000009.SZ", "symbol_index": 2, "date_order": dates,
                "day_indices": list(range(1545, 1566)), "feature_names": names[24:],
                "time_indices": list(range(6181, 6261)), "time_to_slow_row": day_rows}, "slow date/feature/time mapping differs")
        for key in ("original_raw_FP32_comparison", "future_prefix_FP32_comparison"):
            check = sdoc.get(key, {})
            require(check.get("shape") == [21, 98] and all(type(check.get(k)) is int and check[k] == 0 for k in
                    ("NaN_mask_mismatches", "finite_FP32_bit_mismatches", "nonfinite_not_nan")), "slow closed raw/future proof incomplete")
        hour = source_array(np, reads, hdoc, H24 / "H24_raw_80_24.npy", [80, 24], "<f4", "raw_h24_values")
        times = source_array(np, reads, hdoc, H24 / "times_ns.npy", [80], "<i8", "times_ns")
        complete = source_array(np, reads, hdoc, H24 / "hour_complete_80.npy", [80], "|b1", "hour_complete")
        window_complete = source_array(np, reads, hdoc, H24 / "window80_complete_80.npy", [80], "|b1", "window80_complete")
        hnames = artifact(hdoc, H24 / "feature_names.json", "feature_names")
        require(json.loads(reads.read(H24 / "feature_names.json", hnames["sha256"], expected_bytes=hnames["bytes"])) == names[:24], "H24 feature order differs")
        require(times.tolist() == [r["signal_time_ns"] for r in rows] and bool(complete.all()) and bool(window_complete[-1]),
                "H24 clocks/current80 completeness differ")
        slow = source_array(np, reads, sdoc, SLOW / "rebuilt_slow98_000009_21days.npy", [21, 98], "<f4")
        require(not np.isinf(hour).any() and not np.isinf(slow).any(), "source registered features contain infinity")
        joined = np.ascontiguousarray(np.concatenate((hour, slow[np.asarray(day_rows)]), axis=1), dtype="<f4")
        stat_id = win["baseline_fitstat_artifact"]
        fit = npz_arrays(np, reads, Path(stat_id["path"]), stat_id,
                         {n + ".npy": ([80, 122], "<i8" if n == "count" else "<f8") for n in ("count", "lo", "hi", "mean", "std")})
        require(((fit["count"] >= 0) & (fit["count"] <= 500)).all(), "historical500 counts malformed")
        good = fit["count"] >= 2
        require(all(np.isfinite(fit[n][good]).all() for n in ("lo", "hi", "mean", "std")) and
                (fit["std"][good] >= 0).all() and (fit["lo"][good] <= fit["hi"][good]).all(), "saved normalization statistics malformed")
        require(all(np.isnan(fit[n][~good]).all() for n in ("lo", "hi", "mean")), "saved invalid-count NaNs differ")
        raw = joined.astype(np.float64)
        safe_std = np.where(fit["std"] > 0, fit["std"], 1.0)
        normalized = np.clip((np.clip(raw, fit["lo"], fit["hi"]) - fit["mean"]) / safe_std, -5, 5)
        normalized[~np.isfinite(raw)] = np.nan
        normalized = normalized.astype("<f4")
        batch_id = win["reconstructed_batch_artifact"]
        batch_path = Path(batch_id["path"])
        saved = next(x for x in spec["saved_byte_materials"] if x["role"] == "saved449_normalized_batch")
        require(saved["inherited_identity"] == batch_id, "saved reference provenance differs")
        stats_guard[str(batch_path)] = exact_stat(batch_path)
        require(stats_guard[str(batch_path)] == saved["current_stat_only"], "saved batch STAT changed since preparation")
        batch = np.load(batch_path, mmap_mode="r", allow_pickle=False)
        require(batch.shape == (449, 80, 122) and batch.dtype.str == "<f4" and batch.flags.c_contiguous, "reference mmap shape/type/order differs")
        reference = np.array(batch[0], dtype="<f4", copy=True, order="C")
        del batch
        reads.slices.append({"path": str(batch_path), "slice": "[0,:,:]", "array_payload_bytes": reference.nbytes,
                             "whole_SHA_inherited": batch_id["sha256"], "whole_body_SHA_recomputed": False,
                             "NPY_header_and_OS_pages_not_counted_as_selected_payload": True})
        comparison = compare(np, normalized, reference)
        result["input_comparison"] = comparison
        require(comparison["complete_body_bytes_equal_including_NaN_payloads"], "new source joined normalized input differs from frozen batch row0; no tolerance changes")
        scores = []
        require([x["id"] for x in fw["models"]] == [x["id"] for x in spec["existing_original_model_evidence_no_new_forward"]["models"]], "original five model roster differs")
        for model in fw["models"]:
            identity = model["score_equality_artifact"]
            local = FORWARD / "audit_output" / Path(identity["path"]).name
            values = npz_arrays(np, reads, local, identity, {n + ".npy": ([449], "<f4") for n in ("score_before", "score_after")})
            require(np.isfinite(values["score_before"]).all() and values["score_before"].tobytes() == values["score_after"].tobytes(), "original score equality proof differs")
            scores.append({"id": model["id"], "checkpoint_sha256_inherited_no_checkpoint_read": model["checkpoint_sha256"],
                           "source_proof": identity, "original449_batch_index": 0, "union_index": 2,
                           "saved_original_score": float(values["score_before"][0]), "new_forward_observed": False})
        output_artifacts = []
        for name, value in (("source_joined_raw80_122.npy", joined), ("source_joined_normalized80_122.npy", normalized)):
            buffer = io.BytesIO()
            np.save(buffer, value, allow_pickle=False)
            body = buffer.getvalue()
            with (attempt / name).open("xb") as stream:
                stream.write(body)
            output_artifacts.append({"path": str(attempt / name), "bytes": len(body), "sha256": digest(body),
                                     "body_sha256": digest(value.tobytes(order="C"))})
        result.update({"status": "PASS_FIXED000009_CONDITIONAL_SOURCE122_INPUT_AND_INHERITED_FIVE_SCORE_BRIDGE",
                       "upstream_actual_receipts": {"h24_sha256": args.h24_receipt_sha, "slow_sha256": args.slow_receipt_sha},
                       "original_scoring_population": 449, "historical_statistics_population": 500,
                       "fixed_symbol": "000009.SZ", "union_index": 2, "original_batch_index": 0,
                       "window_time_indices_inclusive": [6181, 6260], "day_indices_inclusive": [1545, 1565],
                       "artifacts": output_artifacts, "five_saved_original_scores": scores,
                       "industry_slots_and_model_execution": "Inherited original fixed449-batch forward; no new auxiliary-source reconstruction or forward.",
                       "causality_limit": "Singleissuer source path with author-declared factor context and reused historical500 statistics; raw feedarrival/allissuer PIT/fullupstream future perturbation remain unproved."})
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        result["status"] = "FAIL_FIXED000009_BRIDGE_NO_RETRY"
        result["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    finally:
        try:
            # Fresh hashes only for these closed small receipts/NPY/NPZ inputs.
            # The original17.5MB reference remains STAT plus one39040B slice.
            first_reads = list(reads.files)
            after_identities = []
            for item in first_reads:
                require(exact_stat(item["path"]) == item["STAT"], "read input STAT changed by completion")
                reads.read(item["path"], item["sha256"], expected_bytes=item["bytes_read"])
                after_identities.append({"path": item["path"], "sha256": item["sha256"]})
            for path, before in stats_guard.items():
                require(exact_stat(path) == before, "reference batch STAT changed")
            require(digest(Path(__file__).read_bytes()) == args.source_sha, "reviewed source changed")
            result["inputs_STAT_unchanged_after_read"] = True
            result["closed_small_actual_SHA_before_after_identical"] = True
            result["closed_small_after_identities"] = after_identities
        except (Exception, KeyboardInterrupt) as exc:
            failure = failure or exc
            result["status"] = "FAIL_FIXED000009_BRIDGE_NO_RETRY"
            result["guard_failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        result["read_budget"] = {"file_read_bytes": reads.file_bytes, "file_reads": reads.files,
                "NPZ_member_inflation": reads.npz_members, "mmap_selected_slices": reads.slices,
                "score_selected_scalar_payload_bytes": 40 if "five_saved_original_scores" in result else 0,
                "physical_OS_storage_IO_not_inferred_from_selected_payload": True}
        result["finished_utc"] = utc()
        result["full_original500_upstream_source_rebuilt"] = False
        result["whole122_vendorarrival_PIT_or_alltime_causality_proved"] = False
        identity = write_json(attempt / "actual_conditional_source_input_bridge_receipt_v2.json", result)
        print(json.dumps({"status": result["status"], "receipt": identity, "new_forward": False,
                          "full_PLAN_complete": False}, ensure_ascii=False, allow_nan=False))
    return 1 if failure else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--h24-receipt-sha")
    parser.add_argument("--slow-receipt-sha")
    args = parser.parse_args()
    if not args.execute:
        require(not any((args.source_sha, args.h24_receipt_sha, args.slow_receipt_sha)), "actual pins require explicit --execute")
        print(json.dumps(pure_contract(), ensure_ascii=False, indent=2))
        return 0
    return execute(args)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())

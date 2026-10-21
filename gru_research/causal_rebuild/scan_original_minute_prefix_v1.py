"""One new, isolated full-history generation with prefix quality.

This is a prepared orchestrator. Execution requires a separately reviewed core
operator and an exact input contract. It does not invoke the old scanner,
finalizer, models, or live R2 task. Historical completion clocks are conditional
source-order witnesses, never observed provider arrival times.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CORE = None
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "raw_open",
                   "raw_high", "raw_low", "raw_close", "adjustment_factor",
                   "window_price", "window_raw_price", "window_volume"]
BOOL_COLUMNS = ["hour_complete", "window_complete", "window_tradable", "window_all_flat"]
INTEGER_COLUMNS = ["slot", "signal_time", "minute_count", "window_start_time", "window_end_time"]
MINUTE_STAT_NAMES = ["h_realized_volatility", "h_minute_return_skew", "h_minute_return_max",
                    "h_minute_return_min", "h_up_semivariance_share", "h_return_logvolume_corr",
                    "h_last15_volume_share", "h_zero_volume_share", "h_path_efficiency",
                    "h_upminute_volume_share"]


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stat_identity(path):
    s = Path(path).stat()
    return {"path": str(Path(path).resolve()), "bytes": s.st_size,
            "mtime_ns": s.st_mtime_ns, "inode": s.st_ino, "device": s.st_dev}


def checked_small(path, expected_sha, cap=4 * 1024 * 1024):
    path = Path(path)
    require(path.stat().st_size <= cap, "closed small input exceeds budget")
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_sha, "closed input SHA mismatch: " + str(path))
    return data


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporary, path)


def output_identity(path):
    data = Path(path).read_bytes()
    return {"path": str(Path(path).resolve()), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def load_core(path, expected_sha):
    global CORE
    checked_small(path, expected_sha)
    spec = importlib.util.spec_from_file_location("reviewed_prefix_generation_core_v1", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name in ("original_numeric_clock_ledger", "aggregate_table", "build_symbol_features"):
        require(callable(getattr(module, name, None)), "reviewed core API missing: " + name)
    CORE = module


def aggregate_day(batch):
    rows, audits = [], []
    for day, symbol, data in batch:
        digest = hashlib.sha256(data).hexdigest()
        table = pq.read_table(io.BytesIO(data), use_threads=False)
        require("datetime_nano" in table.column_names, "source numeric clock column missing")
        # This independently retained source-record ledger precedes all value
        # mutations. The core must not certify it by an all-day validity gate.
        ledger = CORE.original_numeric_clock_ledger(
            day, symbol, digest, table["datetime_nano"].to_numpy(zero_copy_only=False))
        generated, audit = CORE.aggregate_table(table, ledger)
        require(len(generated) == 4, "core must emit all four grid hours, including invalid hours")
        require([int(r["slot"]) for r in generated] == [0, 1, 2, 3], "core output slot roster differs")
        for row in generated:
            require(row["date"] == day and row["symbol"] == symbol, "core output source identity differs")
            clean = dict(row)
            # Persist quality detail without Arrow inferring variable struct
            # schemas from the first few historical days.
            for key, value in tuple(clean.items()):
                if isinstance(value, dict):
                    clean[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
            rows.append(clean)
        audits.append({"date": day, "symbol": symbol, "original_payload_sha256": digest,
                       "original_payload_bytes": len(data), "source_rows": len(table),
                       "clock_profile": "ORIGINAL_NUMERIC_CLOCK_MONOTONE_SOURCE_ORDER_COMPLETION",
                       "provider_historical_arrival_observed": False,
                       "local_quality_audit_json": json.dumps(audit, sort_keys=True, default=str)})
    return rows, audits


class SplitHashReader(io.RawIOBase):
    """One physical compressed pass; hashes each of the five volumes."""
    def __init__(self, paths):
        self.paths = [Path(p) for p in paths]
        self.index = 0
        self.handle = self.paths[0].open("rb")
        self.digests = [hashlib.sha256() for _ in paths]
        self.counts = [0 for _ in paths]

    def readable(self):
        return True

    def readinto(self, buffer):
        count = self.handle.readinto(buffer)
        while count == 0 and self.index + 1 < len(self.paths):
            self.handle.close()
            self.index += 1
            self.handle = self.paths[self.index].open("rb")
            count = self.handle.readinto(buffer)
        if count:
            self.digests[self.index].update(memoryview(buffer)[:count])
            self.counts[self.index] += count
        return count

    def close(self):
        self.handle.close()
        super().close()


def canonical_compare(new_frame, old_frame, day):
    """Compare all original aggregation values and quality, without epsilon."""
    key = ["symbol", "slot"]
    require(not new_frame.duplicated(key).any(), "generated duplicate symbol/slot")
    require(not old_frame.duplicated(key).any(), "legacy duplicate symbol/slot")
    new_frame = new_frame.sort_values(key).reset_index(drop=True)
    old_frame = old_frame.sort_values(key).reset_index(drop=True)
    keys_equal = len(new_frame) == len(old_frame) and all(
        np.array_equal(new_frame[k].to_numpy(), old_frame[k].to_numpy()) for k in key)
    comparison = {"date": day, "new_rows": len(new_frame), "legacy_rows": len(old_frame),
                  "exact_key_roster_equal": keys_equal, "fields": {}}
    if not keys_equal:
        comparison["all_numeric_masks_and_times_exact"] = False
        comparison["different_source_keys_sample"] = {
            "new_only": sorted(set(map(tuple, new_frame[key].to_numpy())) - set(map(tuple, old_frame[key].to_numpy())))[:16],
            "legacy_only": sorted(set(map(tuple, old_frame[key].to_numpy())) - set(map(tuple, new_frame[key].to_numpy())))[:16]}
        return comparison
    for column in NUMERIC_COLUMNS + MINUTE_STAT_NAMES + BOOL_COLUMNS + INTEGER_COLUMNS:
        require(column in new_frame and column in old_frame, "required aggregation field missing: " + column)
        if column in BOOL_COLUMNS:
            left = new_frame[column].to_numpy(dtype=np.bool_)
            right = old_frame[column].to_numpy(dtype=np.bool_)
            mismatch = left != right
            item = {"dtype": "bool", "different_elements": int(mismatch.sum())}
        elif column in INTEGER_COLUMNS:
            left = new_frame[column].to_numpy(dtype=np.int64)
            right = old_frame[column].to_numpy(dtype=np.int64)
            mismatch = left != right
            item = {"dtype": "int64", "different_elements": int(mismatch.sum())}
        else:
            left = np.ascontiguousarray(new_frame[column].to_numpy(dtype=np.float64))
            right = np.ascontiguousarray(old_frame[column].to_numpy(dtype=np.float64))
            nan_equal = np.isnan(left) == np.isnan(right)
            bits_equal = (left.view(np.uint64) == right.view(np.uint64)) | (np.isnan(left) & np.isnan(right))
            mismatch = ~(nan_equal & bits_equal)
            item = {"dtype": "float64", "different_elements": int(mismatch.sum()),
                    "different_NaN_masks": int((~nan_equal).sum())}
        if mismatch.any():
            indices = np.flatnonzero(mismatch)[:8]
            item["first_different_keys"] = new_frame.loc[indices, key].values.tolist()
        comparison["fields"][column] = item
    comparison["all_numeric_masks_and_times_exact"] = all(
        v["different_elements"] == 0 for v in comparison["fields"].values())
    return comparison


def execute(args):
    started = time.monotonic()
    contract = json.loads(checked_small(args.contract, args.contract_sha256))
    require(contract["schema_version"] == "PREPARED_FULL_MINUTE_PREFIX_GENERATION_V1", "wrong contract schema")
    checked_small(Path(__file__).resolve(), contract["scanner_sha256"])
    require(contract["source_operator"]["path"] == str(args.operator.resolve()), "core path differs from reviewed contract")
    require(contract["source_operator"]["sha256"] == args.operator_sha256, "core SHA differs from reviewed contract")
    require(contract["execution_allowed"] is True, "operator independent review and actual bounded run required")
    require(1 <= args.workers <= 6, "worker budget exceeded")
    require(str(args.output.resolve()) == contract["exact_new_output_directory"], "output namespace differs")
    checked_small(args.operator, args.operator_sha256)
    calendar_bytes = checked_small(contract["calendar"]["path"], contract["calendar"]["sha256"])
    calendar = json.loads(calendar_bytes)
    dates, symbols = calendar["dates"], set(calendar["union_symbols"])
    date_set = set(dates)
    require(len(dates) == 2119 and len(symbols) == 1178, "original calendar or universe changed")
    require(dates == sorted(set(dates)), "calendar date order changed")
    parts = [Path(item["path"]) for item in contract["original_five_source_volumes"]]
    require(len(parts) == 5, "all five exact source volumes required")
    original_stat = [stat_identity(p) for p in parts]
    require(original_stat == [item["stat"] for item in contract["original_five_source_volumes"]], "source STAT changed")
    legacy_dir = Path(contract["legacy_hourly_raw_directory"])
    require(legacy_dir.is_dir(), "explicit legacy comparison directory absent")
    reference_manifest = json.loads(checked_small(
        contract["legacy_reference_manifest"]["path"], contract["legacy_reference_manifest"]["sha256"]))
    require(reference_manifest["calendar"]["sha256"] == contract["calendar"]["sha256"],
            "preexecution reference calendar differs")
    require(reference_manifest["original_five_source_volumes"] == contract["original_five_source_volumes"],
            "preexecution source-volume bindings differ")
    preexecution_legacy_stat = {Path(item["path"]).stem: item
                               for item in reference_manifest["legacy_hourly_raw_reference_STAT"]}
    require(set(preexecution_legacy_stat) == date_set, "preexecution legacy reference dates differ")
    args.output.mkdir(parents=True, exist_ok=False)
    marker = {"status": "STARTED_ONCE_NO_AUTO_RETRY", "started_utc": now(), "PID": os.getpid(),
              "contract_sha256": args.contract_sha256, "source_operator_sha256": args.operator_sha256,
              "source_profile": "ORIGINAL_NUMERIC_CLOCK_MONOTONE_SOURCE_ORDER_COMPLETION",
              "provider_arrival_proven": False, "full_PLAN_complete": False}
    atomic_json(args.output / "execution_marker_v1.json", marker)
    for name in ("hourly_raw", "stockday_audit", "day_comparison"):
        (args.output / name).mkdir()
    counts = collections.Counter()
    seen_days, source_sets, outputs, comparisons = set(), collections.defaultdict(set), [], []
    legacy_stat = {}
    for day in dates:
        path = legacy_dir / (day + ".parquet")
        require(path.is_file(), "legacy comparison day missing: " + day)
        legacy_stat[day] = stat_identity(path)
        require(legacy_stat[day] == preexecution_legacy_stat[day], "legacy reference changed since preexecution seal")
    pending = collections.deque()

    def persist(day, result):
        rows, audits = result
        generated = pd.DataFrame(rows)
        require(len(generated) == 4 * len(audits), "generated stockday/hour count mismatch")
        path = args.output / "hourly_raw" / (day + ".parquet")
        pq.write_table(pa.Table.from_pandas(generated, preserve_index=False), path, compression="zstd")
        audit_path = args.output / "stockday_audit" / (day + ".parquet")
        pq.write_table(pa.Table.from_pylist(audits), audit_path, compression="zstd")
        old_path = legacy_dir / (day + ".parquet")
        require(stat_identity(old_path) == legacy_stat[day], "legacy file changed before comparison")
        legacy_bytes = old_path.read_bytes()
        counts["legacy_comparison_physical_file_bytes_read"] += len(legacy_bytes)
        old = pq.read_table(io.BytesIO(legacy_bytes), use_threads=False).to_pandas()
        comparison = canonical_compare(generated, old, day)
        comparison["legacy_file_same_read_SHA256"] = hashlib.sha256(legacy_bytes).hexdigest()
        comparison["legacy_STAT_after_same"] = stat_identity(old_path) == legacy_stat[day]
        require(comparison["legacy_STAT_after_same"], "legacy changed during comparison")
        comparison_path = args.output / "day_comparison" / (day + ".json")
        atomic_json(comparison_path, comparison)
        outputs.extend([output_identity(path), output_identity(audit_path), output_identity(comparison_path)])
        comparisons.append({"date": day, "exact": comparison["all_numeric_masks_and_times_exact"]})
        counts["generated_stockdays"] += len(audits)
        counts["generated_hour_rows"] += len(rows)
        counts["actual_selected_minute_rows_decoded"] += sum(a["source_rows"] for a in audits)
        counts["selected_original_payload_bytes"] += sum(a["original_payload_bytes"] for a in audits)
        counts["generated_complete_hours"] += int(generated.hour_complete.sum())
        counts["days_compared"] += 1
        counts["exact_aggregation_days"] += int(comparison["all_numeric_masks_and_times_exact"])
        atomic_json(args.output / "progress_v1.json", {"status": "RUNNING_NEW_FULL_SOURCE_PREFIX_GENERATION",
                    "observed_utc": now(), "elapsed_seconds": time.monotonic() - started,
                    "counts": dict(counts), "last_day_closed": day, "automatic_retry": False,
                    "old_sources_or_live_R2_modified": False})

    stream = SplitHashReader(parts)
    failure = None
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers,
                initializer=load_core, initargs=(str(args.operator), args.operator_sha256)) as pool:
            current_day, batch = None, []
            with io.BufferedReader(stream, buffer_size=64 * 1024) as buffered:
                with gzip.GzipFile(fileobj=buffered) as zipped:
                    with tarfile.open(fileobj=zipped, mode="r|") as archive:
                        for member in archive:
                            # Python's streaming TarFile still retains TarInfo
                            # objects; keep only the current header in memory.
                            archive.members.clear()
                            counts["logical_tar_entries"] += 1
                            if not member.isfile():
                                continue
                            pieces = member.name.split("/")
                            require(len(pieces) == 3 and len(pieces[1]) == 8 and pieces[1].isdigit()
                                    and pieces[-1].endswith(".parquet"), "unexpected archive file name")
                            code = pieces[-1][:-8]
                            require(len(code) == 6 and code.isdigit(), "unexpected source stock code")
                            compact = pieces[1]
                            day = compact[:4] + "-" + compact[4:6] + "-" + compact[6:]
                            symbol = code + (".SH" if code.startswith(("6", "9")) else ".SZ")
                            counts["source_archive_files"] += 1
                            if day not in date_set:
                                counts["outside_calendar_files_not_generated"] += 1
                                continue
                            source_sets[day].add(symbol)
                            if symbol not in symbols:
                                counts["outside_union_files_not_generated"] += 1
                                continue
                            if day != current_day:
                                if batch:
                                    pending.append((current_day, pool.submit(aggregate_day, batch)))
                                require(day not in seen_days, "tar date records are not contiguous")
                                seen_days.add(day)
                                current_day, batch = day, []
                                while len(pending) >= args.workers * 2:
                                    closed_day, future = pending.popleft()
                                    persist(closed_day, future.result())
                            handle = archive.extractfile(member)
                            require(handle is not None and 0 < member.size <= 4 * 1024 * 1024,
                                    "stockday payload exceeds budget or is empty")
                            data = handle.read()
                            require(len(data) == member.size, "source payload truncated")
                            batch.append((day, symbol, data))
                    # Tar end padding is insufficient proof of gzip/volume EOF.
                    # Drain once so CRC and all five compressed hashes close.
                    while zipped.read(64 * 1024):
                        pass
                while buffered.read(64 * 1024):
                    pass
            if batch:
                pending.append((current_day, pool.submit(aggregate_day, batch)))
            while pending:
                closed_day, future = pending.popleft()
                persist(closed_day, future.result())
        require(seen_days == set(dates), "not all original calendar days generated")
        require(counts["days_compared"] == len(dates), "all-day aggregation comparison incomplete")
        for i, item in enumerate(contract["original_five_source_volumes"]):
            require(stream.counts[i] == item["stat"]["bytes"], "full compressed volume not read")
            require(stream.digests[i].hexdigest() == item["sha256"], "same-pass compressed volume SHA differs")
        require([stat_identity(p) for p in parts] == original_stat, "original archive STAT changed")
        require(all(stat_identity(legacy_dir / (day + ".parquet")) == legacy_stat[day] for day in dates),
                "legacy comparison source STAT changed")
        checked_small(args.operator, args.operator_sha256)
        checked_small(contract["calendar"]["path"], contract["calendar"]["sha256"])
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
    receipt = {"schema_version": "ACTUAL_NEW_FULL_MINUTE_PREFIX_GENERATION_V1",
               "status": "FAILED_PRESERVED_NO_RETRY" if failure else "COMPLETE_PREFIX_AGGREGATION_FULL_SOURCE_COMPARISON",
               "started_marker": marker, "finished_utc": now(), "elapsed_seconds": time.monotonic() - started,
               "counts": dict(counts), "source_five_same_pass_SHA": [
                   {"path": str(p.resolve()), "bytes_read": stream.counts[i],
                    "sha256": stream.digests[i].hexdigest()} for i, p in enumerate(parts)],
               "all2119_aggregation_days_exact": not failure and counts["exact_aggregation_days"] == len(dates),
               "nonexact_day_roster": [item["date"] for item in comparisons if not item["exact"]],
               "actual_output_identities": outputs, "failure": failure,
               "clock_profile": "ORIGINAL_NUMERIC_CLOCK_MONOTONE_SOURCE_ORDER_COMPLETION",
               "historical_provider_arrival_or_QFQ_vintage_proven": False,
               "new_full_H24_dense_or_labels_or_X_or_models_completed": False,
               "automatic_retry": False, "full_PLAN_complete": False}
    atomic_json(args.output / "actual_generation_receipt_v1.json", receipt)
    print(json.dumps({"status": receipt["status"], "counts": dict(counts),
                      "receipt": str(args.output / "actual_generation_receipt_v1.json")}), flush=True)
    require(not failure, "new generation failed; inspect preserved receipt, no automatic retry")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--operator", type=Path, required=True)
    parser.add_argument("--operator-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    execute(parser.parse_args())


if __name__ == "__main__":
    main()

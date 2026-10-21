"""Independent new hourly Parquet -> full-grid H24/labels -> strict old diff.

Default is contract metadata only. Explicit actual mode requires a closed
contract, completed upstream receipt and an owned external deadline capture.
Original generators, training, models, archives and slow factors are not run.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import resource
import stat
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
NUMERIC = ("open", "high", "low", "close", "volume", "raw_open", "raw_high",
           "raw_low", "raw_close", "adjustment_factor", "window_price",
           "window_raw_price", "window_volume", "h_realized_volatility",
           "h_minute_return_skew", "h_minute_return_max", "h_minute_return_min",
           "h_up_semivariance_share", "h_return_logvolume_corr", "h_last15_volume_share",
           "h_zero_volume_share", "h_path_efficiency", "h_upminute_volume_share")
BOOLS = ("hour_complete", "window_complete", "window_tradable", "window_all_flat")
INTS = ("slot", "signal_time", "minute_count", "window_start_time", "window_end_time")
FIELDS = ("date", "symbol") + INTS + BOOLS + NUMERIC
OHLCV = ("open", "high", "low", "close", "volume", "raw_open", "raw_high",
         "raw_low", "raw_close", "adjustment_factor")
BASE_ROLES = ("hour_features", "hour_complete", "window80_complete", "members", "labels4",
              "execution_prices", "execution_raw_prices", "execution_complete",
              "execution_tradable", "execution_all_flat", "hour_ohlcv")
EXTRA_BOOL_ROLES = ("window_complete", "window_tradable", "window_all_flat", "source_present")
EXTRA_FLOAT_ROLES = ("window_prices_f64", "window_raw_prices_f64", "window_volumes_f64",
                    "execution_prices_f64", "execution_raw_prices_f64")
CLOCK_ROLES = ("times", "earliest_execution_times", "label_end_times")
WORKER = None


def require(value, message):
    if not value:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def stat_identity(path):
    p = Path(path)
    s = p.lstat()
    require(stat.S_ISREG(s.st_mode), "regular non-symlink file required: " + str(p))
    return {"path": str(p.resolve()), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns,
            "device": s.st_dev, "inode": s.st_ino}


def read_once(path, expected=None, cap=16 * 1024**2):
    p = Path(path)
    before = stat_identity(p)
    require(before["bytes"] <= cap, "physical source byte budget exceeded")
    body = p.read_bytes()
    identity = {"path": before["path"], "bytes": len(body), "sha256": digest(body)}
    require(len(body) == before["bytes"] and before == stat_identity(p), "source changed during same read")
    if expected is not None:
        require(identity == expected, "same-read source identity differs: " + str(p))
    return body, identity, before


def write_json(path, value, cap=16 * 1024**2):
    body = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    require(len(body) <= cap, "JSON output exceeds budget")
    with Path(path).open("xb") as handle:
        handle.write(body)
    return {"path": str(Path(path).resolve()), "bytes": len(body), "sha256": digest(body)}


def hash_output(path):
    before = stat_identity(path)
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024**2), b""):
            h.update(chunk)
    require(before == stat_identity(path), "new output changed during final byte seal")
    return {"path": before["path"], "bytes": before["bytes"], "sha256": h.hexdigest()}


def runtime():
    global np, pd, pa, pq, duckdb
    if "np" not in globals():
        sys.path.insert(0, str(ROOT / "gru_research/.deps"))
        import numpy as np
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
        import duckdb


def load_core(item):
    read_once(item["path"], item, cap=1024**2)
    name = "closed_causal_hourly_dense_core_v3"
    spec = importlib.util.spec_from_file_location(name, item["path"])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    require(callable(getattr(module, "build_symbol_features", None)), "sealed core API missing")
    return module


def array_specs(T, U):
    f4, f8, b1, i8 = "<f4", "<f8", "|b1", "<i8"
    specs = {"hour_features": (f4, (T, U, 24)), "labels4": (f4, (T, U, 4)),
             "hour_ohlcv": (f4, (T, U, 10)), "execution_prices": (f4, (T, U)),
             "execution_raw_prices": (f4, (T, U))}
    for name in ("hour_complete", "window80_complete", "members", "execution_complete",
                 "execution_tradable", "execution_all_flat") + EXTRA_BOOL_ROLES:
        specs[name] = b1, (T, U)
    for name in EXTRA_FLOAT_ROLES:
        specs[name] = f8, (T, U)
    specs.update(times=(i8, (T,)), earliest_execution_times=(i8, (T,)), label_end_times=(i8, (T, 4)))
    return specs


def closed_upstream(receipt, path, dates, core_sha, volumes):
    require(receipt["schema_version"] == "ACTUAL_NEW_FULL_MINUTE_PREFIX_GENERATION_V2" and
            receipt["status"] == "COMPLETE_PREFIX_AGGREGATION_FULL_SOURCE_COMPARISON" and
            receipt["failure"] is None, "upstream is incomplete or not the reviewed schema")
    require(receipt["started_marker"]["source_operator_sha256"] == core_sha,
            "upstream used a different minute operator")
    require(receipt["counts"]["days_compared"] == len(dates), "upstream calendar coverage incomplete")
    require(receipt["source_five_same_pass_SHA"] == [
        {"path": v["path"], "bytes_read": v["stat"]["bytes"], "sha256": v["sha256"]} for v in volumes],
        "upstream exact five source hashes/counts differ")
    root = Path(path).parent.resolve()
    expected = {str(root / directory / (d + extension)) for d in dates
                for directory, extension in (("hourly_raw", ".parquet"), ("stockday_audit", ".parquet"), ("day_comparison", ".json"))}
    items = receipt["actual_output_identities"]
    require(len(items) == len(expected) and len({x["path"] for x in items}) == len(items) and
            {x["path"] for x in items} == expected, "upstream output roster is not exact positive 3x2119")
    daymap = {Path(x["path"]).stem: x for x in items if Path(x["path"]).parent.name == "hourly_raw"}
    require(set(daymap) == set(dates) and all(x["bytes"] > 0 for x in daymap.values()), "empty/changed hourly date roster")
    require({str(p.resolve()) for p in (root / "hourly_raw").glob("*.parquet")} == {x["path"] for x in daymap.values()},
            "unbound extra/missing new raw source file")
    return daymap


def partition_sources(output, daymap, dates, symbols, core, budget, deadline):
    """Read each original new day once, then partition independent disk spool."""
    schema = pa.schema([(n, pa.string()) for n in ("date", "symbol")] +
                       [(n, pa.int64()) for n in INTS] + [(n, pa.bool_()) for n in BOOLS] +
                       [(n, pa.float64()) for n in NUMERIC])
    database = output / "hourly_disk_spool.duckdb"
    con = duckdb.connect(str(database))
    source_ids, total, row_count = [], 0, 0
    try:
        con.execute("SET threads=1")
        con.execute("SET memory_limit='512MiB'")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET partitioned_write_max_open_files=16")
        con.execute("SET partitioned_write_flush_threshold=65536")
        con.register("empty_schema", pa.Table.from_batches([], schema=schema))
        con.execute("CREATE TABLE hourly AS SELECT * FROM empty_schema")
        con.unregister("empty_schema")
        union = set(symbols)
        for day in dates:
            require(time.monotonic() < deadline, "partition elapsed operation budget exceeded")
            body, item, before = read_once(daymap[day]["path"], daymap[day], budget["each_day_file_bytes"])
            total += len(body)
            require(total <= budget["all_new_day_file_bytes"], "total new daily physical source budget exceeded")
            footer = pq.ParquetFile(io.BytesIO(body))
            require(0 < footer.metadata.num_rows <= 8192 and 34 <= footer.metadata.num_columns <= 64 and
                    footer.metadata.num_row_groups <= 16 and footer.metadata.serialized_size <= 1024**2,
                    "hourly day declared schema/resource limits differ")
            require(set(FIELDS) <= set(footer.schema_arrow.names), "hourly source fields absent")
            declared = sum(footer.metadata.row_group(g).column(c).total_uncompressed_size
                           for g in range(footer.metadata.num_row_groups) for c in range(footer.metadata.num_columns))
            require(declared <= budget["each_day_declared_decoded_bytes"], "hourly day declared decoded cap exceeded")
            table = footer.read(columns=list(FIELDS), use_threads=False)
            require(table.nbytes <= budget["each_day_projected_bytes"], "projected new day exceeds cap")
            for name in ("date", "symbol"):
                require(pa.types.is_string(table.schema.field(name).type) or pa.types.is_large_string(table.schema.field(name).type), "identity must be string")
            for name in INTS:
                require(pa.types.is_integer(table.schema.field(name).type), "hourly clock/count must be integer")
            for name in BOOLS:
                require(pa.types.is_boolean(table.schema.field(name).type) and table[name].null_count == 0, "quality flags must be non-null boolean")
            for name in NUMERIC:
                require(pa.types.is_integer(table.schema.field(name).type) or pa.types.is_floating(table.schema.field(name).type), "hourly numeric type differs")
            table = table.cast(schema, safe=True)
            df = table.select(["date", "symbol", "slot", "signal_time", "window_start_time", "window_end_time"]).to_pandas()
            require(df.date.eq(day).all() and set(df.symbol) <= union and not df[["symbol", "slot"]].duplicated().any(), "day identity/key differs")
            require(df.groupby("symbol").slot.apply(lambda s: sorted(s.tolist()) == [0, 1, 2, 3]).all(), "each stockday must retain all four slots")
            require(np.array_equal(df.signal_time.to_numpy(), core.midnight_ns(day) + df.slot.to_numpy() * 0 +
                                   np.asarray(core.SIGNALS, np.int64)[df.slot.to_numpy()] * core.MINUTE), "signal grid mismatch")
            expected_starts = core.midnight_ns(day) + np.asarray(core.EXECUTIONS, np.int64)[df.slot.to_numpy()] * core.MINUTE
            require(np.array_equal(df.window_start_time.to_numpy(), expected_starts) and
                    np.array_equal(df.window_end_time.to_numpy(), expected_starts + 5 * core.MINUTE), "window clock differs")
            con.register("one_closed_day", table)
            try:
                con.execute("INSERT INTO hourly SELECT * FROM one_closed_day")
            finally:
                con.unregister("one_closed_day")
            row_count += len(table)
            source_ids.append(item | {"date": day, "STAT": before, "projected_rows": len(table),
                                      "projected_decoded_bytes": table.nbytes, "physical_reads": 1})
            require(before == stat_identity(item["path"]), "source day changed after insertion")
            del body, table, footer, df
        destination = output / "raw_by_symbol"
        escaped = str(destination).replace("'", "''")
        con.execute("COPY (SELECT * FROM hourly ORDER BY symbol,signal_time) TO '" + escaped +
                    "' (FORMAT PARQUET, PARTITION_BY(symbol), COMPRESSION ZSTD)")
        return source_ids, {"physical_new_day_files_read_once": len(source_ids), "physical_new_day_bytes_read": total,
                            "hourly_rows_inserted": row_count, "spool_database": str(database),
                            "disk_spool_and_partition_numeric_reads_are_additional_derived_IO": True}
    finally:
        con.close()


def worker_initialize(contract, output, deadline):
    global WORKER
    runtime()
    WORKER = contract, Path(output), deadline, load_core(contract["operator"])


def worker_symbol(job):
    contract, output, deadline, core = WORKER
    index, symbol, files = job
    require(time.monotonic() < deadline, "symbol budget elapsed")
    dates, T = contract["calendar_dates"], len(contract["calendar_dates"]) * 4
    frames, source_ids, bytes_read = [], [], 0
    for item in files:
        body, ident, before = read_once(item["path"], cap=contract["budget"]["each_symbol_partition_bytes"])
        require(before == item["STAT"], "derived partition changed before read")
        bytes_read += len(body)
        require(bytes_read <= contract["budget"]["all_one_symbol_partition_bytes"], "one symbol source budget exceeded")
        table = pq.read_table(io.BytesIO(body), columns=[n for n in FIELDS if n != "symbol"], use_threads=False)
        require(table.nbytes <= contract["budget"]["each_symbol_projected_bytes"] and len(table) <= T, "symbol projected byte/row budget exceeded")
        f = table.to_pandas(); f["symbol"] = symbol; f["source_present"] = True
        frames.append(f); source_ids.append(ident | {"STAT": before})
        del body, table
    if frames:
        raw = pd.concat(frames, ignore_index=True).sort_values("signal_time")
        require(len(raw) <= T and not raw.signal_time.duplicated().any() and raw.symbol.eq(symbol).all(), "symbol source duplicate/identity differs")
    else:
        # Present missing-union member: expand missing values, never prices.
        row = {n: np.nan for n in NUMERIC}
        row.update({n: False for n in BOOLS})
        row.update(date=dates[0], symbol=symbol, slot=0, signal_time=core.midnight_ns(dates[0]) + core.SIGNALS[0] * core.MINUTE,
                   minute_count=0, window_start_time=core.midnight_ns(dates[0]) + core.EXECUTIONS[0] * core.MINUTE,
                   window_end_time=core.midnight_ns(dates[0]) + (core.EXECUTIONS[0] + 5) * core.MINUTE, source_present=False)
        raw = pd.DataFrame([row])
    result = core.build_symbol_features(raw, dates, contract["next_execution_start_ns"])
    result["source_present"] = result.source_present.eq(True)
    require(len(result) == T and result.symbol.eq(symbol).all() and result.signal_time.is_monotonic_increasing, "core full grid differs")
    blocks = {"hour_features": np.ascontiguousarray(result[list(core.H24)].to_numpy(), dtype="<f4"),
              "labels4": np.ascontiguousarray(result[["y1", "y2", "y3", "y4"]].to_numpy(), dtype="<f4"),
              "hour_ohlcv": np.ascontiguousarray(result[list(OHLCV)].to_numpy(), dtype="<f4"),
              "execution_prices": result.execution_price.to_numpy(dtype="<f4"),
              "execution_raw_prices": result.execution_raw_price.to_numpy(dtype="<f4")}
    for role, column in {"hour_complete": "hour_complete", "window80_complete": "window80_complete",
                         "execution_complete": "execution_window_complete", "execution_tradable": "execution_tradable",
                         "execution_all_flat": "execution_all_flat", **{n: n for n in EXTRA_BOOL_ROLES}}.items():
        blocks[role] = result[column].to_numpy(bool)
    for role, column in {"window_prices_f64": "window_price", "window_raw_prices_f64": "window_raw_price",
                         "window_volumes_f64": "window_volume", "execution_prices_f64": "execution_price",
                         "execution_raw_prices_f64": "execution_raw_price"}.items():
        blocks[role] = result[column].to_numpy(dtype="<f8")
    entry = blocks["window_prices_f64"][1:]
    require(np.array_equal(blocks["execution_prices_f64"][:-1].view("<u8"), entry.view("<u8")), "entry shift is not raw window+1")
    for delta in range(1, 5):
        expected = np.full(T, np.nan, np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            expected[:-(delta + 1)] = np.log(blocks["window_prices_f64"][delta + 1:] / entry[:-delta])
        expected[~np.isfinite(expected)] = np.nan
        require(expected.astype("<f4").tobytes() == blocks["labels4"][:, delta - 1].tobytes(), "independent label formula FP32 differs")
    memory = io.BytesIO(); np.savez(memory, **blocks); body = memory.getvalue()
    require(len(body) <= contract["budget"]["each_symbol_block_bytes"], "symbol block exceeds cap")
    path = output / "symbol_blocks" / (symbol + ".npz")
    with path.open("xb") as handle:
        handle.write(body)
    block_id = {"path": str(path), "bytes": len(body), "sha256": digest(body)}
    tape = io.BytesIO(); pq.write_table(pa.Table.from_pandas(result, preserve_index=False), tape, compression="zstd")
    tape_body = tape.getvalue()
    require(len(tape_body) <= contract["budget"]["each_symbol_tape_bytes"], "symbol tape exceeds cap")
    tape_path = output / "symbol_hourly_tape" / (symbol + ".parquet")
    with tape_path.open("xb") as handle:
        handle.write(tape_body)
    require(all(x["STAT"] == stat_identity(x["path"]) for x in source_ids), "derived symbol source changed")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    require(rss <= contract["budget"]["each_worker_peak_RSS_bytes"], "worker observed RSS exceeds postcheck cap")
    return {"symbol": symbol, "union_index": index, "partition_same_read_identities": source_ids,
            "block": block_id, "hourly_tape": {"path": str(tape_path), "bytes": len(tape_body), "sha256": digest(tape_body)},
            "full_grid_rows": T, "source_rows": int(result.source_present.sum()), "hour_complete_count": int(result.hour_complete.sum()),
            "window80_complete_count": int(result.window80_complete.sum()), "worker_peak_RSS_bytes": rss}


def compare_block(new, old, role, symbol_index):
    require(new.shape == old.shape and new.dtype == old.dtype, "original dense shape/dtype differs: " + role)
    if new.dtype.kind == "f":
        bitdtype = "<u4" if new.dtype.itemsize == 4 else "<u8"
        changed = new.view(bitdtype) != old.view(bitdtype)
        nan_changed = np.isnan(new) != np.isnan(old)
        finite_changed = changed & ~(np.isnan(new) | np.isnan(old))
        counts = {"all_bits_different": int(changed.sum()), "NaN_mask_different": int(nan_changed.sum()),
                  "nonNaN_bits_different": int(finite_changed.sum())}
    else:
        changed = new != old
        counts = {"all_bits_different": int(changed.sum())}
        if new.dtype.kind == "b":
            counts.update(false_to_true=int((~old & new).sum()), true_to_false=int((old & ~new).sum()))
    first = []
    for position in np.argwhere(changed)[:20]:
        key = tuple(map(int, position))
        first.append({"time_index": key[0], "union_index": symbol_index, "field_index": key[1] if len(key) > 1 else None,
                      "old_scalar_bytes_hex": old[key].tobytes().hex(), "new_scalar_bytes_hex": new[key].tobytes().hex()})
    per_field = changed.sum(axis=0).astype(int).tolist() if changed.ndim > 1 else None
    return {"role": role, "elements": new.size, **counts, "per_field_bits_different": per_field,
            "first_differences": first, "new_symbol_body_sha256": digest(new.tobytes()),
            "old_symbol_body_sha256": digest(old.tobytes()), "mmap_OS_IO_not_measured": True}


def accumulate_differences(summary, diff):
    role = diff["role"]
    target = summary.setdefault(role, {"role": role, "elements": 0, "all_bits_different": 0,
                                      "first_differences": [], "first_difference_example_cap": 20})
    for name in ("elements", "all_bits_different", "NaN_mask_different", "nonNaN_bits_different", "false_to_true", "true_to_false"):
        if name in diff:
            target[name] = target.get(name, 0) + diff[name]
    fields = diff["per_field_bits_different"]
    if fields is not None:
        old = target.get("per_field_bits_different", [0] * len(fields))
        require(len(old) == len(fields), "field-difference count shape changed")
        target["per_field_bits_different"] = [a + b for a, b in zip(old, fields)]
    target["first_differences"] = (target["first_differences"] + diff["first_differences"])[:20]


def initialize_arrays(output, specs):
    for role, (dtype, shape) in specs.items():
        if role in CLOCK_ROLES:
            continue
        path = output / ("minute_" + role + ".npy")
        require(not path.exists(), "exclusive array already exists")
        a = np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=shape)
        del a
        for start in range(0, shape[0], 64):
            # Release each row-block mapping. Keeping a touched entire958MB
            # feature mmap resident would defeat the parent RSS budget.
            a = np.lib.format.open_memmap(path, mode="r+")
            a[start:start + 64] = np.nan if np.dtype(dtype).kind == "f" else False
            a.flush(); del a


def pure_contract():
    return {"schema_version": "PREPARED_NEW_HOURLY_DENSE_V1", "status": "PREPARED_ONLY_NO_MARKET_OR_NUMERIC_READ",
            "shape": [8476, 1178], "day_files": 2119, "maximum_workers": 6, "maximum_inflight_symbols": 12,
            "raw_source_each_physical_read": 1, "old_reference_whole_SHA_recomputed": False,
            "difference_policy": "Preserve exact NaN/bits/masks differences; generation completion is separate from parity/admission",
            "operator": "sealed coreV3.build_symbol_features; no original finalize/scan import",
            "full_PLAN_complete": False, "new_forward_or_training": False}


def execute_once(args):
    source_body, source_id, _ = read_once(Path(__file__).resolve(), cap=1024**2)
    require(source_id["sha256"] == args.source_sha, "own source SHA differs")
    cb, cid, _ = read_once(args.contract)
    require(cid["sha256"] == args.contract_sha256, "contract SHA differs")
    contract = json.loads(cb)
    require(contract["schema_version"] == "PREPARED_NEW_HOURLY_DENSE_V1" and contract["source"] == source_id,
            "wrong closed dense contract/source")
    require(contract["upstream_receipt_path"] == str(Path(args.upstream_receipt).resolve()), "upstream path differs")
    ub, uid, _ = read_once(args.upstream_receipt)
    require(uid["sha256"] == args.upstream_sha256, "explicit actual upstream receipt SHA differs")
    require(str(Path(args.output).resolve()) == contract["exact_output_directory"], "output namespace differs")
    require(args.workers == 6, "frozen worker count differs")
    require(int(os.environ.get("GRU_DENSE_WATCHDOG_PID", "0")) == os.getppid() and
            7200 <= int(os.environ.get("GRU_DENSE_WATCHDOG_LIMIT_SECONDS", "0")) <= 7320,
            "explicit owned external capture7200..7320 seconds required")
    dates, symbols = contract["calendar_dates"], contract["union_symbols"]
    require(len(dates) == 2119 and dates == sorted(set(dates)) and len(symbols) == 1178 and symbols == sorted(set(symbols)), "positive original grid differs")
    daymap = closed_upstream(json.loads(ub), args.upstream_receipt, dates, contract["operator"]["sha256"], contract["original_five_source_volumes"])
    output = Path(args.output).resolve(); output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic(); deadline = started + 7200
    result = {"schema_version": "ACTUAL_NEW_HOURLY_DENSE_V1", "status": "RUNNING_NEW_HOURLY_DENSE_V1",
              "started_utc": now(), "source": source_id, "contract": cid, "upstream_receipt": uid,
              "automatic_retry": False, "full_PLAN_complete": False, "economic_comparison_eligible": False,
              "downstream_reassembly_or_training_admitted": False, "original_whole_dense_SHA_recomputed": False,
              "historical_provider_arrival_or_QFQ_vintage_proven": False, "new_forward_or_training": False}
    write_json(output / "EXECUTION_STARTED_v1.json", result)
    failure = None; pool = None; artifacts = []; source_ids = []; comparisons = {}; workers = []
    try:
        runtime()
        guards = {x["path"]: x for x in contract["small_guards"]}
        for x in guards.values():
            read_once(x["path"], x)
        core = load_core(contract["operator"])
        require(list(core.H24) == contract["H24_names"], "core feature order changed")
        for d in ("symbol_blocks", "symbol_hourly_tape", "symbol_differences"):
            (output / d).mkdir()
        reference = contract["original_dense_references"]
        require(all(stat_identity(x["path"]) == x["STAT"] for x in reference.values()), "old reference preexecution STAT differs")
        T, U = len(dates) * 4, len(symbols); specs = array_specs(T, U)
        member = contract["dated_members_source"]
        mb, mid, _ = read_once(member["path"], member, cap=16 * 1024**2)
        members = np.load(io.BytesIO(mb), allow_pickle=False)
        require(members.shape == (T, U) and members.dtype == np.dtype(bool), "dated member grid shape/type differs")
        require(np.array_equal(members.reshape(len(dates), 4, U), np.repeat(members[::4, None, :], 4, axis=1)), "dated membership changed within day")
        member_counts = members[::4].sum(axis=1).astype(int).tolist()
        require(member_counts == contract["calendar_member_counts"], "member source differs from actual dated calendar counts")
        result["dated_members_actual_same_read_identity"] = mid
        del mb
        source_ids, partition_counts = partition_sources(output, daymap, dates, symbols, core, contract["budget"], deadline)
        result["partition_counts"] = partition_counts
        artifacts.append({"role": "same_read_daily_sources", **write_json(output / "same_read_daily_source_identities_v1.json", source_ids)})
        initialize_arrays(output, specs)
        times = np.asarray([core.midnight_ns(d) + slot * core.MINUTE for d in dates for slot in core.SIGNALS], dtype="<i8")
        starts = np.asarray([core.midnight_ns(d) + slot * core.MINUTE for d in dates for slot in core.EXECUTIONS], dtype="<i8")
        earliest = np.r_[starts[1:], np.int64(contract["next_execution_start_ns"])]
        ends = np.full((T, 4), -1, dtype="<i8")
        for delta in range(1, 5):
            ends[:-(delta + 1), delta - 1] = starts[delta + 1:] + 5 * core.MINUTE
        require((earliest > times).all(), "next execution is not strictly later")
        for role, values in (("times", times), ("earliest_execution_times", earliest), ("label_end_times", ends)):
            with (output / ("minute_" + role + ".npy")).open("xb") as handle:
                np.save(handle, values, allow_pickle=False)
            old = np.load(reference[role]["path"], mmap_mode="r", allow_pickle=False)
            accumulate_differences(comparisons, compare_block(values, np.asarray(old).copy(), role, None))
            del old
        partition_root = output / "raw_by_symbol"
        partition_files = sorted(partition_root.glob("symbol=*/*.parquet"))
        require({p.parent.name[7:] for p in partition_files} <= set(symbols), "extra partition symbol")
        require(set(partition_root.glob("**/*.parquet")) == set(partition_files), "unsafe nested/unknown partitions")
        jobs = []
        for index, symbol in enumerate(symbols):
            files = [{"path": str(p.resolve()), "STAT": stat_identity(p)} for p in partition_files if p.parent.name == "symbol=" + symbol]
            require(len(files) <= 256, "symbol partition file count exceeds cap")
            jobs.append((index, symbol, files))
        pool = multiprocessing.get_context("spawn").Pool(6, initializer=worker_initialize, initargs=(contract, str(output), deadline))
        inflight = collections.deque(); iterator = iter(jobs); total_bits = collections.Counter()
        for _ in range(min(12, len(jobs))):
            inflight.append(pool.apply_async(worker_symbol, (next(iterator),)))
        completed = 0
        while inflight:
            remaining = deadline - time.monotonic(); require(remaining > 0, "dense operation deadline reached")
            item = inflight.popleft().get(timeout=min(300, remaining))
            index, symbol = item["union_index"], item["symbol"]
            require(symbols[index] == symbol and index == completed, "worker result order/identity differs")
            body, bid, _ = read_once(item["block"]["path"], item["block"], contract["budget"]["each_symbol_block_bytes"])
            symbol_differences = []
            with np.load(io.BytesIO(body), allow_pickle=False) as archive:
                expected_roles = set(specs) - set(CLOCK_ROLES) - {"members"}
                require(set(archive.files) == expected_roles, "symbol array role roster differs")
                for role in [n for n in specs if n not in CLOCK_ROLES]:
                    new = np.ascontiguousarray(members[:, index] if role == "members" else archive[role])
                    dtype, shape = specs[role]
                    require(new.dtype == np.dtype(dtype) and new.shape == (T,) + tuple(shape[2:]), "worker block shape/dtype differs: " + role)
                    mmap = np.lib.format.open_memmap(output / ("minute_" + role + ".npy"), mode="r+")
                    mmap[:, index] = new; mmap.flush(); del mmap
                    if role in BASE_ROLES:
                        if role == "members":
                            old = members[:, index].copy()
                        else:
                            oldmap = np.load(reference[role]["path"], mmap_mode="r", allow_pickle=False)
                            require(oldmap.shape == shape and oldmap.dtype == np.dtype(dtype), "old dense registration differs: " + role)
                            old = np.ascontiguousarray(oldmap[:, index]); del oldmap
                        diff = compare_block(new, old, role, index)
                        total_bits[role] += diff["all_bits_different"]
                        symbol_differences.append({"symbol": symbol, **diff})
                        accumulate_differences(comparisons, diff); del old
                    del new
            del body
            item["strict_original_diff"] = write_json(output / "symbol_differences" / (symbol + ".json"), symbol_differences)
            workers.append(item); completed += 1
            if completed % 50 == 0:
                print(json.dumps({"stage": "dense", "symbols_completed": completed, "symbols_total": U}), flush=True)
            try:
                inflight.append(pool.apply_async(worker_symbol, (next(iterator),)))
            except StopIteration:
                pass
        pool.close(); pool.join(); pool = None
        require(completed == U and sum(x["source_rows"] for x in workers) == partition_counts["hourly_rows_inserted"], "symbol coverage/count loss")
        for x in reference.values():
            require(stat_identity(x["path"]) == x["STAT"], "old reference changed after comparison")
        for x in source_ids:
            require(stat_identity(x["path"]) == x["STAT"], "upstream day changed after full read")
        for x in guards.values():
            read_once(x["path"], x)
        read_once(args.upstream_receipt, uid); read_once(args.contract, cid)
        read_once(Path(__file__).resolve(), source_id, cap=1024**2)
        output_files = [p for p in output.rglob("*") if p.is_file()]
        require(len(output_files) <= contract["budget"]["output_file_count"] and
                sum(stat_identity(p)["bytes"] for p in output_files) <= contract["budget"]["output_file_bytes"],
                "derived output file/disk postcheck cap exceeded; preserve evidence")
        artifacts.append({"role": "strict_original_dense_differences", **write_json(output / "strict_original_dense_differences_v1.json", comparisons)})
        artifacts.append({"role": "symbol_artifact_manifest", **write_json(output / "symbol_artifact_manifest_v1.json", workers)})
        arrays = [{"role": role, **hash_output(output / ("minute_" + role + ".npy")), "dtype": dtype, "shape": list(shape)}
                  for role, (dtype, shape) in specs.items()]
        exact = not any(x["all_bits_different"] for x in comparisons.values())
        meta = {"schema_version": "NEW_HOURLY_DENSE_METADATA_V1", "dense_shape": [T, U], "hour_features": list(core.H24),
                "calendar_dates": dates, "union_symbols": symbols, "hour_ohlcv_columns": list(OHLCV),
                "timezone": "Asia/Shanghai", "timestamp_format": "utc_ns", "next_known_trading_day": contract["next_known_trading_day"],
                "next_execution_start_ns": contract["next_execution_start_ns"], "dense_artifacts": arrays,
                "dense_files": {role: "minute_" + role + ".npy" for role in specs}, "source_operator": contract["operator"],
                "upstream_receipt": uid, "whole_day_future_quality_gate_applied": False,
                "historical_provider_arrival_or_QFQ_vintage_proven": False, "original_dense_parity_pass": exact,
                "current_member_normalization_or_X_generation_performed": False, "full_PLAN_complete": False}
        artifacts.extend(arrays)
        artifacts.append({"role": "metadata", **write_json(output / "minute_feature_metadata_v1.json", meta)})
        result.update(status="PASS_NEW_HOURLY_DENSE_FULL_GRID_EXACT_ORIGINAL_QUALIFIED" if exact else
                      "COMPLETE_NEW_HOURLY_DENSE_WITH_PRESERVED_DIFFERENCES_ADMISSION_PENDING",
                      generation_complete=True, original_dense_parity_pass=exact, exact_difference_count=sum(total_bits.values()) +
                      sum(x["all_bits_different"] for x in comparisons.values() if x["role"] in CLOCK_ROLES),
                      original_difference_counts_by_role=dict(total_bits), symbols_completed=completed,
                      dense_shape=[T, U], array_artifacts=arrays, original_reference_STAT_before_after_unchanged=True,
                      full_source_and_original_grid_closed=True, dated_member_counts=member_counts,
                      old_dense_whole_SHA_identity_scope="Inherited where previously sealed; no fresh whole-file SHA. All actual cells compared by readonly mmap snapshots; OSIO not measured.")
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "reason": str(exc), "traceback": traceback.format_exc()[:65536]}
        result.update(status="FAIL_NEW_HOURLY_DENSE_PRESERVED_NO_RETRY", generation_complete=False, failure=failure)
        if pool is not None:
            pool.terminate(); pool.join(); pool = None
    finally:
        # Failure paths retain the same final guards rather than inferring that
        # an interrupted comparison left original inputs unchanged.
        try:
            require(all(stat_identity(x["path"]) == x["STAT"] for x in contract["original_dense_references"].values()),
                    "original dense final STAT changed")
            require(all(stat_identity(x["path"]) == x["STAT"] for x in source_ids), "new day final STAT changed")
            for x in contract["small_guards"]:
                read_once(x["path"], x)
            read_once(args.upstream_receipt, uid); read_once(args.contract, cid)
            read_once(Path(__file__).resolve(), source_id, cap=1024**2)
            result["original_and_small_final_guards_pass"] = True
        except BaseException as exc:
            failure = failure or {"type": type(exc).__name__, "reason": str(exc)}
            result.update(status="FAIL_NEW_HOURLY_DENSE_PRESERVED_NO_RETRY", generation_complete=False,
                          final_guard_failure={"type": type(exc).__name__, "reason": str(exc)})
        result["artifacts"] = artifacts
        result["finished_utc"] = now(); result["elapsed_seconds"] = time.monotonic() - started
        result["parent_peak_RSS_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        if result["elapsed_seconds"] > 7200 or result["parent_peak_RSS_bytes"] > contract["budget"]["parent_peak_RSS_bytes"]:
            failure = failure or {"type": "POSTCHECK_RESOURCE_BUDGET", "hard_OS_or_IO_measurement": False}
            result.update(status="FAIL_NEW_HOURLY_DENSE_PRESERVED_NO_RETRY", generation_complete=False, failure=failure)
        receipt = write_json(output / "actual_hourly_dense_generation_receipt_v1.json", result)
        print(json.dumps({"status": result["status"], "receipt": receipt, "full_PLAN_complete": False}), flush=True)
    return 1 if failure else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    for name in ("contract", "contract-sha256", "upstream-receipt", "upstream-sha256", "source-sha", "output"):
        parser.add_argument("--" + name)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    fields = (args.contract, args.contract_sha256, args.upstream_receipt, args.upstream_sha256, args.source_sha, args.output)
    if not args.execute:
        require(not any(fields), "actual pins require explicit execute")
        print(json.dumps(pure_contract(), indent=2)); return 0
    require(all(fields), "all actual source, contract, upstream and output pins required")
    for value in (args.contract_sha256, args.upstream_sha256, args.source_sha):
        require(len(value) == 64 and all(c in "0123456789abcdef" for c in value), "exact lowercase SHA required")
    return execute_once(args)


if __name__ == "__main__":
    raise SystemExit(main())

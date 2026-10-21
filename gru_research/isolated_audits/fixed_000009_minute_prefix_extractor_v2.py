"""Prepare or perform one fixed, bounded original-minute prefix acquisition.

V2 preserves V1 bytes and its sealed contract/manifest. The historical contract
script_relative_path remains V1; this is a separately pinned V2 implementation.
The only behavioral changes clamp physical reads and retain interrupt failure.
Default mode prints a contract and does not open market sources. Actual mode
requires explicit contract/source SHA pins. It only copies the 41 exact original
Parquet payloads, without decoding any Parquet or calling a market pipeline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat as stat_module
import sys
import tarfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "gru_research/trading_data/fixed_000009_original_h24_preparation_v1_20261002"
CONTRACT = PREP / "research_execution_contract_v1.json"
CONTRACT_SHA = "9078316ba0e9fb7d95047a16d90d8d9b0d953ab41892a680d60adf4e2b68b0ed"
MANIFEST = PREP / "sealed_41_target_acquisition_manifest_v1.json"
MANIFEST_SHA = "d3634e3a74b9225eb7b111e1d7dde4594d480a3e6fe819085266b901f5cb79a0"
NSPACE = ROOT / "gru_research/trading_data/fixed_000009_original_minute_acquisition_v1_20261002"
COMPRESSED_CAP = 4527158977
DECOMPRESSED_CAP = 32 * 1024**3
MEMBER_CAP = 1100000
SECONDS_CAP = 1800
PAYLOAD_CAP = 2 * 1024**2
TOTAL_PAYLOAD_CAP = 16 * 1024**2
CHUNK = 65536
METADATA_CAP = 4 * 1024**2
DATES = (
    "2024-04-17 2024-04-18 2024-04-19 2024-04-22 2024-04-23 "
    "2024-04-24 2024-04-25 2024-04-26 2024-04-29 2024-04-30 "
    "2024-05-06 2024-05-07 2024-05-08 2024-05-09 2024-05-10 "
    "2024-05-13 2024-05-14 2024-05-15 2024-05-16 2024-05-17 "
    "2024-05-20 2024-05-21 2024-05-22 2024-05-23 2024-05-24 "
    "2024-05-27 2024-05-28 2024-05-29 2024-05-30 2024-05-31 "
    "2024-06-03 2024-06-04 2024-06-05 2024-06-06 2024-06-07 "
    "2024-06-11 2024-06-12 2024-06-13 2024-06-14 2024-06-17 2024-06-18"
).split()
TARGETS = tuple("A股分钟k线数据/" + d.replace("-", "") + "/000009.parquet" for d in DATES)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def now():
    return datetime.now(timezone.utc).isoformat()


def small_identity(path, cap=4 * 1024**2):
    path = Path(path)
    s = path.lstat()
    require(stat_module.S_ISREG(s.st_mode) and s.st_size <= cap, "small guard is not regular or exceeds cap: " + str(path))
    data = path.read_bytes()
    require(len(data) == s.st_size, "small guard changed while reading: " + str(path))
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def source_stat(path):
    path = Path(path)
    s = path.lstat()
    require(stat_module.S_ISREG(s.st_mode), "source path must be a regular file, not a symlink: " + str(path))
    return {"path": str(path), "st_size": s.st_size, "st_mtime_ns": s.st_mtime_ns,
            "st_dev": s.st_dev, "st_ino": s.st_ino, "st_mode": s.st_mode}


def exclusive_json(path, value):
    body = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    require(len(body) <= METADATA_CAP, "output metadata exceeds bounded cap")
    with Path(path).open("xb") as handle:
        handle.write(body)
    return {"path": str(path), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def pure_contract():
    return {"schema_version": 1, "status": "PREPARED_ONLY_NOT_EXECUTED",
            "contract_path": str(CONTRACT), "contract_sha256": CONTRACT_SHA,
            "manifest_path": str(MANIFEST), "manifest_sha256": MANIFEST_SHA,
            "fixed_symbol": "000009.SZ", "fixed_target_count": 41,
            "source_parts_in_order": ["A股分钟k线数据.tar.gz." + str(i).zfill(3) for i in range(5)],
            "budgets": {"compressed_bytes": COMPRESSED_CAP, "decompressed_returned_to_tar_bytes": DECOMPRESSED_CAP,
                        "logical_TarInfo_entries": MEMBER_CAP, "elapsed_chunk_check_seconds": SECONDS_CAP,
                        "target_payload_bytes_each": PAYLOAD_CAP, "target_payload_bytes_total": TOTAL_PAYLOAD_CAP,
                        "read_chunk_bytes": CHUNK, "tar_extended_header_bytes_each": CHUNK},
            "implementation_revision": 2,
            "original_v1_source_sha256": "482d3e92d5c2f2f3770fbe3952f4ca9d71bdf96d7d3d3d08a9e7c41d08c5e3bf",
            "historical_contract_script_relative_path": "gru_research/isolated_audits/fixed_000009_minute_prefix_extractor_v1.py",
            "decompressed_cap_is_runtime_budget_not_observed_total": True,
            "elapsed_check_is_not_OS_hard_deadline": True,
            "output_namespace": str(NSPACE), "append_only_single_attempt": True,
            "default_mode_source_or_market_body_reads": 0,
            "actual_requires": ["--execute", "--contract-sha", "--source-sha"],
            "other_payloads_are_read_and_inflated_but_not_decoded_or_saved": True,
            "no_Parquet_decoder_pipeline_numpy_model_scores_network_or_training": True,
            "new_training_fits": 0, "full_PLAN_complete": False}


class Progress:
    def __init__(self, output):
        self.started = time.monotonic()
        self.last_report = self.started
        self.compressed = 0
        self.decompressed = 0
        self.members = 0
        self.last_member = None
        self.found = 0
        self.saved = 0
        self.progress_bytes = 0
        self.log = (output / "actual_progress_v1.jsonl").open("xb")

    def check(self, force=False):
        elapsed = time.monotonic() - self.started
        require(elapsed <= SECONDS_CAP, "1800s elapsed chunk-check budget exceeded; no automatic retry")
        require(self.compressed <= COMPRESSED_CAP, "compressed byte budget exceeded")
        require(self.decompressed <= DECOMPRESSED_CAP, "decompressed-returned byte budget exceeded")
        require(self.members <= MEMBER_CAP, "logical TarInfo budget exceeded")
        require(self.saved <= TOTAL_PAYLOAD_CAP, "aggregate target payload budget exceeded")
        if force or time.monotonic() - self.last_report >= 10:
            self.report()

    def report(self):
        item = {"observed_utc": now(), "elapsed_seconds": time.monotonic() - self.started,
                "compressed_bytes_returned_from_parts": self.compressed,
                "decompressed_bytes_returned_to_tar": self.decompressed,
                "logical_TarInfo_entries": self.members, "last_member": self.last_member,
                "targets_fully_saved": self.found, "target_payload_bytes_written": self.saved}
        body = (json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        require(self.progress_bytes + len(body) <= METADATA_CAP, "progress metadata budget exceeded")
        self.log.write(body)
        self.log.flush()
        self.progress_bytes += len(body)
        print(body.decode("utf-8").rstrip("\n"), flush=True)
        self.last_report = time.monotonic()


class SplitPrefix(io.RawIOBase):
    """One forward-only physical read across the five exact source volumes."""
    def __init__(self, parts, progress):
        self.parts = parts
        self.progress = progress
        self.index = 0
        self.handle = None
        self.per_part_bytes = [0] * len(parts)
        self.per_part_sha = [hashlib.sha256() for _ in parts]
        self.concatenated_sha = hashlib.sha256()
        self._open_current()

    def _open_current(self):
        part = self.parts[self.index]
        require(source_stat(part["path"]) == part["actual_STAT"], "source STAT changed immediately before open")
        self.handle = Path(part["path"]).open("rb")
        s = os.fstat(self.handle.fileno())
        expected = part["actual_STAT"]
        if (s.st_size, s.st_mtime_ns, s.st_dev, s.st_ino, s.st_mode) != tuple(
                expected[k] for k in ("st_size", "st_mtime_ns", "st_dev", "st_ino", "st_mode")):
            self.handle.close()
            raise ValueError("opened source descriptor differs from expected STAT")

    def readable(self):
        return True

    def readinto(self, buffer):
        self.progress.check()
        require(len(buffer) > 0, "compressed caller buffer must be positive; physical read stays bounded64KiB")
        while True:
            remaining = COMPRESSED_CAP - self.progress.compressed
            if remaining == 0:
                return 0
            count = self.handle.readinto(memoryview(buffer)[:min(len(buffer), CHUNK, remaining)])
            if count:
                block = memoryview(buffer)[:count]
                self.per_part_bytes[self.index] += count
                self.per_part_sha[self.index].update(block)
                self.concatenated_sha.update(block)
                self.progress.compressed += count
                self.progress.check()
                return count
            require(self.per_part_bytes[self.index] == self.parts[self.index]["size_bytes"], "source volume ended prematurely")
            if self.index == len(self.parts) - 1:
                return 0
            self.handle.close()
            self.index += 1
            self._open_current()

    def close(self):
        if self.handle is not None and not self.handle.closed:
            self.handle.close()
        super().close()

    def prefix_evidence(self):
        rows = []
        for part, count, digest in zip(self.parts, self.per_part_bytes, self.per_part_sha, strict=True):
            rows.append({"path": part["path"], "bytes_physically_read": count,
                         "actual_prefix_sha256": digest.hexdigest() if count else None,
                         "prefix_is_entire_volume": count == part["size_bytes"],
                         "inherited_whole_sha256": part["sha256"]})
        return {"actual_concatenated_compressed_prefix_bytes": self.progress.compressed,
                "actual_concatenated_compressed_prefix_sha256": self.concatenated_sha.hexdigest(),
                "per_part": rows, "includes_buffer_read_ahead": True,
                "not_a_fresh_whole_five_part_source_verification": True}


class CountedInflated:
    def __init__(self, zipped, progress):
        self.zipped = zipped
        self.progress = progress

    def read(self, size):
        self.progress.check()
        require(0 <= size <= CHUNK, "unbounded or oversize decompressed read forbidden")
        if size == 0:
            return b""
        remaining = DECOMPRESSED_CAP - self.progress.decompressed
        require(remaining > 0, "decompressed byte cap reached before all41 targets")
        body = self.zipped.read(min(size, remaining))
        self.progress.decompressed += len(body)
        self.progress.check()
        return body


class BoundedTarInfo(tarfile.TarInfo):
    """Bound metadata expansion too; none of these methods decodes Parquet."""
    def _bounded_extended_header(self, archive, method):
        require(0 <= self.size <= CHUNK, "extended tar metadata header exceeds64KiB budget")
        depth = getattr(archive, "_fixed41_extended_depth", 0) + 1
        require(depth <= 16, "nested extended tar metadata exceeds16-header budget")
        archive._fixed41_extended_depth = depth
        try:
            return method(archive)
        finally:
            archive._fixed41_extended_depth = depth - 1

    def _proc_pax(self, archive):
        result = self._bounded_extended_header(archive, super()._proc_pax)
        require(not any(k.startswith("GNU.sparse") for k in result.pax_headers), "sparse archive entries outside this contract")
        return result

    def _proc_gnulong(self, archive):
        return self._bounded_extended_header(archive, super()._proc_gnulong)

    def _proc_sparse(self, archive):
        raise ValueError("sparse archive entries outside this fixed regular payload contract")

    def _proc_gnusparse_00(self, next_member, raw_headers):
        raise ValueError("PAX GNU sparse maps outside this fixed regular payload contract")

    def _proc_gnusparse_01(self, next_member, pax_headers):
        raise ValueError("PAX GNU sparse maps outside this fixed regular payload contract")

    def _proc_gnusparse_10(self, next_member, pax_headers, archive):
        raise ValueError("PAX GNU sparse maps outside this fixed regular payload contract")


def checked_inputs():
    contract_identity = small_identity(CONTRACT)
    manifest_identity = small_identity(MANIFEST)
    require(contract_identity["sha256"] == CONTRACT_SHA, "sealed contract changed")
    require(manifest_identity["sha256"] == MANIFEST_SHA, "sealed positive manifest changed")
    contract = json.loads(CONTRACT.read_bytes())
    manifest = json.loads(MANIFEST.read_bytes())
    require(contract["exact_target_manifest"] == manifest_identity, "contract does not bind exact manifest identity")
    require(manifest["target_count"] == 41 and manifest["fixed_symbol"] == "000009.SZ", "positive41 target scope differs")
    roster = manifest["target_roster"]
    require([r["date"] for r in roster] == DATES and
            tuple(r["expected_archive_member"] for r in roster) == TARGETS, "fixed41 date/member order differs")
    require(len(set(TARGETS)) == 41 and all(r["symbol"] == "000009.SZ" for r in roster), "invalid or duplicate target roster")
    require([r["expected_output_basename"] for r in roster] ==
            [d.replace("-", "") + "_000009.parquet" for d in DATES], "target output roster differs")
    parts = manifest["source_parts"]
    require(len(parts) == 5 and sum(v["size_bytes"] for v in parts) == COMPRESSED_CAP, "five-part exact size differs")
    require([v["path"] for v in parts] == [str(ROOT / ("A股分钟k线数据.tar.gz." + str(i).zfill(3))) for i in range(5)], "source path order differs")
    require(contract["acquisition_plan"]["fixed_output_namespace"] == str(NSPACE), "fixed output namespace differs")
    actual_parts = {v["path"]: source_stat(v["path"]) for v in parts}
    require(actual_parts == {v["path"]: v["actual_STAT"] for v in parts}, "source STAT differs from sealed preparation")
    guards = dict(contract["source_small_before"])
    for identity in (contract_identity, manifest_identity, contract["existing_mask_witness"], contract["source_index_evidence"]):
        if identity["path"] in guards:
            require(guards[identity["path"]] == identity, "conflicting small guard identity")
        guards[identity["path"]] = identity
    before = {p: small_identity(p) for p in guards}
    require(before == guards, "one of the sealed source/metadata small SHA guards differs")
    return contract, manifest, parts, actual_parts, before


def run_once(args):
    require(args.contract_sha == CONTRACT_SHA, "actual mode needs the exact out-of-band contract SHA")
    source_identity = small_identity(Path(__file__).resolve())
    require(args.source_sha and source_identity["sha256"] == args.source_sha, "actual mode needs the exact out-of-band collector source SHA")
    # An existing namespace rejects every second attempt, including failed ones.
    NSPACE.mkdir(exist_ok=False)
    marker = exclusive_json(NSPACE / "EXECUTION_STARTED_v1.json", {
        "schema_version": 1, "started_utc": now(), "source": source_identity,
        "contract_sha256": CONTRACT_SHA, "fixed_targets": list(TARGETS),
        "source_prefix_pass_limit": 1, "automatic_retry": False})
    output = NSPACE / "attempt1"
    output.mkdir(exist_ok=False)
    targets_dir = output / "targets"
    targets_dir.mkdir(exist_ok=False)
    progress = Progress(output)
    raw = None
    source_before = {}
    small_before = {}
    observed = {}
    failure = None
    result = {"schema_version": 1, "status": "RUNNING_FIXED41_SOURCE_ACQUISITION",
              "started_utc": now(), "execution_marker": marker, "source": source_identity,
              "contract_sha256": CONTRACT_SHA, "target_manifest_sha256": MANIFEST_SHA,
              "implementation_revision": 2,
              "original_v1_source_sha256": "482d3e92d5c2f2f3770fbe3952f4ca9d71bdf96d7d3d3d08a9e7c41d08c5e3bf",
              "historical_contract_script_relative_path": "gru_research/isolated_audits/fixed_000009_minute_prefix_extractor_v1.py",
              "source_prefix_pass_count": 1, "automatic_retry": False,
              "fixed_symbol": "000009.SZ", "target_count": 41,
              "other_Parquet_payloads_decoded_or_saved": 0,
              "target_Parquet_payloads_decoded": 0,
              "original_pipeline_numpy_arrow_models_scores_training_network_called": False,
              "whole_source_SHA_recomputed_in_preparation_or_extra_pass": False,
              "full_PLAN_complete": False, "economic_comparison_eligible": False}
    try:
        contract, manifest, parts, source_before, small_before = checked_inputs()
        result["budgets"] = contract["acquisition_budgets"]
        by_member = {r["expected_archive_member"]: r for r in manifest["target_roster"]}
        raw = SplitPrefix(parts, progress)
        progress.check(force=True)
        with io.BufferedReader(raw, buffer_size=CHUNK) as compressed:
            with gzip.GzipFile(fileobj=compressed, mode="rb") as zipped:
                counted = CountedInflated(zipped, progress)
                with tarfile.open(fileobj=counted, mode="r|", bufsize=CHUNK, tarinfo=BoundedTarInfo) as archive:
                    for member in archive:
                        progress.members += 1
                        require(len(member.name.encode("utf-8")) <= CHUNK, "archive member name exceeds metadata cap")
                        progress.last_member = member.name
                        progress.check()
                        # Avoid retaining up to a million TarInfo objects. No links
                        # are resolved and only the current exact member is copied.
                        archive.members.clear()
                        if member.name not in by_member:
                            continue
                        require(member.name not in observed, "duplicate exact target encountered before41 complete")
                        require(member.isfile() and not member.issparse() and 0 < member.size <= PAYLOAD_CAP, "target is not a bounded non-sparse regular payload")
                        require(progress.saved + member.size <= TOTAL_PAYLOAD_CAP, "41 target payload aggregate would exceed16MiB")
                        row = by_member[member.name]
                        path = targets_dir / row["expected_output_basename"]
                        handle = archive.extractfile(member)
                        require(handle is not None, "exact target regular payload unreadable")
                        digest = hashlib.sha256()
                        copied = 0
                        with handle, path.open("xb") as destination:
                            while copied < member.size:
                                progress.check()
                                block = handle.read(min(CHUNK, member.size - copied))
                                require(block, "target payload ended before declared size")
                                destination.write(block)
                                digest.update(block)
                                copied += len(block)
                                progress.saved += len(block)
                                progress.check()
                        require(copied == member.size, "target payload byte count differs from header")
                        observed[member.name] = {"date": row["date"], "symbol": row["symbol"],
                            "actual_header_name": member.name, "actual_tar_type_hex": member.type.hex(),
                            "actual_tar_header_offset": member.offset, "actual_tar_data_offset": member.offset_data,
                            "declared_payload_bytes": member.size, "payload": {"path": str(path), "bytes": copied, "sha256": digest.hexdigest()},
                            "payload_STAT_after_save": source_stat(path), "decoded": False}
                        progress.found = len(observed)
                        progress.check(force=True)
                        if progress.found == 41:
                            break
        require(set(observed) == set(TARGETS), "not all41 exact original targets found before budget/EOF; no automatic retry")
        result["status"] = "PASS_FIXED41_ORIGINAL_PAYLOAD_ACQUISITION_ONLY_VALIDATION_PENDING"
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        result["status"] = ("FAIL_INTERRUPTED_FIXED41_SOURCE_ACQUISITION_NO_AUTOMATIC_RETRY"
                            if isinstance(exc, KeyboardInterrupt) else "FAIL_FIXED41_SOURCE_ACQUISITION_NO_AUTOMATIC_RETRY")
        result["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        trace = traceback.format_exc().encode("utf-8")
        with (output / "actual_failure_traceback_v1.txt").open("xb") as handle:
            handle.write(trace[:METADATA_CAP])
        result["traceback_truncated_at_metadata_cap"] = len(trace) > METADATA_CAP
    finally:
        if raw is not None:
            raw.close()
            result["actual_compressed_prefix_evidence"] = raw.prefix_evidence()
        result["actual_decompressed_bytes_returned_to_tar"] = progress.decompressed
        result["actual_logical_TarInfo_entries"] = progress.members
        result["last_header_observed"] = progress.last_member
        result["actual_target_payload_bytes_written"] = progress.saved
        result["actual_targets_fully_saved"] = len(observed)
        result["targets_in_fixed_date_order"] = [observed[v] for v in TARGETS if v in observed]
        result["missing_exact_targets"] = [v for v in TARGETS if v not in observed]
        result["source_parts_actual_STAT_before"] = source_before
        result["small_SHA_before"] = small_before
        try:
            progress.check()
            source_after = {p: source_stat(p) for p in source_before}
            small_after = {p: small_identity(p) for p in small_before}
            collector_after = small_identity(Path(__file__).resolve())
            targets_after = {v["payload"]["path"]: source_stat(v["payload"]["path"]) for v in observed.values()}
            result["source_parts_actual_STAT_after"] = source_after
            result["small_SHA_after"] = small_after
            result["collector_source_after"] = collector_after
            result["saved_target_payload_STAT_after_all41_or_failure"] = targets_after
            require(source_before and small_before and source_after == source_before and small_after == small_before and collector_after == source_identity,
                    "sourceSTAT or closed smallSHA before/after guard failed or preflight incomplete")
            require(targets_after == {v["payload"]["path"]: v["payload_STAT_after_save"] for v in observed.values()},
                    "newly saved target payload STAT changed while locating the remaining targets")
            progress.check()
            result["source_STAT_and_small_SHA_before_after_unchanged"] = True
        except (Exception, KeyboardInterrupt) as exc:
            result["status"] = "FAIL_FIXED41_SOURCE_ACQUISITION_NO_AUTOMATIC_RETRY"
            result["guard_failure"] = {"type": type(exc).__name__, "reason": str(exc)}
            result["source_STAT_and_small_SHA_before_after_unchanged"] = False
            failure = failure or exc
        result["actual_elapsed_seconds"] = time.monotonic() - progress.started
        result["finished_utc"] = now()
        result["full_gzip_EOF_CRC_scope"] = "No explicit whole-source EOF pass requested; a naturally reached EOF on a missing-target failure may have exercised gzip CRC. Success stops at41 targets, including bounded read-ahead."
        result["read_scope"] = "Other compressed and inflated payload bytes necessarily pass through the stream to locate targets; they are not Parquet-decoded or saved. Logical header/PAX metadata is parsed. Whole-source hashes remain inherited; fresh hashes describe physically read compressed prefix and copied exact target payloads."
        result["future_duplicate_target_after_stop"] = "NOT_CHECKED: this fixed prefix stops once41 distinct target members are saved"
        result["raw_240_schema_units_clock_adjustment_and_H24_validation"] = "NOT_PERFORMED_BY_THIS_ACQUISITION_ONLY_PROGRAM"
        progress.log.close()
        receipt = exclusive_json(output / "fixed41_original_payload_acquisition_receipt_v1.json", result)
        print(json.dumps({"status": result["status"], "receipt": receipt, "actual_targets_fully_saved": len(observed),
                          "new_training_fits": 0, "full_PLAN_complete": False}, ensure_ascii=False), flush=True)
    return 1 if failure else 0


def main():
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--contract-sha")
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    if not args.execute:
        require(args.contract_sha is None and args.source_sha is None, "actual pin arguments require explicit --execute")
        print(json.dumps(pure_contract(), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""One first-volume, bounded original-member extraction. No whole archive scan."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import io
import json
import sys
import tarfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gru_research/trading_data/r2_causal_minute_check_20261002"
sys.path.insert(0, str(ROOT / "gru_research/.deps"))
import numpy as np
import pyarrow.parquet as pq

MAX_BYTES = 32 * 1024**2
MAX_ROWS = 200000
MINUTE = 60 * 10**9
OFFSETS = np.r_[np.arange(570, 690), np.arange(780, 900)]


def ident(path):
    p = Path(path)
    b = p.read_bytes()
    return {"path": str(p), "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}


class BoundExceeded(Exception):
    pass


class BoundedGzip:
    def __init__(self, handle):
        self.handle = handle
        self.z = zlib.decompressobj(31)
        self.pending = b""
        self.compressed_bytes = 0
        self.compressed_hash = hashlib.sha256()
        self.uncompressed_bytes = 0
        self.uncompressed_hash = hashlib.sha256()

    def read(self, size):
        if size < 0:
            raise BoundExceeded("unbounded read prohibited")
        if self.uncompressed_bytes + size > MAX_BYTES:
            raise BoundExceeded("32MiB decompressed byte budget")
        out = bytearray()
        while len(out) < size and not self.z.eof:
            if not self.pending:
                self.pending = self.handle.read(65536)
                self.compressed_bytes += len(self.pending)
                self.compressed_hash.update(self.pending)
                if not self.pending:
                    raise BoundExceeded("first volume ended; no next volume opened")
            decoded = self.z.decompress(self.pending, size - len(out))
            self.pending = self.z.unconsumed_tail
            out.extend(decoded)
        body = bytes(out)
        self.uncompressed_bytes += len(body)
        self.uncompressed_hash.update(body)
        return body


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    receipt_path = OUT / "original_sample_extraction_receipt_v1.json"
    if receipt_path.exists():
        raise ValueError("append-only receipt already exists")
    fingerprints = ROOT / "gru_research/artifacts/minute_source_fingerprints.json"
    declared = json.loads(fingerprints.read_bytes())["parts"][0]
    path = ROOT / declared["name"]
    if path.name != "A股分钟k线数据.tar.gz.000" or path.stat().st_size != declared["size_bytes"]:
        raise ValueError("frozen first source part size/name differs")
    before = path.stat()
    rows_read, attempts, selected, failure = 0, [], None, None
    with path.open("rb") as handle:
        source = BoundedGzip(handle)
        try:
            with tarfile.open(fileobj=source, mode="r|") as archive:
                for member in archive:
                    parts = member.name.split("/")
                    if not member.isfile() or len(parts) != 3 or not parts[-1].endswith(".parquet"):
                        continue
                    if len(parts[1]) != 8 or not parts[1].isdigit() or not parts[-1][:-8].isdigit():
                        continue
                    if member.size > MAX_BYTES - source.uncompressed_bytes:
                        raise BoundExceeded("member would exceed decompressed budget")
                    body = archive.extractfile(member).read()
                    if len(body) != member.size:
                        raise ValueError("short member")
                    parquet = pq.ParquetFile(io.BytesIO(body))
                    n = parquet.metadata.num_rows
                    rows_read += n
                    if rows_read > MAX_ROWS:
                        raise BoundExceeded("200000 row budget")
                    required = ["股票代码", "datetime", "datetime_nano", "open", "high", "low", "close", "volume", "qfq_ratio", "qfq_open", "qfq_high", "qfq_low", "qfq_close"]
                    trial = {"member": member.name, "rows": n, "tar_header_offset": member.offset,
                             "tar_data_offset": member.offset_data, "member_bytes": len(body), "member_sha256": hashlib.sha256(body).hexdigest()}
                    attempts.append(trial)
                    if n != 240 or not set(required) <= set(parquet.schema_arrow.names):
                        trial["complete_regular_stockday"] = False
                        continue
                    table = parquet.read(columns=required)
                    a = {k: table[k].to_numpy(zero_copy_only=False) for k in required}
                    rawday, code = parts[1], parts[-1][:-8]
                    day = rawday[:4] + "-" + rawday[4:6] + "-" + rawday[6:]
                    midnight = np.datetime64(day, "ns").astype(np.int64) - 8 * 3600 * 10**9
                    ns = a["datetime_nano"].astype(np.int64)
                    texts = np.asarray(a["datetime"]).astype("datetime64[ns]").astype(np.int64) - 8 * 3600 * 10**9
                    expected = midnight + OFFSETS * MINUTE
                    raw = np.column_stack([a[x] for x in ("open", "high", "low", "close")]).astype(float)
                    adjusted = np.column_stack([a[x] for x in ("qfq_open", "qfq_high", "qfq_low", "qfq_close")]).astype(float)
                    factor = a["qfq_ratio"].astype(float)
                    v = a["volume"].astype(float)
                    healthy = (np.array_equal(ns, expected) and np.array_equal(ns, texts)
                               and np.all(np.asarray(a["股票代码"]).astype(str) == code)
                               and np.isfinite(raw).all() and (raw > 0).all() and np.isfinite(adjusted).all()
                               and (adjusted > 0).all() and np.isfinite(v).all() and (v >= 0).all()
                               and (raw[:, 1] >= raw[:, [0, 2, 3]].max(1) - 1e-8).all()
                               and (raw[:, 2] <= raw[:, [0, 1, 3]].min(1) + 1e-8).all()
                               and np.isfinite(factor).all() and (factor > 0).all()
                               and np.allclose(factor, factor[0], rtol=1e-7, atol=1e-10)
                               and np.allclose(adjusted, raw * factor[:, None], rtol=1e-7, atol=1e-6)
                               and all(v[s:s+60].sum() > 0 for s in (0, 60, 120, 180)))
                    trial["complete_regular_stockday"] = bool(healthy)
                    if not healthy:
                        continue
                    sample_path = OUT / (rawday + "_" + code + "_original.parquet")
                    with sample_path.open("xb") as stream:
                        stream.write(body)
                    selected = dict(trial, day=day, symbol=code + (".SH" if code.startswith(("6", "9")) else ".SZ"),
                                    original_payload=ident(sample_path),
                                    regular_minute_offsets=OFFSETS.tolist(),
                                    row_clock="Physical source row order equals exact regular left/start boundaries; original datetime Shanghai minus8h equals datetime_nano UTC ns, both bound by immutable sample bytes.",
                                    row_completion_clock_basis="Historical bar-completion witness is original trusted start+1minute. This is not an independently observed feed-arrival clock.",
                                    rows_strictly_in_original_chronological_order=True,
                                    complete_240_exact_regular_minutes=True,
                                    source_raw_qfq_relation_verified=True,
                                    original_column_schema=[{"name": f.name, "type": str(f.type)} for f in parquet.schema_arrow])
                    break
        except BoundExceeded as exc:
            failure = str(exc)
        compressed = source.compressed_bytes
        uncompressed = source.uncompressed_bytes
        compressed_sha = source.compressed_hash.hexdigest()
        uncompressed_sha = source.uncompressed_hash.hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("source part stat changed")
    result = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "PASS_ORIGINAL_BOUNDED_COMPLETE_STOCKDAY_EXTRACTED" if selected else "PENDING_NO_COMPLETE_STOCKDAY_IN_BOUND",
              "source_part": {"path": str(path), "file_bytes": before.st_size, "mtime_ns": before.st_mtime_ns,
                              "whole_sha256_inherited": declared["sha256"], "whole_sha256_recomputed": False,
                              "original_fingerprint_receipt": ident(fingerprints)},
              "compressed_prefix_bytes_actually_read": compressed, "compressed_prefix_sha256": compressed_sha,
              "decompressed_prefix_bytes_actually_returned": uncompressed, "decompressed_prefix_sha256": uncompressed_sha,
              "maximum_decompressed_bytes": MAX_BYTES, "maximum_rows": MAX_ROWS, "parquet_rows_read": rows_read,
              "regular_members_examined": attempts, "selected": selected, "bounded_stop_reason": failure,
              "cross_volume_read": False, "gzip_end_crc_or_whole_archive_validation_performed": False,
              "first_complete_original_stockday_then_stopped": bool(selected),
              "source_part_stat_unchanged_before_after": True, "extractor": ident(Path(__file__)),
              "labels_scores_weights_network_Torch_tests_or_frozen_source_writes": False,
              "full_PLAN_complete": False}
    with receipt_path.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"receipt": ident(receipt_path), "sample": selected["original_payload"] if selected else None,
                      "stockday": selected["day"] if selected else None, "symbol": selected["symbol"] if selected else None,
                      "compressed_bytes": compressed, "decompressed_bytes": uncompressed, "rows": rows_read}, ensure_ascii=False))


if __name__ == "__main__":
    main()

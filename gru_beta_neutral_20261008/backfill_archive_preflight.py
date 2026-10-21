"""Inspect existing archived sources without admitting them to the frozen panel."""
import csv
import hashlib
import io
import json
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/gru_beta_neutral_20261008"
TARGETS = {"sz000034", "sz000403", "sz000088", "sz000657"}
SOURCES = ["2000-2025年a股60min", "2000-2025年a股全部日k", "2026年a股60min"]


def inspect(folder):
    ref = json.loads((ROOT / folder / "ARCHIVED_TO_ICLOUD.json").read_text())
    path = Path(ref["archive"])
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    actual_sha = digest.hexdigest()
    if actual_sha != ref["archive_sha256"]:
        raise ValueError(f"Archive checksum mismatch: {path.name}")
    samples = []
    count = 0
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            count += 1
            filename = Path(member.name).name
            if filename.split(".")[0] not in TARGETS:
                continue
            data = archive.extractfile(member).read()
            dest = OUT / "backfill_source_samples" / path.name.removesuffix(".tar.gz") / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            sample = {"archive_member": member.name, "sample_path": str(dest),
                      "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if filename.endswith(".csv"):
                try:
                    text = data.decode("utf-8-sig")
                    encoding = "utf-8-sig"
                except UnicodeDecodeError:
                    text = data.decode("gb18030")
                    encoding = "gb18030"
                rows = list(csv.reader(io.StringIO(text)))
                sample.update(encoding=encoding, header=rows[0] if rows else [],
                              rows_excluding_header=max(0, len(rows)-1),
                              first_rows=rows[1:3], last_rows=rows[-2:])
                records = list(csv.DictReader(io.StringIO(text)))
                dates = [row["日期"] for row in records]
                sample.update(date_range=[min(dates), max(dates)], dates_count=len(set(dates)))
                if "时间" in sample["header"]:
                    sample["time_slots"] = sorted({row["时间"] for row in records})
            else:
                sample["inspection_limit"] = "Nested 7z saved intact; CSV contents not yet inspected."
            samples.append(sample)
    return {"source": folder, "archive": str(path), "archive_sha256": actual_sha,
            "checksum_verified": True, "file_count": count,
            "expected_file_count": ref["file_count"],
            "samples": samples, "admitted_to_panel": False}


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources = list(pool.map(inspect, SOURCES))
    hourly_samples = next(s["samples"] for s in sources if s["source"] == "2026年a股60min")
    receipt = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "status": "EXISTING_ARCHIVE_SAMPLES_ONLY_NOT_BACKFILLED",
               "sources": sources,
               "limits": ["Daily and hourly sources cannot reconstruct original one-minute execution bars.",
                          "Date coverage, price adjustment, timestamps and trading-status consistency require validation before use."]}
    receipt["sample_findings"] = {
        "2026_sample_max_date": max(s["date_range"][1] for s in hourly_samples),
        "2026_hourly_sample_slots": sorted({t for s in hourly_samples for t in s["time_slots"]}),
        "one_minute_source_present_in_these_archives": False,
        "hourly_grid_compatibility": "Sampled legacy 2026 source has five slots; current panel has four one-hour slots. Direct concatenation is invalid.",
        "daily_adjustment_compatibility": "Not certified; validate overlap with frozen iFinD raw/factor data before admission.",
        "historical_7z_csv_contents_inspected": False,
        "new_panel_built": False,
    }
    dest = OUT / "backfill_source_preflight.json"
    dest.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({"receipt": str(dest), "archives_checked": len(sources),
                      "samples_saved": sum(len(s["samples"]) for s in sources),
                      "panel_modified": False}, ensure_ascii=False))

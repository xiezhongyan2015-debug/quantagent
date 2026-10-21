#!/usr/bin/env python3
"""Read-only local availability inventory; never downloads or evaluates returns.

Reads CSV boundary dates and existing audit evidence. Optional pyarrow permits
direct checks of date/symbol columns in already-downloaded Parquet files.
"""
import collections
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
OUT = REPO / "data" / "local_inventory_20260921"


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    return json.loads((WORKSPACE / path).read_text())


def boundaries(path):
    with path.open("rb") as fh:
        header = fh.readline().decode("utf-8-sig").strip()
        first = fh.readline().decode("utf-8-sig").strip()
        fh.seek(max(0, path.stat().st_size - 8192))
        tail = fh.read().decode("utf-8", errors="replace").splitlines()
    last = next((line for line in reversed(tail) if line.strip()), "")
    columns = next(csv.reader([header]))
    idx = columns.index("日期")
    def get_date(line):
        row = next(csv.reader([line]))
        value = row[idx] if len(row) > idx else ""
        try:
            dt.date.fromisoformat(value)
            return value
        except ValueError:
            return "INVALID:" + value
    return columns, get_date(first), get_date(last)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    origin = WORKSPACE / "research_v3/derived/data_audit/data_inventory.csv"
    rows = list(csv.DictReader(origin.open(encoding="utf-8-sig")))
    grouped = collections.defaultdict(list)
    verification, samples = [], []
    for row in rows:
        path = WORKSPACE / row["source_path"]
        rec = {"source": row["source"], "path": row["source_path"], "exists": path.exists()}
        if not path.exists():
            verification.append(rec)
            continue
        rec.update(bytes=path.stat().st_size, size_matches_previous=path.stat().st_size == int(row["file_size"]))
        if path.suffix == ".csv":
            columns, first, last = boundaries(path)
            rec.update(columns=columns, first_date=first, last_date=last)
        else:
            rec.update(columns=row["columns"].split("|"), first_date=row["start_ts"][:10], last_date=row["end_ts"][:10],
                       boundary_source="2026-09-16 inventory; archive contents not reread")
        rec["rows_previous_inventory"] = row["rows"]
        grouped[row["source"]].append(rec)
        verification.append(rec)
    for source, group in grouped.items():
        selected = sorted(group, key=lambda r: hashlib.sha256(r["path"].encode()).hexdigest())[:3]
        old = {r["source_path"]: r["sha256"] for r in rows if r["source"] == source}
        for r in selected:
            digest = hashlib.sha256((WORKSPACE / r["path"]).read_bytes()).hexdigest()
            samples.append({"path": r["path"], "sha256_matches_previous": digest == old[r["path"]]})
    summaries = []
    for source, group in sorted(grouped.items()):
        dates = [r for r in group if r["last_date"][:4].isdigit()]
        summaries.append({"source": source, "files": len(group), "bytes": sum(r["bytes"] for r in group),
                          "earliest_boundary": min((r["first_date"] for r in dates), default=None),
                          "latest_boundary": max((r["last_date"] for r in dates), default=None),
                          "dates_known_files": len(dates),
                          "latest_counts": dict(collections.Counter(r["last_date"] for r in group).most_common(5)),
                          "fields": sorted(set(c for r in group for c in r["columns"])),
                          "previous_known_rows": sum(int(r["rows_previous_inventory"]) for r in group if r["rows_previous_inventory"].isdigit())})
    write_json("raw_sources.json", summaries)
    with (OUT / "raw_file_inventory.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        names = ["source", "path", "exists", "bytes", "size_matches_previous", "first_date", "last_date", "rows_previous_inventory"]
        writer = csv.DictWriter(fh, fieldnames=names, extrasaction="ignore")
        writer.writeheader(); writer.writerows(verification)
    latest_dir = WORKSPACE / "acquired_data/current_prediction_20260920/normalized_v3"
    coverage = list(csv.DictReader((latest_dir / "coverage.csv").open()))
    missing = [{"symbol": r["symbol"], "daily_missing": not int(r["daily_rows"] or 0),
                "m60_missing": not int(r["m60_rows"] or 0), "daily_last": r["daily_last"], "m60_last": r["m60_last"]}
               for r in coverage if not int(r["daily_rows"] or 0) or not int(r["m60_rows"] or 0)]
    with (OUT / "latest_missing_symbols.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(missing[0]) if missing else ["symbol"])
        writer.writeheader(); writer.writerows(missing)
    parquet_checks = []
    try:
        import pyarrow.parquet as pq
        for relative in ["acquired_data/current_prediction_20260920/normalized_v3/daily_native.parquet",
                         "acquired_data/current_prediction_20260920/normalized_v3/m60_native.parquet",
                         "acquired_data/latest_preparation_20260916_full/normalized/current_raw_daily.parquet",
                         "acquired_data/pit_round5_alias_v2/reconstructed_membership_DEV.parquet"]:
            p = WORKSPACE / relative
            pf = pq.ParquetFile(p)
            cols = [c for c in ("symbol", "date", "timestamp") if c in pf.schema_arrow.names]
            df = pf.read(columns=cols).to_pandas()
            key = ["symbol", "timestamp" if "timestamp" in cols else "date"]
            parquet_checks.append({"path": relative, "rows": pf.metadata.num_rows, "fields": pf.schema_arrow.names,
                                   "symbols": int(df.symbol.nunique()), "date_min": str(df.date.min()),
                                   "date_max": str(df.date.max()), "duplicate_keys": int(df.duplicated(key).sum())})
    except ImportError:
        parquet_checks.append({"status": "pyarrow unavailable; use previous audit evidence"})
    latest = read_json("acquired_data/current_prediction_20260920/normalized_v3/audit.json")
    evidence = {
        "inventory_created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": "current project directory; raw boundary metadata and existing acquisition evidence; no return/label calculations",
        "raw_files": len(rows), "missing_files": [r for r in verification if not r["exists"]],
        "size_mismatches": [r["path"] for r in verification if r.get("size_matches_previous") is False],
        "sample_hash_checks": samples, "parquet_checks": parquet_checks,
        "latest_500": {k: latest[k] for k in ["member_count", "daily_securities", "hourly_securities", "daily_rows", "hourly_rows",
                    "coverage_counts", "cross_frequency_mismatch_days", "cross_frequency_mismatch_securities", "m60_model_input_approved"]},
        "latest_missing_daily": sum(not int(r["daily_rows"] or 0) for r in coverage),
        "latest_missing_hourly": sum(not int(r["m60_rows"] or 0) for r in coverage),
        "history_raw": read_json("research_v3/reports/data_round2/full_raw_DEV_summary.json"),
        "corporate_action_batch": read_json("research_v3/reports/data_round2/full_action_batch_manifest.json"),
        "limits": ["Boundary dates do not establish full coverage or valid numeric values",
                   "27 archive files have prior date metadata; other historical archives were not decompressed",
                   "Prior QA findings remain attributed to their earlier audits",
                   "No bulk download, background task, model training or trade execution started"]}
    write_json("verification.json", evidence)
    print(json.dumps({"files": len(rows), "missing": len(evidence["missing_files"]),
                      "size_mismatches": len(evidence["size_mismatches"]),
                      "sample_hash_mismatches": sum(not x["sha256_matches_previous"] for x in samples),
                      "daily_missing": evidence["latest_missing_daily"], "hourly_missing": evidence["latest_missing_hourly"],
                      "parquet_checks": parquet_checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

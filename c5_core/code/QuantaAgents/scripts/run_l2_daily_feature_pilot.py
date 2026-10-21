"""Bounded replay of the previously frozen 24 stock-days; no return data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "experiment_traces/meta_ashare_revision4"
AUDIT = ROOT / "experiment_traces/meta_l2_contract_audit"
OUT = ROOT / "experiment_traces/meta_l2_daily_feature_pilot"
CAP = 512 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def jd(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode())


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare():
    if (OUT / "input_manifest.json").exists():
        raise RuntimeError("Input cache already frozen; use run, not another market read")
    plan, read_plan, prior = [read(AUDIT / f"{name}.json") for name in ("plan", "read_plan", "result")]
    plan_body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if jd(plan_body) != plan["plan_sha256"] or any(x["plan_sha256"] != plan["plan_sha256"] for x in (read_plan, prior)):
        raise RuntimeError("Prior audit plan identity mismatch")
    for manifest in plan["manifests"]:
        if digest(Path(manifest["path"]).read_bytes()) != manifest["sha256"]:
            raise RuntimeError("Raw manifest identity changed")
    source_map = {(x["table"], x["date"], x["path"]): x for x in plan["sources"]}
    sources = [x for x in read_plan["sources"] if x["table"] in ("quotes", "trades")]
    estimated = sum(x["projected_compressed_bytes"] for x in sources)
    if estimated > CAP:
        raise RuntimeError("Frozen projected chunk byte budget exceeded")
    OUT.mkdir(parents=True, exist_ok=True)
    save(OUT / "read_intent.json", {"prior_plan_sha256": plan["plan_sha256"], "cap_bytes": CAP,
         "projected_compressed_bytes": estimated, "codes": plan["codes"], "dates": plan["dates"],
         "created_at": datetime.now(timezone.utc).isoformat(), "no_returns_or_model_calls": True,
         "input_identity": "manifest hashes, raw file stat and independently matched projected row hashes; no raw full-file rehash"})
    pieces = {"quotes": [], "trades": []}
    actual_estimate, counts = 0, {}
    for item in sources:
        table, day, path = item["table"], item["date"], Path(item["path"])
        base = source_map[(table, day, str(path))]
        for field in ("size", "mtime_ns", "codes", "expected_selected_rows"):
            if item[field] != base[field]:
                raise RuntimeError(f"Read plan changed {field}")
        stat = path.stat()
        if stat.st_size != base["size"] or stat.st_mtime_ns != base["mtime_ns"]:
            raise RuntimeError(f"Raw file changed: {path}")
        groups = item["matched_row_groups"]
        if len(groups) != len(set(groups)) or not set(groups).issubset(base["candidate_row_groups"]):
            raise RuntimeError("Row-group plan not bounded by original audit")
        pf = pq.ParquetFile(path)
        columns = plan["columns"][table]
        chunk_size = sum(pf.metadata.row_group(g).column(pf.schema.names.index(c)).total_compressed_size for g in groups for c in columns)
        if chunk_size != item["projected_compressed_bytes"] or actual_estimate + chunk_size > CAP:
            raise RuntimeError("Projected chunk identity/budget differs")
        actual_estimate += chunk_size
        arrow = pf.read_row_groups(groups, columns=columns, use_threads=False)
        frame = arrow.filter(pc.is_in(arrow["wind_code"], value_set=pa.array(item["codes"]))).to_pandas()
        for code, expected in item["expected_selected_rows"].items():
            selected = frame[frame.wind_code == code]
            if len(selected) != expected:
                raise RuntimeError("Manifest row count differs")
            key = (day, code, table)
            if key in counts:
                raise RuntimeError("Duplicate stock-day source")
            counts[key] = int(expected)
        pieces[table].append(frame)
    frames = {name: pd.concat(parts, ignore_index=True) for name, parts in pieces.items()}
    verified = []
    for item in prior["stock_days"]:
        day, code = item["date"], item["wind_code"]
        for table in ("quotes", "trades"):
            frame = frames[table]
            selected = frame[(frame.date.astype(str) == day) & (frame.wind_code == code)].sort_values("source_row_no", kind="stable")
            content_hash = digest(pd.util.hash_pandas_object(selected, index=False).values.tobytes())
            if content_hash != item["tables"][table]["projected_rows_sha256"]:
                raise RuntimeError(f"Prior projected content differs: {day} {code} {table}")
            verified.append({"date": day, "wind_code": code, "table": table, "sha256": content_hash})
    expected = [{"date": day, "wind_code": code, **{name: counts[(day, code, name)] for name in frames}}
                for day in plan["dates"] for code in plan["codes"]]
    files = {}
    for name, frame in frames.items():
        target = OUT / f"{name}.parquet"
        frame.to_parquet(target, index=False)
        files[name] = {"path": target.name, "sha256": digest(target.read_bytes()), "rows": len(frame)}
    save(OUT / "expected_counts.json", expected)
    files["expected_counts"] = {"path": "expected_counts.json", "sha256": digest((OUT / "expected_counts.json").read_bytes())}
    save(OUT / "input_manifest.json", {"files": files, "prior_plan_sha256": plan["plan_sha256"],
         "prior_audit_files": {n: digest((AUDIT / f"{n}.json").read_bytes()) for n in ("plan", "read_plan", "result")},
         "verified_stock_day_table_hashes": verified, "projected_compressed_bytes": actual_estimate,
         "byte_metric_excludes_metadata_and_OS_overhead": True, "sample_stock_days": len(expected),
         "execution_valid": False, "actual_available_at": None})
    print(json.dumps({"prepared": len(expected), "projected_compressed_bytes": actual_estimate, "files": files}), flush=True)


def run():
    sys.path.insert(0, str(STAGE / "src"))
    from quanta_agents.meta.l2_daily_features import build_daily_features, FeaturePolicy
    manifest = read(OUT / "input_manifest.json")
    for item in manifest["files"].values():
        if digest((OUT / item["path"]).read_bytes()) != item["sha256"]:
            raise RuntimeError("Frozen pilot input cache changed")
    inputs = {name: pd.read_parquet(OUT / f"{name}.parquet") for name in ("quotes", "trades")}
    expected = pd.DataFrame(read(OUT / "expected_counts.json"))
    availability = {"2026-01-06": "2026-01-07T09:00:00+08:00", "2026-01-07": "2026-01-08T09:00:00+08:00",
                    "2026-01-08": "2026-01-09T09:00:00+08:00"}
    label = "fixed-next-day-0900-pilot-assumption; archive feed receipt time unknown"
    policy = FeaturePolicy()
    features = build_daily_features(inputs["quotes"], inputs["trades"], expected, policy=policy,
                                    simulated_available_at=availability, simulation_label=label)
    save(OUT / "frozen_contract.json", features.attrs["frozen_contract"])
    records = json.loads(features.to_json(orient="records", date_format="iso", double_precision=15))
    save(OUT / "features.json", records)
    summary = {"sample_stock_days": len(records), "accepted": sum(bool(r["accepted"]) for r in records),
               "rejected": [dict(date=r["trade_date"], wind_code=r["wind_code"], reasons=r["reason_codes"]) for r in records if not r["accepted"]],
               "module_sha256": digest((STAGE / "src/quanta_agents/meta/l2_daily_features.py").read_bytes()),
               "runner_sha256": digest(Path(__file__).read_bytes()), "input_manifest_sha256": digest((OUT / "input_manifest.json").read_bytes()),
               "features_sha256": digest((OUT / "features.json").read_bytes()), "simulated_available_at": availability,
               "frozen_contract_sha256": digest((OUT / "frozen_contract.json").read_bytes()),
               "simulation_label": label, "actual_available_at": None, "no_return_calculation": True,
               "model_calls": 0, "execution_valid": False, "strategy_success": False}
    save(OUT / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run"))
    arguments = parser.parse_args()
    prepare() if arguments.action == "prepare" else run()

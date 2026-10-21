"""Frozen raw-CSV nine-stock development-window input pilot, no strategy returns."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "experiment_traces/meta_ashare_revision5"
DEFAULT_OUT = ROOT / "experiment_traces/meta_raw_daily_data_pilot"
RAW_ROOT = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/vendor/stock-trading-data-2025-12-23N")
CALENDAR = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt")
REFERENCE = ROOT / "experiment_traces/meta_cash_dividend_pilot/raw_price_projection.json"
START, END = "2019-06-20", "2019-06-28"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(out):
    if (out / "result.json").exists():
        raise RuntimeError("Completed frozen pilot exists; do not overwrite its evidence")
    sys.path.insert(0, str(STAGE / "src"))
    from quanta_agents.raw_daily_data import RawDailyData, normalize_code, digest
    audit_plan_path = ROOT / "experiment_traces/meta_l2_contract_audit/plan.json"
    audit_plan = json.loads(audit_plan_path.read_text(encoding="utf-8"))
    if digest({k: v for k, v in audit_plan.items() if k != "plan_sha256"}) != audit_plan["plan_sha256"]:
        raise RuntimeError("L2 seed selection plan changed")
    codes = [normalize_code(c) for c in audit_plan["codes"]] + ["sz000001"]
    if len(codes) != 9 or len(set(codes)) != 9:
        raise RuntimeError("Expected fixed eight L2 stocks plus distinct sz000001")
    source_metadata = []
    for code in codes:
        path = RAW_ROOT / (code + ".csv")
        source_metadata.append({"code": code, "path": str(path), "exists": path.is_file(),
                                "bytes": path.stat().st_size if path.is_file() else None,
                                "mtime_ns": path.stat().st_mtime_ns if path.is_file() else None})
    module = STAGE / "src/quanta_agents/raw_daily_data.py"
    plan = {"codes": codes, "start_date": START, "end_date": END,
            "selection": "Prior frozen L2 code selection plus user-selected dividend example sz000001; no replacements",
            "l2_selection_plan_sha256": audit_plan["plan_sha256"], "raw_sources": source_metadata,
            "calendar_path": str(CALENDAR), "calendar_sha256": sha(CALENDAR),
            "raw_read_cap_bytes": 32 * 1024 * 1024, "module_sha256": sha(module),
            "reference_path": str(REFERENCE), "reference_sha256": sha(REFERENCE),
            "converter_source_sha256": sha(ROOT / "scripts/convert_qlib_daily_to_parquet.py"),
            "tradeable_conversion_manifest_sha256": sha(Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025/manifest.json")),
            "no_market_price_reads_after_end": True, "no_qlib_price_bin_reads": True,
            "no_model_calls": True, "no_strategy_returns": True}
    plan["plan_sha256"] = digest(plan)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "plan.json").exists():
        if json.loads((out / "plan.json").read_text(encoding="utf-8")) != plan:
            raise RuntimeError("Frozen pilot plan differs; use a separate reviewed output directory")
    else:
        save(out / "plan.json", plan)
    reference = pd.DataFrame(json.loads(REFERENCE.read_text(encoding="utf-8")))
    adapter = RawDailyData({"raw_root": str(RAW_ROOT), "calendar_path": str(CALENDAR),
                           "max_read_bytes": plan["raw_read_cap_bytes"]})
    print(json.dumps({"frozen_codes": codes, "date_range": [START, END],
                      "raw_file_full_size_upper_bound": sum(x["bytes"] or 0 for x in source_metadata),
                      "raw_read_cap_bytes": plan["raw_read_cap_bytes"]}), flush=True)
    data = adapter.load(codes, START, END, reference=reference)
    if sha(module) != plan["module_sha256"]:
        raise RuntimeError("Module changed during pilot")
    records = json.loads(data.to_json(orient="records", double_precision=15))
    save(out / "raw_daily_rows.json", records)
    save(out / "source_manifest.json", data.attrs["source_manifest"])
    save(out / "availability_contract.json", data.attrs["availability_contract"])
    data.to_parquet(out / "raw_daily_rows.parquet", index=False)
    summary = {"plan_sha256": plan["plan_sha256"], "requested_stock_days": len(data),
        "accepted": int(data.accepted.sum()),
        "rejected": [{"date": r["date"], "code": r["code"], "reason_codes": r["reason_codes"]} for r in records if not r["accepted"]],
        "reference_matches": int(data.reference_status.eq("matched_raw_open").sum()),
        "reference_not_checked": int(data.reference_status.eq("not_checked").sum()),
        "raw_read_bytes": adapter.manifest()["raw_read_bytes"], "qlib_price_bin_bytes_read": 0,
        "source_full_file_hashes_computed": False, "no_model_calls": True, "no_strategy_returns": True,
        "execution_valid": False, "actual_available_at": None,
        "module_sha256": sha(module), "runner_sha256": sha(Path(__file__)),
        "artifact_hashes": {name: sha(out / name) for name in ["raw_daily_rows.json", "raw_daily_rows.parquet", "source_manifest.json", "availability_contract.json"]}}
    save(out / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    run(args.output_dir)

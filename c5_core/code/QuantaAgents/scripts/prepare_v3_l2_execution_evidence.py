"""Freeze and read full-depth quotes for the existing eight-stock three-day slice.

No strategy returns, model calls, original CSV reads, or source-data writes.
All physical reads are bounded before dispatch, with per-part receipts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
OLD = REPO/"experiment_traces/meta_l2_contract_audit"
OUT = REPO/"experiment_traces/meta_framework_v3/l2_execution_evidence_001"
COLUMNS = ["code", "wind_code", "date", "time_raw", "event_ts", "source_row_no",
    "last_price_x1e4", "open_price_x1e4", "prev_close_x1e4", "cum_volume", "cum_amount"] + [
    f"{side}_{kind}_{level}"+("_x1e4" if kind == "price" else "")
    for side in ("ask", "bid") for kind in ("price", "volume") for level in range(1,11)]


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(serial(value))


def freeze():
    OUT.mkdir(parents=True, exist_ok=False)
    old, old_read = read(OLD/"plan.json"), read(OLD/"read_plan.json")
    assert sha(serial({k:v for k,v in old.items() if k!="plan_sha256"}).encode()) == old["plan_sha256"]
    assert old_read["plan_sha256"] == old["plan_sha256"]
    for manifest in old["manifests"]:
        assert sha(Path(manifest["path"]).read_bytes()) == manifest["sha256"]
    sources=[]
    for item in old_read["sources"]:
        if item["table"] != "quotes":
            continue
        path=Path(item["path"]);stat=path.stat()
        assert stat.st_size==item["size"] and stat.st_mtime_ns==item["mtime_ns"]
        pf=pq.ParquetFile(path)
        assert all(c in pf.schema_arrow.names for c in COLUMNS)
        estimate=sum(pf.metadata.row_group(g).column(pf.schema_arrow.get_field_index(c)).total_compressed_size
                     for g in item["matched_row_groups"] for c in COLUMNS)
        sources.append({**item,"new_projected_column_bytes":estimate})
    physical=sum(s["size"] for s in sources)
    projection=sum(s["new_projected_column_bytes"] for s in sources)
    assert physical+projection <= 1536*1024**2
    plan={"kind":"v3_fixed_l2_execution_evidence", "created_at":datetime.now(timezone.utc).isoformat(),
        "deadline_epoch":time.time()+3600, "old_plan_sha256":old["plan_sha256"],
        "source_bindings":[{"path":str(OLD/f),"sha256":sha((OLD/f).read_bytes())} for f in ("plan.json","read_plan.json","result.json")],
        "manifests":old["manifests"], "codes":old["codes"], "dates":old["dates"],
        "selection":old["selection"], "selection_hashes":old["selection_hashes"],
        "columns":COLUMNS,"sources":sources,"expected_rows":sum(sum(s["expected_selected_rows"].values()) for s in sources),
        "resource_limits":{"full_file_hash_plus_projected_payload_bytes":1536*1024**2,
            "planned_full_file_hash_bytes":physical,"planned_projected_column_bytes":projection,
            "max_output_bytes":256*1024**2,"max_rows":120000,"max_raw_rowgroup_rows":1000000,
            "model_calls":0,"strategy_searches":0,"network_search_queries":4,"network_page_fetches":4,
            "network_per_page_bytes":10*1024**2,"network_total_page_bytes":32*1024**2,"network_timeout_seconds":30,"network_retries":0},
        "quote_policy":{"decision_local_time":"09:35:00", "assumed_feed_delay_ms":1000,
            "max_decision_quote_source_age_ms":4000,"assumed_order_latency_ms":1000,
            "max_execution_snapshot_delay_ms":4000,"per_level_displayed_depth_fraction":"0.10",
            "buy_limit_vs_decision_ask_fraction":"0.001","sell_limit_vs_decision_bid_fraction":"0.001",
            "fixed_initial_capital":"1000000.00","requested_sleeve_cash":"100000.00",
            "unit_lot":100,"price_scale":10000,"tick_scaled":100,
            "buy_day":"2026-01-07","sell_day":"2026-01-08",
            "matching":"Latest decision-visible quote; earliest quote at/after order arrival, never skip an invalid snapshot to obtain a better one. Aggregate finite eligible contra-side displayed depth, then whole-lot order fill. No book reuse within an order.",
            "scope":"Quote execution feasibility, not actual fills or portfolio PnL. No account cash/share movement until company actions, account fees and state obligations admitted. Unknown receipt time and supplier units remain unverified."},
        "no_model_calls":True,"no_return_calculation":True,"sealed_2024_2025_opened":False,
        "script_sha256":sha(Path(__file__).read_bytes()),"execution_valid":False,"formal_target_success":False}
    plan["plan_sha256"]=sha(serial(plan).encode());save_new(OUT/"plan.json",plan)
    print(serial({"plan_sha256":plan["plan_sha256"],"sources":len(sources),"expected_rows":plan["expected_rows"],"full_hash_bytes":physical,"projection_bytes":projection}))


def acquire():
    plan=read(OUT/"plan.json");assert sha(serial({k:v for k,v in plan.items() if k!="plan_sha256"}).encode())==plan["plan_sha256"]
    assert sha(Path(__file__).read_bytes())==plan["script_sha256"] and time.time()<plan["deadline_epoch"]
    for binding in plan["source_bindings"]+plan["manifests"]:
        assert sha(Path(binding["path"]).read_bytes())==binding["sha256"]
    parts=OUT/"parts";parts.mkdir(exist_ok=True);receipts=[]
    for index,item in enumerate(plan["sources"]):
        prefix=f"{index:02d}";receipt_path=parts/(prefix+".json");output=parts/(prefix+".parquet")
        if receipt_path.exists():
            receipt=read(receipt_path);assert receipt["plan_sha256"]==plan["plan_sha256"]
            assert sha(output.read_bytes())==receipt["output_sha256"];receipts.append(receipt);continue
        assert not (parts/(prefix+".intent.json")).exists(), "unresolved read: inspect saved state, no automatic repeat"
        assert time.time()<plan["deadline_epoch"]
        path=Path(item["path"]);stat=path.stat();assert stat.st_size==item["size"] and stat.st_mtime_ns==item["mtime_ns"]
        save_new(parts/(prefix+".intent.json"),{"plan_sha256":plan["plan_sha256"],"source":item,"started_at":time.time()})
        h=hashlib.sha256();count=0
        with path.open("rb") as stream:
            while block:=stream.read(1024**2):
                count+=len(block);assert count<=item["size"];h.update(block)
        assert count==item["size"] and h.hexdigest()==item["manifest_file_sha256_unverified"], "full physical file content differs from frozen manifest"
        pf=pq.ParquetFile(path);tables=[];actual=0
        for group in item["matched_row_groups"]:
            assert pf.metadata.row_group(group).num_rows<=plan["resource_limits"]["max_raw_rowgroup_rows"]
            actual+=sum(pf.metadata.row_group(group).column(pf.schema_arrow.get_field_index(c)).total_compressed_size for c in plan["columns"])
            table=pf.read_row_group(group,columns=plan["columns"],use_threads=False)
            tables.append(table.filter(pc.is_in(table["wind_code"],value_set=pa.array(item["codes"]))))
        assert actual==item["new_projected_column_bytes"]
        table=pa.concat_tables(tables)
        counts={c:pc.sum(pc.cast(pc.equal(table["wind_code"],c),pa.int64())).as_py() for c in item["codes"]}
        assert counts==item["expected_selected_rows"] and table.num_rows<=plan["resource_limits"]["max_rows"]
        assert all(str(x)==item["date"] for x in pc.unique(table["date"]).to_pylist())
        stat_after=path.stat();assert (stat_after.st_size,stat_after.st_mtime_ns)==(stat.st_size,stat.st_mtime_ns)
        pq.write_table(table,output,compression="zstd")
        assert sum(f.stat().st_size for f in parts.glob("*.parquet"))<=plan["resource_limits"]["max_output_bytes"]
        receipt={"plan_sha256":plan["plan_sha256"],"source_path":str(path),"full_source_sha256_verified":h.hexdigest(),
            "full_file_hash_bytes":count,"projected_column_bytes":actual,"rows":table.num_rows,"counts":counts,
            "output_path":str(output),"output_sha256":sha(output.read_bytes()),"finished_at":time.time()}
        save_new(receipt_path,receipt);receipts.append(receipt)
        print(serial({"part":prefix,"rows":table.num_rows,"full_hash_verified":True}),flush=True)
    assert sum(r["rows"] for r in receipts)==plan["expected_rows"]
    summary={"plan_sha256":plan["plan_sha256"],"parts":receipts,"rows":sum(r["rows"] for r in receipts),
        "full_file_hash_bytes":sum(r["full_file_hash_bytes"] for r in receipts),
        "projected_column_bytes":sum(r["projected_column_bytes"] for r in receipts),
        "byte_interpretation":"Hash bytes plus Parquet compressed column payload, excluding footer, readahead and OS overhead; not measured physical I/O.",
        "original_sources_modified":False,"model_calls":0,"returns_computed":False,"execution_valid":False}
    save_new(OUT/"acquisition.json",summary);print(serial({k:v for k,v in summary.items() if k!="parts"}))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["freeze","read"])
    args=parser.parse_args();freeze() if args.action=="freeze" else acquire()

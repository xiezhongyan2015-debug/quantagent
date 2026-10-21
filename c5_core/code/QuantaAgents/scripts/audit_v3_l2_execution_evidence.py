"""Apply the frozen limit/depth scenario to saved full-depth quote parts once."""
import hashlib
import json
from pathlib import Path
import sys
import time
from decimal import Decimal, ROUND_CEILING

import pyarrow.parquet as pq

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"src"))
from quanta_agents.meta_v3.l2_execution_evidence import assess_order, local_time
from quanta_agents.meta_v3.ledger import digest, serial

OUT=REPO/"experiment_traces/meta_framework_v3/l2_execution_evidence_001"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_new(path,value):
    with path.open("x",encoding="utf-8") as stream:
        stream.write(serial(value))


def main():
    plan,acquisition=read(OUT/"plan.json"),read(OUT/"acquisition.json")
    assert digest({k:v for k,v in plan.items() if k!="plan_sha256"})==plan["plan_sha256"]==acquisition["plan_sha256"]
    assert time.time()<plan["deadline_epoch"]
    pins={str(p.relative_to(REPO)):sha(p) for p in [Path(__file__),REPO/"src/quanta_agents/meta_v3/l2_execution_evidence.py"]}
    save_new(OUT/"analysis_intent.json",{"plan_sha256":plan["plan_sha256"],"source_pins":pins,"started_at":time.time(),
        "model_calls":0,"portfolio_pnl_calculation":False,"orders_sent":0})
    rows=[]
    for part in acquisition["parts"]:
        path=Path(part["output_path"])
        assert sha(path)==part["output_sha256"]
        table=pq.ParquetFile(path).read()
        assert table.num_rows==part["rows"]
        rows.extend(table.to_pylist())
    assert len(rows)==plan["expected_rows"] and len({(r["wind_code"],str(r["date"]),r["source_row_no"]) for r in rows})==len(rows)
    assert set(r["wind_code"] for r in rows)==set(plan["codes"]) and set(str(r["date"]) for r in rows)==set(plan["dates"])
    policy=plan["quote_policy"]
    assert plan["dates"].index(policy["sell_day"])==plan["dates"].index(policy["buy_day"])+1
    prior_day=plan["dates"][plan["dates"].index(policy["buy_day"])-1]
    results=[]
    for code in plan["codes"]:
        subset=[r for r in rows if r["wind_code"]==code]
        buy=assess_order(subset,code,policy["buy_day"],"buy",policy)
        reference=buy["matched_reference_quantity_native"]
        sell=assess_order(subset,code,policy["sell_day"],"sell",policy,reference_quantity=reference)
        previous=sorted([r for r in subset if str(r["date"])==prior_day],key=lambda r:(local_time(r["event_ts"]),r["source_row_no"]))[-1]
        volume=previous["cum_volume"]
        proxy=volume//2000*100  # 5% of previous native volume, rounded down to 100.
        comparison={"previous_day":prior_day,"previous_day_volume_native":volume,
            "previous_daily_volume_5pct_proxy_native":proxy,
            "full_request_allowed_by_daily_volume_proxy":None if "requested_quantity_native" not in buy else proxy>=buy["requested_quantity_native"],
            "interpretation":"Only a comparison of the old volume proxy with finite later displayed depth; not a matched-time slippage estimate or actual liquidity guarantee."}
        chosen=buy.get("execution_snapshot")
        if chosen:
            raw=next(r for r in subset if str(r["date"])==chosen["date"] and r["source_row_no"]==chosen["source_row_no"])
            opening=raw["open_price_x1e4"]
            comparison.update(raw_open_price_scaled=opening,
                old_open_plus_10bps_scaled=int((Decimal(opening)*Decimal("1.001")/100).to_integral_value(rounding=ROUND_CEILING))*100,
                ask1_at_later_execution_snapshot_scaled=raw["ask_price_1_x1e4"])
        results.append({"wind_code":code,"buy":buy,"next_day_sell_same_reference_shares":sell,
            "unmatched_exit_reference_quantity_native":reference-sell["matched_reference_quantity_native"],
            "daily_proxy_comparison":comparison})
    requested=[x["buy"].get("requested_quantity_native",0) for x in results]
    summary={"stocks_requested":len(results),"quote_rows":len(rows),"stock_days":len(plan["codes"])*len(plan["dates"]),
        "buy_full_reference_matches":sum(x["buy"]["status"]=="full_reference_match" for x in results),
        "buy_partial_reference_matches":sum(x["buy"]["status"]=="partial_reference_match" for x in results),
        "buy_rejected_evidence":sum(x["buy"]["status"]=="rejected_evidence" for x in results),
        "buy_unmatched":sum(x["buy"]["status"]=="unmatched" for x in results),
        "requested_quantity_native_sum_for_count_only":sum(requested),
        "matched_reference_quantity_native_sum_for_count_only":sum(x["buy"]["matched_reference_quantity_native"] for x in results),
        "daily_volume_proxy_allows_full_but_depth_does_not":sum(x["daily_proxy_comparison"]["full_request_allowed_by_daily_volume_proxy"] is True and x["buy"]["matched_reference_quantity_native"]<x["buy"]["requested_quantity_native"] for x in results),
        "stocks_with_unmatched_exit_reference_quantity":sum(x["unmatched_exit_reference_quantity_native"]>0 for x in results),
        "matched_buy_notional_excluding_fees":str(sum((Decimal(x["buy"].get("quoted_notional_excluding_fees","0")) for x in results),Decimal(0))),
        "actual_fills":None,"portfolio_pnl":None,"execution_valid":False,"formal_target_success":False}
    result={"kind":"fixed_l2_quote_execution_evidence","plan_sha256":plan["plan_sha256"],"source_pins":pins,
        "summary":summary,"records":results,"policy":policy,"completed_at":time.time(),
        "limitations":["One hypothetical order per stock per selected date; no actual orders sent.",
            "The source archive was compiled months later; 1s feed and order delays are scenario assumptions, not observed receipt latency.",
            "10 percent of displayed per-level depth is an imposed haircut; it cannot certify queue priority, cancellation, market impact or actual fills.",
            "Price and quantity units match prior empirical checks, but this vendor conversion schema has not been independently supplied.",
            "Company actions, historical securities state, actual account fees and tax are not admitted. This report moves no account cash/shares and calculates no portfolio PnL.",
            "The next-day same-share quote check is conditional on no intervening share-changing action; it is not an assertion that the reference quantity was legally sellable."],
        "new_model_calls":0,"original_sources_modified":False,"formal_case_denominator":0}
    save_new(OUT/"result.json",result)
    print(serial(summary))
    for r in results:
        print(serial({"code":r["wind_code"],"buy_status":r["buy"]["status"],"requested":r["buy"].get("requested_quantity_native"),
            "matched":r["buy"]["matched_reference_quantity_native"],"sell_status":r["next_day_sell_same_reference_shares"]["status"],
            "unmatched_exit":r["unmatched_exit_reference_quantity_native"],"reason":r["buy"]["reasons"]}))


if __name__=="__main__":main()

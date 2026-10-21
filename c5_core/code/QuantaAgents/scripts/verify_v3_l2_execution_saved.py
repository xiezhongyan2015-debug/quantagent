"""Independent integer reconstruction of the saved quote scenario, no matcher."""
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]/"experiment_traces/meta_framework_v3/l2_execution_evidence_001"


def load(path):return json.loads(path.read_text(encoding="utf-8"))


def main():
    plan,receipt,result=[load(ROOT/name) for name in ("plan.json","acquisition.json","result.json")]
    assert result["plan_sha256"]==receipt["plan_sha256"]==plan["plan_sha256"]
    policy=plan["quote_policy"]
    assert policy["per_level_displayed_depth_fraction"]=="0.10" and policy["unit_lot"]==100 and policy["price_scale"]==10000
    rows=[]
    for part in receipt["parts"]:
        p=Path(part["output_path"])
        assert hashlib.sha256(p.read_bytes()).hexdigest()==part["output_sha256"]
        rows.extend(pq.ParquetFile(p).read().to_pylist())
    assert len(rows)==104712
    checked=[];snapshots=[]
    for record in result["records"]:
        buy_quantity=0
        for which in ("buy","next_day_sell_same_reference_shares"):
            saved=record[which];side=saved["side"];day=saved["date"]
            subset=sorted([r for r in rows if r["wind_code"]==record["wind_code"] and str(r["date"])==day],key=lambda r:(r["event_ts"],r["source_row_no"]))
            cutoff=datetime.fromisoformat(day+"T09:34:59")
            decision=[r for r in subset if r["event_ts"]<=cutoff][-1]
            execution=[r for r in subset if r["event_ts"]>=datetime.fromisoformat(day+"T09:35:01")][0]
            assert decision["source_row_no"]==saved["decision_quote"]["source_row_no"]
            assert execution["source_row_no"]==saved["execution_snapshot"]["source_row_no"]
            assert decision["event_ts"]>=datetime.fromisoformat(day+"T09:34:56")
            assert execution["event_ts"]<=datetime.fromisoformat(day+"T09:35:05")
            best=decision["ask_price_1_x1e4"] if side=="buy" else decision["bid_price_1_x1e4"]
            # Independent scaled-integer rounding of +/-10 basis points.
            limit=((best*1001+100000-1)//100000)*100 if side=="buy" else (best*999//100000)*100
            assert limit==saved["limit_price_scaled"]
            quantity=(100000*10000//(limit*100))*100 if side=="buy" else buy_quantity
            assert quantity==saved["requested_quantity_native"]
            prefix="ask" if side=="buy" else "bid"
            eligible=[]
            for level in range(1,11):
                price=execution[f"{prefix}_price_{level}_x1e4"];depth=execution[f"{prefix}_volume_{level}"]
                if not price or (side=="buy" and price>limit) or (side=="sell" and price<limit):break
                eligible.append((level,price,depth,depth//10))
            full=sum(x[2] for x in eligible);cap=sum(x[3] for x in eligible)
            matched=min(quantity,cap//100*100)
            assert matched==saved["matched_reference_quantity_native"] and quantity-matched==saved["unmatched_reference_quantity_native"]
            expected=[];remaining=matched;amount=0
            for level,price,depth,allowed in eligible:
                q=min(remaining,allowed)
                if q:expected.append((level,price,depth,allowed,q));amount+=price*q;remaining-=q
            actual=[(x["level"],x["price_scaled"],x["displayed_quantity_native"],x["scenario_allowance_native"],x["matched_quantity_native"]) for x in saved["allocations"]]
            assert expected==actual and remaining==0
            assert Decimal(amount)/10000==Decimal(saved["quoted_notional_excluding_fees"])
            assert saved["actual_fill_verified"] is False and saved["execution_valid"] is False
            if side=="buy":buy_quantity=matched
            checked.append({"code":record["wind_code"],"day":day,"side":side,"requested":quantity,
                "full_eligible_displayed_depth_native":full,"scenario_depth_allowance_native":cap,
                "matched_reference_quantity_native":matched,"full_displayed_depth_insufficient":full<quantity,
                "partial_due_only_to_frozen_haircut_and_lot_rounding":full>=quantity and matched<quantity})
            snapshots.append({"code":record["wind_code"],"side":side,
                "decision":{k:(str(v) if k in ("date","event_ts") else v) for k,v in decision.items()},
                "execution":{k:(str(v) if k in ("date","event_ts") else v) for k,v in execution.items()}})
        assert record["unmatched_exit_reference_quantity_native"]==buy_quantity-record["next_day_sell_same_reference_shares"]["matched_reference_quantity_native"]
    report={"source_result_sha256":hashlib.sha256((ROOT/"result.json").read_bytes()).hexdigest(),
        "plan_sha256":plan["plan_sha256"],"all_16_scenarios_reconciled":len(checked)==16,
        "snapshot_selection_limit_quantity_depth_and_notional_verified":True,
        "buy_full_displayed_depth_shortages":sum(x["side"]=="buy" and x["full_displayed_depth_insufficient"] for x in checked),
        "buy_partial_due_only_to_haircut_and_rounding":sum(x["side"]=="buy" and x["partial_due_only_to_frozen_haircut_and_lot_rounding"] for x in checked),
        "checks":checked,"new_model_calls":0,"new_strategy_backtests":0,"source_prices_modified":False,
        "actual_fills_or_pnl_certified":False}
    folder=ROOT/"independent_audit";folder.mkdir(exist_ok=False)
    (folder/"audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    (folder/"selected_snapshots.json").write_text(json.dumps(snapshots,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="checks"}))


if __name__=="__main__":main()

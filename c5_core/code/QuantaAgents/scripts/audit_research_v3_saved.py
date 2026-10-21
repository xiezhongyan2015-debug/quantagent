"""Read-only source-bound audit; exports authentic reports, never calls a model."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quanta_agents.meta_v3.kernel import module
from quanta_agents.meta_v3.ledger import digest
from audit_raw_cashflows_v3 import cash_action_audit


def daily_audit(result, case):
    """Independent Decimal account reconstruction for these no-entitlement cases.

    Reads only the already pinned small row archives. No strategy is rerun.
    Other corporate events and post-2021 fee schedules require another scope.
    """
    if case["raw_source_bindings"].get("corporate_actions"):
        return cash_action_audit(result, case)
    assert not result["final_snapshot"]["actions"]
    prices = {}
    for source in case["raw_source_bindings"]["source_artifacts"]:
        blob = (Path(source["root"]) / source["rows_file"]).read_bytes()
        assert len(blob) <= 2 * 1024**2 and hashlib.sha256(blob).hexdigest() == source["rows_sha256"]
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
            raw = stream.read(8 * 1024**2 + 1)
        assert len(raw) <= 8 * 1024**2
        rows = json.loads(raw)
        assert len(rows) <= 2000
        for row in rows:
            prices[(row["date"], source["code"])] = row
    days = case["decision_fixture"]["calendar"]
    assert [r["date"] for r in result["daily"]] == days and days[-1] <= "2021-12-31"
    trades = defaultdict(list)
    for trade in result["trades"]:
        assert trade["date"] in days
        trades[trade["date"]].append(trade)
    cash = initial = Decimal(result["initial_cash"])
    positions, lots = defaultdict(int), defaultdict(list)
    fees = slip = gross_flow = Decimal(0)
    high = initial
    drawdown = Decimal(0)
    daily = []
    for day in days:
        for trade in trades[day]:
            code, quantity = trade["symbol"], trade["quantity"]
            assert type(quantity) is int and quantity > 0 and quantity % 100 == 0
            raw_open = Decimal(prices[(day, code)]["raw_price_text"]["raw_open"])
            assert raw_open == Decimal(trade["raw_open"])
            buy = trade["side"] == "buy"
            assert buy or trade["side"] == "sell"
            fill = (raw_open * (Decimal("1.001") if buy else Decimal("0.999"))).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING if buy else ROUND_FLOOR)
            assert fill == Decimal(trade["raw_price"])
            gross = fill * quantity
            cents = lambda v: v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            expected_fee = {"commission": cents(max(Decimal(5), gross*Decimal("0.0003"))),
                "transfer_fee": cents(gross*Decimal("0.00002")),
                "stamp_duty": Decimal(0) if buy else cents(gross*Decimal("0.001"))}
            expected_fee["total"] = sum(expected_fee.values())
            assert all(Decimal(trade["fees"][k]) == v for k,v in expected_fee.items())
            cost = expected_fee["total"]
            adverse = abs(fill-raw_open)*quantity
            assert adverse == Decimal(trade["slippage_amount"])
            fees += cost; slip += adverse
            if buy:
                assert cash >= gross+cost
                cash -= gross+cost; positions[code] += quantity
                lots[code].append([day, quantity]); gross_flow -= raw_open*quantity
            else:
                assert positions[code] >= quantity
                assert sum(q for acquired,q in lots[code] if acquired < day) >= quantity
                remaining = quantity
                for lot in lots[code]:
                    if lot[0] < day:
                        used = min(lot[1], remaining); lot[1] -= used; remaining -= used
                assert remaining == 0
                cash += gross-cost; positions[code] -= quantity; gross_flow += raw_open*quantity
        expected_holdings = {c:q for c,q in positions.items() if q}
        saved = result["daily"][len(daily)]
        assert expected_holdings == saved["holdings"] and cash == Decimal(saved["cash_available"])
        assert all(Decimal(saved[k]) == 0 for k in ("cash_receivable_gross", "realized_tax_unpaid", "remaining_tax_reserve"))
        nav = cash + sum(q * Decimal(prices[(day,c)]["raw_price_text"]["raw_close"]) for c,q in expected_holdings.items())
        assert nav == Decimal(saved["simulated_net_asset_value"])
        assert nav-initial == Decimal(saved["simulated_net_pnl"])
        assert fees == Decimal(saved["fees_paid_cumulative"]) and slip == Decimal(saved["slippage_in_fill_prices_cumulative"])
        assert nav >= 0 and cash >= 0
        high = max(high, nav); drawdown = max(drawdown, 1-nav/high)
        daily.append({"date":day,"cash":str(cash),"nav":str(nav),"positions":expected_holdings})
    return {"all_days_reconciled": True, "full_capital":str(initial), "net_return":str(nav/initial-1),
        "maximum_drawdown":str(drawdown), "raw_open_cash_flow":str(gross_flow),
        "slippage_in_fill_prices_not_double_charged":str(slip), "daily":daily,
        "formal_score":None, "execution_certified":False}


def audit(root):
    db = sqlite3.connect(f"file:{root.as_posix()}/ledger.sqlite3?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    gateway = module("meta.codex_gateway")
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    pins = plan["provenance"]["source_pins"]
    stage = db.execute("SELECT plan,plan_hash FROM stage WHERE id=1").fetchone()
    assert json.loads(stage["plan"]) == plan and stage["plan_hash"] == digest(plan)
    for task in plan["tasks"].values():
        assert digest(task["case"]) == task["case_hash"]
        for proof in task["case"].get("evidence_sources", []):
            assert hashlib.sha256(Path(proof["path"]).read_bytes()).hexdigest() == proof["sha256"]
    repo = Path(__file__).resolve().parents[1]
    assert all(hashlib.sha256((repo / p).read_bytes()).hexdigest() == h for p, h in pins.items())
    calls, executions, reports = [], [], {}
    for row in db.execute("SELECT * FROM calls ORDER BY task_id,ordinal"):
        receipt = json.loads(row["receipt"]) if row["receipt"] else None
        item = {"id": row["id"], "task_id": row["task_id"], "status": row["status"],
                "known_tokens": row["known_tokens"], "nominal_reserve": row["reserve"]}
        if receipt:
            intent = json.loads(row["intent"])
            checked = gateway.verify_saved_completion(root / "calls" / row["id"],
                expected_prompt_hash=intent["prompt_hash"], expected_schema=intent["schema"],
                expected_artifact_sha256=receipt["artifact_sha256"])
            assert checked["response"] == receipt["response"] and checked["usage"] == receipt["usage"]
            assert checked["request_identity"]["intent_id"] == row["id"]
            assert checked["model"] == "gpt-6-astra" and checked["effort"] == "xhigh"
            item.update(action=receipt["response"]["action"], model=checked["model"],
                effort=checked["effort"], supplier_model_verified=checked["model_verified"],
                prompt_bytes=len(intent["prompt"].encode()), saved_completion_verified=True)
            assert row["known_tokens"] == checked["usage"]["input_tokens"] + checked["usage"]["output_tokens"]
            if row["status"] in ("applied", "failed"):
                app = json.loads((root / "calls" / row["id"] / "application.json").read_text(encoding="utf-8"))
                assert app["model_response_hash"] == digest(receipt["response"])
                assert app["result"] == json.loads(row["result"])
                if item["action"] == "submit_research_report" and not app["failed"]:
                    report = app["result"]["model_report"]
                    assert report == json.loads(receipt["response"]["arguments_json"])
                    reports[row["task_id"]] = {"call_id": row["id"], "report": report}
                elif item["action"] == "develop_strategy" and not app["failed"]:
                    folder = root / "tools" / row["task_id"] / row["id"]
                    artifact = json.loads((folder / "artifact.json").read_text(encoding="utf-8"))
                    assert digest(artifact) == app["result"]["artifact_hash"]
                    raw = artifact["raw"]
                    if raw and raw["status"] == "completed_mechanical":
                        child = folder / "workbench/raw_children/program"
                        if plan["tasks"][row["task_id"]]["case"].get("execution_backend") == "v3_streamed_001":
                            from quanta_agents.meta_v3 import saved_execution as raw_engine
                        else:
                            raw_engine = module("raw_saved_research")
                        restored = raw_engine.SavedRawResearch(child).reconcile_saved_only(
                            expected_plan_sha256=raw["plan_sha256"])
                        assert restored["result"] == raw["result"]
                        r = raw["result"]
                        account_audit = daily_audit(r, plan["tasks"][row["task_id"]]["case"])
                        cash = Decimal(r["initial_cash"])
                        positions = defaultdict(int)
                        fee = defaultdict(Decimal)
                        slip = Decimal(0)
                        for trade in r["trades"]:
                            q = trade["quantity"]
                            sign = 1 if trade["side"] == "buy" else -1
                            positions[trade["symbol"]] += sign * q
                            cash -= sign * q * Decimal(trade["raw_price"]) + Decimal(trade["fees"]["total"])
                            for key in ("total", "commission", "stamp_duty", "transfer_fee"):
                                fee[key] += Decimal(trade["fees"][key])
                            slip += Decimal(trade["slippage_amount"])
                        final = r["final_snapshot"]
                        if final["actions"]:
                            cash += Decimal(account_audit["net_dividend_credits"])-Decimal(account_audit["tax_cash_paid"])
                        else:
                            assert final["event_count"] == len(r["trades"]) + 1
                        assert cash == Decimal(final["cash"])
                        assert fee["total"] == Decimal(final["fees_paid"])
                        saved_positions = defaultdict(int)
                        for lot in final["lots"].values():
                            saved_positions[lot["symbol"]] += lot["quantity"]
                        assert dict(saved_positions) == dict(positions)
                        executions.append({"call_id": row["id"], "task_id": row["task_id"],
                            "initial_cash": r["initial_cash"], "final_cash": str(cash),
                            "net_cash_change": str(cash-Decimal(r["initial_cash"])),
                            "fees": {k: str(v) for k,v in fee.items()}, "slippage": str(slip),
                            "positions": dict(positions), "trade_count": len(r["trades"]),
                            "rejection_count": len(r["rejections"]), "daily_count": len(r["daily"]),
                            "saved_only_reconciliation": True, "arithmetic_passed": True,
                            "execution_valid": False, "formal_target_success": False,
                            "daily_audit": account_audit})
        calls.append(item)
    tasks = [dict(x) for x in db.execute("SELECT id,mode,terminal,final_call FROM tasks ORDER BY id")]
    db.close()
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "source_pins_verified": True,
            "scope": sorted({t["case"]["research_class"] for t in plan["tasks"].values()}), "calls": calls, "tasks": tasks,
            "known_tokens": sum(x["known_tokens"] or 0 for x in calls),
            "unknown_or_pending_nominal_reserve": sum(x["nominal_reserve"] for x in calls if x["known_tokens"] is None),
            "executions": executions, "authentic_reports": reports, "new_gateway_calls": 0,
            "formal_target_success": False, "independent_market_samples": 0}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = audit(args.root.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for task, saved in result["authentic_reports"].items():
        r = saved["report"]
        content = "# 模型原始终稿\n\n来源调用：" + saved["call_id"] + "\n\n"
        content += r["conclusion"] + "\n\n## 局限\n\n" + "\n".join("- " + x for x in r["limitations"])
        content += "\n\n## 证伪条件\n\n" + "\n".join("- " + x for x in r["falsifiers"])
        content += "\n\n## 下一步\n\n" + r["next_step"] + "\n"
        (args.output / (task + "_model_report.md")).write_text(content, encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("known_tokens", "unknown_or_pending_nominal_reserve", "tasks", "executions")}, ensure_ascii=False))

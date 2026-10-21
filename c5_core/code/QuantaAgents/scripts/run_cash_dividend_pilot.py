"""Real document and raw open prices, explicitly simulated cash-account path."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "experiment_traces/meta_ashare_revision4"
OUT = ROOT / "experiment_traces/meta_cash_dividend_pilot"
PRICE = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025/sz000001.parquet")
CALENDAR = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt")
PDF = ROOT / "experiment_traces/meta_corporate_action_sources/20260905T175154Z/pingan_2019_031.pdf"
D = Decimal


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run():
    sys.path.insert(0, str(STAGE / "src"))
    from quanta_agents.raw_share_ledger import RawShareLedger
    from quanta_agents.corporate_action_adapter import CashDividendAdapter, load_pingan_2019_031
    if (OUT / "result.json").exists():
        raise RuntimeError("Completed real-price pilot is frozen; do not silently rerun/overwrite")
    OUT.mkdir(parents=True, exist_ok=True)
    plan = {
        "purpose": "accounting integration pilot, not strategy selection or profitability validation",
        "symbol": "sz000001", "deposit": "10000.00", "quantity": 100,
        "buy_at": "2019-06-24T09:30:00+08:00", "sell_at": "2019-06-26T09:30:00+08:00",
        "adverse_open_price_fraction": "0.001", "tick": "0.01",
        "illustrative_broker_fee_each_side": "5.00", "illustrative_sell_turnover_tax": "0.001",
        "fee_scope": "frozen illustrative debits; historical all-in fee completeness not certified",
        "price_availability": "raw opening auction price used as simulated open fill anchor; actual order arrival/fill unobserved",
        "calendar_availability": "supplied archived session calendar assumed known at deposit, not proven PIT delivery",
        "selection_provenance": "June24 buy / June26 sell and one source PDF selected before raw price inspection in discussion; this file written after inspection",
        "timing_policy": "cash date-only scheduled payment becomes usable next supplied session 08:00; simulated, not observed",
        "created_at": datetime.now(timezone.utc).isoformat(), "execution_valid": False,
    }
    save("plan.json", plan)
    assumption_hash = canonical(plan)
    calendar_bytes = CALENDAR.read_bytes()
    days = [s.strip()[:10] for s in calendar_bytes.decode("utf-8-sig").splitlines()
            if "2019-06-20" <= s.strip()[:10] <= "2019-06-28"]
    if days != sorted(set(days)) or days != ["2019-06-20", "2019-06-21", "2019-06-24", "2019-06-25", "2019-06-26", "2019-06-27", "2019-06-28"]:
        raise RuntimeError("Pilot session calendar differs")
    source = {"ref": str(PRICE), "sha256": sha(PRICE.read_bytes()), "verified": True,
              "evidence_type": "simulated", "assumption_ref": str(OUT / "plan.json"), "assumption_sha256": assumption_hash}
    calendar_source = {**source, "ref": str(CALENDAR), "sha256": sha(calendar_bytes)}
    spec = load_pingan_2019_031(PDF)
    adapter = CashDividendAdapter(spec, days, price_basis="raw")
    ledger = RawShareLedger(days, calendar_source=calendar_source, calendar_available_at="2019-06-24T08:00:00+08:00")
    frame = pq.read_table(PRICE, columns=["date", "code", "raw_open", "is_st", "is_delisting"],
                          filters=[("date", ">=", datetime(2019, 6, 24)), ("date", "<=", datetime(2019, 6, 26))]).to_pandas()
    frame["date"] = pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
    if frame.date.duplicated().any() or len(frame) != 3 or frame.is_st.any() or frame.is_delisting.any():
        raise RuntimeError("Missing, duplicate or flagged raw-price rows")
    raw = {}
    for day in ("2019-06-24", "2019-06-26"):
        number = D(str(frame.loc[frame.date == day, "raw_open"].iloc[0]))
        tick_price = number.quantize(D("0.01"), rounding=ROUND_HALF_UP)
        if not number.is_finite() or number <= 0 or abs(number - tick_price) > D("0.00001"):
            raise RuntimeError("Source raw price is not a tick within float storage tolerance")
        raw[day] = tick_price
    buy_price = (raw["2019-06-24"] * D("1.001")).quantize(D("0.01"), rounding=ROUND_CEILING)
    sell_price = (raw["2019-06-26"] * D("0.999")).quantize(D("0.01"), rounding=ROUND_FLOOR)
    sell_fee = D("5.00") + (sell_price * 100 * D("0.001")).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    events, snapshots = [], []

    def apply(item):
        ledger.apply(item, as_of=item["available_at"])
        events.append(item)
        snapshots.append({"after": item["event_id"], "state": ledger.snapshot()})

    def event(identifier, kind, at, data):
        return {"event_id": identifier, "kind": kind, "effective_at": at, "available_at": at,
                "source": source, "data": data}

    apply(event("pilot-deposit", "cash_deposit", "2019-06-24T08:00:00+08:00", {"amount": "10000.00"}))
    apply(event("pilot-buy", "buy_fill", plan["buy_at"],
                {"symbol": "sz000001", "quantity": 100, "raw_price": str(buy_price), "fees": "5.00"}))
    assert ledger.sellable_quantity("sz000001", as_of=plan["buy_at"]) == 0
    apply(adapter.record_event(ledger))
    apply(adapter.activation_event(ledger))
    allocations = adapter.fifo_sale_allocations(ledger, quantity=100, sale_at=plan["sell_at"])
    apply(event("pilot-sell", "sell_fill", plan["sell_at"],
                {"symbol": "sz000001", "quantity": 100, "raw_price": str(sell_price),
                 "fees": str(sell_fee), "lot_allocations": allocations}))
    unresolved = ledger.valuation({}, as_of=plan["sell_at"])
    assert unresolved["net_asset_value"] is None
    apply(adapter.tax_assessment_event(ledger, as_of=plan["sell_at"]))
    tax_evidence = adapter.tax_evidence(ledger, as_of=plan["sell_at"])
    before_paid = ledger.valuation({}, as_of=plan["sell_at"])
    for item in adapter.settlement_events(ledger):
        apply(item)
    after_paid = ledger.valuation({}, as_of=events[-1]["available_at"])
    manual = D("10000.00") - buy_price * 100 - D("5.00") + sell_price * 100 - sell_fee + D("0.145") * 100 * D("0.80")
    assert before_paid["net_asset_value"] == after_paid["net_asset_value"] == format(manual, ".2f")
    assert before_paid["cash_receivable_gross"] == "14.50" and before_paid["known_tax_payable"] == "2.90"
    assert after_paid["cash_receivable_gross"] == after_paid["known_tax_payable"] == "0.00"
    assert ledger.snapshot()["lots"]["pilot-buy"]["quantity"] == 0
    replayed = RawShareLedger.replay(ledger.genesis, ledger.journal)
    assert replayed.snapshot() == ledger.snapshot()
    journal = {"genesis": ledger.genesis, "journal": ledger.journal}
    save("journal.json", journal)
    save("snapshots.json", snapshots)
    save("adapter_manifest.json", adapter.manifest())
    save("tax_evidence.json", tax_evidence)
    save("raw_price_projection.json", json.loads(frame.to_json(orient="records")))
    result = {"status": "accounting_pilot_passed", "raw_open": {k: str(v) for k, v in raw.items()},
              "simulated_buy_price": str(buy_price), "simulated_sell_price": str(sell_price),
              "buy_fee": "5.00", "sell_fee": str(sell_fee), "manual_cash_reconciliation": format(manual, ".2f"),
              "tax_unresolved_before_assessment": unresolved, "before_dividend_cash_credit": before_paid,
              "after_dividend_cash_credit": after_paid, "journal_replay_matches": True,
              "source_price_sha256": source["sha256"], "source_pdf_sha256": sha(PDF.read_bytes()),
              "calendar_sha256": calendar_source["sha256"], "plan_sha256": assumption_hash,
              "code_sha256": {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in [Path(__file__), STAGE / "src/quanta_agents/corporate_action_adapter.py", STAGE / "src/quanta_agents/raw_share_ledger.py"]},
              "model_calls": 0, "strategy_backtest": False, "execution_valid": False,
              "company_action_coverage_complete": False, "observed_account_receipts": False}
    save("result.json", result)
    print(json.dumps({"status": result["status"], "final_simulated_cash": result["manual_cash_reconciliation"],
                      "journal_replay_matches": True, "execution_valid": False}), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()

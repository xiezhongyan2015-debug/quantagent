"""Independent saved cash/share-flow audit, without strategy or ledger execution.

Supports the frozen development cash-dividend account only. Assessed dividend
tax and hypothetical-sale reserves remain recorded simulation estimates: this
checks their cash and NAV treatment, not independent statutory classification.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import gzip
import hashlib
import io
import json
from pathlib import Path

D = Decimal
HK = timezone(timedelta(hours=8))


def cents(value):
    return value.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def cash_action_audit(result, case):
    days = case["decision_fixture"]["calendar"]
    assert result["calendar"] == [r["date"] for r in result["daily"]] == days
    assert days == sorted(set(days)) and days[-1] <= "2021-12-31"
    prices = {}
    for source in case["raw_source_bindings"]["source_artifacts"]:
        blob = (Path(source["root"]) / source["rows_file"]).read_bytes()
        assert len(blob) <= 2*1024**2 and hashlib.sha256(blob).hexdigest() == source["rows_sha256"]
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
            raw = stream.read(8*1024**2+1)
        assert len(raw) <= 8*1024**2
        rows = json.loads(raw)
        assert len(rows) <= 2000
        for row in rows:
            key = (row["date"], source["code"])
            assert key not in prices
            prices[key] = row
    notices = {a["action_id"]: a for a in case["raw_source_bindings"]["corporate_actions"]}
    assert len(notices) == len(case["raw_source_bindings"]["corporate_actions"])
    for a in notices.values():
        assert a["facts_verified"] and a["implementation_status"] == "implementation_notice"
        assert not a["rights_issue"] and D(a["stock_distribution_per_share"]) == 0
        assert a["stock_distribution_kind"] == "none"
        assert a["tax_rule"] == "account_fifo_individual_differentiated_cash_dividend"
        assert a["account_type"] == "mainland_individual_post_ipo_unrestricted"
        assert D(a["short_holding_tax_rate"]) == D("0.20")
    trades = {t["event_id"]: t for t in result["trades"]}
    assert len(trades) == len(result["trades"])
    entries, ids = defaultdict(list), set()
    previous_at = None
    for index, entry in enumerate(result["journal"]):
        event = entry["event"]
        applied = datetime.fromisoformat(entry["applied_at"])
        assert applied >= datetime.fromisoformat(event["effective_at"])
        assert applied >= datetime.fromisoformat(event["available_at"])
        assert previous_at is None or applied >= previous_at
        previous_at = applied
        day = applied.astimezone(HK).date().isoformat()
        assert day in days and entry["sequence"] == index+1 and event["event_id"] not in ids
        ids.add(event["event_id"])
        entries[day].append(entry)
    cash, initial = D(0), D(result["initial_cash"])
    assert initial == D(case["initial_cash"]) > 0
    positions, lots, actions = defaultdict(int), {}, {}
    fee = defaultdict(Decimal)
    slip = gross_flow = dividend_net = paid_tax = D(0)
    high, drawdown, deposits = initial, D(0), 0
    daily, seen_trades, kinds = [], set(), Counter()
    head = None
    for day, saved in zip(days, result["daily"]):
        for entry in entries[day]:
            head = entry["entry_hash"]
            event, data = entry["event"], entry["event"]["data"]
            kind = event["kind"]
            kinds[kind] += 1
            stamp = datetime.fromisoformat(entry["applied_at"]).astimezone(HK)
            if kind == "cash_deposit":
                assert day == days[0] and not deposits and not lots and not actions
                assert entry["sequence"] == 1 and D(data["amount"]) == initial
                cash += initial
                deposits += 1
            elif kind in ("buy_fill", "sell_fill"):
                trade = trades[event["event_id"]]
                seen_trades.add(event["event_id"])
                code, q = data["symbol"], data["quantity"]
                buy = kind == "buy_fill"
                assert stamp.strftime("%H:%M:%S") == "09:30:00"
                assert type(q) is int and q > 0 and q % 100 == 0
                assert (day, code, q, kind) == (trade["date"], trade["symbol"], trade["quantity"], trade["side"]+"_fill")
                raw_open = D(prices[(day, code)]["raw_price_text"]["raw_open"])
                assert raw_open == D(trade["raw_open"])
                fill = (raw_open*(D("1.001") if buy else D("0.999"))).quantize(
                    D("0.01"), rounding=ROUND_CEILING if buy else ROUND_FLOOR)
                assert fill == D(trade["raw_price"]) == D(data["raw_price"])
                gross = fill*q
                fees = {"commission": cents(max(D(5), gross*D("0.0003"))),
                        "transfer_fee": cents(gross*D("0.00002")),
                        "stamp_duty": D(0) if buy else cents(gross*D("0.001"))}
                fees["total"] = sum(fees.values())
                assert all(D(trade["fees"][k]) == v for k,v in fees.items())
                assert D(data["fees"]) == fees["total"]
                for k,v in fees.items():
                    fee[k] += v
                adverse = abs(fill-raw_open)*q
                assert adverse == D(trade["slippage_amount"])
                slip += adverse
                if buy:
                    assert cash >= gross+fees["total"]
                    cash -= gross+fees["total"]
                    positions[code] += q
                    lots[event["event_id"]] = {"symbol":code, "day":day, "quantity":q}
                    gross_flow -= raw_open*q
                else:
                    assert positions[code] >= q
                    remaining, expected_allocations = q, {}
                    for lot_id, lot in lots.items():
                        if lot["symbol"] == code and lot["day"] < day and lot["quantity"] and remaining:
                            n = min(remaining, lot["quantity"])
                            expected_allocations[lot_id] = n
                            lot["quantity"] -= n
                            remaining -= n
                    assert not remaining and expected_allocations == data["lot_allocations"]
                    cash += gross-fees["total"]
                    positions[code] -= q
                    gross_flow += raw_open*q
            elif kind in {"record_entitlement", "activate_entitlement", "cash_dividend_paid", "tax_assessed", "tax_paid"}:
                action_id = data["action_id"]
                notice = notices[action_id]
                assert event["source"]["sha256"] == notice["source_sha256"]
                assert event["source"]["ref"] == notice["source_url"]
                if kind == "record_entitlement":
                    assert action_id not in actions and day == notice["record_date"]
                    assert stamp.strftime("%H:%M:%S") == "15:00:00"
                    code = notice["symbol"]
                    assert data["symbol"] == code and data["entitled_shares"] == positions[code] > 0
                    actions[action_id] = {"symbol":code, "entitled_shares":positions[code],
                        "registered_lots":{k:v["quantity"] for k,v in lots.items() if v["symbol"]==code and v["quantity"]},
                        "active":False, "gross":D(0), "paid":False, "due":None, "tax_paid":D(0), "tax_final":False}
                else:
                    action = actions[action_id]
                    if kind == "activate_entitlement":
                        assert not action["active"] and day == notice["ex_date"]
                        assert stamp.strftime("%H:%M:%S") == "08:00:00"
                        gross = cents(D(notice["gross_cash_per_share"])*action["entitled_shares"])
                        assert D(data["cash_gross_due"]) == gross > 0
                        assert data["bonus_shares"] == 0 and data["share_kind"] == "none"
                        assert data["tax_due"] is None and data["tax_final"] is False
                        action.update(active=True, gross=gross)
                    else:
                        assert action["active"]
                        cash_day = next(d for d in days if d > notice["cash_payment_date"])
                        if kind == "cash_dividend_paid":
                            assert not action["paid"] and day == cash_day
                            assert stamp.strftime("%H:%M:%S") == "08:00:00"
                            gross, net, withheld = (D(data[k]) for k in ("gross_amount", "net_cash_credit", "tax_withheld"))
                            assert gross == action["gross"] and net+withheld == gross
                            assert withheld == 0  # Frozen deferred-withholding simulation.
                            cash += net
                            dividend_net += net
                            action["paid"] = True
                        elif kind == "tax_assessed":
                            due = D(data["total_tax_due"])
                            assert not action["tax_final"] and due >= action["tax_paid"]
                            assert action["due"] is None or due >= action["due"]
                            assert D(0) <= due <= cents(action["gross"]*D("0.20"))
                            assert type(data["tax_final"]) is bool
                            action.update(due=due, tax_final=data["tax_final"])
                        else:
                            amount = D(data["amount"])
                            assert action["paid"] and day >= cash_day and action["due"] is not None
                            assert D(0) < amount <= action["due"]-action["tax_paid"]
                            action["tax_paid"] += amount
                            cash -= amount
                            paid_tax += amount
            else:
                raise AssertionError("Unsupported cash-flow event: "+kind)
            assert cash >= 0
        holdings = {c:q for c,q in positions.items() if q}
        for action_id, notice in notices.items():
            if day == notice["record_date"]:
                assert bool(holdings.get(notice["symbol"], 0)) == (action_id in actions)
        receivable = sum((a["gross"] for a in actions.values() if a["active"] and not a["paid"]), D(0))
        unpaid = sum(((a["due"] or D(0))-a["tax_paid"] for a in actions.values()), D(0))
        reserve = D(saved["remaining_tax_reserve"])
        assert reserve >= 0
        assert reserve <= sum((cents(a["gross"]*D("0.20")) for a in actions.values() if a["active"] and not a["tax_final"]), D(0))
        assert cash == D(saved["cash_available"]) and holdings == saved["holdings"]
        assert receivable == D(saved["cash_receivable_gross"]) and unpaid == D(saved["realized_tax_unpaid"])
        gross_nav = cash+receivable+sum(q*D(prices[(day,c)]["raw_price_text"]["raw_close"]) for c,q in holdings.items())
        nav = gross_nav-unpaid-reserve
        assert gross_nav == D(saved["gross_asset_value"]) and nav == D(saved["simulated_net_asset_value"])
        assert nav-initial == D(saved["simulated_net_pnl"]) and nav >= 0
        assert cash-unpaid-reserve == D(saved["conservative_cash_after_tax_reserve"])
        assert fee["total"] == D(saved["fees_paid_cumulative"])
        assert slip == D(saved["slippage_in_fill_prices_cumulative"])
        assert head == saved["valuation"]["ledger_head"]
        high = max(high, nav)
        drawdown = max(drawdown, 1-nav/high)
        daily.append({"date":day, "cash":str(cash), "nav":str(nav), "positions":holdings,
                      "receivable":str(receivable), "assessed_unpaid":str(unpaid), "recorded_reserve":str(reserve)})
    final = result["final_snapshot"]
    assert deposits == 1 and seen_trades == set(trades)
    assert final["event_count"] == len(result["journal"]) and final["journal_head"] == head
    assert D(final["cash"]) == cash and D(final["external_cash_flow"]) == initial
    assert D(final["fees_paid"]) == fee["total"] and set(final["lots"]) == set(lots)
    for lot_id, lot in lots.items():
        saved = final["lots"][lot_id]
        assert (saved["symbol"], saved["quantity"]) == (lot["symbol"], lot["quantity"])
    assert set(final["actions"]) == set(actions)
    for action_id, action in actions.items():
        saved = final["actions"][action_id]
        assert saved["registered_lots"] == action["registered_lots"]
        for key in ("symbol", "entitled_shares", "active", "tax_final"):
            assert saved[key] == action[key]
        assert saved["cash_paid"] == action["paid"]
        assert D(saved["cash_gross_due"]) == action["gross"]
        assert (None if saved["tax_due"] is None else D(saved["tax_due"])) == action["due"]
        assert D(saved["tax_paid"]) == action["tax_paid"]
        assert saved["bonus_entitled"] == saved["bonus_pending"] == 0
    return {"all_days_reconciled":True, "full_capital":str(initial), "final_cash":str(cash),
        "net_return":str(nav/initial-1), "maximum_drawdown":str(drawdown), "raw_open_cash_flow":str(gross_flow),
        "fees":{k:str(v) for k,v in fee.items()}, "positions_including_closed":dict(positions),
        "slippage_in_fill_prices_not_double_charged":str(slip), "net_dividend_credits":str(dividend_net),
        "tax_cash_paid":str(paid_tax), "journal_event_counts":dict(kinds), "daily":daily,
        "tax_classification_and_reserve_independently_recomputed":False,
        "tax_scope":"Recorded frozen adapter estimates; cash movements, assessment/payment balances and NAV treatment independently reconciled. Saved-only kernel replay separately verifies adapter outputs. No independent statutory/account certification.",
        "formal_score":None, "execution_certified":False}

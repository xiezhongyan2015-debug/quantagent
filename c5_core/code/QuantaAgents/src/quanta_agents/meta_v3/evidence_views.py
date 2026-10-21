"""Deterministic evidence views; no new research calculation or row selection."""
from collections import Counter, defaultdict
from decimal import Decimal

from .ledger import digest, serial


def page_for_context(page, project, wrap, requested_limit, maximum_bytes=8000):
    """Return an exact contiguous page that fits the entire context wrapper.

    The requested limit is an upper bound. Source tables remain in the prior
    artifact. Never jump past undisplayed rows or replace a row by a text slice.
    """
    rows = page["rows"]
    for count in range(len(rows), -1, -1):
        if rows and count == 0:
            break
        end = page["offset"]+count
        actual = {**page, "rows":rows[:count], "next_offset":end if end < page["total_rows"] else None}
        public = {**project(actual), "requested_limit":requested_limit, "returned_rows":count,
            "page_delivery":"Whole contiguous rows bounded by context bytes. Follow next_offset; requested limit is a maximum. Full source table remains in prior artifact."}
        if len(serial(wrap(actual, public)).encode("utf-8")) <= maximum_bytes:
            return actual, public
    # One raw row can exceed the entire budget. Preserve it, report a blocked
    # delivery and leave the cursor unchanged rather than silently skipping it.
    if not rows:
        raise ValueError("empty page metadata cannot fit context budget")
    public = {"offset":page["offset"], "next_offset":page["offset"], "total_rows":page["total_rows"],
        "requested_limit":requested_limit, "returned_rows":0, "delivery_blocked":True,
        "oversized_row_sha256":digest(rows[0]),
        "page_delivery":"One row exceeds context byte budget. Original retained; no row delivered and cursor did not advance. Use compact view if available; otherwise report unavailable evidence. Do not repeat this unchanged request."}
    if len(serial(wrap(page, public)).encode("utf-8")) > maximum_bytes:
        raise ValueError("page metadata cannot fit context budget")
    return page, public


def account_summary(result):
    """All full-capital cash days, no annualization or strategy selection."""
    initial = Decimal(result["initial_cash"])
    high = initial
    drawdown = Decimal(0)
    for row in result["daily"]:
        nav = Decimal(row["simulated_net_asset_value"])
        high = max(high, nav)
        drawdown = max(drawdown, 1-nav/high)
    return {"initial_cash": str(initial), "final_net_asset_value": str(nav),
        "net_return_on_full_initial_cash":str(nav/initial-1), "maximum_daily_drawdown":str(drawdown),
        "cash_days_including_no_position_days":len(result["daily"]),
        "daily_dates_sha256":digest([r["date"] for r in result["daily"]]),
        "order_status_counts":dict(Counter(o["status"] for o in result["orders"])),
        "scope":"Descriptive accounting across every saved day, not verified OOS/capacity or annualized Sharpe. Partial orders and failures remain included."}


def horizon_summary(report):
    # Group membership and individual outcomes remain in the saved original.
    # Repeating them here previously hid the group statistics in an excerpt.
    return {**{k: report[k] for k in ("status", "horizons", "curve", "denominators", "scope", "limitations", "policy")},
        "descriptive_conditioning": [{k: v for k, v in group.items() if k != "rows"}
                                     for group in report["descriptive_conditioning"]],
        "view": "All saved horizon/group aggregate results; individual rows and identity metadata omitted from this view. Full artifact hash binds originals. Use events_compact for every event including exclusions."}


def compact_events(page):
    """Columnar projected fields for every requested row, in original order."""
    columns = ["event_id", "symbol", "signal_date", "feature", "included_common_sample",
               "entry_date", "entry_open", "exclusion_reasons", "outcomes"]
    outcome_columns = ["horizon_sessions", "exit_date", "exit_open", "gross_return",
                       "individually_available", "exclusion_reasons"]
    rows = []
    for row in page["rows"]:
        item = [row[k] for k in columns[:-1]]
        item.append([[outcome[k] for k in outcome_columns] for outcome in row["outcomes"].values()])
        rows.append(item)
    return {**{k: page[k] for k in ("offset", "next_offset", "total_rows")},
        "columns": columns, "outcome_columns": outcome_columns, "rows": rows,
        "source_page_sha256": digest(page), "returned_rows": len(rows),
        "projection": "Every requested row retained; no filtering, sorting, rounding or imputation. Session indices, repeated nominal clocks and per-row payload hashes omitted; full rows remain in events. Gross outcomes are post-signal development observations, not fills or portfolio returns."}


def compact_execution(page, table):
    """All requested accounting rows; omit repeated provenance, never amounts."""
    if table == "daily_compact":
        columns = ["date", "cash_available", "cash_receivable_gross", "realized_tax_unpaid",
            "remaining_tax_reserve", "conservative_cash_after_tax_reserve", "gross_asset_value",
            "simulated_net_asset_value", "simulated_net_pnl", "fees_paid_cumulative",
            "slippage_in_fill_prices_cumulative", "holdings", "orders", "rejected_orders", "stock_day_denominator"]
        rows = [[row[k] for k in columns] for row in page["rows"]]
    elif table == "trades_compact":
        columns = ["date", "symbol", "side", "quantity", "raw_open", "raw_price", "slippage_amount",
            "fees.total", "fees.commission", "fees.transfer_fee", "fees.stamp_duty",
            "opening_evidence.prior_day", "opening_evidence.prior_volume", "opening_evidence.prior_name",
            "opening_evidence.reference", "opening_evidence.target.available_at",
            "opening_evidence.target.target_weight", "event_id"]
        rows = []
        for row in page["rows"]:
            values = []
            for key in columns:
                value = row
                for part in key.split("."):
                    value = value[part]
                values.append(value)
            rows.append(values)
    else:
        raise ValueError("unknown compact execution table")
    return {**{k: page[k] for k in ("offset", "next_offset", "total_rows")},
        "columns": columns, "rows": rows, "returned_rows": len(rows), "source_page_sha256": digest(page),
        "projection": "Every requested row retained in original order; no rounding or filtering. Full nested valuation/source records and repeated fee-policy metadata omitted. Original daily/trades pages remain available. Slippage is already in fill prices; do not charge it again. These are declared simulations, not execution certification."}


def input_coverage(case):
    fixture = case["decision_fixture"]
    rows = []
    for field in fixture["fields"]:
        name = field["name"]
        group = [r for r in fixture["field_rows"] if r["field"] == name]
        rows.append({"kind": "field_coverage", "field": name, "rows": len(group),
            "expected_cells": len(fixture["calendar"]) * len(fixture["codes"]),
            "null_values": sum(r["value"] is None for r in group),
            "availability_min": min(r["available_at"] for r in group),
            "availability_max": max(r["available_at"] for r in group),
            "available_after_effective": sum(r["available_at"] > r["effective_at"] for r in group),
            "rows_sha256": digest(group), "field_contract": field})
    grouped = defaultdict(list)
    for row in case["raw_source_bindings"]["obligations"]:
        grouped[(row["kind"], row["status"], row["note"])].append(row)
    for (kind, status, note), group in sorted(grouped.items()):
        rows.append({"kind": "execution_obligation_coverage", "obligation": kind, "status": status,
            "rows": len(group), "symbols": sorted({r["code"] for r in group}),
            "first_date": min(r["date"] for r in group), "last_date": max(r["date"] for r in group),
            "note": note, "rows_sha256": digest(group)})
    rows.append({"kind": "whole_input_scope", "symbols": fixture["codes"],
        "sessions": len(fixture["calendar"]), "first_session": fixture["calendar"][0],
        "last_session": fixture["calendar"][-1], "initial_cash": case["initial_cash"],
        "obligation_status_counts": dict(Counter(r["status"] for r in case["raw_source_bindings"]["obligations"])),
        "interpretation": "Coverage describes saved inputs, not externally verified availability, membership or real fills. Inspect original rows for specific contradictions."})
    return rows

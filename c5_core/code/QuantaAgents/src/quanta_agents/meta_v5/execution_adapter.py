"""Extract continuous paths from verified V4 artifacts, without new market reads."""
from decimal import Decimal

from quanta_agents.meta_v3.ledger import need
from quanta_agents.meta_v3.research_iteration import account_attribution


def saved_path(artifact, evidence_id, calendar, initial_cash):
    if artifact is None:
        return None
    raw = artifact.get("raw") or {}
    body = raw.get("result") or raw.get("partial") or {}
    if not body:
        # A failed compile/pre-execution attempt is an unobserved account, not
        # a zero-return account. Preserve it as missing evidence in analytics.
        return None
    initial = body.get("initial_cash")
    snapshot = body.get("final_snapshot") or {}
    need(initial is not None and Decimal(str(initial)) == Decimal(str(initial_cash)), "saved initial capital differs")
    flow = snapshot.get("external_cash_flow")
    # A missing snapshot cannot establish no deposits/withdrawals.
    need(flow is not None and Decimal(str(flow)) == Decimal(str(initial_cash)), "saved cash-flow identity unknown or changed")
    attr = account_attribution(evidence_id, artifact, calendar)
    raw_days = body.get("daily", [])
    need([r["date"] for r in raw_days] == calendar[:len(raw_days)], "saved account must retain its original continuous calendar prefix")
    by_date = {r["date"]: r for r in attr["daily"]}
    fees, exposures, stale = {}, {}, {}
    prior_fee = Decimal(0)
    for row in raw_days:
        day = row["date"]
        cumulative = row.get("fees_paid_cumulative")
        current = Decimal(str(cumulative)) if cumulative is not None else None
        need(current is None or current.is_finite() and current >= 0, "invalid cumulative fees")
        if current is not None and prior_fee is not None:
            need(current >= prior_fee, "cumulative fees decreased")
            fees[day] = float(current - prior_fee)
        else:
            fees[day] = None
        prior_fee = current
        r = by_date[day]
        marked, cash, receivable = (Decimal(r[k]) for k in ("marked_share_value", "cash", "gross_receivable"))
        gross = marked + cash + receivable
        exposures[day] = float(marked / gross) if gross > 0 else None
        valuation = row.get("valuation", {})
        raw_valuation = valuation.get("raw_ledger_valuation", {})
        marks = raw_valuation.get("mark_evidence")
        held = row.get("holdings")
        halted = row.get("halted_position_estimates", valuation.get("halted_position_estimates"))
        if halted is not None:
            # The structural engine explicitly records carried halt marks.
            stale[day] = len(halted)
        elif isinstance(marks, dict) and isinstance(held, dict) and set(held) <= set(marks):
            stale[day] = sum(m.get("effective_at", "")[:10] != day for m in marks.values())
        else:
            stale[day] = None
    return {"nav": [float(by_date[d]["nav"]) if d in by_date else None for d in calendar],
            "exposure": [exposures.get(d) for d in calendar], "fees": [fees.get(d) for d in calendar],
            "stale": [stale.get(d) for d in calendar]}

"""Long-only raw-price portfolio accounting simulation, never execution proof.

Opening decisions use current raw open/reference price, strictly earlier targets,
and previous-session volume/name proxies. Current OHLC quality flags and closing
volume never decide opening fills. The archive's historical arrival is unknown.
Every requested session is retained. Documented full-session halts use explicitly
labelled last-close accounting estimates; other missing held closes fail closed.
Versioned from frozen v18; the original matching/fee/cash primitives are reused.
Mixed stock-distribution tax lineage remains unsupported and cannot be omitted.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import wraps
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
import re
from typing import Any, Sequence

import pandas as pd

from .kernel import module
from .suspension_market import SuspensionMarket
_CA = module("corporate_action_adapter")
CashDividendAdapter = _CA.CashDividendAdapter
CashDividendAnnouncement = _CA.CashDividendAnnouncement
portfolio_tax_valuation = _CA.portfolio_tax_valuation
_RL = module("raw_share_ledger")
LedgerError, RawShareLedger = _RL.LedgerError, _RL.RawShareLedger

CENT = Decimal("0.01")
ZERO = Decimal("0")
CHINA = timezone(timedelta(hours=8))
VERSION = "v3-structural-portfolio-v1"


class PortfolioError(ValueError):
    """Invalid or incomplete replay inputs; no result may be promoted."""


class CheckpointPersistenceError(RuntimeError):
    """A controller checkpoint failed; trading must stop immediately."""


class _CheckpointState:
    """Controller persistence hook; no change to trading or accounting rules."""

    def __init__(self, callback):
        if not callable(callback):
            raise TypeError("checkpoint must be callable")
        self.callback, self.ledger, self.progress, self.broken = callback, None, {}, False

    def emit(self, phase, **details):
        value = {"phase": phase, "execution_valid": False, "formal_target_success": False,
                 "progress": self.progress, **details}
        if self.ledger is not None:
            value.update(genesis=self.ledger.genesis, journal=self.ledger.journal,
                         snapshot=self.ledger.snapshot())
        # Detach mutable order lists before handing them to external persistence.
        value = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        try:
            self.callback(value)
        except Exception as exc:
            self.broken = True
            raise CheckpointPersistenceError(str(exc)) from exc
        except BaseException:
            self.broken = True
            raise

    def ledger_class(self):
        observer = self

        class ObservedLedger(RawShareLedger):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                observer.ledger = self

            def apply(self, event, *, as_of):
                observer.emit("event_intent", event=event, as_of=as_of)
                try:
                    result = super().apply(event, as_of=as_of)
                except LedgerError as exc:
                    observer.emit("event_rejected", event=event, as_of=as_of,
                                  error={"type": type(exc).__name__, "message": str(exc)})
                    raise
                observer.emit("event_applied", event=event, as_of=as_of, application=result)
                return result

        return ObservedLedger


def _with_checkpoints(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        callback = kwargs.pop("checkpoint", None)
        if callback is None:
            return function(*args, **kwargs)
        observer = _CheckpointState(callback)
        try:
            result = function(*args, checkpoint=observer, **kwargs)
            observer.emit("simulation_completed", result=result)
            return result
        except Exception as exc:
            if not observer.broken:
                observer.emit("simulation_failed", error={"type": type(exc).__name__, "message": str(exc)})
            raise
    return wrapped


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _serial(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _serial(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serial(item) for item in value]
    return value


def _day(value: Any) -> str:
    if isinstance(value, (datetime, pd.Timestamp)):
        if value.tzinfo is not None:
            raise PortfolioError("date identity must not carry a timezone")
        value = value.date().isoformat()
    elif isinstance(value, date):
        value = value.isoformat()
    if type(value) is not str:
        raise PortfolioError("date identity requires YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PortfolioError("invalid date identity") from exc
    if parsed.isoformat() != value:
        raise PortfolioError("noncanonical date identity")
    return value


def _at(day: str, clock: str) -> str:
    return day + "T" + clock + "+08:00"


def _time(value: Any) -> datetime:
    if type(value) is not str:
        raise PortfolioError("available_at requires timezone-aware ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortfolioError("invalid available_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PortfolioError("available_at requires explicit timezone")
    return parsed.astimezone(CHINA)


def _symbol(value: Any) -> str:
    if type(value) is not str:
        raise PortfolioError("stock identity must be an explicit string")
    text = value.lower()
    if re.fullmatch(r"\d{6}", text):
        text = ("sh" if text[0] in "69" else "sz") + text
    if not re.fullmatch(r"(?:sh[69]\d{5}|sz[023]\d{5})", text):
        raise PortfolioError("invalid or mismatched exchange/stock identity")
    return text


def _decimal(value: Any, label: str, *, positive=False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PortfolioError("missing/invalid " + label)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PortfolioError("invalid " + label) from exc
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise PortfolioError("missing/nonpositive " + label)
    return result


def _cents(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _price(row: dict | None, field: str) -> Decimal:
    if row is None:
        raise PortfolioError("missing daily row:" + field)
    # The loader preserves raw lexical prices even if unrelated EOD validation
    # fails. Its composite accepted flag is deliberately NEVER an opening gate.
    lexical = row.get("raw_price_text")
    value = lexical.get(field) if isinstance(lexical, dict) and field in lexical else row.get(field)
    result = _decimal(value, field, positive=True)
    if field != "raw_prev_close" and result % CENT:
        raise PortfolioError("raw price off 0.01 tick:" + field)
    return result


def fee_breakdown(side: str, trade_date: str, quantity: int, raw_price: Any) -> dict:
    """Frozen CNY fee simulation; component rounding is explicitly half-up."""
    day = _day(trade_date)
    if day < "2015-08-01":
        raise PortfolioError("pre-2015-08-01 transfer fee schedule unsupported")
    if side not in {"buy", "sell"} or type(quantity) is not int or quantity <= 0:
        raise PortfolioError("invalid fee order")
    price = _decimal(raw_price, "raw_price", positive=True)
    if price % CENT:
        raise PortfolioError("raw price off tick")
    gross = price * quantity
    transfer_rate = Decimal("0.00001" if day >= "2022-04-29" else "0.00002")
    stamp_rate = Decimal("0") if side == "buy" else Decimal("0.0005" if day >= "2023-08-28" else "0.001")
    commission = max(Decimal("5"), gross * Decimal("0.0003")).quantize(CENT, rounding=ROUND_HALF_UP)
    transfer = (gross * transfer_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    stamp = (gross * stamp_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"gross_amount": _cents(gross), "commission": _cents(commission),
            "transfer_fee": _cents(transfer), "stamp_duty": _cents(stamp),
            "total": _cents(commission + transfer + stamp),
            "transfer_rate": str(transfer_rate), "stamp_rate": str(stamp_rate),
            "commission_rate": "0.0003", "commission_minimum": "5.00", "rounding": "component_half_up_simulated"}


def _holdings(state: dict) -> dict[str, int]:
    result = {}
    for lot in state["lots"].values():
        if lot["quantity"]:
            result[lot["symbol"]] = result.get(lot["symbol"], 0) + lot["quantity"]
    return result


def _allocations(state: dict, symbol: str, quantity: int, moment: str) -> dict:
    remaining, result = quantity, {}
    lots = sorted(state["lots"].items(), key=lambda x: (x[1]["acquired_at"], x[0]))
    for key, lot in lots:
        if lot["symbol"] == symbol and _time(lot["sellable_at"]) <= _time(moment):
            take = min(remaining, lot["quantity"])
            if take:
                result[key] = take
                remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise PortfolioError("T+1 or insufficient sellable shares")
    return result


@_with_checkpoints
def simulate_raw_portfolio(*, daily_data: pd.DataFrame, calendar: Sequence[str], targets: pd.DataFrame,
                           initial_cash: Decimal | str = "1000000", corporate_actions: Sequence[CashDividendAnnouncement] = (),
                           allow_incomplete_for_integration: bool = False, participation_rate: Decimal | str = "0.05",
                           slippage_fraction: Decimal | str = "0.001",
                           rounding_policy: str = "exact_only", start_date: str | None = None,
                           end_date: str | None = None, market_schedule=None, checkpoint=None) -> dict:
    """Replay partial target maps; omitted symbols keep holdings, weight 0 sells.

    Targets: symbol (or code), signal_date, trade_date, available_at,
    target_weight. Every day's stated weights must sum to <=1. A failed symbol's
    allocated budget stays cash; remaining symbols are NEVER renormalized.
    Calendar is an explicitly supplied complete session list, including warmup
    and a next session for T+1. Its historical publication is not certified.
    All simulations currently require the explicit incomplete-coverage opt-in.
    Optional checkpoint receives detached pre/post-event, daily and terminal
    evidence. A persistence error stops immediately; it never permits a fill
    to continue. Saved-only recovery must not call this simulation again.
    """
    if allow_incomplete_for_integration is not True:
        raise PortfolioError("corporate-action coverage is unverified; explicit allow_incomplete_for_integration=True required")
    if not isinstance(daily_data, pd.DataFrame) or not isinstance(targets, pd.DataFrame):
        raise PortfolioError("daily_data and targets must be DataFrames")
    if isinstance(calendar, str) or len(calendar) == 0:
        raise PortfolioError("explicit nonempty calendar required")
    days = [_day(value) for value in calendar]
    if days != sorted(set(days)):
        raise PortfolioError("calendar must be unique and ordered")
    rows = {}
    if not {"date", "raw_open", "raw_close", "raw_prev_close", "volume", "stock_name"} <= set(daily_data.columns):
        raise PortfolioError("required raw daily columns missing")
    if not ({"symbol", "code"} & set(daily_data.columns)):
        raise PortfolioError("daily stock identity missing")
    for row in daily_data.to_dict("records"):
        day = _day(row["date"])
        symbol = _symbol(row.get("symbol", row.get("code")))
        if "code" in row and _symbol(row["code"]) != symbol:
            raise PortfolioError("conflicting stock identities")
        if day not in days:
            raise PortfolioError("data date outside explicit calendar")
        if (day, symbol) in rows:
            raise PortfolioError("duplicate stock/date identity")
        rows[day, symbol] = row
    if not rows:
        raise PortfolioError("empty daily data")
    first = _day(start_date) if start_date else min(key[0] for key in rows)
    last = _day(end_date) if end_date else max(key[0] for key in rows)
    if first not in days or last not in days or first > last:
        raise PortfolioError("simulation boundaries not covered by calendar")
    replay_days = [day for day in days if first <= day <= last]
    if first < "2015-08-01":
        raise PortfolioError("simulation predates supported fees")
    cash = _decimal(initial_cash, "initial_cash", positive=True)
    if cash % CENT:
        raise PortfolioError("initial cash must be exact cents")
    participation = _decimal(participation_rate, "participation_rate", positive=True)
    if participation > 1:
        raise PortfolioError("participation_rate must be <=1")
    slippage = _decimal(slippage_fraction, "slippage_fraction")
    if slippage >= 1:
        raise PortfolioError("slippage_fraction must be <1")
    required_targets = {"signal_date", "trade_date", "available_at", "target_weight"}
    if not required_targets <= set(targets.columns) or not ({"symbol", "code"} & set(targets.columns)):
        raise PortfolioError("required target columns missing")
    by_day, seen = {}, set()
    for raw_target in targets.to_dict("records"):
        target = dict(raw_target)
        symbol = _symbol(target.get("symbol", target.get("code")))
        if "code" in target and _symbol(target["code"]) != symbol:
            raise PortfolioError("conflicting target stock identities")
        trade_day, signal_day = _day(target["trade_date"]), _day(target["signal_date"])
        if trade_day not in replay_days or signal_day >= trade_day:
            raise PortfolioError("target must have signal_date<trade_date within replay")
        available = _time(target["available_at"])
        if available > _time(_at(trade_day, "09:30:00")) or available.date().isoformat() < signal_day:
            raise PortfolioError("target availability outside causal signal/open boundary")
        if (trade_day, symbol) in seen:
            raise PortfolioError("duplicate target stock/date identity")
        seen.add((trade_day, symbol))
        weight = _decimal(target["target_weight"], "target_weight")
        if weight > 1:
            raise PortfolioError("target weight >1")
        by_day.setdefault(trade_day, []).append({"symbol": symbol, "trade_date": trade_day,
            "signal_date": signal_day, "available_at": available.isoformat(), "target_weight": str(weight)})
    for group in by_day.values():
        if sum((Decimal(row["target_weight"]) for row in group), ZERO) > 1:
            raise PortfolioError("daily target weights sum above one")
    adapters = [CashDividendAdapter(spec, days, rounding_policy=rounding_policy) for spec in corporate_actions]
    action_manifests = [adapter.manifest() for adapter in adapters]
    market = SuspensionMarket(market_schedule, sorted({key[1] for key in rows}), days,
                              [m["announcement"] for m in action_manifests])
    market.verify_sources()
    market.validate_rows(rows)
    ids = [manifest["announcement"]["action_id"] for manifest in action_manifests]
    if len(set(ids)) != len(ids):
        raise PortfolioError("duplicate corporate action identity")
    policy = {"version": VERSION, "price_basis": "raw", "initial_cash": _cents(cash),
        "participation_rate": str(participation), "capacity_basis": "previous explicit session volume; estimate only, no matching proof",
        "opening_fill": "raw open times (1+slippage) for buy, (1-slippage) for sell; buy tick-ceiling, sell tick-floor; adverse limit touched or crossed rejects, never clips",
        "slippage_fraction": str(slippage), "slippage_accounting": "included in fill price and position sizing; diagnostic slippage amount is not charged again",
        "limit_reference": "vendor raw_prev_close, no second dividend subtraction; half-up to 0.01",
        "market_status": "previous-session stock_name proxy, normal mainboard 10%, ST5%; vintage unverified",
        "unsupported": "other boards, IPO/delisting name proxies; market-wide special-status completeness unknown",
        "ordering": "sell before buy; symbols lexical within side; partial targets leave omitted positions unchanged",
        "target_budget": "previous EOD simulated net NAV after tax reserve; no renormalization on rejected stocks",
        "cash_budget": "cash less prior EOD tax reserve net of taxes paid since that EOD, plus maximum-rate tax reserve for newly activated actions; reserve is not an actual tax assessment",
        "fees": "commission 3bp min5 includes trading handling fees as assumption; transfer2/100000 from2015-08-01 then1/100000 from2022-04-29; sell stamp1/1000 then0.5/1000 from2023-08-28",
        "fee_rounding": "per component aggregate half-up simulated", "corporate_action_rounding_policy": rounding_policy,
        "source_available_at": "unknown; opening and EOD use are frozen simulated economic-time conventions",
        "calendar_completeness": "caller supplied explicit sessions; uniqueness/range checked, external completeness not certified",
        "allow_incomplete_for_integration": True}
    policy["suspension_schedule"] = market.schedule
    policy["suspension_schedule_sha256"] = market.sha256
    policy_hash = _hash(policy)

    def source(evidence: dict) -> dict:
        return {"ref": "simulated-input://raw-portfolio", "sha256": _hash(evidence), "verified": True,
                "evidence_type": "simulated", "assumption_ref": "frozen-policy://" + VERSION,
                "assumption_sha256": policy_hash}

    ledger_type = checkpoint.ledger_class() if checkpoint is not None else RawShareLedger
    ledger = ledger_type(days, calendar_source=source({"calendar": days}),
                            calendar_available_at=_at(first, "00:00:00"))
    ledger.apply({"event_id": "initial-capital", "kind": "cash_deposit", "effective_at": _at(first, "07:00:00"),
                  "available_at": _at(first, "07:00:00"), "source": source({"initial_cash": _cents(cash)}),
                  "data": {"amount": _cents(cash)}}, as_of=_at(first, "07:00:00"))
    orders, trades, daily, skipped_actions = [], [], [], []
    if checkpoint is not None:
        checkpoint.progress = {"orders": orders, "trades": trades, "daily": daily, "skipped_actions": skipped_actions}
    previous_nav, previous_tax_unpaid, previous_tax_paid = cash, ZERO, ZERO
    slippage_paid = ZERO
    for day in replay_days:
        if checkpoint is not None:
            checkpoint.progress["current_date"] = day
            checkpoint.emit("day_started", date=day)
        opening, eod = _at(day, "09:30:00"), _at(day, "15:05:00")
        # Cash events and activation are all pre-open. Registration occurs after
        # today's trading; no-held registration is explicitly retained as skip.
        scheduled = []
        newly_activated_reserve = ZERO
        for adapter, manifest in zip(adapters, action_manifests):
            for field, stage, priority in [("activate_at", "activate", 0), ("cash_available_at", "cash", 1)]:
                if manifest[field][:10] == day:
                    scheduled.append((manifest[field], priority, manifest["announcement"]["action_id"], stage, adapter))
        for moment, _, action_id, stage, adapter in sorted(scheduled, key=lambda row: row[:3]):
            if action_id not in ledger.snapshot()["actions"]:
                skipped_actions.append({"date": day, "action_id": action_id, "stage": stage, "reason": "no_recorded_entitlement"})
                continue
            events = [adapter.activation_event(ledger)] if stage == "activate" else adapter.settlement_events(ledger)
            for event in events:
                ledger.apply(event, as_of=moment)
            if stage == "activate":
                action = ledger.snapshot()["actions"][action_id]
                # No current-day EOD tax estimate is available at the open.
                # Reserve the maximum supported rate conservatively instead;
                # actual FIFO tax assessment still waits for completed EOD.
                rate = Decimal(adapter.manifest()["announcement"]["short_holding_tax_rate"])
                newly_activated_reserve += (Decimal(action["cash_gross_due"]) * rate).quantize(CENT, rounding=ROUND_CEILING)
        opening_state = ledger.snapshot()
        held_at_open = _holdings(opening_state)
        paid_now = sum((Decimal(action["tax_paid"]) for action in opening_state["actions"].values() if action["active"]), ZERO)
        reserved_cash = max(previous_tax_unpaid - (paid_now - previous_tax_paid), ZERO) + newly_activated_reserve
        intents = []
        for target in sorted(by_day.get(day, []), key=lambda row: row["symbol"]):
            symbol = target["symbol"]
            order = {**target, "budget_previous_tax_net_nav": _cents(previous_nav),
                "target_budget": _cents(previous_nav * Decimal(target["target_weight"])),
                "opening_tax_cash_reserve": _cents(reserved_cash),
                "status": "pending", "filled_quantity": 0, "reasons": [], "capacity_is_estimate": True}
            orders.append(order)
            try:
                if market.halted(day, symbol):
                    raise PortfolioError("documented full-session suspension; no tradable quote or fill")
                if symbol[2:5] not in {"000", "001", "002", "003", "600", "601", "603", "605"}:
                    raise PortfolioError("unsupported board")
                row = rows.get((day, symbol))
                price, reference = _price(row, "raw_open"), _price(row, "raw_prev_close")
                pos = days.index(day)
                prior_day = days[pos - 1] if pos else None
                prior = rows.get((prior_day, symbol))
                if prior is None:
                    raise PortfolioError("missing previous-session capacity/status evidence")
                volume = _decimal(prior.get("volume"), "previous-session volume")
                if volume % 1:
                    raise PortfolioError("previous-session volume not integer shares")
                name = prior.get("stock_name")
                if type(name) is not str or not name.strip():
                    raise PortfolioError("missing previous-session stock name proxy")
                name = re.sub(r"\s+", "", name).upper()
                name = re.sub(r"^(?:XD|XR|DR)+", "", name)
                if name.startswith(("N", "C")) or "退" in name:
                    raise PortfolioError("unsupported IPO/delisting status proxy")
                limit_rate = Decimal("0.05") if name.startswith(("ST", "*ST", "SST", "S*ST")) else Decimal("0.10")
                upper = (reference * (1 + limit_rate)).quantize(CENT, rounding=ROUND_HALF_UP)
                lower = (reference * (1 - limit_rate)).quantize(CENT, rounding=ROUND_HALF_UP)
                if not lower <= price <= upper:
                    raise PortfolioError("raw open outside assumed price band")
                budget = previous_nav * Decimal(target["target_weight"])
                desired = int((budget / price / 100).to_integral_value(rounding=ROUND_FLOOR)) * 100
                current = held_at_open.get(symbol, 0)
                delta = desired - current
                side = "buy" if delta > 0 else "sell" if delta < 0 else "none"
                fill_price = price
                if side != "none":
                    factor = 1 + slippage if side == "buy" else 1 - slippage
                    fill_price = (price * factor).quantize(CENT, rounding=ROUND_CEILING if side == "buy" else ROUND_FLOOR)
                    if fill_price <= 0:
                        raise PortfolioError("slipped fill price is nonpositive")
                    sized = int((budget / fill_price / 100).to_integral_value(rounding=ROUND_FLOOR)) * 100
                    # Friction may remove a small intended adjustment. It must
                    # never reverse that adjustment into an opposite-side fill.
                    desired = max(current, sized) if side == "buy" else min(current, sized)
                    delta = desired - current
                cap = int((volume * participation).to_integral_value(rounding=ROUND_FLOOR))
                order.update(side=side, raw_open=_cents(price), raw_prev_close=str(reference), desired_quantity=desired,
                    current_quantity=current, requested_quantity=abs(delta), lagged_volume_date=prior_day,
                    lagged_volume=int(volume), capacity_estimate_quantity=cap, name_proxy=name,
                    proposed_fill_price=_cents(fill_price), slippage_fraction=str(slippage),
                    assumed_limit_rate=str(limit_rate), assumed_upper_limit=_cents(upper), assumed_lower_limit=_cents(lower))
                if delta == 0:
                    order["status"] = "no_change"
                    continue
                if side == "buy" and fill_price >= upper:
                    raise PortfolioError("buy slipped fill touches/crosses assumed upper limit; queue unknown")
                if side == "sell" and fill_price <= lower:
                    raise PortfolioError("sell slipped fill touches/crosses assumed lower limit; queue unknown")
                quantity = min(abs(delta), cap)
                if side == "buy":
                    quantity = quantity // 100 * 100
                else:
                    quantity = min(quantity, ledger.sellable_quantity(symbol, as_of=opening))
                    # Round partial sales to round lots; complete exits may sell
                    # an existing odd lot if one entered through a future adapter.
                    if quantity != current:
                        quantity = quantity // 100 * 100
                if quantity <= 0:
                    raise PortfolioError("capacity estimate or T+1 yields no tradable lot")
                evidence = {"target": target, "open": _cents(price), "fill_price": _cents(fill_price),
                            "slippage_fraction": str(slippage), "reference": str(reference),
                            "prior_day": prior_day, "prior_volume": str(volume), "prior_name": name}
                intents.append((side, symbol, quantity, fill_price, order, evidence))
            except PortfolioError as exc:
                order.update(status="rejected", reasons=[str(exc)])
        for side, symbol, wanted, price, order, evidence in sorted(intents, key=lambda row: (row[0] != "sell", row[1])):
            quantity = wanted
            if side == "buy":
                available_cash = max(Decimal(ledger.snapshot()["cash"]) - reserved_cash, ZERO)
                # Monotone binary search includes component rounding/minimum fee
                # without an unbounded per-lot decrement loop.
                lo, hi = 0, quantity // 100
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    fees = fee_breakdown(side, day, mid * 100, price)
                    if price * mid * 100 + Decimal(fees["total"]) <= available_cash:
                        lo = mid
                    else:
                        hi = mid - 1
                quantity = lo * 100
            if quantity == 0:
                order.update(status="rejected", reasons=["insufficient cash including fees/tax reserve"])
                continue
            fees = fee_breakdown(side, day, quantity, price)
            event_id = f"portfolio:{day}:{side}:{symbol}"
            data = {"symbol": symbol, "quantity": quantity, "raw_price": _cents(price), "fees": fees["total"]}
            if side == "sell":
                data["lot_allocations"] = _allocations(ledger.snapshot(), symbol, quantity, opening)
            event = {"event_id": event_id, "kind": side + "_fill", "effective_at": opening,
                     "available_at": opening, "source": source(evidence), "data": data}
            try:
                ledger.apply(event, as_of=opening)
            except LedgerError as exc:
                order.update(status="rejected", reasons=[str(exc)])
                continue
            residual = order["requested_quantity"] - quantity
            adverse_per_share = abs(price - Decimal(order["raw_open"]))
            adverse_amount = adverse_per_share * quantity
            slippage_paid += adverse_amount
            order.update(status="partial" if residual else "filled", filled_quantity=quantity,
                         unfilled_quantity=residual, fees=fees,
                         fill_price=_cents(price), slippage_per_share=_cents(adverse_per_share),
                         slippage_amount=_cents(adverse_amount),
                         reasons=["capacity/T+1/cash constraints; residual retained"] if residual else [])
            trades.append({"event_id": event_id, "date": day, "symbol": symbol, "side": side,
                           "quantity": quantity, "raw_price": _cents(price), "fees": fees,
                           "raw_open": order["raw_open"], "slippage_fraction": str(slippage),
                           "slippage_per_share": _cents(adverse_per_share), "slippage_amount": _cents(adverse_amount),
                           "opening_evidence": evidence, "simulated_fill": True, "execution_valid": False})
        for adapter, manifest in sorted(zip(adapters, action_manifests), key=lambda pair: pair[1]["announcement"]["action_id"]):
            action_id, symbol = manifest["announcement"]["action_id"], manifest["announcement"]["symbol"]
            if manifest["record_at"][:10] == day:
                if _holdings(ledger.snapshot()).get(symbol, 0):
                    event = adapter.record_event(ledger)
                    ledger.apply(event, as_of=manifest["record_at"])
                else:
                    skipped_actions.append({"date": day, "action_id": action_id, "stage": "record", "reason": "no_held_shares_at_registration"})
        for adapter, manifest in zip(adapters, action_manifests):
            action = ledger.snapshot()["actions"].get(manifest["announcement"]["action_id"])
            if action and action["active"]:
                adapter.validate_eod_integrity(ledger)
                if not action["tax_final"]:
                    ledger.apply(adapter.tax_assessment_event(ledger, as_of=eod), as_of=eod)
                action = ledger.snapshot()["actions"][manifest["announcement"]["action_id"]]
                if action["cash_paid"] and action["tax_due"] is not None and Decimal(action["tax_due"]) > Decimal(action["tax_paid"]):
                    ledger.apply(adapter.tax_payment_event(ledger, as_of=eod), as_of=eod)
        holdings = _holdings(ledger.snapshot())
        marks, stale_marks = {}, {}
        for symbol in holdings:
            estimated = market.mark(rows, day, symbol, holdings[symbol], eod)
            if estimated is not None:
                marks[symbol], stale_marks[symbol] = estimated
                continue
            try:
                close = _price(rows.get((day, symbol)), "raw_close")
            except PortfolioError as exc:
                raise PortfolioError(f"held-stock close unavailable on {day}/{symbol}; NAV must not drop holding: {exc}") from exc
            marks[symbol] = {"raw_price": _cents(close), "effective_at": eod, "available_at": eod,
                             "source": source({"date": day, "symbol": symbol, "raw_close": _cents(close), "mark_time": "15:05 simulated"})}
        valuation = portfolio_tax_valuation(ledger, adapters, marks, as_of=eod)
        valuation["halted_position_estimates"] = stale_marks
        valuation["estimated_locked_share_value"] = _cents(sum((Decimal(x["locked_share_value_estimate"]) for x in stale_marks.values()), ZERO))
        previous_nav = Decimal(valuation["simulated_net_asset_value"])
        if previous_nav < 0:
            raise PortfolioError("negative simulated NAV; insolvent account cannot continue")
        previous_tax_unpaid = Decimal(valuation["estimated_total_tax_unpaid"])
        previous_tax_paid = sum((Decimal(action["tax_paid"]) for action in ledger.snapshot()["actions"].values() if action["active"]), ZERO)
        day_orders = [row for row in orders if row["trade_date"] == day]
        daily.append({"date": day, "simulated_net_asset_value": valuation["simulated_net_asset_value"],
            "simulated_net_pnl": valuation["simulated_net_pnl"], "gross_asset_value": valuation["gross_asset_value"],
            "cash_available": valuation["cash_available"], "cash_receivable_gross": valuation["raw_ledger_valuation"]["cash_receivable_gross"],
            "realized_tax_unpaid": valuation["realized_tax_unpaid"], "remaining_tax_reserve": valuation["remaining_tax_reserve"],
            "conservative_cash_after_tax_reserve": valuation["conservative_cash_after_tax_reserve"],
            "fees_paid_cumulative": ledger.snapshot()["fees_paid"], "holdings": holdings,
            "halted_position_estimates": stale_marks,
            "estimated_locked_share_value": valuation["estimated_locked_share_value"],
            "slippage_in_fill_prices_cumulative": _cents(slippage_paid),
            "orders": len(day_orders), "rejected_orders": sum(row["status"] == "rejected" for row in day_orders),
            "stock_day_denominator": sum(key[0] == day for key in rows), "valuation": valuation})
        if checkpoint is not None:
            checkpoint.emit("day_completed", date=day)
    if not ledger.verify_state_commitment():
        raise PortfolioError("ledger state commitment verification failed")
    final = ledger.snapshot()
    result = {"version": VERSION, "scope": "accounting_integration_only", "execution_valid": False,
        "formal_target_success": False, "promotion": False, "company_action_coverage_complete": False,
        "historical_data_available_at_verified": False, "strategy_metrics_for_promotion": None,
        "initial_cash": _cents(cash), "calendar": days, "replay_dates": replay_days,
        "daily": daily, "orders": orders, "trades": trades, "rejections": [row for row in orders if row["status"] == "rejected"],
        "skipped_actions": skipped_actions, "final_snapshot": final, "genesis": ledger.genesis, "journal": ledger.journal,
        "manifest": {"policy": policy, "policy_sha256": policy_hash, "corporate_actions": action_manifests,
                     "daily_source_attrs": _serial(dict(daily_data.attrs)), "input_stock_days": len(rows),
                     "replay_stock_days": sum(first <= key[0] <= last for key in rows), "input_targets": len(seen),
                     "execution_valid": False, "formal_target_success": False}}
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result

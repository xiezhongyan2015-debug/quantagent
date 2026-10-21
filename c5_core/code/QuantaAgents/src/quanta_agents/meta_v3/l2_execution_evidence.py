"""Finite snapshot matching evidence, without asserting actual fills or PnL.

This module does not admit securities, assume away corporate actions, or move
account cash/shares. Quote clocks and depth haircuts are declared scenarios.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .ledger import digest

HK = timezone(timedelta(hours=8))
D = Decimal


def local_time(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return stamp.replace(tzinfo=HK) if stamp.tzinfo is None else stamp.astimezone(HK)


def integer(value, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError("missing, negative, or non-integer native value")
    return value


def quote_identity(row):
    return {"wind_code":row["wind_code"],"date":str(row["date"]),
        "source_row_no":row["source_row_no"],"source_event_time":local_time(row["event_ts"]).isoformat()}


def validate_quote(row, policy):
    ts = local_time(row["event_ts"])
    if str(row["date"]) != ts.date().isoformat():
        raise ValueError("quote source date mismatch")
    raw = integer(row["time_raw"])
    expected = ts.hour*10000000+ts.minute*100000+ts.second*1000+ts.microsecond//1000
    if raw != expected or ts.microsecond % 1000:
        raise ValueError("source timestamp differs from millisecond time_raw")
    integer(row["source_row_no"])
    for side in ("ask","bid"):
        last, ended = None, False
        for level in range(1,11):
            price, quantity = integer(row[f"{side}_price_{level}_x1e4"]), integer(row[f"{side}_volume_{level}"])
            if price==quantity==0:
                ended=True
                continue
            if price==0 or quantity==0 or ended or price%policy["tick_scaled"]:
                raise ValueError("incomplete or non-tick depth level")
            if last is not None and ((side=="ask" and price<=last) or (side=="bid" and price>=last)):
                raise ValueError("depth prices are not strictly ordered")
            last=price
        if integer(row[f"{side}_price_1_x1e4"])==0:
            raise ValueError("no valid two-sided best quote")
    if row["bid_price_1_x1e4"]>row["ask_price_1_x1e4"]:
        raise ValueError("crossed quote")


def finite_depth_match(row, side, requested_quantity, limit_price_scaled, policy):
    """One observation, one order; no future replenishment or price invention."""
    validate_quote(row,policy)
    integer(requested_quantity);integer(limit_price_scaled,positive=True)
    lot=policy["unit_lot"]
    if side not in ("buy","sell") or requested_quantity%lot:
        raise ValueError("side and whole-lot requested quantity required")
    book_side="ask" if side=="buy" else "bid"
    fraction=D(policy["per_level_displayed_depth_fraction"])
    if not D(0)<fraction<=D(1):
        raise ValueError("displayed-depth fraction outside (0,1]")
    levels=[]
    for level in range(1,11):
        price, shown = row[f"{book_side}_price_{level}_x1e4"], row[f"{book_side}_volume_{level}"]
        if not price or (side=="buy" and price>limit_price_scaled) or (side=="sell" and price<limit_price_scaled):
            break
        allowance=int((D(shown)*fraction).to_integral_value(rounding=ROUND_FLOOR))
        levels.append({"level":level,"price_scaled":price,"displayed_quantity_native":shown,
                       "scenario_allowance_native":allowance})
    available=sum(x["scenario_allowance_native"] for x in levels)
    fill=min(requested_quantity,available//lot*lot)
    remaining=fill;allocations=[];amount_scaled=0
    for level in levels:
        q=min(remaining,level["scenario_allowance_native"])
        if q:
            allocations.append({**level,"matched_quantity_native":q})
            amount_scaled+=q*level["price_scaled"];remaining-=q
    assert remaining==0 and sum(x["matched_quantity_native"] for x in allocations)==fill
    return {"requested_quantity_native":requested_quantity,"matched_reference_quantity_native":fill,
        "unmatched_reference_quantity_native":requested_quantity-fill,
        "status":"no_order" if requested_quantity==0 else "full_reference_match" if fill==requested_quantity else "partial_reference_match" if fill else "unmatched",
        "limit_price_scaled":limit_price_scaled,"allocations":allocations,
        "quoted_notional_excluding_fees":str(D(amount_scaled)/policy["price_scale"]),
        "quoted_vwap":None if not fill else str(D(amount_scaled)/policy["price_scale"]/fill),
        "eligible_displayed_quantity_native":sum(x["displayed_quantity_native"] for x in levels),
        "scenario_depth_allowance_native":available,"actual_fill_verified":False}


def assess_order(rows, code, day, side, policy, reference_quantity=None):
    """Size from information available before dispatch, then query later depth."""
    selected=[r for r in rows if r["wind_code"]==code and str(r["date"])==day]
    selected.sort(key=lambda r:(local_time(r["event_ts"]),r["source_row_no"]))
    result={"wind_code":code,"date":day,"side":side,"status":"rejected_evidence",
        "reasons":[],"matched_reference_quantity_native":0,"actual_fill_verified":False,
        "source_unit_certified":False,"arrival_time_observed":False,"execution_valid":False}
    ids=[r["source_row_no"] for r in selected]
    if len(ids)!=len(set(ids)):
        result["reasons"]=["duplicate source event key; no deduplication"]
        return result
    decision=local_time(day+"T"+policy["decision_local_time"])
    cutoff=decision-timedelta(milliseconds=policy["assumed_feed_delay_ms"])
    visible=[r for r in selected if local_time(r["event_ts"])<=cutoff]
    if not visible:
        result["reasons"]=["no decision-visible quote"]
        return result
    quote=visible[-1]
    result["decision_quote"]=quote_identity(quote)
    result["decision_time"]=decision.isoformat()
    if (decision-local_time(quote["event_ts"])).total_seconds()*1000>policy["max_decision_quote_source_age_ms"]:
        result["reasons"]=["decision quote exceeds frozen age; no stale carry"]
        return result
    try:
        validate_quote(quote,policy)
    except ValueError as exc:
        result["reasons"]=["decision quote: "+str(exc)]
        return result
    tick=policy["tick_scaled"]
    best=quote["ask_price_1_x1e4"] if side=="buy" else quote["bid_price_1_x1e4"]
    move=D(policy["buy_limit_vs_decision_ask_fraction"] if side=="buy" else policy["sell_limit_vs_decision_bid_fraction"])
    limit=int((D(best)*(1+move if side=="buy" else 1-move)/tick).to_integral_value(
        rounding=ROUND_CEILING if side=="buy" else ROUND_FLOOR))*tick
    if side=="buy":
        # A quote-feasibility notional sleeve, explicitly excluding unknown fees.
        quantity=int(D(policy["requested_sleeve_cash"])*policy["price_scale"]/(limit*policy["unit_lot"]))*policy["unit_lot"]
    elif side=="sell" and reference_quantity is not None:
        quantity=integer(reference_quantity)
    else:
        raise ValueError("sell requires prior matched reference quantity")
    result.update(requested_quantity_native=quantity,limit_price_scaled=limit,
                  unmatched_reference_quantity_native=quantity)
    arrival=decision+timedelta(milliseconds=policy["assumed_order_latency_ms"])
    later=[r for r in selected if local_time(r["event_ts"])>=arrival]
    if not later:
        result["reasons"]=["no snapshot at or after frozen order arrival"]
        return result
    execution=later[0]
    result["execution_snapshot"]=quote_identity(execution)
    delay=(local_time(execution["event_ts"])-arrival).total_seconds()*1000
    result["snapshot_after_order_arrival_ms"]=delay
    if delay>policy["max_execution_snapshot_delay_ms"]:
        result["reasons"]=["first execution snapshot exceeds frozen delay; no future-book search"]
        return result
    try:
        matched=finite_depth_match(execution,side,quantity,limit,policy)
    except ValueError as exc:
        result["reasons"]=["first execution snapshot: "+str(exc)]
        return result
    result.update(matched)
    result["decision_and_execution_depth_sha256"]=digest([
        {**quote_identity(r),"depth":{k:v for k,v in r.items() if k.startswith(("ask_","bid_"))}}
        for r in (quote,execution)])
    result["reasons"]=[] if matched["status"] in ("full_reference_match","no_order") else ["finite displayed depth or frozen limit retained the unmatched remainder"]
    result["interpretation"]="Conditional quote match only. Inventory continuity, securities state, corporate actions, real fees and receipt latency still require admission; no portfolio return follows from this record."
    return result

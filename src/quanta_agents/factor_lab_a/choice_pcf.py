"""Choice competition HTTP adapter. Network order contracts need operator validation.

This module is not an official Choice SDK. Secrets are read from environment only.
No session extraction, telemetry, redirects or automatic order retry is performed.
"""
from __future__ import annotations

import math
import os
from decimal import Decimal

import requests

from .pcf import normalize_code

BASE = "https://choicelabapi.eastmoney.com//emlab-api/"


class TradeError(RuntimeError):
    pass


def number(value, integer=False):
    result = float(value)
    if not math.isfinite(result) or result < 0 or (integer and result % 1):
        raise TradeError("Invalid nonnegative numeric field")
    return int(result) if integer else result


def field(obj, path):
    for part in path.split("."):
        if not part or not isinstance(obj, dict) or part not in obj:
            raise TradeError("Response contract missing field: " + path)
        obj = obj[part]
    return obj


def order_payload(account, code, side, quantity, price):
    code = normalize_code(code)
    quantity = number(quantity, integer=True)
    p = Decimal(str(price))
    if not account or side not in (1, 2) or quantity <= 0:
        raise TradeError("Invalid order")
    if not p.is_finite() or p <= 0 or p != p.quantize(Decimal("0.01")):
        raise TradeError("Only positive A-share limit prices at 0.01 tick are supported")
    minimum = 200 if code.startswith("SH688") else 100
    if side == 1 and (quantity < minimum or quantity % 100):
        raise TradeError("Invalid conservative buy lot")
    return dict(accId=account, mktCode="1" if code.startswith("SH") else "0",
                stkCode=code[2:], price=format(p, ".2f"), volume=quantity,
                orderDrt=side, orderType=1, stkType=1)


class ChoiceBroker:
    def __init__(self, contract, allow_orders=False, session=None):
        self.contract = dict(contract)
        # Authorized 2026-09-20 response: submit uses orderID; order queries use orderId.
        if not self.contract.get("order_id"):
            self.contract["order_id"] = "Data.orderID"
        contract = self.contract
        self.allow_orders = allow_orders
        self.account = os.environ.get("CHOICE_ACCOUNT_ID", "")
        token = os.environ.get("CHOICE_JTOKEN", "")
        if not self.account or not token:
            raise TradeError("Missing CHOICE_ACCOUNT_ID / CHOICE_JTOKEN environment")
        self.session = session or requests.Session()
        self.session.headers.update({"token": token})
        if allow_orders:
            initial_buy = contract.get("initial_buy_only", False)
            if initial_buy and not (contract.get("validated_submission_with_authorized_test") and
                                    contract.get("validated_cancel_with_authorized_test")):
                raise TradeError("Initial-buy test requires verified submit and cancellation contracts")
            if not initial_buy and not contract.get("validated_with_authorized_test", False):
                raise TradeError("Order response / sellable / deal linkage contract not validated")
            for k in (("order_id",) if initial_buy else ("order_id", "sellable", "deal_order_id", "deal_id")):
                if not contract.get(k):
                    raise TradeError("Missing validated contract mapping: " + k)
            if not all(os.environ.get(k) for k in ("CHOICE_UTOKEN", "CHOICE_CTOKEN", "CHOICE_UID")):
                raise TradeError("Missing order credential environment")

    def _request(self, path, params=None, payload=None):
        reads = {"AguTrade/SpoBalInfo", "AguTrade/SpoHold", "AguTrade/SpoOrders", "AguTrade/SpoDeal"}
        if payload is None and path not in reads:
            raise TradeError("Unsupported query")
        if payload is not None:
            writes = {"AguTrade/SpoOrder"}
            if self.contract.get("validated_cancel_with_authorized_test", False):
                writes.add("AguTrade/SpoCancel")
            if path not in writes or not self.allow_orders:
                raise TradeError("Order transport disabled")
        try:
            if payload is None:
                r = self.session.get(BASE + path, params=params, timeout=15, allow_redirects=False)
            else:
                r = self.session.post(BASE + path, json=payload, timeout=15, allow_redirects=False)
            if r.status_code != 200:
                raise TradeError("Choice HTTP response requires reconciliation")
            obj = r.json()
        except (requests.RequestException, ValueError):
            # Never expose request URLs, headers, response bodies or credentials.
            raise TradeError("Choice transport/JSON failure; do not retry orders") from None
        if not isinstance(obj, dict) or str(obj.get("Code")) != "0":
            if isinstance(obj, dict) and str(obj.get("Code")) == "207":
                raise TradeError("Choice session expired; log in again")
            raise TradeError("Choice business failure; not an empty account")
        if "Data" not in obj:
            raise TradeError("Choice response missing Data")
        return obj

    def _rows(self, path):
        params = {"accId": self.account}
        if path.endswith("SpoHold"):
            params.update(ssid=1, dvid=2, appver=3, fToken="ok", uToken="test.hfstrade")
        else:
            params.update(dataRange=1, pageNo="0", pageSize="1000")
        data = self._request(path, params=params)["Data"]
        rows = field(data, "data")
        if not isinstance(rows, list) or len(rows) >= 1000:
            raise TradeError("Unexpected/truncated list: explicit pagination needed")
        for key in ("total", "totalCount", "count"):
            if key in data and number(data[key], True) > len(rows):
                raise TradeError("Incomplete account list")
        return rows

    def snapshot(self):
        b = self._request("AguTrade/SpoBalInfo", params={"accId": self.account})["Data"]
        positions = {}
        for row in self._rows("AguTrade/SpoHold"):
            code = normalize_code(str(row["secCode"]))
            if code in positions:
                raise TradeError("Duplicate position")
            held = number(row["count"], True)
            sellable = (0 if self.contract.get("initial_buy_only") else
                        number(field(row, self.contract["sellable"]), True))
            if sellable > held:
                raise TradeError("Sellable exceeds position")
            positions[code] = {"shares": held, "sellable": sellable}
        pending = self._rows("AguTrade/SpoOrders")
        if any(str(x["status"]) not in ("4", "7", "8", "9") for x in pending):
            raise TradeError("Account has unfinished orders")
        return {"cash": number(b["availBalance"]), "nav": number(b["totalAssets"]),
                "positions": positions, "frozen": number(b["frozenMoney"])}

    def submit(self, order):
        if self.contract.get("initial_buy_only") and order["side"] != 1:
            raise TradeError("Initial-build test cannot sell; sellable contract remains unverified")
        payload = order_payload(self.account, order["code"], order["side"], order["quantity"], order["price"])
        payload.update(uToken=os.environ.get("CHOICE_UTOKEN"), cToken=os.environ.get("CHOICE_CTOKEN"),
                       uid=os.environ.get("CHOICE_UID"))
        obj = self._request("AguTrade/SpoOrder", payload=payload)
        oid = field(obj, self.contract["order_id"])
        if isinstance(oid, bool) or not isinstance(oid, (str, int)) or not str(oid).strip():
            raise TradeError("Accepted response without verifiable order ID")
        return str(oid)

    def order_status(self, oid, order):
        rows = [x for x in self._rows("AguTrade/SpoOrders") if str(x["orderId"]) == oid]
        if len(rows) != 1:
            raise TradeError("Order ID absent or ambiguous; no matching by code/price")
        row = rows[0]
        if (normalize_code(str(row["secCode"])) != order["code"] or
                number(row["orderCount"], True) != order["quantity"] or int(row["drt"]) != order["side"]):
            raise TradeError("Order identity mismatch")
        if "price" in order and Decimal(str(row["orderPrice"])) != Decimal(str(order["price"])):
            raise TradeError("Order limit price mismatch")
        filled = number(row["tradeCount"], True)
        if filled > order["quantity"]:
            raise TradeError("Excess fill")
        state = str(row["status"])
        if state not in {str(x) for x in range(1, 11)}:
            raise TradeError("Unknown order status")
        return {"state": state, "filled": filled}

    def cancel(self, oid, order):
        """One cancellation request, never a claim that cancellation has completed."""
        if not self.allow_orders or not self.contract.get("validated_cancel_with_authorized_test", False):
            raise TradeError("Cancellation contract not validated / transport disabled")
        status = self.order_status(oid, order)
        if status["state"] in ("4", "7", "8", "9"):
            return {"request_sent": False, "reason": "already_terminal"}
        if status["state"] in ("5", "6"):
            return {"request_sent": False, "reason": "cancel_already_pending"}
        if status["state"] == "10":
            raise TradeError("Prior cancellation failed; no automatic second cancellation")
        code = normalize_code(order["code"])
        payload = dict(accId=self.account, mktCode="1" if code.startswith("SH") else "0",
                       stkCode=code[2:], orderId=oid,
                       uToken=os.environ.get("CHOICE_UTOKEN"),
                       cToken=os.environ.get("CHOICE_CTOKEN"), uid=os.environ.get("CHOICE_UID"))
        self._request("AguTrade/SpoCancel", payload=payload)
        return {"request_sent": True, "reason": "acknowledged_not_confirmed"}

    def progress(self, oid, order):
        status = self.order_status(oid, order)
        state, filled = status["state"], status["filled"]
        if self.contract.get("initial_buy_only"):
            # Narrow alternative for an initially empty, buy-only account: require
            # exactly one server-side order for this security today, and agree on
            # cumulative fills across order, inventory and trade-detail queries.
            # This does NOT establish a general deal-ID linkage or sellable schema.
            same = [x for x in self._rows("AguTrade/SpoOrders")
                    if normalize_code(str(x["secCode"])) == order["code"]]
            if order["side"] != 1 or len(same) != 1 or str(same[0]["orderId"]) != oid:
                raise TradeError("Initial-buy fill audit requires one order per security per day")
            holdings = [x for x in self._rows("AguTrade/SpoHold")
                        if normalize_code(str(x["secCode"])) == order["code"]]
            if len(holdings)>1:
                raise TradeError("Duplicate inventory rows")
            held = number(holdings[0]["count"],True) if holdings else 0
            deals = [x for x in self._rows("AguTrade/SpoDeal")
                     if normalize_code(str(x["secCode"])) == order["code"]]
            if any(int(x["drt"])!=1 for x in deals):
                raise TradeError("Unexpected sell in initial-buy test")
            total=sum(number(x["tradeCount"],True) for x in deals)
            if held>filled or total>filled:
                raise TradeError("Initial-buy account/order/deal quantities disagree")
            complete=held==total==filled
            return {"filled":filled,"done":state=="4" and filled==order["quantity"] and complete,
                    "state":state,"deals_complete":complete,
                    "terminal_failure":state in ("7","8","9","10"),
                    "verification":"single_initial_buy_order_plus_inventory_plus_deals"}
        deals = [x for x in self._rows("AguTrade/SpoDeal")
                 if str(field(x, self.contract["deal_order_id"])) == oid]
        ids = [str(field(x, self.contract["deal_id"])) for x in deals]
        if len(set(ids)) != len(ids):
            raise TradeError("Duplicate deal IDs")
        if any(normalize_code(str(x["secCode"])) != order["code"] or int(x["drt"]) != order["side"] for x in deals):
            raise TradeError("Linked deal has wrong security/direction")
        total = sum(number(x["tradeCount"], True) for x in deals)
        if total > filled:
            raise TradeError("Deal/order quantity mismatch")
        # An order response is not a fill. Require order ID linked deal confirmation.
        done = state == "4" and filled == order["quantity"] and total == filled
        return {"filled": filled, "done": done, "state": state,
                "deals_complete": total == filled,
                "terminal_failure": state in ("7", "8", "9", "10")}

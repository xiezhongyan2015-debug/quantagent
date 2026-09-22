"""After-close PCF archive and next-session Choice execution CLI. Defaults to paper."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from decimal import Decimal, ROUND_CEILING

import pandas as pd

from .pcf import build_target, download_pcf, validate_pcf, normalize_code
from .choice_pcf import ChoiceBroker, TradeError, number, order_payload

CN = dt.timezone(dt.timedelta(hours=8))


def now_cn():
    return dt.datetime.now(CN)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise TradeError("Another run or an unreviewed crash lock exists") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def calendar(cfg):
    days = read_json(cfg["calendar"])
    if not days or sorted(set(days)) != days or any(dt.date.fromisoformat(x).isoformat() != x for x in days):
        raise TradeError("Exchange calendar must be explicit ISO dates, ordered and unique")
    return days


def hook(cfg, stage, **context):
    command = cfg.get(stage + "_command", [])
    if command:
        if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
            raise TradeError("Provider command must be an argv array, not shell text")
        # Local, operator-configured data provider. Never record its stdout or secrets.
        command = [x.format(**context) if context else x for x in command]
        subprocess.run(command, check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def close_job(cfg, now=None, fetch=download_pcf):
    now = now or now_cn()
    today = now.astimezone(CN).date().isoformat()
    if now.astimezone(CN).time() < dt.time(15, 5):
        raise TradeError("After-close archive cannot run before 15:05 China time")
    days = calendar(cfg)
    if today not in days:
        if today > days[-1]:
            raise TradeError("Calendar expired")
        return {"status": "non_trading_day"}
    idx = days.index(today)
    if idx + 1 >= len(days):
        raise TradeError("Next trading day missing; extend official exchange calendar")
    out = Path(cfg["state_dir"]) / "archive" / today
    with lock(Path(cfg["state_dir"]) / "close.lock"):
        out.mkdir(parents=True, exist_ok=True)
        candidate = out / "candidate.json"
        record = fetch("563030", today, str(candidate))
        validate_pcf(record)
        if record["date"] != today:
            raise TradeError("Provider substituted another PCF date")
        core = {k: record[k] for k in ("date", "baseinfo", "stocklist")}
        sha = digest(core)
        manifest = out / "manifest.json"
        if manifest.exists():
            old = read_json(manifest)
            if old["pcf_sha256"] != sha:
                write_json(out / ("revision_" + sha + ".json"), record)
                write_json(out / "REVISION_BLOCK.json", {"old": old["pcf_sha256"], "new": sha})
                raise TradeError("PCF revised after first archive; execution blocked for review")
            hook(cfg, "close")
            return old
        write_json(out / "pcf.json", record)
        result = {"pcf_date": today, "execution_date": days[idx + 1], "pcf_sha256": sha,
                  "captured_at": now.isoformat(), "historical_publication_timestamp_verified": False}
        write_json(manifest, result)
        candidate.unlink(missing_ok=True)
        hook(cfg, "close")
        return result


def live_quote(cfg, code, now):
    # Provider updates this file atomically, including each exchange quote timestamp.
    hook(cfg, "quote", code=code)
    if cfg.get("quote_command"):
        now = now_cn()
    data = read_json(cfg["live_quotes"])
    row = data[code]
    stamp = dt.datetime.fromisoformat(row["timestamp"])
    if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= float(cfg.get("quote_max_age_seconds", 30)):
        raise TradeError("Missing, future or stale execution quote")
    if row["tradable"] != 1 or row["is_st"] != 0:
        raise TradeError("Execution stock halted or ST")
    for name in ("bid", "ask", "last", "lower_limit", "upper_limit"):
        if number(row[name]) <= 0:
            raise TradeError("Invalid execution price")
    if not row["lower_limit"] <= row["bid"] <= row["ask"] <= row["upper_limit"]:
        raise TradeError("Quote prices outside band")
    return row


def validate_session(now, days):
    now = now.astimezone(CN)
    if now.date().isoformat() not in days or not (
            dt.time(9, 35) <= now.time() <= dt.time(11, 25) or dt.time(13, 5) <= now.time() <= dt.time(14, 50)):
        raise TradeError("Outside configured continuous execution window")


class PaperBroker:
    """Persistent local fake account; deterministic fills, not a performance backtest."""
    account = "paper"

    def __init__(self, path, day=None):
        self.path = Path(path)
        self.day = day or now_cn().date().isoformat()
        self.orders = {}

    def snapshot(self):
        state = read_json(self.path)
        if state.get("asof", self.day) > self.day:
            raise TradeError("Paper account dated in future")
        if state.get("asof", self.day) < self.day:
            for position in state["positions"].values():
                position["sellable"] = position["shares"]
            state["asof"] = self.day
            write_json(self.path, state)
        if state.get("frozen", 0) != 0:
            raise TradeError("Paper account has frozen cash")
        return state

    def submit(self, order):
        order_payload(self.account, order["code"], order["side"], order["quantity"], order["price"])
        state = self.snapshot()
        state["asof"] = self.day
        p = state["positions"].setdefault(order["code"], {"shares": 0, "sellable": 0})
        value = order["quantity"] * order["price"]
        if order["side"] == 1:
            if state["cash"] < value * 1.001:
                raise TradeError("Paper cash insufficient")
            state["cash"] -= value * 1.001
            p["shares"] += order["quantity"]
        else:
            if p["sellable"] < order["quantity"]:
                raise TradeError("Paper T+1 inventory insufficient")
            p["shares"] -= order["quantity"]
            p["sellable"] -= order["quantity"]
            state["cash"] += value * .999
        # New purchases are not sellable until the next exchange day.
        write_json(self.path, state)
        oid = "paper-" + str(len(self.orders) + 1)
        self.orders[oid] = order
        return oid

    def progress(self, oid, order):
        return {"filled": order["quantity"], "done": True, "terminal_failure": False}


class Journal:
    def __init__(self, path, account):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY, account TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS batches (day TEXT PRIMARY KEY, state TEXT NOT NULL, plan TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS orders (day TEXT, code TEXT, state TEXT NOT NULL, oid TEXT, filled INTEGER DEFAULT 0, PRIMARY KEY(day,code))")
        self.db.execute("CREATE TABLE IF NOT EXISTS capital (day TEXT, code TEXT, reserved_cents INTEGER NOT NULL, PRIMARY KEY(day,code))")
        identity = hashlib.sha256(account.encode()).hexdigest()
        row = self.db.execute("SELECT account FROM binding WHERE id=1").fetchone()
        if row and row[0] != identity:
            raise TradeError("Journal belongs to another account")
        self.db.execute("INSERT OR IGNORE INTO binding VALUES(1,?)", (identity,))
        self.db.commit()

    def start(self, day, plan):
        # A changed plan never creates another order batch for this account/date.
        row = self.db.execute("SELECT state FROM batches WHERE day=?", (day,)).fetchone()
        if row:
            if row[0] == "COMPLETE":
                return False
            raise TradeError("Unfinished/uncertain batch: reconcile before any new submissions")
        self.db.execute("INSERT INTO batches VALUES(?, 'RUNNING', ?)", (day, json.dumps(plan)))
        self.db.commit()
        return True

    def mark(self, day, code, state, oid=None, filled=0):
        self.db.execute("INSERT INTO orders VALUES(?,?,?,?,?) ON CONFLICT(day,code) DO UPDATE SET state=excluded.state, oid=excluded.oid, filled=excluded.filled", (day,code,state,oid,filled))
        self.db.commit()

    def complete(self, day):
        self.db.execute("UPDATE batches SET state='COMPLETE' WHERE day=?", (day,))
        self.db.commit()

    def reserve_capital(self, day, code, amount, limit):
        """Durable gross spending cap. Never recycle cancellations, rejects or sells."""
        cents = int((Decimal(str(amount))*100).to_integral_value(rounding=ROUND_CEILING))
        ceiling = int(Decimal(str(limit))*100)
        if cents <= 0 or ceiling <= 0:
            raise TradeError("Invalid capital reservation")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.db.execute("SELECT COALESCE(SUM(reserved_cents),0) FROM capital WHERE day=?",(day,)).fetchone()[0]
            if used+cents > ceiling:
                raise TradeError("User capital limit exceeded including pending orders and fee reserve")
            try:
                self.db.execute("INSERT INTO capital VALUES(?,?,?)",(day,code,cents))
            except sqlite3.IntegrityError:
                raise TradeError("This security already consumed capital; never resubmit") from None


def order_cash_reserve(cfg, amount):
    # Research cost remains 0.001 per side. Extra execution buffer covers minimum
    # commission / temporary freeze differences; it is not reported as paid cost.
    return float(Decimal(str(amount))*Decimal('1.001') + Decimal(str(number(cfg.get("execution_fee_buffer_per_order",0)))))


def target_check_ok(check, frame, trial=False):
    if (frame.loc[frame.exec_shares > 0,"bucket"] == "outside").any():
        return False
    if not trial:
        return check["fill_1.00"]["ok"]
    # A user-capped initial-build experiment is not a fully invested competition
    # account. Minimum investment ratios are reported, never silently called met.
    selected=frame.loc[frame.exec_shares>0,"exec_mv"]
    return not selected.empty


def settle_unfilled(cfg, day, order, oid, broker, journal, last_filled, sleeper=time.sleep):
    """Cancel once, then query only. Always stop the batch; never place a replacement."""
    journal.mark(day, order["code"], "CANCEL_SENDING", oid, last_filled)
    try:
        broker.cancel(oid, order)
    except Exception:
        # A lost cancellation response may still have succeeded. Do not repeat it.
        journal.mark(day, order["code"], "CANCEL_UNKNOWN", oid, last_filled)
    else:
        journal.mark(day, order["code"], "CANCEL_PENDING", oid, last_filled)
    deadline = time.monotonic() + max(0., float(cfg.get("cancel_timeout_seconds", 30)))
    while True:
        try:
            progress = broker.progress(oid, order)
            filled = number(progress["filled"], True)
            if not last_filled <= filled <= order["quantity"]:
                raise TradeError("Nonmonotonic/excess fill during cancellation")
            last_filled = filled
            if progress["done"]:
                if filled != order["quantity"]:
                    raise TradeError("False full fill during cancellation")
                journal.mark(day, order["code"], "FILLED", oid, filled)
                return "filled_during_cancel"
            if progress.get("state") in ("7", "8", "9") and progress.get("deals_complete"):
                label = "REJECTED" if progress["state"] == "9" else "CANCELLED"
                journal.mark(day, order["code"], label, oid, filled)
                return label.lower()
        except Exception:
            journal.mark(day, order["code"], "REVIEW_REQUIRED", oid, last_filled)
            raise TradeError("Cancellation outcome unverified; query account, do not resend") from None
        journal.mark(day, order["code"], "CANCEL_PENDING", oid, last_filled)
        if time.monotonic() >= deadline or progress.get("state") == "10":
            journal.mark(day, order["code"], "REVIEW_REQUIRED", oid, last_filled)
            raise TradeError("Cancellation not confirmed; outstanding quantity may still fill")
        sleeper(max(1., float(cfg.get("poll_seconds", 2))))


def execute_orders(cfg, plan, broker, journal, clock=now_cn, sleeper=time.sleep):
    cap=cfg.get("capital_limit")
    if cap is not None:
        if not cfg.get("initial_build_test") or number(cap)<=0 or plan["initial_positions"]:
            raise TradeError("Capital-limited launch requires a new empty test book")
        if any(x["side"]!=1 for x in plan["orders"]):
            raise TradeError("Initial capital-limited test is buy-only")
    if cfg.get("cancel_on_timeout", False):
        if not callable(getattr(broker, "cancel", None)):
            raise TradeError("Cancellation policy requires a cancellation-capable broker")
        if isinstance(broker, ChoiceBroker) and not broker.contract.get("validated_cancel_with_authorized_test", False):
            raise TradeError("Validate cancellation contract before enabling the cancellation policy")
    day = plan["execution_date"]
    if not journal.start(day, plan):
        return {"status": "already_complete", "orders": 0}
    positions = {k: v for k, v in plan["initial_positions"].items() if v}
    for order in plan["orders"]:
        now = clock()
        validate_session(now, calendar(cfg))
        if cfg.get("allowed_execution_date") and day != cfg["allowed_execution_date"]:
            raise TradeError("Execution date outside user-approved test")
        if now.date().isoformat() != day or Path(cfg["state_dir"], "STOP").exists():
            raise TradeError("Wrong execution day or STOP file present")
        state = broker.snapshot()
        actual = {k: number(v["shares"], True) for k, v in state["positions"].items() if v["shares"]}
        if actual != positions or number(state.get("frozen", 0)) > 0:
            raise TradeError("Unexpected account activity: refusing cross-strategy interference")
        q = live_quote(cfg, order["code"], now)
        price = q["ask"] if order["side"] == 1 else q["bid"]
        if abs(price / order["reference_price"] - 1) > float(cfg.get("max_price_move", .03)):
            raise TradeError("Price moved beyond execution tolerance")
        order["price"] = price
        amount = price * order["quantity"]
        reserve = order_cash_reserve(cfg,amount)
        if amount > float(cfg["max_order_value"]):
            raise TradeError("Order value cap exceeded")
        if order["side"] == 1:
            if state["cash"] < reserve:
                raise TradeError("Insufficient actual cash including 0.001 fee reserve")
        elif state["positions"].get(order["code"], {}).get("sellable", 0) < order["quantity"]:
            raise TradeError("Insufficient actual sellable inventory")
        order_payload(broker.account, order["code"], order["side"], order["quantity"], price)
        if cap is not None:
            journal.reserve_capital(day,order["code"],reserve,cap)
        # Commit intent before HTTP. Timeout/crash never automatically resubmits.
        journal.mark(day, order["code"], "SENDING")
        try:
            oid = broker.submit(order)
        except Exception:
            journal.mark(day, order["code"], "UNKNOWN")
            raise TradeError("Submit outcome uncertain/rejected; batch stopped, no retry") from None
        journal.mark(day, order["code"], "ACK", oid)
        deadline = time.monotonic() + float(cfg.get("fill_timeout_seconds", 60))
        last_filled = 0
        while True:
            try:
                progress = broker.progress(oid, order)
            except Exception:
                if cfg.get("cancel_on_timeout",False):
                    settle_unfilled(cfg,day,order,oid,broker,journal,last_filled,sleeper)
                raise TradeError("Fill verification failed; stop and reconcile known order") from None
            filled = number(progress["filled"], True)
            if not last_filled <= filled <= order["quantity"]:
                raise TradeError("Nonmonotonic/excess cumulative fill")
            last_filled = filled
            journal.mark(day, order["code"], "FILLED" if progress["done"] else "PARTIAL", oid, filled)
            if progress["done"]:
                if filled != order["quantity"]:
                    raise TradeError("False full-fill acknowledgement")
                positions[order["code"]] = positions.get(order["code"], 0) + filled * (1 if order["side"] == 1 else -1)
                positions = {k: v for k, v in positions.items() if v}
                break
            if progress["terminal_failure"] or time.monotonic() >= deadline or Path(cfg["state_dir"], "STOP").exists():
                if cfg.get("cancel_on_timeout", False) and not progress["terminal_failure"]:
                    result = settle_unfilled(cfg, day, order, oid, broker, journal, filled, sleeper)
                    raise TradeError("Batch stopped after cancellation reconciliation: " + result)
                raise TradeError("Order partial/unfilled/failed: no further orders; reconcile outstanding order")
            sleeper(max(1., float(cfg.get("poll_seconds", 2))))
        sleeper(max(0., float(cfg.get("order_spacing_seconds", 1))))
    journal.complete(day)
    return {"status": "complete", "orders": len(plan["orders"])}


def execute_job(cfg, mode="paper", now=None, broker=None, clock=now_cn):
    now = now or clock()
    days = calendar(cfg)
    today = now.astimezone(CN).date().isoformat()
    if today > days[-1]:
        raise TradeError("Calendar expired")
    if today not in days:
        return {"status":"non_trading_day"}
    validate_session(now, days)
    day = now.astimezone(CN).date().isoformat()
    if days.index(day) == 0:
        raise TradeError("Previous trading date absent")
    previous = days[days.index(day) - 1]
    root = Path(cfg["state_dir"])
    archive = root / "archive" / previous
    if (archive / "REVISION_BLOCK.json").exists() or (root / "STOP").exists():
        raise TradeError("STOP or PCF revision block present")
    manifest = read_json(archive / "manifest.json")
    capture = dt.datetime.fromisoformat(manifest["captured_at"])
    if (capture.tzinfo is None or not previous <= capture.astimezone(CN).date().isoformat() < day or capture >= now):
        raise TradeError("Prior-session PCF must actually be archived before execution day")
    record = read_json(archive / "pcf.json")
    if manifest["execution_date"] != day or manifest["pcf_sha256"] != digest({k:record[k] for k in ("date","baseinfo","stocklist")}):
        raise TradeError("PCF archive integrity/date failed")
    if mode == "choice" and (not cfg.get("enable_choice_orders") or not cfg.get("exclusive_account")):
        raise TradeError("Choice execution requires explicit configuration and exclusive PCF account")
    hook(cfg, "open")
    with lock(root / (mode + ".lock")):
        broker = broker or (ChoiceBroker(cfg["choice_contract"], allow_orders=True) if mode == "choice"
                            else PaperBroker(cfg["paper_account"], day))
        if isinstance(broker,ChoiceBroker) and broker.contract.get("initial_buy_only") and not cfg.get("initial_build_test"):
            raise TradeError("Restricted initial-buy adapter cannot be used for general rebalancing")
        existing = Journal(root / (mode + ".sqlite"), broker.account)
        try:
            prior = existing.db.execute("SELECT state FROM batches WHERE day=?", (day,)).fetchone()
        finally:
            existing.db.close()
        if prior:
            if prior[0] == "COMPLETE":
                return {"status":"already_complete", "orders":0}
            raise TradeError("Unfinished/uncertain batch: no automatic resubmission")
        state = broker.snapshot()
        if number(state.get("frozen", 0)) > 0:
            raise TradeError("Account has frozen funds")
        quotes = pd.read_csv(cfg["close_quotes"], dtype={"code": str})
        quotes["code"] = quotes.code.map(normalize_code)
        prices = quotes.set_index("code").close
        # Subaccount is exclusive. Mark actual morning share quantities at signal close.
        positions = {k:number(v["shares"],True) for k,v in state["positions"].items() if v["shares"]}
        nav = number(state["cash"]) + sum(v * float(prices[k]) for k,v in positions.items())
        account_nav=nav
        trial=bool(cfg.get("initial_build_test"))
        if trial:
            if positions or cfg.get("capital_limit") is None or day != cfg.get("allowed_execution_date"):
                raise TradeError("Initial-build trial requires an empty book, cap and exact date")
            nav=min(nav,number(cfg["capital_limit"]))
        held = pd.DataFrame(list(positions.items()), columns=["code", "shares"])
        frame, summary = build_target(record, quotes, held, nav, days, day, previous)
        # Existing competition constraints are a hard gate, not a silent portfolio rewrite.
        if not target_check_ok(summary["precheck"],frame,trial):
            raise TradeError("PCF target fails competition limits / approved index union")
        orders = []
        for row in frame.itertuples():
            if row.delta_shares:
                orders.append({"code":row.code, "side":1 if row.delta_shares>0 else 2,
                               "quantity":int(abs(row.delta_shares)), "reference_price":float(row.close)})
        gross = sum(x["quantity"]*x["reference_price"] for x in orders)
        if trial and sum(order_cash_reserve(cfg,x["quantity"]*x["reference_price"]) for x in orders)>cfg["capital_limit"]:
            raise TradeError("Projected initial orders exceed approved capital including execution reserve")
        if len(orders) > int(cfg["max_orders"]) or gross > float(cfg["max_batch_value"]):
            raise TradeError("Batch count/value cap exceeded")
        # Revalue the whole projected book at current prices before submitting anything.
        from .competition import precheck
        current = {}
        for row in frame.itertuples():
            if row.exec_shares > 0 or positions.get(row.code, 0):
                current[row.code] = live_quote(cfg, row.code, now)["last"]
        live_nav = state["cash"] + sum(v*current[k] for k,v in positions.items())
        live_hold = {k:v*current[k] for k,v in positions.items()}
        live_target = {r.code:r.exec_shares*current[r.code] for r in frame.itertuples() if r.exec_shares>0}
        member = {r.code:r.bucket=="csi500" for r in frame.itertuples()}
        check=precheck(live_hold,live_target,nav if trial else live_nav,member)
        if trial and (max(live_target.values(),default=0)>nav*.095 or
                sum(order_cash_reserve(cfg,x["quantity"]*current[x["code"]]) for x in orders)>cfg["capital_limit"]):
            raise TradeError("Live target exceeds single-security or total approved test capital")
        if not trial and not check["fill_1.00"]["ok"]:
            raise TradeError("Projected book fails competition limits at current prices")
        summary.update(capital_limit=cfg.get("capital_limit"),initial_build_test=trial,account_nav=account_nav,
                       account_precheck=precheck(live_hold,live_target,live_nav,member),
                       competition_minimums_enforced=not trial)
        plan = {"execution_date":day, "pcf_date":previous, "pcf_sha256":manifest["pcf_sha256"],
                "initial_positions":positions, "orders":orders, "cost_per_side":.001}
        output = root / mode / day
        output.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output / "target.csv", index=False)
        write_json(output / "target.json", summary)
        journal = Journal(root / (mode + ".sqlite"), broker.account)
        try:
            return execute_orders(cfg, plan, broker, journal, clock=clock)
        finally:
            journal.db.close()


def reconcile_job(cfg, day):
    """Read-only broker reconciliation. Never guesses IDs, cancels, resumes or resends."""
    dt.date.fromisoformat(day)
    if day != now_cn().date().isoformat():
        raise TradeError("This adapter queries current-day orders only")
    root = Path(cfg["state_dir"])
    path = root / "choice.sqlite"
    if not path.exists():
        raise TradeError("No Choice journal")
    with lock(root / "choice.lock"):
        broker = ChoiceBroker(cfg["choice_contract"], allow_orders=False)
        journal = Journal(path, broker.account)
        try:
            batch = journal.db.execute("SELECT plan FROM batches WHERE day=?", (day,)).fetchone()
            if not batch:
                raise TradeError("No batch for requested date")
            plan = json.loads(batch[0])
            orders = {x["code"]:x for x in plan["orders"]}
            rows = journal.db.execute("SELECT code,state,oid,filled FROM orders WHERE day=?", (day,)).fetchall()
            result = []
            for code,state,oid,filled in rows:
                if oid:
                    progress = broker.progress(oid, orders[code])
                    if progress["filled"] < filled:
                        raise TradeError("Cumulative fill decreased")
                    state = "FILLED" if progress["done"] else "REVIEW_REQUIRED"
                    filled = progress["filled"]
                    journal.mark(day,code,state,oid,filled)
                result.append({"code":code,"state":state,"filled":filled})
            if len(rows) == len(orders) and all(x["state"] == "FILLED" for x in result):
                journal.complete(day)
            return {"orders":result, "unsubmitted":len(orders)-len(rows), "resubmissions":0}
        finally:
            journal.db.close()


def daemon(cfg, mode="paper"):
    """Daily archive and next-session execution; explicit Choice configuration required."""
    if mode == "choice" and (not cfg.get("enable_choice_orders") or not cfg.get("exclusive_account") or
                              not cfg.get("choice_contract", {}).get("validated_with_authorized_test")):
        raise TradeError("Choice daemon requires validated contract and dedicated account configuration")
    root = Path(cfg["state_dir"])
    while True:
        now = now_cn()
        day = now.date().isoformat()
        try:
            days = calendar(cfg)
            if day > days[-1]:
                raise TradeError("Calendar expired")
            if day in days:
                for label, threshold in (("close", dt.time(16)), (mode, dt.time(9,35))):
                    marker = root / "scheduler" / (day + "_" + label + ".json")
                    within = now.time() >= threshold and (label == "close" or now.time() <= dt.time(9,50))
                    if within and not marker.exists():
                        try:
                            result = close_job(cfg) if label == "close" else execute_job(cfg, mode)
                            write_json(marker, result)
                        except Exception:
                            # No unattended retry of executions. Failure is observable and sticky.
                            write_json(marker, {"status":"failed", "action":"inspect local inputs and order journal"})
                            print(day, label, "FAILED: inspect inputs/journal", flush=True)
        except Exception:
            print(day, "scheduler input failure", flush=True)
        time.sleep(30)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("command", choices=["close", "execute", "daemon", "reconcile"])
    ap.add_argument("--mode", choices=["paper", "choice"], default="paper")
    ap.add_argument("--date", help="Current date for read-only reconciliation")
    args = ap.parse_args()
    cfg = read_json(args.config)
    # All paths and provider commands are resolved from the configuration's directory.
    os.chdir(Path(args.config).resolve().parent)
    try:
        if args.command == "daemon":
            daemon(cfg, args.mode)
        else:
            if args.command == "reconcile":
                result = reconcile_job(cfg, args.date or now_cn().date().isoformat())
            else:
                result = close_job(cfg) if args.command == "close" else execute_job(cfg, args.mode)
            print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # Do not print third-party exception text which may contain credentials/URLs.
        print(str(error) if isinstance(error, TradeError) else "Input/provider failure; inspect local data contract")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

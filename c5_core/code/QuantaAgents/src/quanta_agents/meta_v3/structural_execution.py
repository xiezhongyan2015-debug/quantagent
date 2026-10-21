"""V3 structural saved-data accounting with a versioned suspension schedule.

Versioned from saved_execution; its prior schema and all frozen v18 files remain
unchanged. Matching/fees/cash-dividend rules are inherited; halt marks and order
rejections are distinct from actual executable quotes. Mixed tax remains pending.

Versioned from the v18 saved-data persistence layer. Only this new namespace
extends resource bounds and changes checkpoint storage/reconciliation.
Bounded controller-only saved-data accounting research, never execution proof.

No network/market reader/model calls. Source artifacts are controller-pinned;
Python objects and paths are not an OS sandbox. Recovery verifies only saved
events and never invokes portfolio simulation, fills an input gap or retries.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3

import pandas as pd

from .kernel import FROZEN, module

CashDividendAnnouncement = module("corporate_action_adapter").CashDividendAnnouncement
RawShareLedger = module("raw_share_ledger").RawShareLedger
from . import structural_portfolio as portfolio
from .suspension_market import SuspensionMarket, POLICY as HALT_POLICY

VERSION = "meta-v3-structural-saved-execution-v1"
MAX_CODES, MAX_SESSIONS, MAX_ROWS_PER_FILE = 16, 512, 2000
MAX_COMPRESSED, MAX_DECOMPRESSED = 2 * 1024**2, 8 * 1024**2
MAX_RECORD, MAX_ARCHIVE = 64 * 1024**2, 512 * 1024**2
MAX_EVENTS = 8192
MAX_RECORDS = 30000
MAX_INPUT_BYTES = 128 * 1024**2
RESOURCE_BUDGET = {"max_codes": MAX_CODES, "max_sessions": MAX_SESSIONS,
    "max_event_intents": MAX_EVENTS, "max_checkpoint_bytes": MAX_RECORD,
    "max_persisted_payload_bytes": MAX_ARCHIVE, "post_event_reserved_bytes": MAX_RECORD,
    "terminal_reserved_bytes": MAX_RECORD, "max_records": MAX_RECORDS, "max_input_bytes": MAX_INPUT_BYTES}
KINDS = ("corporate_actions", "market_status", "capacity", "fee_policy", "availability", "membership")
STATES = ("pending", "unknown", "unsupported", "documented_scope", "declared_simulation", "fixture_complete")
FLAGS = {"execution_valid": False, "formal_target_success": False, "promotion": False,
         "model_verified": False, "historical_data_available_at_verified": False}
SOURCE_NAMES = ("raw_saved_research.py", "raw_portfolio_backtest.py", "raw_share_ledger.py",
                "corporate_action_adapter.py", "structural_execution.py", "structural_portfolio.py", "suspension_market.py")


class SavedResearchError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise SavedResearchError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _copy(value):
    return json.loads(_json(value))


def _sha(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


class StreamObserver(portfolio._CheckpointState):
    """Pinned observer interface; read new journal entries, never mutate ledger.

    Orders for the current day are mutable until day_completed. Save that small
    tail as successive versions; finalized prior days are never rewritten.
    """
    def __init__(self, callback):
        super().__init__(callback)
        self.journal_count = 0
        self.sent_genesis = False
        self.offsets = dict.fromkeys(("trades", "daily", "skipped_actions"), 0)
        self.order_floor = 0

    def emit(self, phase, **details):
        if phase == "day_started":
            self.order_floor = len(self.progress.get("orders", []))
        value = {"phase": phase, "stream_version": 1, "current_date": self.progress.get("current_date"),
            "progress_offsets": dict(self.offsets),
            "progress_append": {k:self.progress.get(k, [])[v:] for k,v in self.offsets.items()},
            "orders_start": self.order_floor,
            "orders_tail": self.progress.get("orders", [])[self.order_floor:], **FLAGS, **details}
        if self.ledger is not None:
            entries = self.ledger._journal
            value.update(journal_start=self.journal_count, journal_delta=entries[self.journal_count:],
                journal_length=len(entries), ledger_head=self.ledger._previous_hash)
            if not self.sent_genesis:
                value["genesis"] = self.ledger.genesis
        try:
            self.callback(_copy(value))
        except BaseException:
            self.broken = True
            raise
        if self.ledger is not None:
            self.journal_count = len(self.ledger._journal)
            self.sent_genesis = True
        self.offsets = {k:len(self.progress.get(k, [])) for k in self.offsets}


def simulate_streamed_portfolio(*, checkpoint, **kwargs):
    """Call the versioned structural accounting body with a write-ahead observer."""
    observer = StreamObserver(checkpoint)
    try:
        result = portfolio.simulate_raw_portfolio.__wrapped__(**kwargs, checkpoint=observer)
        observer.emit("simulation_completed", result=result)
        return result
    except BaseException as exc:
        if not observer.broken:
            observer.emit("simulation_failed", error={"type":type(exc).__name__, "message":str(exc)})
        raise


def engine_sources():
    return {name: hashlib.sha256((Path(__file__).with_name(name) if name in {"structural_execution.py", "structural_portfolio.py", "suspension_market.py"} else FROZEN / name).read_bytes()).hexdigest() for name in SOURCE_NAMES}


def freeze_saved_research_plan(*, identity, codes, calendar, targets, source_artifacts,
                               obligations, corporate_actions=(), initial_cash="1000000.00",
                               fixture_only=False, market_schedule=None):
    """Pure selection/contract freeze except hashing this small local engine.

    Artifacts contain code, root (controller absolute directory), rows_file,
    rows_sha256, manifest_file, manifest_sha256. This does not open artifacts.
    Every code/session needs all six explicit obligation states. Pending plans
    can be frozen; execution will stop at the first unresolved session.
    """
    plan = {"version": VERSION, "identity": identity, "codes": codes, "calendar": calendar,
            "targets": targets, "source_artifacts": source_artifacts, "obligations": obligations,
            "corporate_actions": [asdict(x) if isinstance(x, CashDividendAnnouncement) else x
                                  for x in corporate_actions], "initial_cash": initial_cash,
            "fixture_only": fixture_only, "engine_sources": engine_sources(), "resource_budget": RESOURCE_BUDGET,
            "market_schedule": market_schedule if market_schedule is not None else {"policy":HALT_POLICY,"episodes":[]},
            "policy": {"participation_rate": "0.05", "slippage_fraction": "0.001",
                       "rounding_policy": "aggregate_half_up_simulated",
                       "research_kind": "exposed_development_mechanical_accounting",
                       "failed_targets_redistributed": False, "source_arrival_verified": False,
                       "membership_is_strategy_eligibility": False,
                       "maximum_runs": 1, "recovery": "saved_only_no_simulation"}, **FLAGS}
    plan = _copy(plan)
    from decimal import Decimal
    _require(type(initial_cash) is str, "initial cash must be a decimal string")
    plan["initial_cash"] = format(Decimal(initial_cash), ".2f") if Decimal(initial_cash).is_finite() and Decimal(initial_cash) % Decimal("0.01") == 0 else initial_cash
    _validate_plan(plan)
    return {**plan, "plan_sha256": content_hash(plan)}


def _validate_plan(plan):
    _require(plan.get("version") == VERSION and all(plan.get(k) is v for k, v in FLAGS.items()), "version/flags changed")
    _require(_json(plan.get("resource_budget")) == _json(RESOURCE_BUDGET), "resource budget changed")
    _require(type(plan.get("fixture_only")) is bool, "fixture_only must be bool")
    identity = plan.get("identity")
    _require(type(identity) is dict and set(identity) == {"run_id", "architecture", "observation_id", "strategy_hash", "data_hash", "source_hash"}, "identity fields incomplete")
    _require(all(type(identity[k]) is str and 0 < len(identity[k]) <= 200 for k in ("run_id", "architecture", "observation_id")), "identity labels invalid")
    _require(all(_sha(identity[k]) for k in ("strategy_hash", "data_hash", "source_hash")), "identity hash invalid")
    codes, days = plan.get("codes"), plan.get("calendar")
    _require(type(codes) is list and 0 < len(codes) <= MAX_CODES and len(set(codes)) == len(codes), "code bound/duplicates")
    _require(all(type(x) is str and re.fullmatch(r"(?:sh[69]|sz[023])\d{5}", x) for x in codes), "invalid code")
    _require(type(days) is list and 3 <= len(days) <= MAX_SESSIONS and days == sorted(set(days)), "calendar bound/order")
    _require(all(type(x) is str and date.fromisoformat(x).isoformat() == x and "2017-01-01" <= x <= "2021-12-31" for x in days), "development calendar only")
    from decimal import Decimal
    cash = Decimal(plan["initial_cash"])
    _require(type(plan["initial_cash"]) is str and cash.is_finite() and cash > 0 and cash % Decimal("0.01") == 0, "initial cash must be full positive cents")
    _require(type(plan.get("targets")) is list and len(plan["targets"]) <= MAX_CODES * MAX_SESSIONS, "target bound")
    seen, weights = set(), {}
    for row in plan["targets"]:
        _require(set(row) == {"symbol", "signal_date", "trade_date", "available_at", "target_weight"}, "target fields invalid")
        _require(row["symbol"] in codes and row["trade_date"] in days[1:], "target scope invalid")
        pos = days.index(row["trade_date"])
        _require(row["signal_date"] == days[pos - 1], "target must trade on exactly next session")
        at = datetime.fromisoformat(row["available_at"])
        _require(at.tzinfo is not None, "target availability timezone missing")
        lower = datetime.fromisoformat(row["signal_date"] + "T15:05:00+08:00")
        upper = datetime.fromisoformat(row["trade_date"] + "T09:30:00+08:00")
        _require(lower <= at <= upper, "target not after completed prior bar / before open")
        w = Decimal(row["target_weight"])
        _require(type(row["target_weight"]) is str and w.is_finite() and 0 <= w <= 1, "weight invalid")
        _require(pos < len(days) - 1 or w == 0, "last session cannot open new target")
        key = (row["symbol"], row["trade_date"])
        _require(key not in seen, "duplicate target")
        seen.add(key)
        weights[row["trade_date"]] = weights.get(row["trade_date"], Decimal(0)) + w
    _require(all(w <= 1 for w in weights.values()), "target weights exceed capital")
    artifacts = plan.get("source_artifacts")
    _require(type(artifacts) is list and [a["code"] for a in artifacts] == codes, "source artifacts must exactly cover code order")
    for a in artifacts:
        _require(set(a) == {"code", "root", "rows_file", "rows_sha256", "manifest_file", "manifest_sha256"}, "artifact fields invalid")
        _require(Path(a["root"]).is_absolute() and _sha(a["rows_sha256"]) and _sha(a["manifest_sha256"]), "artifact root/hash invalid")
        for field in ("rows_file", "manifest_file"):
            p = Path(a[field])
            _require(not p.is_absolute() and ".." not in p.parts and len(p.parts) > 0, "artifact path escapes root")
    obligations = plan.get("obligations")
    _require(type(obligations) is list and len(obligations) == len(codes) * len(days) * len(KINDS), "obligation grid incomplete")
    needed = {(c, d, k) for c in codes for d in days for k in KINDS}
    for item in obligations:
        _require(set(item) == {"code", "date", "kind", "status", "evidence_sha256", "note"}, "obligation fields invalid")
        key = (item["code"], item["date"], item["kind"])
        _require(key in needed and item["status"] in STATES and type(item["note"]) is str and len(item["note"]) <= 1000, "duplicate/unknown obligation")
        needed.remove(key)
        if item["status"] not in {"pending", "unknown", "unsupported"}:
            _require(_sha(item["evidence_sha256"]), "resolved obligation requires evidence identity")
        _require(item["status"] != "fixture_complete" or plan["fixture_only"], "real source cannot claim fixture completeness")
        _require(not (item["kind"] == "corporate_actions" and item["status"] == "declared_simulation"), "unknown company actions cannot be assumed absent")
    _require(type(plan.get("corporate_actions")) is list and len(plan["corporate_actions"]) <= 128, "action bound")
    for item in plan["corporate_actions"]:
        _require(item.get("symbol") in codes, "corporate action outside scope")
        CashDividendAnnouncement(**item)
    SuspensionMarket(plan["market_schedule"], codes, days, plan["corporate_actions"])
    _require(set(plan.get("engine_sources", {})) == set(SOURCE_NAMES) and all(_sha(x) for x in plan["engine_sources"].values()), "engine identities invalid")
    _require(_json(plan["policy"]) == _json({"participation_rate": "0.05", "slippage_fraction": "0.001",
        "rounding_policy": "aggregate_half_up_simulated", "research_kind": "exposed_development_mechanical_accounting",
        "failed_targets_redistributed": False, "source_arrival_verified": False,
        "membership_is_strategy_eligibility": False, "maximum_runs": 1,
        "recovery": "saved_only_no_simulation"}), "frozen policy changed")
    _require(len(_json(plan).encode()) <= MAX_RECORD, "plan byte bound")


def _read_bounded(path, limit):
    _require(path.is_file() and path.stat().st_size <= limit, "source file missing/oversized")
    with path.open("rb") as stream:
        result = stream.read(limit + 1)
    _require(len(result) <= limit, "source grew beyond byte bound")
    return result


def _artifact_path(root, relative):
    root = Path(root).absolute()
    _require(root.resolve() == root and not root.is_symlink(), "source root redirected")
    target = root / relative
    _require(target.resolve().is_relative_to(root) and not target.is_symlink(), "source artifact redirected")
    return target


def _load_saved(plan):
    rows, manifests, total = [], [], 0
    for item in plan["source_artifacts"]:
        raw = _read_bounded(_artifact_path(item["root"], item["rows_file"]), MAX_COMPRESSED)
        mraw = _read_bounded(_artifact_path(item["root"], item["manifest_file"]), 262144)
        _require(hashlib.sha256(raw).hexdigest() == item["rows_sha256"], "saved rows hash mismatch")
        _require(hashlib.sha256(mraw).hexdigest() == item["manifest_sha256"], "saved manifest hash mismatch")
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(MAX_DECOMPRESSED + 1)
        _require(len(decoded) <= MAX_DECOMPRESSED, "decompressed byte bound")
        total += len(decoded)
        _require(total <= MAX_INPUT_BYTES, "total decompressed byte bound")
        source_rows, manifest = json.loads(decoded), json.loads(mraw)
        _require(type(source_rows) is list and len(source_rows) <= MAX_ROWS_PER_FILE, "saved source row bound")
        _require(manifest.get("codes") == [item["code"]] and manifest.get("execution_valid") is False,
                 "manifest code/flags mismatch")
        _require(manifest.get("adjusted_price_or_factor_inversion_used") is False, "raw-price provenance missing")
        seen = set()
        for row in source_rows:
            _require(type(row) is dict and row.get("code", row.get("symbol")) == item["code"], "source row code mismatch")
            day = row.get("date")
            _require(type(day) is str and "2017-01-01" <= day <= "2021-12-31" and date.fromisoformat(day).isoformat() == day, "saved source outside development")
            _require(day not in seen and row.get("execution_valid") is False, "duplicate source row or execution flag")
            seen.add(day)
            if day in plan["calendar"]:
                _require({"raw_open", "raw_close", "raw_prev_close", "volume", "stock_name"} <= set(row), "raw fields missing")
                rows.append(row)  # Keep invalid fields and rejection/membership metadata.
        _require(set(plan["calendar"]) <= seen, "saved grid has a missing date; no synthetic repair")
        manifests.append(manifest)
    frame = pd.DataFrame(rows)
    frame.attrs.update(saved_source_artifacts=_copy(plan["source_artifacts"]), saved_manifests=manifests,
                       saved_selected_rows_sha256=content_hash(rows), **FLAGS)
    return frame


@contextmanager
def _lease(root):
    with (root / "dispatch.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SavedResearchError("another controller holds dispatch lease") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if __import__("os").name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class SavedRawResearch:
    """One immutable research intent per independent directory; no automatic run."""

    def __init__(self, root):
        self.root = Path(root).absolute()
        self.db = self.root / "research.sqlite3"
        _require(self.root.resolve() == self.root and self.db.is_file() and not self.db.is_symlink(), "research store missing/redirected")

    @classmethod
    def create(cls, root, plan):
        plan = _copy(plan)
        expected = plan.pop("plan_sha256", None)
        _require(content_hash(plan) == expected, "plan self hash mismatch")
        _validate_plan(plan)
        root = Path(root).absolute()
        _require(not root.exists(), "research directory already exists; never replace")
        _require(root.parent.resolve() == root.parent, "research parent redirected")
        root.mkdir(parents=True)
        with sqlite3.connect(root / "research.sqlite3") as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE records (seq INTEGER PRIMARY KEY, previous_sha256 TEXT NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL)")
            db.executemany("INSERT INTO metadata VALUES (?, ?)", [("plan", _json(plan)), ("plan_sha256", expected),
                ("usage", _json({"bytes":0,"event_intents":0,"records":0}))])
        with (root / "plan.json").open("xb") as stream:
            stream.write((_json({**plan, "plan_sha256": expected}) + "\n").encode())
            stream.flush()
            __import__("os").fsync(stream.fileno())
        return cls(root)

    def _read(self, expected_plan_sha256):
        _require(_sha(expected_plan_sha256), "explicit expected plan hash required")
        uri = self.db.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            plan = json.loads(meta["plan"])
            _require(meta["plan_sha256"] == expected_plan_sha256 == content_hash(plan), "saved plan hash/scope mismatch")
            _validate_plan(plan)
            public = json.loads(_read_bounded(self.root / "plan.json", MAX_RECORD))
            _require(_json(public) == _json({**plan, "plan_sha256": expected_plan_sha256}), "plan file and store differ")
            records, previous, total = [], "0" * 64, 0
            for seq, prior, body, digest in db.execute("SELECT seq,previous_sha256,body,sha256 FROM records ORDER BY seq"):
                total += len(body.encode())
                _require(len(records) < MAX_RECORDS and total <= MAX_ARCHIVE and len(body.encode()) <= MAX_RECORD, "saved evidence bound exceeded")
                _require(seq == len(records) + 1 and prior == previous and digest == content_hash({"seq": seq, "previous_sha256": prior, "body": json.loads(body)}), "saved record hash chain mismatch")
                previous = digest
                records.append(json.loads(body))
            usage = json.loads(meta["usage"])
            expected_usage = {"bytes":total,"records":len(records),
                "event_intents":sum(x["kind"] == "checkpoint" and x["payload"]["phase"] == "event_intent" for x in records)}
            _require(usage == expected_usage, "resource counters differ from committed records")
        return plan, records, previous

    def _append(self, kind, payload):
        body = _json({"kind": kind, "payload": payload})
        _require(len(body.encode()) <= MAX_RECORD, "checkpoint byte bound exceeded")
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT seq,sha256 FROM records ORDER BY seq DESC LIMIT 1").fetchone()
            seq, previous = (prior[0] + 1, prior[1]) if prior else (1, "0" * 64)
            usage = json.loads(db.execute("SELECT value FROM metadata WHERE key='usage'").fetchone()[0])
            total = usage["bytes"]
            _require(usage["records"] == seq-1, "resource record count drift")
            _require(seq <= MAX_RECORDS and total + len(body.encode()) <= MAX_ARCHIVE, "checkpoint archive bound exceeded")
            reserve = 0 if kind == "terminal" else MAX_RECORD
            if kind == "checkpoint" and payload.get("phase") == "event_intent":
                count = usage["event_intents"]
                _require(count < MAX_EVENTS, "event intent bound exceeded before apply")
                reserve += MAX_RECORD
            _require(total + len(body.encode()) + reserve <= MAX_ARCHIVE, "next checkpoint admission lacks reserved persistence bytes")
            digest = content_hash({"seq": seq, "previous_sha256": previous, "body": json.loads(body)})
            db.execute("INSERT INTO records VALUES (?,?,?,?)", (seq, previous, body, digest))
            usage.update(bytes=total+len(body.encode()), records=seq,
                event_intents=usage["event_intents"]+int(kind == "checkpoint" and payload.get("phase") == "event_intent"))
            db.execute("UPDATE metadata SET value=? WHERE key='usage'", (_json(usage),))

    def execute_saved(self, *, expected_plan_sha256):
        """Explicit single dispatch. A previous attempt is inspected, never rerun."""
        with _lease(self.root):
            plan, records, _ = self._read(expected_plan_sha256)
            if records:
                _require(plan["engine_sources"] == engine_sources(), "engine drift; use saved-only inspection")
                _load_saved(plan)  # Explicit execute cannot certify changed inputs from an old result.
                SuspensionMarket(plan["market_schedule"], plan["codes"], plan["calendar"], plan["corporate_actions"]).verify_sources()
                return self.reconcile_saved_only(expected_plan_sha256=expected_plan_sha256)
            self._append("execution_intent", {"plan_sha256": expected_plan_sha256, "engine_sources": engine_sources(), **FLAGS})
            try:
                _require(plan["engine_sources"] == engine_sources(), "engine drift since plan freeze")
                frame = _load_saved(plan)
                self._append("inputs_accepted", {"selected_rows_sha256": frame.attrs["saved_selected_rows_sha256"],
                    "row_count": len(frame), "source_artifacts": plan["source_artifacts"], **FLAGS})
                def checkpoint(value):
                    self._append("checkpoint", value)
                    if value["phase"] == "day_started":
                        missing = [x for x in plan["obligations"] if x["date"] == value["date"] and x["status"] in {"pending", "unknown", "unsupported"}]
                        _require(not missing, "unresolved execution obligations: " + _json(missing))
                result = simulate_streamed_portfolio(daily_data=frame, calendar=plan["calendar"],
                    targets=pd.DataFrame(plan["targets"], columns=["symbol", "signal_date", "trade_date", "available_at", "target_weight"]),
                    initial_cash=plan["initial_cash"], corporate_actions=[CashDividendAnnouncement(**x) for x in plan["corporate_actions"]],
                    allow_incomplete_for_integration=True, participation_rate=plan["policy"]["participation_rate"],
                    slippage_fraction=plan["policy"]["slippage_fraction"], rounding_policy=plan["policy"]["rounding_policy"],
                    start_date=plan["calendar"][0], end_date=plan["calendar"][-1],
                    market_schedule=plan["market_schedule"], checkpoint=checkpoint)
                self._append("terminal", {"status": "completed_mechanical", "result_sha256": content_hash(result), **FLAGS})
            except Exception as exc:
                # A second persistence failure propagates. Earlier committed
                # intent/checkpoints remain available; do not retry simulation.
                self._append("terminal", {"status": "failed", "error": {"type": type(exc).__name__, "message": str(exc)}, **FLAGS})
            return self.reconcile_saved_only(expected_plan_sha256=expected_plan_sha256)

    def reconcile_saved_only(self, *, expected_plan_sha256):
        """Replay each committed ledger event once, without prices or strategy."""
        plan, records, head = self._read(expected_plan_sha256)
        _require(plan["engine_sources"]["raw_share_ledger.py"] == engine_sources()["raw_share_ledger.py"], "ledger replay source changed")
        if not records:
            return {"status":"prepared_not_run", "run_count":0, "plan_sha256":expected_plan_sha256, **FLAGS}
        _require(records[0]["kind"] == "execution_intent" and sum(r["kind"] == "execution_intent" for r in records) == 1, "execution intent count")
        _require(records[0]["payload"]["plan_sha256"] == expected_plan_sha256, "execution intent identity")
        ledger = None
        progress = {"orders":[], "trades":[], "daily":[], "skipped_actions":[]}
        current_date = None
        order_floor = 0
        terminal = pending = result = None
        for index, record in enumerate(records):
            kind, payload = record["kind"], record["payload"]
            _require(kind in {"execution_intent", "inputs_accepted", "checkpoint", "terminal"}, "unknown record kind")
            if kind == "terminal":
                _require(terminal is None and index == len(records)-1, "terminal ordering")
                terminal = payload
            if kind != "checkpoint":
                continue
            _require(payload["stream_version"] == 1 and all(payload.get(k) is v for k,v in FLAGS.items()), "checkpoint version/flags")
            phase = payload["phase"]
            _require(phase in {"event_intent", "event_applied", "event_rejected", "day_started", "day_completed", "simulation_failed", "simulation_completed"}, "checkpoint phase")
            if "genesis" in payload:
                _require(ledger is None, "genesis restarted")
                ledger = RawShareLedger.replay(payload["genesis"], [])
                _require(ledger.genesis["trading_days"] == plan["calendar"], "ledger calendar changed")
            if ledger is not None:
                _require(payload["journal_start"] == len(ledger._journal), "journal offset differs")
                delta = payload["journal_delta"]
                _require(len(delta) == int(phase == "event_applied"), "unexpected journal additions")
                for entry in delta:
                    applied = ledger.apply(entry["event"], as_of=entry["applied_at"])
                    _require(applied["status"] == "applied" and ledger._journal[-1] == entry, "saved event hash/state differs")
                _require(payload["journal_length"] == len(ledger._journal) and payload["ledger_head"] == ledger._previous_hash, "journal head/count differs")
                _require(ledger.verify_state_commitment(), "ledger commitment invalid")
            if phase == "day_started":
                expected_index = len(progress["daily"])
                _require(expected_index < len(plan["calendar"]) and payload["date"] == plan["calendar"][expected_index], "day started out of order")
                order_floor = len(progress["orders"])
            _require(payload["orders_start"] == order_floor <= len(progress["orders"]), "past orders overwritten")
            _require(order_floor+len(payload["orders_tail"]) >= len(progress["orders"]), "orders removed")
            progress["orders"][order_floor:] = payload["orders_tail"]
            for key in ("trades", "daily", "skipped_actions"):
                _require(payload["progress_offsets"][key] == len(progress[key]), "progress offset differs")
                progress[key].extend(payload["progress_append"][key])
            current_date = payload["current_date"]
            _require([row["date"] for row in progress["daily"]] == plan["calendar"][:len(progress["daily"])], "saved NAV calendar prefix differs")
            if phase == "event_intent":
                _require(pending is None and ledger is not None, "prior event unresolved / no ledger")
                pending = {"event":payload["event"], "as_of":payload["as_of"], "journal_length":len(ledger._journal)}
            elif phase in {"event_applied", "event_rejected"}:
                _require(pending is not None and pending["event"] == payload["event"] and pending["as_of"] == payload["as_of"], "event intent mismatch")
                _require(len(ledger._journal) == pending["journal_length"] + int(phase == "event_applied"), "applied/rejected event count")
                if phase == "event_applied":
                    _require(ledger._journal[-1]["event"] == pending["event"], "different event applied")
                pending = None
            elif phase == "simulation_completed":
                result = payload["result"]
                _require(pending is None and ledger is not None, "completed with unresolved ledger")
                _require(result["journal"] == ledger._journal and result["final_snapshot"] == ledger.snapshot(), "final ledger differs")
                _require(all(result[k] == progress[k] for k in progress), "final progress differs")
                _require(result["rejections"] == [o for o in progress["orders"] if o["status"] == "rejected"], "rejections omitted")
                _require(result["initial_cash"] == plan["initial_cash"] and result["execution_valid"] is False and result["formal_target_success"] is False, "final capital/flags differ")
                _require([r["date"] for r in result["daily"]] == plan["calendar"], "final omitted cash days")
        status = terminal["status"] if terminal else "interrupted_saved_only"
        _require(status in {"completed_mechanical", "failed", "interrupted_saved_only"}, "terminal status invalid")
        if terminal:
            _require(all(terminal.get(k) is v for k,v in FLAGS.items()), "terminal flags changed")
        if status == "completed_mechanical":
            _require(result is not None and pending is None and terminal["result_sha256"] == content_hash(result), "terminal result missing/changed")
        snapshot = ledger.snapshot() if ledger else None
        partial = None if status == "completed_mechanical" else {"genesis":ledger.genesis if ledger else None,
            "journal":ledger.journal if ledger else [], "snapshot":snapshot, "progress":{**progress,"current_date":current_date}, **FLAGS}
        return {"status":status,"run_count":1,"plan_sha256":expected_plan_sha256,
            "record_head_sha256":head,"records":len(records),"pending_event_intent":pending,
            "result":result if status == "completed_mechanical" else None,"partial":partial,
            "error":terminal.get("error") if terminal else None,"last_started_date":current_date,
            "valuation_complete_through":progress["daily"][-1]["date"] if progress["daily"] else None,
            "failed_or_interrupted_nav_is_complete":False if status != "completed_mechanical" else None,
            "resource_usage":{"persisted_payload_bytes":sum(len(_json(r).encode()) for r in records),
                "pending_write_reserved_bytes":MAX_RECORD if pending else 0,"terminal_reserved_bytes":0 if terminal else MAX_RECORD,
                "event_intents":sum(r["kind"] == "checkpoint" and r["payload"]["phase"] == "event_intent" for r in records)},
            "recovery_action":"verified_saved_evidence_only_no_simulation","source_artifacts_reopened":False, **FLAGS}

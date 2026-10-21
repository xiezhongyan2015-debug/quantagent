"""V3 task modes, shared reservations and application journal in one database.

Only the trusted controller can access this database. Model responses cannot
alter plans or call this interface. Reservations are nominal, not billing caps.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4

from .closing import BudgetState, ClosingPolicy, FINAL_ACTION, allowed_actions, decide


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode("utf-8")).hexdigest()


def need(value, reason):
    if not value:
        raise AdmissionBlocked(reason)


class AdmissionBlocked(ValueError):
    pass


_threads = set()
_mutex = threading.Lock()


@contextmanager
def worker_lease(root):
    """OS ownership, released by process death; saved PIDs are informational."""
    root = Path(root).resolve()
    key = os.path.normcase(str(root))
    with _mutex:
        need(key not in _threads, "V3 worker lease held")
        _threads.add(key)
    file = None
    try:
        file = (root / "worker.lock").open("a+b")
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        if file:
            file.close()
        with _mutex:
            _threads.discard(key)


class Ledger:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "ledger.sqlite3"
        need(self.path.is_file(), "V3 stage missing")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def create(cls, root, *, policy, tasks, deadline_epoch, provenance):
        need(type(policy) is ClosingPolicy, "explicit policy required")
        need(type(deadline_epoch) in (float, int) and math.isfinite(deadline_epoch)
             and deadline_epoch > time.time(), "future finite deadline required")
        need(type(tasks) is dict and 1 <= len(tasks) <= 32, "frozen tasks required")
        need(all(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k) and type(v) is dict for k, v in tasks.items()),
             "task identity invalid")
        need(len(tasks) * policy.closing_reserve <= policy.stage_tokens
             and len(tasks) <= policy.stage_calls, "stage cannot protect each task's final")
        if 'controller_policy' in provenance:
            from .controller_plan import validate
            validate(provenance['controller_policy'], tasks)
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=False)
        path = root / "ledger.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.executescript("""
                CREATE TABLE stage (id INTEGER PRIMARY KEY CHECK(id=1), plan TEXT NOT NULL,
                    plan_hash TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0, reason TEXT);
                CREATE TABLE tasks (id TEXT PRIMARY KEY, input TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'explore', terminal TEXT, final_call TEXT);
                CREATE TABLE calls (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    ordinal INTEGER NOT NULL, mode TEXT NOT NULL, actions TEXT NOT NULL,
                    reserve INTEGER NOT NULL, status TEXT NOT NULL, intent TEXT NOT NULL,
                    receipt TEXT, known_tokens INTEGER, result TEXT, created REAL NOT NULL,
                    UNIQUE(task_id, ordinal));
                CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL);
            """)
            plan = {"kind": "meta_framework_v3", "policy": asdict(policy), "tasks": tasks,
                    "deadline_epoch": deadline_epoch, "provenance": provenance,
                    "model": "gpt-6-astra", "effort": "xhigh", "created_epoch": time.time()}
            from .study_registry import verify_stage
            verify_stage(root, plan, bind=True)
            db.execute("INSERT INTO stage(id,plan,plan_hash) VALUES(1,?,?)", (serial(plan), digest(plan)))
            db.executemany("INSERT INTO tasks(id,input) VALUES(?,?)", [(k, serial(v)) for k, v in tasks.items()])
            db.commit()
            (root / "plan.json").write_text(serial(plan), encoding="utf-8")
        finally:
            db.close()
        return cls(root)

    @staticmethod
    def event(db, kind, body):
        db.execute("INSERT INTO events(at,kind,body) VALUES(?,?,?)", (time.time(), kind, serial(body)))

    def _plan(self, db):
        stage = db.execute("SELECT * FROM stage WHERE id=1").fetchone()
        plan = json.loads(stage["plan"])
        need(digest(plan) == stage["plan_hash"] and
             json.loads((self.root / "plan.json").read_text(encoding="utf-8")) == plan,
             "frozen plan drift")
        from .study_registry import verify_stage
        verify_stage(self.root, plan)
        return stage, plan, ClosingPolicy(**plan["policy"])

    def _state(self, db, task_id):
        stage, plan, policy = self._plan(db)
        task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        need(task is not None, "unknown task")
        calls = list(db.execute("SELECT * FROM calls ORDER BY created,id"))
        own = [x for x in calls if x["task_id"] == task_id]
        unresolved = [x for x in calls if x["status"] in ("pending", "unknown", "received", "applying")]
        # Every unfinished sibling retains its own closing opportunity. Its
        # already in-flight reserve is accounted in exposure, never released.
        siblings = db.execute("SELECT count(*) FROM tasks WHERE id<>? AND terminal IS NULL", (task_id,)).fetchone()[0]
        exposure = lambda xs: sum(x["known_tokens"] if x["known_tokens"] is not None else x["reserve"] for x in xs)
        state = BudgetState(exposure(own), exposure(calls) + siblings * policy.closing_reserve,
            len(own), len(calls) + siblings, 0 if task["final_call"] else 1,
            time.time(), plan["deadline_epoch"], unknown=any(x["status"] == "unknown" for x in unresolved),
            pending=bool(unresolved), evidence_valid=not bool(stage["paused"]))
        decision = decide(policy, state, task["mode"])
        from .study_allocation import constrain
        decision = constrain(self.root, decision)
        from .process_envelope import constrain as constrain_process
        decision = constrain_process(self.root, decision)
        if task["terminal"] and not unresolved:
            decision = type(decision)("terminal_without_submission", ("task_already_terminal",))
        return stage, plan, policy, task, calls, state, decision

    def status(self, task_id):
        with self.transaction() as db:
            _, plan, policy, task, calls, state, decision = self._state(db, task_id)
            return {"task_id": task_id, "mode": decision.mode, "persisted_mode": task["mode"],
                "reasons": list(decision.reasons), "terminal": task["terminal"], "final_call": task["final_call"],
                "budget_state": asdict(state), "policy": asdict(policy), "plan_hash": digest(plan),
                "known_tokens": sum(x["known_tokens"] or 0 for x in calls),
                "unknown_or_pending_reserve": sum(x["reserve"] for x in calls if x["known_tokens"] is None),
                "old_v2_unknown_reserve": plan["provenance"].get("old_v2_unknown_reserve", 80000),
                "calls": [{k: x[k] for k in ("id", "task_id", "ordinal", "mode", "status", "known_tokens", "reserve")}
                          for x in calls]}

    def reserve(self, task_id, registered_actions, make_request):
        """Derive schema, persist mode and reserve in the SAME shared transaction.

        make_request is trusted deterministic prompt/schema construction only.
        No gateway or tool may execute until this transaction commits.
        """
        denied = None
        intent = None
        with self.transaction() as db:
            _, plan, policy, task, calls, state, decision = self._state(db, task_id)
            menu = allowed_actions(decision, registered_actions)
            if not menu:
                denied = ",".join(decision.reasons)
                if decision.mode == "terminal_without_submission" and not task["terminal"]:
                    db.execute("UPDATE tasks SET mode=?,terminal=? WHERE id=?",
                               (decision.mode, "terminal_without_submission", task_id))
                    self.event(db, "terminal_without_submission", {"task_id": task_id, "reason": denied})
            else:
                if decision.mode == "close_only":
                    db.execute("UPDATE tasks SET mode='close_only' WHERE id=?", (task_id,))
                ordinal = 1 + sum(x["task_id"] == task_id for x in calls)
                prompt, schema = make_request(menu, asdict(state), decision)
                need(type(prompt) is str and 0 < len(prompt.encode("utf-8")) <= 262144, "prompt bound")
                need(type(schema) is dict, "schema object required")
                call_id = f"{task_id}_{ordinal:03d}_{uuid4().hex[:12]}"
                # These hashes intentionally match the existing gateway protocol.
                hash_text = lambda v: hashlib.sha256(json.dumps(v, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                intent = {"intent_version": 1, "intent_id": call_id, "task_id": task_id,
                    "ordinal": ordinal, "plan_hash": digest(plan), "model": plan["model"], "effort": plan["effort"],
                    "mode": decision.mode, "actions": list(menu), "prompt": prompt, "schema": schema,
                    "prompt_hash": hash_text(prompt), "schema_hash": hash_text(schema)}
                reserve = policy.closing_reserve if decision.mode == "close_only" else policy.call_reserve
                from .study_registry import reserve_model
                reserve_model(self.root, intent, reserve)
                db.execute("INSERT INTO calls(id,task_id,ordinal,mode,actions,reserve,status,intent,created) VALUES(?,?,?,?,?,?,'pending',?,?)",
                    (call_id, task_id, ordinal, decision.mode, serial(menu), reserve, serial(intent), time.time()))
                self.event(db, "call_reserved", {"id": call_id, "mode": decision.mode, "reserve": reserve})
        if denied is not None:
            raise AdmissionBlocked(denied)
        return intent

    def call(self, call_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            need(row is not None, "call missing")
            value = dict(row)
            for key in ("intent", "actions", "receipt", "result"):
                value[key] = json.loads(value[key]) if value[key] is not None else None
            return value

    def receive_saved(self, call_id, verifier):
        """Verifier is the pinned read-only gateway verifier, never model code."""
        call = self.call(call_id)
        intent = call["intent"]
        receipt = verifier(self.root / "calls" / call_id,
            expected_prompt_hash=intent["prompt_hash"], expected_schema=intent["schema"],
            expected_artifact_sha256=call["receipt"]["artifact_sha256"] if call["receipt"] else None)
        identity = receipt.get("request_identity")
        need(identity is not None and identity["intent_id"] == call_id, "receipt belongs to another intent")
        usage = receipt["usage"]
        need(all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")), "unknown usage")
        cost = usage["input_tokens"] + usage["output_tokens"]
        with self.transaction() as db:
            current = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if current["receipt"] is not None:
                saved = json.loads(current["receipt"])
                need(saved["artifact_sha256"] == receipt["artifact_sha256"] and saved["response"] == receipt["response"]
                     and saved["usage"] == receipt["usage"], "saved receipt drift")
                from .study_registry import settle_model
                settle_model(self.root, intent, saved)
                return saved
            need(current["status"] in ("pending", "unknown"), "call already settled")
            db.execute("UPDATE calls SET status='received',receipt=?,known_tokens=? WHERE id=?", (serial(receipt), cost, call_id))
            self.event(db, "receipt_verified", {"id": call_id, "known_tokens": cost})
        from .study_registry import settle_model
        settle_model(self.root, intent, receipt)
        return receipt

    def unknown(self, call_id, reason):
        with self.transaction() as db:
            db.execute("UPDATE calls SET status='unknown' WHERE id=? AND status='pending'", (call_id,))
            self.event(db, "unresolved_call", {"id": call_id, "reason": str(reason)[:2000]})

    def begin_apply(self, call_id):
        denied = None
        with self.transaction() as db:
            call = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            need(call is not None and call["receipt"] is not None, "no verified model receipt")
            if call["status"] in ("applied", "failed"):
                return None
            need(call["status"] == "received", "application unresolved; saved-only recovery required")
            action = json.loads(call["receipt"])["response"]
            if action.get("action") not in json.loads(call["actions"]):
                denied = "model action excluded by admitted public menu"
                db.execute("UPDATE calls SET status='failed',result=? WHERE id=?", (serial({"error": denied}), call_id))
                db.execute("UPDATE tasks SET terminal='invalid_action' WHERE id=?", (call["task_id"],))
                self.event(db, "invalid_action", {"id": call_id, "reason": denied})
            else:
                db.execute("UPDATE calls SET status='applying' WHERE id=?", (call_id,))
                self.event(db, "action_intent", {"id": call_id, "action": action["action"], "request_hash": digest(action)})
        if denied:
            raise AdmissionBlocked(denied)
        return action

    def finish_apply(self, call_id, result, *, failed=False, final=False):
        with self.transaction() as db:
            call = db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            need(call is not None and call["status"] == "applying", "no pending action")
            action = json.loads(call["receipt"])["response"]["action"]
            need(final == (action == FINAL_ACTION), "final flag does not match model action")
            db.execute("UPDATE calls SET status=?,result=? WHERE id=?",
                       ("failed" if failed else "applied", serial(result), call_id))
            if final:
                terminal = "invalid_final" if failed else "submitted"
                db.execute("UPDATE tasks SET terminal=?,final_call=? WHERE id=?",
                           (terminal, None if failed else call_id, call["task_id"]))
            elif call["mode"] == "close_only":
                raise AdmissionBlocked("nonfinal escaped closing gate")
            self.event(db, "action_settled", {"id": call_id, "failed": failed, "final": final, "result_hash": digest(result)})

    def history(self, task_id):
        with self.transaction() as db:
            rows = db.execute("SELECT id,status,receipt,result FROM calls WHERE task_id=? ORDER BY ordinal", (task_id,))
            return [{"id": x["id"], "status": x["status"],
                     "response": json.loads(x["receipt"])["response"] if x["receipt"] else None,
                     "result": json.loads(x["result"]) if x["result"] else None} for x in rows]

    def pause(self, reason):
        with self.transaction() as db:
            db.execute("UPDATE stage SET paused=1,reason=? WHERE id=1", (reason,))
            self.event(db, "stage_paused", {"reason": reason})

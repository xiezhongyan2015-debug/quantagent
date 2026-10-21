"""Isolated v18 compatibility worker; original sources and module objects stay intact.

EOD trade commitments use one detached journal and canonical prefix hashes.
The private observer exports append-only daily commits and complete initial,
periodic and terminal checkpoints. No cross-call cache can bless historical
edits. FIFO, tax brackets, financial events, fees, NAV and the complete final
native result remain original. Runtime callers use the supervised job API.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import types

from .jobs import run_job

VERSION = "v6_private_v18_account_tax_snapshot_v4"
FULL_CHECKPOINT_INTERVAL_DAYS = 64
PROGRESS_TABLES = ("daily", "orders", "trades", "skipped_actions")
REPLACE_RETRY_DELAYS = (.02, .05, .10)
ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "experiment_traces/meta_ashare_revision18/src/quanta_agents"
_LOCK = threading.RLock()
_MODULES = {}


def _serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_serial(value).encode("utf-8")).hexdigest()


def _write(path, value, *, exclusive=False):
    path = Path(path)
    temporary = path if exclusive else path.with_name(path.name + ".tmp")
    encoded = (_serial(value) + "\n").encode("utf-8")
    with temporary.open("xb" if exclusive else "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        _replace(temporary, path)
    return len(encoded)


def _replace(temporary, path):
    """Retry only short Windows sharing/access conflicts, then preserve failure."""
    for attempt in range(len(REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == len(REPLACE_RETRY_DELAYS):
                raise
            time.sleep(REPLACE_RETRY_DELAYS[attempt])


def _checkpoint_state(module):
    """Replace only the private module's observer, before full-history copying."""
    original = module._CheckpointState

    class IncrementalCheckpointState(original):
        def __init__(self, callback):
            super().__init__(callback)
            self._genesis_persisted = False
            self._last_complete_day = None
            self._event_count = 0
            self._journal_head = None
            self._committed_counts = dict.fromkeys(PROGRESS_TABLES, 0)
            self._interval = getattr(callback, "checkpoint_interval_days", FULL_CHECKPOINT_INTERVAL_DAYS)
            if type(self._interval) is not int or self._interval < 1:
                raise ValueError("checkpoint interval must be a positive integer")

        def emit(self, phase, **details):
            application = details.get("application", {})
            if phase == "event_applied" and application.get("status") == "applied":
                self._event_count += 1
                self._journal_head = application["entry_hash"]
                # The pinned native apply has just appended this exact entry.
                # Read one private entry, never the deepcopy-producing public
                # journal property. It is detached before external callbacks.
                entry = self.ledger._journal[-1]
                if entry["entry_hash"] != self._journal_head:
                    raise RuntimeError("native journal application identity mismatch")
                details["journal_entry"] = entry
            current = self.progress.get("current_date")
            counts = {name: len(self.progress.get(name, [])) for name in PROGRESS_TABLES}
            frontier = details.get("date") if phase == "day_completed" else self._last_complete_day
            persistence = {"last_complete_day": frontier, "current_date": current,
                "completed_daily_rows": counts["daily"], "counts": counts,
                "observed_applied_events": self._event_count, "journal_head": self._journal_head,
                "unfinished_current_day": current if current != frontier else None}
            if phase == "day_completed":
                details["progress_starts"] = dict(self._committed_counts)
                details["progress_delta"] = {name: self.progress.get(name, [])[start:]
                    for name, start in self._committed_counts.items()}
            full = (phase not in {"event_intent", "event_applied", "event_rejected", "day_started", "day_completed"}
                    or (phase == "day_completed" and counts["daily"] % self._interval == 0)
                    or (self.ledger is not None and not self._genesis_persisted))
            if full:
                # Whole native histories are materialized only at sparse anchors.
                super().emit(phase, checkpoint_kind="full", persistence=persistence, **details)
                self._genesis_persisted = self.ledger is not None
                if phase == "day_completed":
                    self._last_complete_day = details["date"]
                    self._committed_counts = counts
                return
            # Deliberately never access ledger.journal/snapshot or accumulated
            # progress lists here. Event/application copies preserve original hashes.
            value = {"phase": phase, "checkpoint_kind": "daily" if phase == "day_completed" else "light", "persistence": persistence,
                     "execution_valid": False, "formal_target_success": False, **details}
            value = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
            try:
                self.callback(value)
            except Exception as exc:
                self.broken = True
                raise module.CheckpointPersistenceError(str(exc)) from exc
            except BaseException:
                self.broken = True
                raise
            if phase == "day_completed":
                self._last_complete_day = details["date"]
                self._committed_counts = counts

    return IncrementalCheckpointState


class _CheckpointSink:
    """Fsync event evidence, then append one exact daily delta commit.

    account_days.jsonl is the complete-day authority. The replaceable small
    frontier is a monitoring hint and can lag after interruption. Full snapshots
    are anchors, never an alternative authority for complete-day certification.
    No resume or automatic retry is performed by this persistence interface.
    """
    def __init__(self, output_dir, request_hash, *, checkpoint_interval_days=FULL_CHECKPOINT_INTERVAL_DAYS):
        self.folder, self.request_hash = Path(output_dir), request_hash
        self.checkpoint_interval_days = checkpoint_interval_days
        self.sequence = self.full_count = self.full_bytes = self.progress_bytes = 0
        self.day_bytes = self.frontier_bytes = self.genesis_bytes = self.day_count = 0
        self.previous_log_hash = "0" * 64
        self.previous_day_hash = "0" * 64
        self.committed_counts = dict.fromkeys(PROGRESS_TABLES, 0)
        self.last_complete_day = self.current_date = None
        self.last_full_phase = None
        self._stream = (self.folder / "account_progress.jsonl").open("xb")
        self._days = (self.folder / "account_days.jsonl").open("xb")
        self._closed = False

    @staticmethod
    def _append(stream, record):
        encoded = (_serial(record) + "\n").encode("utf-8")
        if stream.write(encoded) != len(encoded):
            raise OSError("short checkpoint append")
        stream.flush()
        os.fsync(stream.fileno())
        return len(encoded)

    def _append_progress(self, record):
        record = {**record, "previous_log_hash": self.previous_log_hash}
        record["record_hash"] = _hash(record)
        self.progress_bytes += self._append(self._stream, record)
        self.previous_log_hash = record["record_hash"]

    def _append_day(self, record):
        record = {**record, "previous_commit_hash": self.previous_day_hash}
        record["commit_hash"] = _hash(record)
        self.day_bytes += self._append(self._days, record)
        self.previous_day_hash = record["commit_hash"]

    def _frontier(self):
        self.frontier_bytes += _write(self.folder / "account_frontier.json", {
            "version": VERSION, "request_hash": self.request_hash,
            "authority": "account_days.jsonl", "hint_may_lag_after_interruption": True,
            "complete_days": self.day_count, "last_complete_day": self.last_complete_day,
            "current_date": self.current_date,
            "unfinished_current_day": self.current_date if self.current_date != self.last_complete_day else None,
            "commit_hash": self.previous_day_hash, "progress_log_head": self.previous_log_hash,
            "progress_sequence": self.sequence, "financial_validation_claimed": False})

    def __call__(self, value):
        self.sequence += 1
        kind = value.get("checkpoint_kind", "full")  # Original observer comparison remains supported.
        progress = value.get("progress", {})
        persistence = value.get("persistence") or {
            "current_date": progress.get("current_date"),
            "last_complete_day": value.get("date") if value["phase"] == "day_completed" else self.last_complete_day,
            "completed_daily_rows": len(progress.get("daily", [])),
            "counts": {name: len(progress.get(name, [])) for name in PROGRESS_TABLES},
            "journal_head": value.get("snapshot", {}).get("journal_head")}
        self.current_date = persistence.get("current_date")
        if not self.genesis_bytes and "genesis" in value:
            if value.get("journal"):
                raise ValueError("initial checkpoint must precede native events")
            base = {"version": VERSION, "request_hash": self.request_hash,
                    "genesis": value["genesis"], "snapshot": value["snapshot"]}
            base["record_hash"] = _hash(base)
            self.genesis_bytes = _write(self.folder / "account_genesis.json", base, exclusive=True)
        if kind == "full":
            written = _write(self.folder / "account_checkpoint.json", {
                "version": VERSION, "request_hash": self.request_hash, "sequence": self.sequence,
                "native_checkpoint": value, "persistence": persistence,
                "financial_validation_claimed": False})
            self.full_count += 1
            self.full_bytes += written
            self.last_full_phase = value["phase"]
        elif kind not in {"light", "daily"}:
            raise ValueError("unknown checkpoint persistence kind")
        record = {"version": VERSION, "request_hash": self.request_hash, "sequence": self.sequence,
            "phase": value["phase"], "checkpoint_kind": kind, "persistence": persistence,
            "financial_validation_claimed": False}
        for key in ("date", "as_of", "application", "error"):
            if key in value:
                record[key] = value[key]
        if "event" in value:
            record["event_payload_sha256"] = _hash(value["event"])
            if value["phase"] == "event_applied" and value["application"]["status"] == "applied":
                record["journal_entry"] = value.get("journal_entry") or value["journal"][-1]
            else:
                record["event"] = value["event"]
        self._append_progress(record)
        if value["phase"] == "day_completed":
            starts = value.get("progress_starts", self.committed_counts)
            delta = value.get("progress_delta")
            if delta is None:  # Support original observer in equivalence measurements.
                delta = {name: progress.get(name, [])[start:] for name, start in starts.items()}
            if starts != self.committed_counts or len(delta["daily"]) != 1 or delta["daily"][0]["date"] != value["date"]:
                raise ValueError("invalid complete-day delta boundary")
            if {key: starts[key] + len(delta[key]) for key in PROGRESS_TABLES} != persistence["counts"]:
                raise ValueError("complete-day delta counts mismatch")
            self._append_day({"version": VERSION, "request_hash": self.request_hash,
                "day_sequence": self.day_count + 1, "date": value["date"],
                "progress_sequence": self.sequence, "progress_log_head": self.previous_log_hash,
                "starts": starts, "delta": delta, "persistence": persistence,
                "financial_validation_claimed": False})
            # Advance only after the complete newline was flushed and fsynced.
            self.day_count += 1
            self.last_complete_day = value["date"]
            self.committed_counts = dict(persistence["counts"])
        if value["phase"] in {"day_started", "day_completed", "simulation_completed", "simulation_failed"}:
            self._frontier()

    def metrics(self):
        return {"callback_count": self.sequence, "full_checkpoint_writes": self.full_count,
            "full_checkpoint_bytes_written": self.full_bytes, "progress_bytes_written": self.progress_bytes,
            "daily_commit_bytes_written": self.day_bytes, "daily_commits": self.day_count,
            "frontier_bytes_written": self.frontier_bytes, "genesis_bytes_written": self.genesis_bytes,
            "full_checkpoint_interval_days": self.checkpoint_interval_days,
            "total_payload_bytes_written": self.full_bytes + self.progress_bytes + self.day_bytes + self.frontier_bytes + self.genesis_bytes,
            "last_complete_day": self.last_complete_day, "current_date": self.current_date,
            "unfinished_current_day": self.current_date if self.current_date != self.last_complete_day else None,
            "last_full_phase": self.last_full_phase, "progress_log_head": self.previous_log_hash,
            "day_commit_head": self.previous_day_hash,
            "financial_validation_claimed": False}

    def close(self):
        if not self._closed:
            self._stream.close()
            self._days.close()
            self._closed = True
            _write(self.folder / "account_checkpoint_metrics.json", self.metrics(), exclusive=True)


def _read_chain(path, request_hash, *, hash_key, previous_key, sequence_key):
    """Read committed newline records; an unterminated final suffix is ignored.

    A malformed *terminated* record is corruption, not a recoverable suffix.
    Hashes prove consistency against saved heads, not authenticity/signatures.
    """
    rows, head, ignored = [], "0" * 64, 0
    with Path(path).open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n"):
                ignored = len(raw)
                break
            row = json.loads(raw)
            if (row.get("version") != VERSION or row.get("request_hash") != request_hash
                    or row.get(sequence_key) != len(rows) + 1 or row.get(previous_key) != head
                    or row.get(hash_key) != _hash({key: value for key, value in row.items() if key != hash_key})):
                raise ValueError(f"invalid {Path(path).name} chain/identity")
            head = row[hash_key]
            rows.append(row)
    return rows, head, ignored


def verify_checkpoint_evidence(output_dir, *, request_hash=None):
    """Reconstruct only durable complete days; never resume market execution.

    Offline verification checks both chains, exact original native journal
    hashes/state transitions, delta boundaries, frontier hints and any full
    anchor/result. Event records after the last daily commit remain explicitly
    uncommitted. Temporary files never supply an anchor or frontier.
    """
    folder = Path(output_dir)
    base = json.loads((folder / "account_genesis.json").read_bytes())
    identity = base["request_hash"]
    if (base.get("version") != VERSION or (request_hash is not None and request_hash != identity)
            or base.get("record_hash") != _hash({key: value for key, value in base.items() if key != "record_hash"})):
        raise ValueError("genesis checkpoint identity mismatch")
    input_verified = False
    request_path = folder / "account_input.json"
    if request_path.exists():
        request_bytes = request_path.read_bytes()
        request = json.loads(request_bytes)
        if (hashlib.sha256(request_bytes).hexdigest() != identity or request.get("version") != VERSION
                or request.get("payload_hash") != _hash(request.get("payload"))
                or request.get("source_manifest") != source_manifest()):
            raise ValueError("account input/source identity mismatch")
        input_verified = True
    native_module = legacy_modules().ledger
    journal_type = native_module.RawShareLedger
    initial = journal_type.replay(base["genesis"], [])
    if initial.snapshot() != base["snapshot"]:
        raise ValueError("genesis snapshot mismatch")
    logs, log_head, log_tail = _read_chain(folder / "account_progress.jsonl", identity,
        hash_key="record_hash", previous_key="previous_log_hash", sequence_key="sequence")
    days, day_head, day_tail = _read_chain(folder / "account_days.jsonl", identity,
        hash_key="commit_hash", previous_key="previous_commit_hash", sequence_key="day_sequence")
    entries, boundaries, pending, applied_ids = [], {}, None, {}
    current = completed_phase_date = None
    ended = False
    for row in logs:
        phase = row["phase"]
        if ended:
            raise ValueError("progress continues after terminal phase")
        if phase == "event_intent":
            if pending is not None or row["event_payload_sha256"] != _hash(row["event"]):
                raise ValueError("invalid event intent boundary")
            pending = row
        elif phase in {"event_applied", "event_rejected"}:
            if (pending is None or row["event_payload_sha256"] != pending["event_payload_sha256"]
                    or row["as_of"] != pending["as_of"]):
                raise ValueError("event application has no matching durable intent")
            if phase == "event_applied":
                application = row["application"]
                if (application["event_hash"] != pending["event_payload_sha256"]
                        or application["event_id"] != pending["event"]["event_id"]):
                    raise ValueError("event application hash mismatch")
                if application["status"] == "applied":
                    entry = row["journal_entry"]
                    if (entry["event"] != pending["event"] or entry["entry_hash"] != application["entry_hash"]
                            or entry["event_hash"] != application["event_hash"]
                            or entry["applied_at"] != native_module._timestamp(pending["as_of"]).isoformat()
                            or application["event_id"] in applied_ids):
                        raise ValueError("native journal entry/application mismatch")
                    entries.append(entry)
                    applied_ids[application["event_id"]] = application["event_hash"]
                elif application["status"] == "duplicate":
                    if applied_ids.get(application["event_id"]) != application["event_hash"] or row.get("event") != pending["event"]:
                        raise ValueError("duplicate has no matching earlier native application")
                else:
                    raise ValueError("unknown native application status")
            elif row["event"] != pending["event"]:
                raise ValueError("rejected event differs from intent")
            pending = None
        elif phase == "day_started":
            if pending is not None or current != completed_phase_date:
                raise ValueError("day started before previous day completed")
            current = row["date"]
            if completed_phase_date is not None and current <= completed_phase_date:
                raise ValueError("nonchronological day start")
        elif phase == "day_completed":
            if pending is not None or current != row["date"] or current == completed_phase_date:
                raise ValueError("invalid complete-day phase boundary")
            completed_phase_date = current
            persistence = row["persistence"]
            head = entries[-1]["entry_hash"] if entries else _hash(base["genesis"])
            if persistence["journal_head"] != head or persistence.get("observed_applied_events", len(entries)) != len(entries):
                raise ValueError("day boundary journal commitment mismatch")
            boundaries[row["sequence"]] = (row, len(entries))
        elif phase == "simulation_completed":
            if pending is not None or current != completed_phase_date:
                raise ValueError("simulation completed with unfinished day")
            ended = True
        elif phase == "simulation_failed":
            ended = True
        else:
            raise ValueError("unsupported progress phase")
    # Native replay validates state_hash, applied_at, sequence and every entry
    # byte-equivalent object, not merely the outer persistence hash chain.
    observed_ledger = journal_type.replay(base["genesis"], entries)
    progress = {name: [] for name in PROGRESS_TABLES}
    committed_events, last, previous_progress_sequence = 0, None, 0
    for day in days:
        sequence = day["progress_sequence"]
        if sequence not in boundaries or sequence <= previous_progress_sequence:
            raise ValueError("daily commit has no complete progress boundary")
        boundary, count = boundaries[sequence]
        if (day["progress_log_head"] != boundary["record_hash"] or day["date"] != boundary["date"]
                or day["persistence"] != boundary["persistence"]):
            raise ValueError("daily commit/progress mismatch")
        if last is not None and day["date"] <= last:
            raise ValueError("nonchronological daily commit")
        starts = {name: len(progress[name]) for name in PROGRESS_TABLES}
        delta = day["delta"]
        if (day["starts"] != starts or set(delta) != set(PROGRESS_TABLES)
                or len(delta["daily"]) != 1 or delta["daily"][0]["date"] != day["date"]):
            raise ValueError("invalid daily delta boundary")
        for name in PROGRESS_TABLES:
            progress[name].extend(delta[name])
        expected_counts = {name: len(progress[name]) for name in PROGRESS_TABLES}
        persistence = day["persistence"]
        if (persistence["counts"] != expected_counts or persistence["completed_daily_rows"] != len(progress["daily"])
                or persistence["last_complete_day"] != day["date"]
                or persistence["current_date"] != day["date"] or persistence.get("unfinished_current_day") is not None):
            raise ValueError("daily commit frontier/count mismatch")
        last, committed_events, previous_progress_sequence = day["date"], count, sequence
    # A missing commit in the middle is never bridged by a later commit.
    committed_boundary_sequences = [day["progress_sequence"] for day in days]
    if committed_boundary_sequences != sorted(boundaries)[:len(days)]:
        raise ValueError("daily commit skipped a completed phase")
    committed_journal = entries[:committed_events]
    committed_ledger = journal_type.replay(base["genesis"], committed_journal)
    progress["current_date"] = last
    hint_path = folder / "account_frontier.json"
    hint_stale = False
    if hint_path.exists():
        hint = json.loads(hint_path.read_bytes())
        n = hint["complete_days"]
        if (hint.get("version") != VERSION or hint.get("request_hash") != identity or type(n) is not int or not 0 <= n <= len(days)
                or hint["commit_hash"] != (days[n-1]["commit_hash"] if n else "0"*64)
                or hint["last_complete_day"] != (days[n-1]["date"] if n else None)):
            raise ValueError("frontier hint points beyond a verified daily commit")
        hint_sequence = hint.get("progress_sequence")
        if (type(hint_sequence) is not int or not 1 <= hint_sequence <= len(logs)
                or hint.get("progress_log_head") != logs[hint_sequence-1]["record_hash"]):
            raise ValueError("frontier hint has unknown progress sequence/head")
        if (hint.get("current_date") != logs[hint_sequence-1]["persistence"]["current_date"]
                or hint.get("unfinished_current_day") != (hint["current_date"] if hint["current_date"] != hint["last_complete_day"] else None)
                or (n and days[n-1]["progress_sequence"] > hint_sequence)):
            raise ValueError("frontier hint has inconsistent phase/date boundary")
        hint_stale = n != len(days) or hint.get("current_date") != current
    checkpoint_path = folder / "account_checkpoint.json"
    terminal_result, anchor_uncommitted = None, False
    if checkpoint_path.exists():
        anchor = json.loads(checkpoint_path.read_bytes())
        if anchor.get("version") != VERSION or anchor.get("request_hash") != identity:
            raise ValueError("full anchor identity mismatch")
        native = anchor["native_checkpoint"]
        if native.get("genesis") != base["genesis"]:
            raise ValueError("full anchor genesis mismatch")
        anchor_journal = native["journal"]
        if anchor_journal != entries[:len(anchor_journal)]:
            raise ValueError("full anchor differs from durable event evidence")
        if journal_type.replay(base["genesis"], anchor_journal).snapshot() != native["snapshot"]:
            raise ValueError("full anchor snapshot mismatch")
        # A periodic/full failure snapshot may be ahead of the last committed
        # day. Compare only shared committed rows, never promote its frontier.
        for name in PROGRESS_TABLES:
            rows = native.get("progress", {}).get(name, [])
            shared = min(len(rows), len(progress[name]))
            if rows[:shared] != progress[name][:shared]:
                raise ValueError("full anchor differs from committed progress")
        anchor_sequence = anchor["sequence"]
        anchor_uncommitted = anchor_sequence > len(logs)
        if not anchor_uncommitted and (anchor_sequence < 1 or logs[anchor_sequence-1]["phase"] != native["phase"]):
            raise ValueError("full anchor has incorrect progress boundary")
        if native["phase"] == "simulation_completed" and not anchor_uncommitted:
            terminal_result = native["result"]
    complete = bool(terminal_result is not None and logs and logs[-1]["phase"] == "simulation_completed" and not log_tail and not day_tail
                    and len(days) == len(boundaries) and committed_events == len(entries))
    if terminal_result is not None:
        if not complete or terminal_result["journal"] != committed_journal or terminal_result["final_snapshot"] != committed_ledger.snapshot():
            raise ValueError("terminal result differs from complete-day commits")
        if any(terminal_result[name] != progress[name] for name in PROGRESS_TABLES):
            raise ValueError("terminal result progress mismatch")
    result_path = folder / "account_result.json"
    if result_path.exists():
        result = json.loads(result_path.read_bytes())
        if (not complete or result.get("version") != VERSION or result.get("request_hash") != identity
                or result.get("native_result_hash") != _hash(result.get("native_result"))
                or result["native_result"] != terminal_result):
            raise ValueError("final result artifact identity/hash mismatch")
    return {"version": VERSION, "request_hash": identity, "genesis": base["genesis"],
        "journal": committed_journal, "snapshot": committed_ledger.snapshot(), "progress": progress,
        "last_complete_day": last, "complete_days": len(days), "current_date": current,
        "unfinished_current_day": current if current != last else None,
        "observed_events": len(entries), "uncommitted_events": len(entries)-committed_events,
        "observed_journal_head": observed_ledger.snapshot()["journal_head"],
        "progress_log_head": log_head, "day_commit_head": day_head,
        "ignored_unterminated_bytes": {"progress": log_tail, "days": day_tail},
        "ignored_temporary_files": sorted(path.name for path in folder.glob("*.tmp")),
        "full_anchor_uncommitted": anchor_uncommitted, "result_artifact_present": result_path.exists(),
        "frontier_hint_stale": hint_stale, "simulation_completed": complete,
        "input_identity_verified": input_verified, "source_manifest_verified_against_current": input_verified,
        "financial_validation_claimed": False}


def source_manifest():
    paths = [Path(__file__).resolve(), Path(__file__).with_name("jobs.py").resolve(),
             Path(__file__).with_name("legacy_tax_cache.py").resolve(),
             ROOT / "src/quanta_agents/meta_v3/windows_job.py"]
    paths += [LEGACY / (name + ".py") for name in
              ("raw_share_ledger", "corporate_action_adapter", "raw_portfolio_backtest")]
    return {str(path.relative_to(ROOT)).replace("\\", "/"):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _integrity_method(module):
    def validate_eod_integrity(self, ledger):
        # A single call observes one detached snapshot, exactly as required by
        # the original caller-serialized-day contract. Never reuse it later.
        entries = ledger.journal
        prefix = f"{self._spec.action_id}:tax-eod-"
        finalized = []
        for entry in entries:
            event = entry["event"]
            if event["kind"] != "tax_assessed" or not event["event_id"].startswith(prefix):
                continue
            tag = event["event_id"][len(prefix):]
            if len(tag) != 75 or tag[10] != "-":
                raise module.AdapterError("malformed EOD finalization identity")
            finalized.append((module._day(tag[:10]), tag[11:]))
        if not finalized:
            return
        trades = [entry["event"] for entry in entries
                  if entry["event"]["kind"] in {"buy_fill", "sell_fill"}
                  and entry["event"]["data"]["symbol"] == self._spec.symbol]
        dates = [module._time(event["effective_at"]).date() for event in trades]
        monotone = all(left <= right for left, right in zip(dates, dates[1:]))
        hashes = {}
        if monotone:
            digest = hashlib.sha256(b"[")
            index = 0
            for closed in sorted({day for day, _ in finalized}):
                while index < len(trades) and dates[index] <= closed:
                    if index:
                        digest.update(b",")
                    digest.update(_serial(trades[index]).encode("utf-8"))
                    index += 1
                terminated = digest.copy()
                terminated.update(b"]")
                hashes[closed] = terminated.hexdigest()
        else:
            # A tampered/nonchronological snapshot must preserve the original
            # list-order/filter semantics, not be silently sorted into validity.
            for closed in {day for day, _ in finalized}:
                hashes[closed] = module._hash([event for event, day in zip(trades, dates) if day <= closed])
        for closed, expected in finalized:
            if hashes[closed] != expected:
                raise module.AdapterError("post-finalization trade changed a closed tax day; explicit rebuild required")
    return validate_eod_integrity


def legacy_modules(*, optimized=True):
    """Private originals for equivalence tests, private optimized worker modules.

    Loading never executes the legacy package __init__ and never alters its
    public modules or V3's frozen-kernel namespace.
    """
    with _LOCK:
        key = bool(optimized)
        if key in _MODULES:
            return _MODULES[key]
        namespace = "_quanta_v6_v18_prefix" if key else "_quanta_v6_v18_original"
        package = types.ModuleType(namespace)
        package.__path__ = [str(LEGACY)]
        package.__package__ = namespace
        sys.modules[namespace] = package
        ledger = importlib.import_module(namespace + ".raw_share_ledger")
        adapter = importlib.import_module(namespace + ".corporate_action_adapter")
        if key:
            adapter.CashDividendAdapter.validate_eod_integrity = _integrity_method(adapter)
            from .legacy_tax_cache import install
            install(adapter)
        portfolio = importlib.import_module(namespace + ".raw_portfolio_backtest")
        if key:
            portfolio._CheckpointState = _checkpoint_state(portfolio)
        loaded = types.SimpleNamespace(ledger=ledger, adapter=adapter, portfolio=portfolio)
        _MODULES[key] = loaded
        return loaded


def simulate_legacy_account(*, optimized=True, **kwargs):
    """In-process worker/test primitive; runtime callers must use the job API."""
    modules = legacy_modules(optimized=optimized)
    actions = kwargs.pop("corporate_actions", ())
    kwargs["corporate_actions"] = [modules.adapter.CashDividendAnnouncement(
        **(asdict(action) if is_dataclass(action) else dict(action))) for action in actions]
    return modules.portfolio.simulate_raw_portfolio(**kwargs)


def run_legacy_account(payload: dict, *, output_dir, timeout_seconds,
                       deadline_epoch=None, closing_seconds=0,
                       max_output_bytes=256 * 1024**2, cancelled=None):
    """Run a new JSON fixture/account under supervision, retaining raw checkpoints.

    payload uses original simulate_raw_portfolio keyword names, except
    daily_data/targets are lists of row dictionaries and corporate_actions are
    announcement dictionaries. No old account, model or saved result is reused.
    completed means process completion only; inspect the native result's scope
    and validity flags before making any scientific claim.
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    request = {"version": VERSION, "payload": payload, "payload_hash": _hash(payload),
               "source_manifest": source_manifest()}
    _write(output_dir / "account_input.json", request, exclusive=True)
    pythonpath = str(ROOT / "src")
    if os.environ.get("PYTHONPATH"):
        pythonpath += os.pathsep + os.environ["PYTHONPATH"]
    result = run_job([sys.executable, "-m", "quanta_agents.meta_v6.legacy_account", "worker", str(output_dir)],
                     cwd=ROOT, output_dir=output_dir, timeout_seconds=timeout_seconds,
                     deadline_epoch=deadline_epoch, closing_seconds=closing_seconds,
                     max_output_bytes=max_output_bytes, cancelled=cancelled,
                     env={"PYTHONPATH": pythonpath, "PYTHONIOENCODING": "utf-8"})
    return result


def _worker(output_dir):
    import pandas as pd
    output_dir = Path(output_dir).resolve()
    request_path = output_dir / "account_input.json"
    request_bytes = request_path.read_bytes()
    request = json.loads(request_bytes)
    sink = None
    try:
        if request["version"] != VERSION or request["payload_hash"] != _hash(request["payload"]):
            raise ValueError("account request identity mismatch")
        if request["source_manifest"] != source_manifest():
            raise ValueError("account source manifest changed")
        payload = dict(request["payload"])
        payload["daily_data"] = pd.DataFrame(payload["daily_data"])
        payload["targets"] = pd.DataFrame(payload["targets"], columns=[
            "symbol", "signal_date", "trade_date", "available_at", "target_weight"])
        sink = _CheckpointSink(output_dir, hashlib.sha256(request_bytes).hexdigest())
        native = simulate_legacy_account(**payload, checkpoint=sink)
        if request_path.read_bytes() != request_bytes or request["source_manifest"] != source_manifest():
            raise ValueError("account inputs or sources changed during execution")
        _write(output_dir / "account_result.json", {
            "version": VERSION, "request_hash": hashlib.sha256(request_bytes).hexdigest(),
            "source_manifest": request["source_manifest"], "native_result": native,
            "native_result_hash": _hash(native)}, exclusive=True)
    except Exception as exc:
        _write(output_dir / "account_error.json", {"type": type(exc).__name__, "message": str(exc),
               "request_hash": hashlib.sha256(request_bytes).hexdigest(),
               "financial_validation_claimed": False}, exclusive=True)
        raise
    finally:
        if sink is not None:
            sink.close()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "worker":
        raise SystemExit("usage: python -m quanta_agents.meta_v6.legacy_account worker OUTPUT_DIR")
    _worker(sys.argv[2])

"""Prepared outer capture for the sealed V4 research driver.

Default: print the dry contract without filesystem access. --execute explicitly
permits the existing driver to read its already frozen scores and write new
research accounts. This wrapper does not fit, predict, select, install, certify
point net returns, or produce an accepted completion receipt. A separate reviewed
v4_actual_evidence inspector must inspect the candidate packet later.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

PINNED = {
    "protocol": "b288c7c9d1b5db777ad2bd3519415309123b1acee9adce55639bcc7b4c6188c9",
    "source_seal": "34c39255a5cd055cf233d594d03935841d6b99ae1f943dbb2e2251eab41fa20c",
    "driver_receipt": "e5998c9dfa8eab4be0b0849f475f8e736581d53ac75f24925c3529c933039996",
}
MODELS = ("fixed_label_1", "fixed_label_2", "fixed_label_3", "fixed_label_4",
          "blo_selected", "selected_full", "pv", "pv_factors", "pv_fund")
SCENARIOS = ("primary", "stress")
BASES = ("pretax", "tax_upper_reserve")
SPLITS = ("test_2025", "recent_2026")
HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 128 * 1024 * 1024
CHILD_CLEANUP_TIMEOUT_SECONDS = 3


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    require(isinstance(value, str) and HASH.fullmatch(value) is not None, "invalid SHA256")
    return value


def hash_file(path):
    answer = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            answer.update(chunk)
    return answer.hexdigest()


def pairs(items):
    answer = {}
    for key, value in items:
        require(key not in answer, "duplicate JSON key: " + key)
        answer[key] = value
    return answer


def load_json(path):
    path = Path(path)
    require(path.stat().st_size <= MAX_JSON_BYTES, "JSON exceeds bounded reader")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def merge_maps(*maps):
    result = {}
    for current in maps:
        require(isinstance(current, dict) and bool(current), "nonempty source map required")
        for path, expected in current.items():
            require(isinstance(path, str) and bool(path), "source path required")
            digest(expected)
            require(path not in result or result[path] == expected, "conflicting source identity: " + path)
            result[path] = expected
    return result


def measure(mapping):
    """Record actual values and failures; never substitute expected hashes."""
    actual, failures = {}, {}
    for path in mapping:
        try:
            actual[path] = hash_file(path)
        except OSError as error:
            actual[path] = None
            failures[path] = type(error).__name__ + ": " + str(error)
    return actual, failures


def ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hash_file(path)}


def write_new(path, payload):
    with Path(path).open("xb") as stream:
        stream.write((json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def settle_started_child(process, state):
    """Bounded cleanup, called while the original stdout/stderr are still open."""
    actions, errors = [], []
    code = None
    try:
        code = process.poll()
    except BaseException as error:
        errors.append({"operation": "poll", "error_type": type(error).__name__, "reason": str(error)})
    if code is None:
        try:
            process.terminate(); actions.append("terminate")
        except BaseException as error:
            errors.append({"operation": "terminate", "error_type": type(error).__name__, "reason": str(error)})
    try:
        code = process.wait(timeout=CHILD_CLEANUP_TIMEOUT_SECONDS)
        actions.append("wait_after_terminate_or_observed_exit")
    except BaseException as error:
        code = None
        errors.append({"operation": "first_wait", "error_type": type(error).__name__, "reason": str(error)})
    if code is None:
        try:
            process.kill(); actions.append("kill")
        except BaseException as error:
            errors.append({"operation": "kill", "error_type": type(error).__name__, "reason": str(error)})
        try:
            code = process.wait(timeout=CHILD_CLEANUP_TIMEOUT_SECONDS)
            actions.append("wait_after_kill")
        except BaseException as error:
            code = None
            errors.append({"operation": "final_wait", "error_type": type(error).__name__, "reason": str(error)})
    require(code is None or type(code) is int, "noninteger child exit observation")
    state.update(exit_code=code, child_exit_unknown=code is None,
        child_cleanup={"process_id": process.pid, "actions": actions, "errors": errors,
                       "wait_timeout_seconds_per_attempt": CHILD_CLEANUP_TIMEOUT_SECONDS,
                       "child_exit_observed": code is not None, "cleanup_completed_at_utc": utc()})
    if code is not None:
        state["finished_at_utc"] = utc()


def run_captured_child(command, stdout, stderr, event, comparison, state):
    """Own a started child through normal exit or bounded failed-path cleanup."""
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
    state.update(driver_process_started=True, process_id=process.pid, child_exit_unknown=True)
    try:
        event("driver_process_started", pid=process.pid)
        while True:
            code = process.poll()
            if state["comparison_first_observed_at_utc"] is None and comparison.is_file():
                state["comparison_first_observed_at_utc"] = utc()
                state["comparison_file_mtime_ns_when_first_observed"] = comparison.stat().st_mtime_ns
                event("comparison_file_first_observed")
            if code is not None:
                require(type(code) is int, "noninteger child exit observation")
                state.update(exit_code=code, finished_at_utc=utc(), child_exit_unknown=False)
                return process
            time.sleep(.25)
    except BaseException:
        # This must finish before leaving the caller's stdout/stderr context.
        settle_started_child(process, state)
        raise


def contract():
    return {
        "schema_version": 1, "status": "prepared_only", "default": "dry_contract_stdout_only",
        "execute_requires_explicit_flag": "--execute",
        "required_execute_arguments": ["--manifest", "--driver", "--protocol", "--source-seal", "--driver-receipt",
            "--calendar", "--tail-calendar", "--capture-directory", "--approved-wrapper-source-sha256"],
        "optional_interpreter": "--python; otherwise the current sys.executable",
        "driver_argv": ["PYTHON", "-B", "-u", "SEALED_DRIVER", "run", "--manifest", "EXISTING_PREPARED_MANIFEST",
                        "--output", "NEW_CAPTURE_DIRECTORY/driver_output"],
        "new_sidecars": ["capture_events.jsonl", "before_hash_receipt.json", "stdout.log", "stderr.log",
                         "process_exit_receipt.json", "after_hash_receipt.json", "wrapper_receipt.json",
                         "candidate_actual_v4_packet.json"],
        "source_map": "actual whole-run merged immutable/minute/driver/frozen inputs + four original metadata path hashes",
        "before_after_measured_only_on_execute": True, "driver_imported_by_dry_contract": False,
        "existing_outputs_reused_or_overwritten": False, "automatic_retry": False,
        "per_run_phase_map_directly_observed": False,
        "comparison_timing": "first existence observed by wrapper; not asserted exact file-creation instant",
        "started_child_failure_lifecycle": "bounded terminate/wait then kill/wait; explicit unknown if exit cannot be observed",
        "packet_is_completion_receipt": False, "point_net_excess_return": None,
        "economic_target_accepted": False, "all_PLAN_acceptance_complete": False,
        "legacy_manifest_generated": False,
    }


def packet_for(paths, manifest, protocol, output, wrapper_ref, stdout_ref):
    """Future output references only; do not alter any original financial values."""
    comparison_path = output / "comparison.json"
    comparison = load_json(comparison_path)
    require(comparison["models"] == list(MODELS) and comparison["freeze_id"] == manifest["freeze_id"],
            "comparison model/freeze identity mismatch")
    require(comparison["input_manifest_sha256"] == hash_file(paths["input_manifest"]), "comparison manifest changed")
    require(comparison.get("economic_target_accepted") is False and comparison.get("all_PLAN_acceptance_complete") is False,
            "comparison completion claim rejected")
    run_root = Path(manifest["run_directory"])
    freeze_path = run_root / "freeze.json"
    exports_path = run_root / "corrected_score_exports/manifest.json"
    frozen, export = load_json(freeze_path), load_json(exports_path)
    require(frozen["freeze_id"] == export["freeze_id"] == manifest["freeze_id"], "freeze/export identity mismatch")
    require(set(export["reports"]) == {s + "/" + m for s in SPLITS for m in MODELS}, "all18 preservation reports required")
    artifacts = {name: ref(path) for name, path in paths.items()}
    artifacts.update(freeze=ref(freeze_path), comparison=ref(comparison_path),
        benchmark=ref(protocol["official_benchmark_csv"]), score_export_manifest=ref(exports_path),
        stdout=stdout_ref, wrapper_receipt=wrapper_ref)
    require(artifacts["freeze"]["sha256"] == manifest["freeze_sha256"], "actual freeze bytes differ")
    score_receipts, csvs = {}, {}
    for model in MODELS:
        lineages = manifest["score_exports"][model]
        require(len(lineages) == 2 and {row["split"] for row in lineages} == set(SPLITS), "two original splits required")
        for row in lineages:
            key = row["split"] + "/" + model
            raw, corrected = ref(row["raw_path"]), ref(row["csv_path"])
            require(raw["sha256"] == row["raw_sha256"] and corrected["sha256"] == row["csv_sha256"], "score lineage bytes changed")
            report_path = Path(row["csv_path"]).with_suffix(".receipt.json")
            report = load_json(report_path)
            require({k: v for k, v in report.items() if k != "sections"} == export["reports"][key], "preservation receipt differs")
            csvs[key] = {"raw": raw, "corrected": corrected}
            score_receipts[key] = ref(report_path)
    runs, coverage = [], {}
    records = comparison["artifacts"]
    require(set(records) == set(MODELS), "nine account models required")
    for model in MODELS:
        coverage[model] = ref(output / (model + "_score_coverage.json"))
        require(set(records[model]) == set(SCENARIOS), "two scenarios required")
        for scenario in SCENARIOS:
            require(set(records[model][scenario]) == set(BASES), "two account bases required")
            for basis in BASES:
                destination = output / model / scenario / basis
                row = records[model][scenario][basis]
                replay, summary = ref(destination / "replay.json"), ref(destination / "summary.json")
                require(row["replay_path"] == replay["path"] and row["replay_sha256"] == replay["sha256"]
                    and row["summary_sha256"] == summary["sha256"], "actual36 account output bytes differ")
                runs.append({"model": model, "scenario": scenario, "basis": basis, "replay": replay, "summary": summary})
    return {"schema_version": 1, "evidence_kind": "ACTUAL_V4_RUN_PACKET", "artifacts": artifacts,
            "score_export_receipts": score_receipts, "score_csvs": csvs, "model_score_coverage": coverage,
            "runs": runs, "candidate_pending_independent_inspection": True,
            "point_net_excess_return": None, "all_PLAN_acceptance_complete": False}


def execute(args):
    required_args = ("manifest", "driver", "protocol", "source_seal", "driver_receipt", "calendar",
                     "tail_calendar", "capture_directory", "approved_wrapper_source_sha256")
    require(all(getattr(args, name) is not None for name in required_args), "all explicit execute paths/source approval required")
    own = Path(__file__).resolve()
    approved = digest(args.approved_wrapper_source_sha256)
    require(hash_file(own) == approved, "wrapper source differs from out-of-band approval")
    capture = Path(args.capture_directory).resolve()
    require(capture.parent.is_dir(), "capture parent must already exist")
    # Exclusive new directory prevents retries from overwriting any prior attempt.
    capture.mkdir(exist_ok=False)
    events_path = capture / "capture_events.jsonl"
    def event(kind, **data):
        with events_path.open("ab") as stream:
            stream.write((json.dumps({"event": kind, "observed_at_utc": utc(), **data}, allow_nan=False) + "\n").encode())
            stream.flush()
    event("capture_created", approved_wrapper_source_sha256=approved)
    paths = {name: Path(getattr(args, "manifest" if name == "input_manifest" else name)).resolve()
             for name in ("protocol", "source_seal", "driver_receipt", "input_manifest", "calendar", "tail_calendar")}
    output = capture / "driver_output"
    state = {"schema_version": 1, "evidence_kind": "ACTUAL_READONLY_V4_EXECUTION_CAPTURE",
        "wrapper_source_sha256": approved, "synthetic": False, "actual_hashes_measured": False,
        "stdout_captured_verbatim": False, "exit_code": None, "driver_process_started": False,
        "child_exit_unknown": False,
        "comparison_sha256": None, "comparison_first_observed_at_utc": None,
        "per_run_phase_map_directly_observed": False, "point_net_excess_return": None,
        "economic_target_accepted": False, "all_PLAN_acceptance_complete": False,
        "packet_is_completion_receipt": False, "legacy_manifest_generated": False}
    return_code, expected = 1, None
    try:
        for name, wanted in PINNED.items():
            require(hash_file(paths[name]) == wanted, "unreviewed V4 metadata: " + name)
        protocol, seal, driver, manifest = (load_json(paths[k]) for k in ("protocol", "source_seal", "driver_receipt", "input_manifest"))
        require(driver["comparison_protocol_sha256"] == PINNED["protocol"] and driver["source_seal_sha256"] == PINNED["source_seal"], "driver metadata binding differs")
        for field, name in (("comparison_protocol_sha256", "protocol"), ("source_seal_sha256", "source_seal"), ("driver_receipt_sha256", "driver_receipt")):
            require(manifest[field] == PINNED[name], "prepared manifest metadata mismatch")
        executable_driver = Path(args.driver).resolve()
        require(executable_driver.name == "trading_comparison_v4.py" and str(executable_driver) in driver["driver_source_hashes"], "exact sealed V4 driver required")
        require(hash_file(executable_driver) == driver["driver_source_hashes"][str(executable_driver)], "driver bytes changed")
        expected = merge_maps(seal["immutable_files"], seal["minute_raw_day_files"], driver["driver_source_hashes"],
            manifest["frozen_files"], {str(paths[k]): hash_file(paths[k]) for k in ("protocol", "source_seal", "driver_receipt", "input_manifest")})
        for name in ("calendar", "tail_calendar"):
            require(expected.get(str(paths[name])) == hash_file(paths[name]), "calendar absent from original sealed map")
        before, failures = measure(expected)
        state.update(before_hashes=before, before_captured_at_utc=utc())
        write_new(capture / "before_hash_receipt.json", {"expected_hashes": expected, "actual_hashes": before,
            "measurement_failures": failures, "completed_at_utc": state["before_captured_at_utc"]})
        event("before_measurement_completed", entries=len(before), failures=len(failures))
        require(not failures and before == expected, "before execution source map is missing/changed")
        require(hash_file(own) == approved, "wrapper bytes changed before invocation")
        python = str(Path(args.python).resolve()) if args.python else sys.executable
        command = [python, "-B", "-u", str(executable_driver), "run", "--manifest", str(paths["input_manifest"]), "--output", str(output)]
        state.update(command=command, started_at_utc=utc())
        event("driver_invocation_requested", command=command)
        # Direct argument vector, no shell, pipes, decoding, tee, retries or installs.
        with (capture / "stdout.log").open("xb") as stdout, (capture / "stderr.log").open("xb") as stderr:
            comparison = output / "comparison.json"
            process = run_captured_child(command, stdout, stderr, event, comparison, state)
        state["stdout_captured_verbatim"] = True
        state["stdout_sha256"] = hash_file(capture / "stdout.log")
        state["stderr_sha256"] = hash_file(capture / "stderr.log")
        write_new(capture / "process_exit_receipt.json", {"exit_code": state["exit_code"], "exit_observed_at_utc": state["finished_at_utc"],
            "process_id": process.pid, "stdout": ref(capture / "stdout.log"), "stderr": ref(capture / "stderr.log")})
        event("driver_exit_observed", exit_code=state["exit_code"])
        after, failures = measure(expected)
        state.update(after_hashes=after, after_captured_at_utc=utc(), actual_hashes_measured=not failures)
        write_new(capture / "after_hash_receipt.json", {"expected_hashes": expected, "actual_hashes": after,
            "measurement_failures": failures, "completed_at_utc": state["after_captured_at_utc"]})
        event("after_measurement_completed", entries=len(after), failures=len(failures))
        if comparison.is_file():
            state["comparison_sha256"] = hash_file(comparison)
        require(state["exit_code"] == 0, "actual driver exit was nonzero")
        require(not failures and after == before == expected, "after execution source map changed/missing")
        require(hash_file(own) == approved, "wrapper bytes changed after invocation")
        require(state["comparison_sha256"] is not None, "comparison output missing")
        state["status"] = "ACTUAL_DRIVER_CAPTURE_PENDING_INDEPENDENT_INSPECTION"
        # No economic/complete decision here. Even genuine INCOMPLETE run journals
        # remain original and can be classified by the independent inspector.
        wrapper_path = capture / "wrapper_receipt.json"
        write_new(wrapper_path, state)
        packet = packet_for(paths, manifest, protocol, output, ref(wrapper_path), ref(capture / "stdout.log"))
        event("candidate_packet_ready_for_publication_not_accepted")
        write_new(capture / "candidate_actual_v4_packet.json", packet)
        return_code = 0
    except (Exception, KeyboardInterrupt) as error:
        publication_errors = []
        # The started-child helper has already attempted bounded cleanup with
        # raw handles open. Observe available after maps even on a failed path.
        if expected is not None and "after_hashes" not in state:
            try:
                after, failures = measure(expected)
                state.update(after_hashes=after, after_captured_at_utc=utc(),
                    actual_hashes_measured=not failures,
                    after_map_artifacts_finalized=not state["child_exit_unknown"])
                write_new(capture / "after_hash_receipt.json", {"expected_hashes": expected,
                    "actual_hashes": after, "measurement_failures": failures,
                    "completed_at_utc": state["after_captured_at_utc"],
                    "child_exit_unknown": state["child_exit_unknown"],
                    "artifacts_may_still_change": state["child_exit_unknown"]})
            except (Exception, KeyboardInterrupt) as observation_error:
                state["actual_hashes_measured"] = False
                state["after_measurement_unknown"] = {"error_type": type(observation_error).__name__, "reason": str(observation_error)}
        for name in ("stdout", "stderr"):
            try:
                log_path = capture / (name + ".log")
                if log_path.is_file():
                    state[name + "_sha256"] = hash_file(log_path)
            except (Exception, KeyboardInterrupt) as observation_error:
                state[name + "_hash_unknown"] = type(observation_error).__name__ + ": " + str(observation_error)
        state["stdout_captured_verbatim"] = state["driver_process_started"]
        state["captured_log_hashes_are_final"] = state["driver_process_started"] and not state["child_exit_unknown"]
        if state["driver_process_started"] and not (capture / "process_exit_receipt.json").exists():
            try:
                write_new(capture / "process_exit_receipt.json", {"exit_code": state["exit_code"],
                    "exit_observed_at_utc": state.get("finished_at_utc"), "process_id": state["process_id"],
                    "child_exit_unknown": state["child_exit_unknown"], "cleanup": state.get("child_cleanup"),
                    "stdout_sha256": state.get("stdout_sha256"), "stderr_sha256": state.get("stderr_sha256"),
                    "captured_log_hashes_are_final": state["captured_log_hashes_are_final"]})
            except (Exception, KeyboardInterrupt) as publication_error:
                publication_errors.append({"artifact": "process_exit_receipt.json", "reason": str(publication_error)})
        failure = {"status": "CAPTURE_OR_PACKET_FAILED_OR_REJECTED", "error_type": type(error).__name__, "reason": str(error),
                   "observed_at_utc": utc(), "all_original_attempt_bytes_retained": True,
                   "automatic_retry_performed": False, "accepted_completion_receipt_written": False,
                   "child_exit_unknown": state["child_exit_unknown"], "process_id": state.get("process_id"),
                   "artifacts_finalized": not state["child_exit_unknown"]}
        try:
            event("capture_failed", **failure)
        except (Exception, KeyboardInterrupt) as publication_error:
            publication_errors.append({"artifact": "capture_events.jsonl", "reason": str(publication_error)})
        failure["failure_publication_errors"] = publication_errors
        # Do not replace a successful-exit capture if candidate construction fails.
        for filename in ("wrapper_receipt.json", "capture_failure_receipt.json"):
            try:
                if filename == "wrapper_receipt.json" and (capture / filename).exists():
                    continue
                write_new(capture / filename, {**state, **failure} if filename == "wrapper_receipt.json" else failure)
            except (Exception, KeyboardInterrupt) as publication_error:
                publication_errors.append({"artifact": filename, "reason": str(publication_error)})
        state["failure_publication_errors"] = publication_errors
    print(json.dumps({"capture_directory": str(capture), "exit_code": state["exit_code"],
        "child_exit_unknown": state["child_exit_unknown"], "process_id": state.get("process_id"),
        "failure_publication_errors": state.get("failure_publication_errors", []),
        "candidate_packet_created": return_code == 0, "pending_independent_inspection": True,
        "point_net_excess_return": None, "all_PLAN_acceptance_complete": False}, indent=2))
    return return_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    for name in ("manifest", "driver", "protocol", "source-seal", "driver-receipt", "calendar", "tail-calendar", "capture-directory", "python"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--approved-wrapper-source-sha256")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(contract(), ensure_ascii=False, indent=2))
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())

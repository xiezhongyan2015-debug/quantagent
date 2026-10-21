"""Capture one exact229-native/35-checkpoint identity collection and bound its owned child group.

Only a newly created child session may be terminated. This never contacts the
GPU job/process groups, reuses an attempt, removes outputs, or retries the collection.
Default invocation still requires explicit pinned launch arguments; preparation
contracts are disabled. Exit0/receipt presence do not certify native completeness or checkpoint finite PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def read_small(path, expected):
    p = Path(path)
    require(p.stat().st_size <= 4 * 1024**2, "closed capture input exceeds budget")
    b = p.read_bytes()
    require(hashlib.sha256(b).hexdigest() == expected, "capture input SHA differs")
    return b


def write_json(path, value):
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporary, path)


def group_present(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False


def terminate_owned(proc, group):
    require(group == proc.pid and group != os.getpgrp(), "refuse to signal a non-owned process group")
    if group_present(group):
        os.killpg(group, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if group_present(group):
        os.killpg(group, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return {"parent_reaped": False, "remaining_group_state": "UNKNOWN_AFTER_SIGKILL"}
    return {"parent_reaped": True, "remaining_group_state": "PRESENT_OR_ZOMBIE" if group_present(group) else "ABSENT"}


def execute(args):
    contract = json.loads(read_small(args.launch_contract, args.launch_contract_sha256))
    require(contract["schema_version"] == "NATIVE35_OWNED_CAPTURE_LAUNCH_V1", "wrong launch schema")
    require(contract["execution_allowed"] is True, "prepared launch cannot execute")
    require(contract["fixed_scope"] == {"native_small_files": 229, "checkpoints": 35,
                                       "model_forward_calls": 0, "additional_fits": 0},
            "exact229 native/35 checkpoint collection scope differs")
    read_small(Path(__file__).resolve(), contract["capture_source_sha256"])
    collector = Path(contract["collector"]["path"])
    read_small(collector, contract["collector"]["sha256"])
    collection = json.loads(read_small(contract["collection_contract"]["path"],
                                  contract["collection_contract"]["sha256"]))
    require(collection["execution_allowed"] is True, "closed root actual nativecollection execution contract required")
    require(collection["source"]["sha256"] == contract["collector"]["sha256"] and collection["source"]["path"] == str(collector), "collector bindings differ")
    require(contract["capture_directory"] == str(args.capture_directory.resolve()), "capture namespace differs")
    require(type(contract["child_hard_deadline_seconds"]) is int and contract["child_hard_deadline_seconds"] == 660, "hard deadline must be the fixed660 seconds")
    interpreter = Path(contract["python_runtime"])
    require(interpreter.is_absolute() and interpreter.is_file(), "explicit existing runtime required")
    output = Path(collection["exact_new_output_directory"])
    require(str(output) == contract["expected_collection_output_directory"], "fixed collection output namespace differs")
    require(output.is_absolute() and not output.exists(), "collection output absent absolute namespace required; no retry or overwrite")
    require(args.capture_directory.is_absolute(), "absolute capture namespace required")
    args.capture_directory.mkdir(parents=True, exist_ok=False)
    require(collection["schema_version"] == "PREPARED_R2_NATIVE35_CAPTURE_V1",
            "wrong exact native35 collection contract schema")
    argv = [str(interpreter), str(collector), "--execute", "--contract", contract["collection_contract"]["path"],
            "--contract-sha256", contract["collection_contract"]["sha256"],
            "--source-sha256", contract["collector"]["sha256"], "--output", str(output)]
    require(collection["required_owned_capture"] == {
        "schema_version": "NATIVE35_OWNED_CAPTURE_LAUNCH_V1",
        "capture_source_sha256": contract["capture_source_sha256"],
        "child_hard_deadline_seconds": 660}, "collector owned workflow binding differs")
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["OMP_NUM_THREADS"] = "1"
    env["GRU_NATIVE35_CAPTURE_SOURCE_SHA256"] = contract["capture_source_sha256"]
    env["GRU_NATIVE35_CAPTURE_PARENT_PID"] = str(os.getpid())
    started = time.monotonic()
    capture = {"status": "STARTING_ONCE", "started_utc": now(), "PID": os.getpid(), "argv": argv,
               "launch_contract_sha256": args.launch_contract_sha256, "automatic_retry": False,
               "GPU_server_or_existing_process_groups_touched": False, "full_PLAN_complete": False,
               "fixed_scope": contract["fixed_scope"], "collector_source_sha256": contract["collector"]["sha256"],
               "collection_contract_sha256": contract["collection_contract"]["sha256"],
               "checkpoint_finite_PASS_inferred": False, "forward_or_training": False}
    receipt_path = args.capture_directory / "root_once_execution_capture_v1.json"
    write_json(receipt_path, capture)
    proc, group, failure = None, None, None
    try:
        with (args.capture_directory / "stdout.log").open("xb") as stdout:
            with (args.capture_directory / "stderr.log").open("xb") as stderr:
                proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env, start_new_session=True)
                # start_new_session establishes ownership at successful spawn.
                # Retain that identity before any fallible observation call.
                group = proc.pid
                observed_group = os.getpgid(proc.pid)
                require(observed_group == group, "child session not independently owned")
                capture.update({"status": "RUNNING", "child_PID": proc.pid, "owned_PGID": group,
                                "child_hard_deadline_seconds": contract["child_hard_deadline_seconds"]})
                write_json(receipt_path, capture)
                remaining = max(0.01, contract["child_hard_deadline_seconds"] - (time.monotonic() - started))
                capture["child_exit_code"] = proc.wait(timeout=remaining)
                capture["status"] = "CHILD_EXITED"
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
        capture["status"] = "FAILED_OR_HARD_DEADLINE_WITH_OWNED_CHILD_CLEANUP"
        if proc is not None and group == proc.pid:
            try:
                capture["owned_group_cleanup"] = terminate_owned(proc, group)
                capture["child_exit_code"] = proc.poll()
            except BaseException as cleanup_exc:
                capture["owned_group_cleanup"] = {
                    "cleanup_failure": {"type": type(cleanup_exc).__name__, "message": str(cleanup_exc)},
                    "remaining_group_state": "UNKNOWN_CLEANUP_EXCEPTION"}
                capture["child_exit_code"] = proc.poll()
    finally:
        capture["finished_utc"] = now()
        capture["elapsed_seconds"] = time.monotonic() - started
        capture["failure"] = failure
        capture["logs"] = {}
        for name in ("stdout.log", "stderr.log"):
            path = args.capture_directory / name
            if path.exists():
                size = path.stat().st_size
                item = {"path": str(path), "bytes": size}
                if size <= 4 * 1024**2:
                    item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                else:
                    item["sha256"] = "NOT_READ_LOG_BUDGET_EXCEEDED"
                capture["logs"][name] = item
        capture["child_main_receipt_present"] = (output / "actual_r2_native35_capture_receipt_v1.json").is_file()
        capture["child_main_receipt_success_is_not_inferred_from_exit_or_presence"] = True
        capture["deadline_scope"] = "660 seconds to owned child wait; bounded TERM10s/KILL5s cleanup and local receipt/log sealing may extend capture elapsed"
        capture["checkpoint_body_reads_by_wrapper"] = 0
        capture["other_existing_process_groups_signalled"] = False
        write_json(receipt_path, capture)
    print(json.dumps({"status": capture["status"], "child_exit_code": capture.get("child_exit_code"),
                      "receipt": str(receipt_path)}), flush=True)
    return 1 if failure or capture.get("child_exit_code") != 0 else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-contract", type=Path, required=True)
    parser.add_argument("--launch-contract-sha256", required=True)
    parser.add_argument("--capture-directory", type=Path, required=True)
    raise SystemExit(execute(parser.parse_args()))


if __name__ == "__main__":
    main()

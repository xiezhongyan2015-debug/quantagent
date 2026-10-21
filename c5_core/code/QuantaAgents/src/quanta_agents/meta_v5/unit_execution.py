"""Optional separately funded stock diagnostics; no allocation of portfolio PnL.

The original full-universe signal and target weights are preserved. Each stock
receives its own continuous cash account; all other target weights become zero.
These diagnostic capitals are not an additive deployed portfolio. Raw ledgers,
failures and bounded worker attempts remain durable and are never rerun.
"""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from quanta_agents.meta_v3.ledger import digest, need, serial
from quanta_agents.meta_v3.research_tools import save_once
from .registry import file_hash

VERSION = "v5_independent_stock_accounts_v1"
KEY = "unit_execution"
FIELDS = {"version", "codes", "initial_cash_per_account", "weight_policy",
          "max_account_runs_total", "max_wall_seconds_per_profile",
          "max_wall_seconds_total", "max_retained_bytes"}


def validate_policy(policy, case=None):
    need(type(policy) is dict and set(policy) == FIELDS, "exact V5 stock diagnostic contract required")
    need(policy["version"] == VERSION and policy["weight_policy"] == "preserve_original_weights_zero_other_targets",
         "unknown V5 stock diagnostic accounting policy")
    codes = policy["codes"]
    need(type(codes) is list and 2 <= len(codes) <= 16 and len(set(codes)) == len(codes), "fixed multi-stock diagnostic universe required")
    need(all(type(c) is str and len(c) == 8 and c[:2] in ("sh", "sz") and c[2:].isdigit() for c in codes), "diagnostic symbol identity")
    cash = policy["initial_cash_per_account"]
    need(type(cash) is str and Decimal(cash).is_finite() and Decimal(cash) > 0 and Decimal(cash) % Decimal('.01') == 0,
         "diagnostic capital must be explicit positive cents")
    for name, maximum in (("max_account_runs_total", 128), ("max_wall_seconds_per_profile", 3600),
                          ("max_wall_seconds_total", 86400), ("max_retained_bytes", 1024**3)):
        need(type(policy[name]) is int and 0 < policy[name] <= maximum, "bounded diagnostic resource: " + name)
    need(policy["max_wall_seconds_per_profile"] <= policy["max_wall_seconds_total"], "diagnostic wall bounds differ")
    if case is not None:
        need(case["research_class"] == "real_saved_development" and case.get("execution_backend") in (None, "v3_streamed_001", "v3_structural_001"),
             "stock diagnostics require admitted daily raw execution")
        need(codes == case["decision_fixture"]["codes"], "diagnostics must retain every frozen stock in original order")
        need(Decimal(cash) == Decimal(case["initial_cash"]), "diagnostic full capital must equal the frozen main-account capital")
    return policy


def public_contract(policy):
    return {**policy, "capital_semantics": "Each stock has a separate full initial-cash diagnostic account. Accounts are not pooled or additive deployed capital.",
            "signal_semantics": "Compute exact original program on the complete original universe, then preserve the selected stock target and zero every other target. No weight rescaling, annual reset or outcome selection.",
            "benchmark_reuse": "The same frozen benchmark program/case/policy reuses its original stock ledgers without another execution.",
            "scope": "Development diagnostics under the original declared mechanics; not execution certification."}


def build_targets(case, program):
    from quanta_agents.meta_v3 import real_program
    from quanta_agents.meta_v3.target_schedule import apply_schedule
    compiled = real_program.validate_program(program, public_field_contract=case["decision_fixture"]["fields"])
    targets = real_program.build_targets(compiled, decision_fixture=case["decision_fixture"], frozen_policy=real_program.POLICY)
    return compiled, apply_schedule(targets, case=case)


def mask_targets(targets, code):
    result = deepcopy(targets["targets"])
    need(any(r["symbol"] == code for r in result), "missing diagnostic stock in full targets")
    for row in result:
        if row["symbol"] != code:
            row["target_weight"] = "0"
    return result


def _backend(case):
    from quanta_agents.meta_v3.kernel import module
    if case.get("execution_backend") == "v3_structural_001":
        from quanta_agents.meta_v3 import structural_execution
        return structural_execution
    if case.get("execution_backend") == "v3_streamed_001":
        from quanta_agents.meta_v3 import saved_execution
        return saved_execution
    return module("raw_saved_research")


def _worker(folder):
    """One native child process, one immutable saved raw account, zero inference."""
    from quanta_agents.meta_v3.runtime import verify_case_sources, source_pins
    from quanta_agents.meta_v3.archive_codec import case_policy
    folder = Path(folder)
    request = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    need(request["unit_source_sha256"] == file_hash(Path(__file__)), "V5 stock adapter changed before worker dispatch")
    case, program = request["case"], request["program"]
    validate_policy(request["unit_policy"], case)
    need(request["policy_hash"] == digest(request["unit_policy"]), "worker diagnostic policy binding drift")
    need(request["source_pins"] == source_pins(), "V4 sources changed before stock execution")
    verify_case_sources({"case": case, "case_hash": request["case_hash"]})
    start, cpu = time.perf_counter(), time.process_time()
    _, original_targets = build_targets(case, program)
    need(digest(original_targets) == request["original_target_hash"] and
         mask_targets(original_targets, request["code"]) == request["masked_targets"],
         "worker targets must be exact mask of original full-universe program")
    raw = _backend(case)
    storage = case_policy(case)
    storage_args = {"archive_storage_policy": storage} if storage is not None else {}
    plan = raw.freeze_saved_research_plan(identity={"run_id": request["run_id"], "architecture": VERSION,
        "observation_id": request["code"], "strategy_hash": digest(program), "data_hash": digest(case),
        "source_hash": digest(raw.engine_sources(**storage_args))},
        codes=case["decision_fixture"]["codes"], calendar=case["decision_fixture"]["calendar"],
        targets=request["masked_targets"], initial_cash=case["initial_cash"], fixture_only=False,
        **case["raw_source_bindings"], **storage_args)
    job = raw.SavedRawResearch.create(folder / "raw_account", plan)
    value = job.execute_saved(expected_plan_sha256=plan["plan_sha256"])
    need(value["status"] in ("completed_mechanical", "failed"), "stock raw execution unresolved")
    artifact = {"program": program, "raw": value, "workbench": {"status": "completed" if value["status"] == "completed_mechanical" else "failed"},
                "diagnostic": {"code": request["code"], "original_target_hash": request["original_target_hash"],
                               "masked_target_hash": digest(request["masked_targets"]), "full_universe": case["decision_fixture"]["codes"],
                               "independent_initial_cash": case["initial_cash"], "weight_rescaled": False}}
    save_once(folder / "worker_result.json", {"artifact": artifact, "input_hash": digest(request),
        "wall_seconds": time.perf_counter() - start, "cpu_seconds": time.process_time() - cpu})


def _bytes(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def _usage(root):
    attempts = list(root.glob("*/*/attempt.json"))
    measurements = []
    unknown = []
    for path in attempts:
        receipt = path.parent / "measurement.json"
        if receipt.is_file():
            measurements.append(json.loads(receipt.read_text(encoding="utf-8")))
        else:
            unknown.append(str(path))
    preparations = [json.loads(p.read_text(encoding="utf-8")) for p in root.glob("*/preparation_measurement.json")]
    return {"account_runs": len(attempts), "wall_seconds": sum(r["wall_seconds"] for r in measurements + preparations),
            "cpu_seconds": sum(r["worker_cpu_seconds"] for r in measurements if r["worker_cpu_seconds"] is not None) + sum(r["controller_cpu_seconds"] for r in preparations),
            "cpu_complete": not unknown and all(r["worker_cpu_seconds"] is not None for r in measurements),
            "cpu_measurement_scope": "Measured preparation CPU plus worker target-generation/execution CPU; interpreter/import startup CPU is not included.",
            "retained_bytes": _bytes(root), "unknown_attempts": unknown}


def _launch(folder, cache, *, seconds, output_bound):
    start = time.perf_counter()
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    command = [sys.executable, "-m", "quanta_agents.meta_v5.unit_execution", "worker", str(folder)]
    stopped = None
    with (folder / "stdout.log").open("xb") as out, (folder / "stderr.log").open("xb") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, env=env, **options)
        while process.poll() is None:
            if time.perf_counter() - start >= seconds:
                stopped = "wall_budget_exhausted"
            elif _bytes(cache) > output_bound:
                stopped = "output_stop_threshold_exceeded"
            if stopped:
                process.kill()
                break
            time.sleep(.1)
        returncode = process.wait()
    result_path = folder / "worker_result.json"
    worker = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else None
    measurement = {"wall_seconds": time.perf_counter() - start,
        "worker_cpu_seconds": worker["cpu_seconds"] if worker else None, "process_exit_code": returncode,
        "stop_reason": stopped, "retained_bytes": _bytes(folder), "output_bound_is_polling_stop_threshold": True,
        "model_calls": 0, "provider_currency_cost": None}
    save_once(folder / "measurement.json", measurement)
    return worker, measurement


def run_accounts(stage_root, task_id, case, original, policy, *, deadline_epoch=None):
    """Materialize/reuse every frozen unit, with missing accounts explicit.

    Cache attempts have a single-writer runtime lease. A recorded attempt without
    a complete measurement cannot be reexecuted or permit further unit dispatch.
    """
    validate_policy(policy, case)
    from quanta_agents.meta_v3.runtime import source_pins
    cache = Path(stage_root) / "v5_unit_executions" / task_id
    cache.mkdir(parents=True, exist_ok=True)
    identity = {"version": VERSION, "case_hash": digest(case), "program_hash": digest(original["program"]),
                "policy_hash": digest(policy), "original_artifact_hash": digest(original)}
    folder = cache / digest(identity)
    binding = folder / "identity.json"
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        need(manifest["identity"] == identity, "diagnostic manifest identity drift")
        for relative, expected in manifest["files"].items():
            need(file_hash(folder / relative) == expected, "saved stock diagnostic source changed")
    if binding.exists():
        need(json.loads(binding.read_text(encoding="utf-8")) == identity, "diagnostic cache identity drift")
    else:
        folder.mkdir(exist_ok=False)
        save_once(binding, identity)
    compiled_path, target_path, compile_failure = folder / "compiled.json", folder / "targets.json", folder / "compile_failure.json"
    if not target_path.exists() and not compile_failure.exists():
        prep_start, prep_cpu = time.perf_counter(), time.process_time()
        try:
            compiled, targets = build_targets(case, original["program"])
        except ValueError as exc:
            save_once(compile_failure, {"type": type(exc).__name__, "message": str(exc), "account_observed": False})
        else:
            save_once(compiled_path, compiled)
            save_once(target_path, targets)
        save_once(folder / "preparation_measurement.json", {"wall_seconds": time.perf_counter() - prep_start,
            "controller_cpu_seconds": time.process_time() - prep_cpu, "model_calls": 0})
    targets = json.loads(target_path.read_text(encoding="utf-8")) if target_path.exists() else None
    profile_start = time.perf_counter()
    results, sources = [], {str(binding.resolve()): file_hash(binding)}
    for code in policy["codes"]:
        account = folder / code
        result_path = account / "result.json"
        reused = result_path.is_file()
        if account.exists() and not reused and not manifest_path.exists():
            # A parent crash after measurement must reconcile the native saved
            # account, never dispatch a second worker or manufacture a bill.
            measurement_path = account / "measurement.json"
            if not measurement_path.is_file():
                results.append({"code": code, "artifact": None, "status": "unknown",
                                "reason": "interrupted_attempt_measurement_unknown", "reused": True})
                continue
            measurement = json.loads(measurement_path.read_text(encoding="utf-8"))
            request = json.loads((account / "input.json").read_text(encoding="utf-8"))
            need(all(request[k] == v for k, v in identity.items()) and request["case"] == case and
                 request["program"] == original["program"] and request["code"] == code and targets is not None and
                 request["original_target_hash"] == digest(targets) and request["masked_targets"] == mask_targets(targets, code),
                 "saved interrupted diagnostic request changed")
            worker_path = account / "worker_result.json"
            worker = json.loads(worker_path.read_text(encoding="utf-8")) if worker_path.is_file() else None
            if worker is not None:
                need(worker["input_hash"] == digest(request), "saved stock worker input binding drift")
                raw_plan = json.loads((account / "raw_account" / "plan.json").read_text(encoding="utf-8"))
                reconciled = _backend(case).SavedRawResearch(account / "raw_account").reconcile_saved_only(expected_plan_sha256=raw_plan["plan_sha256"])
                need(digest(reconciled) == digest(worker["artifact"]["raw"]), "saved worker differs from native raw ledger")
            save_once(result_path, {"code": code, "artifact": worker["artifact"] if worker and measurement["process_exit_code"] == 0 else None,
                "status": worker["artifact"]["raw"]["status"] if worker and measurement["process_exit_code"] == 0 else "unknown",
                "reason": measurement["stop_reason"], "measurement_hash": digest(measurement)})
            reused = True
        if manifest_path.exists() and not reused:
            frozen = next(r for r in manifest["outcomes"] if r["code"] == code)
            results.append({**frozen, "artifact": None, "reused": True})
            continue
        if not reused:
            usage = _usage(cache)
            room = min(policy["max_wall_seconds_per_profile"] - (time.perf_counter() - profile_start),
                       policy["max_wall_seconds_total"] - usage["wall_seconds"],
                       float("inf") if deadline_epoch is None else deadline_epoch - time.time())
            reason = ("compile_failed" if targets is None else "unresolved_prior_attempt" if usage["unknown_attempts"] else
                      "account_run_budget_exhausted" if usage["account_runs"] >= policy["max_account_runs_total"] else
                      "wall_budget_exhausted" if room <= 0 else
                      "output_stop_threshold_exceeded" if usage["retained_bytes"] >= policy["max_retained_bytes"] else None)
            if reason:
                results.append({"code": code, "artifact": None, "status": "not_executed", "reason": reason, "reused": False})
                continue
            account.mkdir(exist_ok=False)
            request = {**identity, "run_id": Path(stage_root).name, "code": code, "case": case,
                "program": original["program"], "source_pins": source_pins(),
                "unit_policy": policy,
                "unit_source_sha256": file_hash(Path(__file__)),
                "original_target_hash": digest(targets), "masked_targets": mask_targets(targets, code)}
            save_once(account / "input.json", request)
            save_once(account / "attempt.json", {"input_hash": digest(request), "started_epoch": time.time(),
                "reserved_account_runs": 1, "wall_seconds_admitted": room, "no_reexecution": True})
            worker, measurement = _launch(account, cache, seconds=room, output_bound=policy["max_retained_bytes"])
            need(worker is None or worker["input_hash"] == digest(request), "stock worker response binding drift")
            result = {"code": code, "artifact": worker["artifact"] if worker and measurement["process_exit_code"] == 0 else None,
                "status": worker["artifact"]["raw"]["status"] if worker and measurement["process_exit_code"] == 0 else "unknown",
                "reason": measurement["stop_reason"], "measurement_hash": digest(measurement)}
            save_once(result_path, result)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        measurement = json.loads((account / "measurement.json").read_text(encoding="utf-8"))
        need(result["measurement_hash"] == digest(measurement), "diagnostic measurement changed")
        results.append({**result, "reused": reused})
    if not manifest_path.exists():
        save_once(manifest_path, {"identity": identity,
            "outcomes": [{k: v for k, v in r.items() if k not in ("artifact", "reused")} for r in results], "files": {
            str(p.relative_to(folder)): file_hash(p) for p in folder.rglob("*") if p.is_file()}})
    for path in folder.rglob("*"):
        if path.is_file():
            sources[str(path.resolve())] = file_hash(path)
    return {"identity": identity, "accounts": results, "source_hashes": sources, "usage": _usage(cache),
            "accounting": public_contract(policy)}


if __name__ == "__main__":
    need(len(sys.argv) == 3 and sys.argv[1] == "worker", "internal stock worker invocation only")
    _worker(sys.argv[2])

"""Host-controlled engineering review and activation between research versions.

Only this trusted host chooses test commands and activation. Separate real model
calls propose the patch and audit its implementation. Numerical financial
acceptance remains exclusively with the original frozen oracle.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from quanta_agents.research_kernel.store import digest, write_json
from .model import ModelLoop
from .resources import ResourceGuard, CostLedger
from .revisions import RevisionManager

BASE = "src/quanta_agents/factor_campaign/"
MUTABLE = {BASE + name for name in ("candidates.py", "selection.py", "combination.py", "numeric.py", "numeric_cache.py")}
TESTS = ("tests/test_factor_campaign_numeric.py", "tests/test_factor_campaign_controller.py",
         "tests/test_factor_campaign_oracle.py", "tests/test_factor_campaign_control_support.py",
         "tests/test_factor_campaign_framework_host.py", "tests/test_factor_campaign_independent_controller.py",
         "tests/test_factor_campaign_maintenance.py", "tests/test_factor_campaign_execution_order.py",
         "tests/test_factor_campaign_maintenance_dependencies.py")
AUDIT_SCHEMA = {"type": "object", "properties": {
    "approved": {"type": "boolean"}, "reason": {"type": "string"},
    "evidence_ids": {"type": "array", "items": {"type": "string"}},
    "violations": {"type": "array", "items": {"type": "string"}}},
    "required": ["approved", "reason", "evidence_ids", "violations"], "additionalProperties": False}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _once(path, value):
    path = Path(path)
    if path.exists():
        if _read(path) != value:
            raise ValueError("Retained framework evidence changed: " + str(path))
    else:
        write_json(path, value)


def _algorithm_shape(content):
    """Ignore literal tuning/docstrings; require changed computation structure."""
    class Shape(ast.NodeTransformer):
        def visit_Constant(self, node):
            return ast.copy_location(ast.Constant(value="LITERAL"), node)
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return None
            return self.generic_visit(node)
    return ast.dump(Shape().visit(ast.parse(content)), include_attributes=False)


def _validate_changes(workspace, response):
    changes = response.get("changes", [])
    allowed = set(MUTABLE)
    substantive = False
    changed_existing = False
    added_algorithm = False
    for change in changes:
        path = change["path"]
        is_algorithm = bool(re.fullmatch(re.escape(BASE) + r"algorithms/[a-z][a-z0-9_]{0,60}\.py", path))
        if path not in MUTABLE and not is_algorithm:
            raise ValueError("Patch cannot edit controller, frozen evaluator, tests, protocol, gateway or guards")
        allowed.add(path)
        source = workspace / path
        new_shape = _algorithm_shape(change["content"])
        if source.exists():
            changed = new_shape != _algorithm_shape(source.read_text(encoding="utf-8"))
            substantive |= changed
            changed_existing |= changed and path in MUTABLE
        else:
            tree = ast.parse(change["content"])
            substantive |= any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in tree.body)
            added_algorithm |= is_algorithm
    if not substantive:
        raise ValueError("Literal window/weight/formula tuning or comments belongs to a research batch, not a framework version")
    if added_algorithm and not changed_existing:
        raise ValueError("A new algorithm must be wired into existing research computation")
    return allowed


def _include_maintenance_pins(root, pinned):
    """Retain the original oracle pins and verify approved controller repairs.

    Maintenance copies may move the active host without moving the independent
    oracle. Each additional manifest is hash-bound and retained permanently.
    This index and all manifests are outside automatic patch targets.
    """
    root = Path(root).resolve()
    index = root / "framework_additional_dependency_pins.json"
    merged = dict(pinned)
    if index.exists():
        entries = _read(index)
        if not isinstance(entries, list):
            raise ValueError("Maintenance dependency index must be a retained manifest list")
        for entry in entries:
            manifest_path = Path(entry["path"]).resolve()
            if not manifest_path.is_relative_to(root) or _sha(manifest_path) != entry["sha256"]:
                raise ValueError("Approved maintenance dependency manifest changed")
            manifest = _read(manifest_path)
            for file, expected in manifest["files"].items():
                resolved = Path(file).resolve()
                if not resolved.is_relative_to(root):
                    raise ValueError("Maintenance dependency must remain inside retained campaign artifacts")
                if file in merged and merged[file] != expected:
                    raise ValueError("Conflicting retained dependency identity: " + file)
                merged[file] = expected
    for file, expected in merged.items():
        if not Path(file).is_file() or _sha(file) != expected:
            raise ValueError("Frozen framework/acceptance dependency changed: " + file)
    return merged


def _freeze_dependencies(campaign, workspace):
    """First freeze persists outside all allowed model patch targets."""
    path = campaign.root / "framework_dependency_pins.json"
    if path.exists():
        pinned = _include_maintenance_pins(campaign.root, _read(path))
        return list(pinned)
    frozen = [campaign.root / "protocol.json", campaign.root / "protocol_identity.json"]
    frozen += list((campaign.root / "acceptance").glob("*.json"))
    for package in ("factor_acceptance_v10", "factor_research", "meta_v6", "meta_v3", "research_kernel"):
        frozen += list((workspace / "src/quanta_agents" / package).rglob("*.py"))
    frozen += list((workspace / "experiment_traces/meta_ashare_revision18/src").rglob("*.py"))
    frozen += [workspace / BASE / name for name in ("campaign.py", "protocol.py", "model.py", "resources.py", "revisions.py", "framework_host.py")]
    frozen += [workspace / file for file in TESTS]
    frozen += [workspace / "scripts/audit_factor_campaign_v10.py"]
    missing = [str(p) for p in frozen if not p.is_file()]
    if missing:
        raise ValueError("Cannot freeze absent required dependency: " + ", ".join(missing[:5]))
    if not (campaign.root / "acceptance/authority.json").is_file():
        raise ValueError("Original independent acceptance authority must be frozen before framework evolution")
    pinned = {str(p.resolve()): _sha(p) for p in frozen}
    _once(path, pinned)
    return list(_include_maintenance_pins(campaign.root, pinned))


def _test_patch(workspace, destination, *, timeout_seconds=600):
    destination.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace / "src")
    command = [sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"]
    started = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout_seconds,
                                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = None
    (destination / "stdout.txt").write_text(stdout, encoding="utf-8")
    (destination / "stderr.txt").write_text(stderr, encoding="utf-8")
    report = {"passed": code == 0, "returncode": code, "command": command,
              "wall_seconds": time.perf_counter() - started,
              "stdout_path": str(destination / "stdout.txt"), "stderr_path": str(destination / "stderr.txt"),
              "stdout_sha256": _sha(destination / "stdout.txt"), "stderr_sha256": _sha(destination / "stderr.txt"),
              "stdout_tail": stdout[-10000:], "stderr_tail": stderr[-4000:],
              "original_oracle_suite_included": True}
    write_json(destination / "test_report.json", report)
    return report


def _decision(loop, campaign, action, kind, context, *, schema=None):
    path = campaign.version_root / "decisions" / (action + ".json")
    if path.exists():
        return _read(path)
    request_path = campaign.version_root / "framework_requests" / (action + ".json")
    if request_path.exists():
        registered = _read(request_path)
    else:
        registered = {"kind": kind, "context": context, "schema": schema}
        _once(request_path, registered)
    result = loop.request(action, registered["kind"], registered["context"], schema=registered["schema"])
    if result["status"] not in {"ready", "applied"}:
        campaign._state(phase="waiting", blocker={"kind": "framework_model", "action_id": action,
                        "details": result.get("blocker"), "resume_action_id": result.get("action_id")})
        return None
    result = loop.apply_once(action, lambda response, action_id: _once(path, response) or {"path": str(path), "sha256": _sha(path)})
    if result["status"] != "applied":
        campaign._state(phase="waiting", blocker={"kind": "framework_model_application", "details": result})
        return None
    return _read(path)


def _complete_transition(campaign, transition):
    parent_state = transition["parent_state"]
    previous_root = campaign.root / "versions" / parent_state["version"]
    next_root = campaign.root / "versions" / transition["next_version"]
    next_root.mkdir(parents=True, exist_ok=True)
    inherited = deepcopy(_read(previous_root / "catalog.json"))
    for row in inherited:
        row["inherited_from_version"] = parent_state["version"]
        row["original_origin"] = row.get("original_origin", row.get("origin"))
        if row.get("origin") not in {"reference", "control"}:
            row["origin"] = "inherited"
    _once(next_root / "inherited_catalog.json", inherited)
    _once(next_root / "catalog.json", inherited)
    _once(next_root / "summaries.json", {})
    _once(next_root / "combination_summaries.json", {})
    _once(next_root / "framework_origin.json", {"parent_version": parent_state["version"],
          "patch_id": transition["patch_id"], "workspace": transition["active_workspace"],
          "source_scale_completed": transition["scale_completed"], "inherited_candidates": len(inherited)})
    completed = list(parent_state.get("framework_versions_completed", []))
    if transition["scale_completed"] and parent_state["version"] not in completed:
        completed.append(parent_state["version"])
    reset = {key: 0 for key in ("batch", "primary_evaluated", "new_evaluated", "reference_evaluated", "inherited_evaluated", "controls_evaluated",
                               "combinations_evaluated", "combination_controls_evaluated", "combination_controls_attempted",
                               "failures", "numeric_duplicates", "canonical_duplicates", "plateau_extensions")}
    reset.update(version=transition["next_version"], phase="framework_activated_restart_required", blocker=None,
                 numeric_wall_seconds=0., numeric_cpu_seconds=0., best_factor_floor=None, best_combination_floor=None,
                 historical_target_met=False, independent_stability_proven=False, profitability_proven=False,
                 framework_versions_completed=completed, active_workspace=transition["active_workspace"],
                 last_parent_version=parent_state["version"], version_scale_status="not_started")
    campaign._state(**reset)
    campaign._context = None
    gc.collect()
    campaign.event("framework_activated_restart_required", version=transition["next_version"], patch_id=transition["patch_id"], workspace=transition["active_workspace"])
    return {**campaign.status(), "status": "framework_activated_restart_required", "active_workspace": transition["active_workspace"]}


def advance_campaign(campaign, *, model_loop=None, manager_factory=RevisionManager, test_runner=None, resource_guard=None):
    """One bounded engineering attempt per call; rejection retains the old version."""
    if campaign.state.get("historical_target_met"):
        return campaign.status()
    if campaign.state.get("phase") == "framework_activated_restart_required":
        return {**campaign.status(), "status": "framework_activated_restart_required"}
    transition_path = campaign.root / "framework_transition.json"
    active_path = campaign.root / "active_framework.json"
    if transition_path.exists() and active_path.exists():
        transition, active = _read(transition_path), _read(active_path)
        if active.get("patch_id") == transition["patch_id"] and campaign.state["version"] == transition["parent_state"]["version"]:
            return _complete_transition(campaign, transition)
    resources = (resource_guard or ResourceGuard(campaign.root)).check()
    if not resources["allowed"]:
        campaign._state(phase="waiting", blocker={"kind": "framework_resources", "details": resources})
        return campaign.status()
    state = campaign.state
    version = state["version"]
    workspace = Path(__file__).resolve().parents[3]
    if active_path.exists():
        workspace = Path(_read(active_path)["workspace"])
    review_path = campaign.version_root / "decisions" / f"{version}_batch{state['batch']:04d}_review.json"
    if not review_path.exists():
        campaign._state(phase="waiting", blocker={"kind": "missing_evidence_bound_framework_review", "path": str(review_path)})
        return campaign.status()
    review = _read(review_path)
    for question in ("failure_analysis", "framework_diagnosis", "proposed_improvement", "cross_version_comparison"):
        if not review.get(question, {}).get("answer") or not review[question].get("evidence_ids"):
            campaign._state(phase="waiting", blocker={"kind": "review_missing_evidence", "question": question})
            return campaign.status()
    attempt_path = campaign.version_root / "framework_attempt.json"
    attempt = _read(attempt_path) if attempt_path.exists() else {"number": 1, "status": "pending"}
    if attempt["status"] == "rejected":
        attempt = {"number": attempt["number"] + 1, "status": "pending", "previous_attempt": {
            k: attempt.get(k) for k in ("number", "status", "patch_id", "error")}}
    write_json(attempt_path, attempt)
    patch_id = f"{version}_framework_{attempt['number']:04d}"
    loop = model_loop or ModelLoop(campaign.root)
    try:
        frozen = _freeze_dependencies(campaign, workspace)
        index = {path: {"bytes": (workspace / path).stat().st_size, "sha256": _sha(workspace / path)} for path in sorted(MUTABLE) if (workspace / path).is_file()}
        text = json.dumps(review, ensure_ascii=False).lower()
        ranked = sorted(index, key=lambda path: (Path(path).stem not in text, path not in {BASE+"combination.py", BASE+"selection.py"}, path))
        source = {}
        total = 0
        for path in ranked:
            size = index[path]["bytes"]
            if total + size <= 42000:
                source[path] = (workspace / path).read_text(encoding="utf-8")
                total += size
        context = {"task": "Propose one evidence-bound substantive framework patch. Replace full contents only of supplied source files, or add a focused algorithms/name.py module and wire it into supplied source. Do not change tests/controller/guards/evaluator/threshold/years/labels. Literal parameter/window/weight/formula tuning does not qualify. Every diagnosis must reference real review evidence. A separate model audits exact code and unchanged oracle tests before activation.",
                   "review": review, "review_evidence_id": "review:" + _sha(review_path), "parent_version": version,
                   "allowed_existing_files": list(source), "file_index": index, "source_files": source,
                   "previous_patch_rejection": attempt.get("previous_attempt"), "required_frozen_tests": list(TESTS)}
        proposal = _decision(loop, campaign, patch_id + "_proposal", "framework_patch", context)
        if proposal is None:
            return campaign.status()
        allowed = _validate_changes(workspace, proposal)
        # The model cannot replace a file omitted from its bounded source context.
        if any(c["path"] in MUTABLE and c["path"] not in source for c in proposal["changes"]):
            raise ValueError("Patch touched an existing file not supplied in its decision context")
        manager = manager_factory(workspace, campaign.root, frozen_paths=frozen, allowed_paths=allowed)
        next_version = "V" + str(int(version[1:-1]) + 1) + "A"
        staged = manager.stage(patch_id, proposal["changes"], component=proposal["component"],
                   hypothesis=proposal["hypothesis"], falsifier=proposal["falsifier"], evidence_ids=proposal["evidence_ids"],
                   next_version=next_version, parent_version=version)
        def test(candidate_workspace):
            saved = campaign.root / "framework_patches" / patch_id / "tests_admitted.json"
            if saved.exists():
                previous = _read(saved)
                if previous.get("passed") is True:
                    return previous
            result = (test_runner(candidate_workspace) if test_runner else
                      _test_patch(candidate_workspace, campaign.root / "framework_patches" / patch_id / ("tests_" + str(time.time_ns()))))
            _once(saved, result)
            return result
        audit_pending = []
        def audit(candidate_workspace, manifest, tests):
            audit_context = {"task": "Independent implementation audit. You did not author this patch. Assess frozen-contract preservation, causality, leakage, missing/constant handling, cache identity, reproducibility, algorithmic substance versus literal tuning, evidence binding and test adequacy. Reject any target/year/label/protocol/acceptance modification, covert threshold bypass, source file/command access, or unsupported data. This is engineering review only; passing cannot establish financial attainment. Return approved only with no violations and cite supplied evidence IDs.",
                "review": review, "patch": proposal, "parent_source": {c["path"]: source.get(c["path"], "new file") for c in proposal["changes"]},
                "tests": tests, "patch_manifest_sha256": digest(manifest), "evidence_ids": [patch_id, "tests:" + digest(tests), "review:" + _sha(review_path)]}
            decision = _decision(loop, campaign, patch_id + "_independent_audit", "review", audit_context, schema=AUDIT_SCHEMA)
            if decision is None:
                audit_pending.append(True)
                return {"approved": False, "independent": True, "evidence_id": None, "pending_model": True}
            return {"approved": decision["approved"] is True and not decision["violations"] and bool(decision["evidence_ids"]),
                    "independent": True, "evidence_id": patch_id + "_independent_audit", "model_audit": decision}
        verified = manager.verify(patch_id, test_runner=test, independent_auditor=audit)
        if audit_pending:
            return campaign.status()
        if verified["status"] != "verified":
            raise ValueError("Patch verification rejected: " + str(verified.get("error", verified)))
        budget = campaign.config["budget"]
        complete = state["primary_evaluated"] >= budget["minimum_factors"] and state["combinations_evaluated"] >= budget["minimum_combinations"]
        if version == "V10A":
            complete &= state.get("new_evaluated", 0) >= budget["minimum_new_v10a"]
        snapshot = {"state": state, "cost": CostLedger(campaign.root).summary(),
              "scale_completed": bool(complete), "review_path": str(review_path), "review_sha256": _sha(review_path),
              "summaries_sha256": _sha(campaign.version_root / "summaries.json"),
              "combination_summaries_sha256": _sha(campaign.version_root / "combination_summaries.json")}
        if not (campaign.version_root / "framework_close_snapshot.json").exists():
            _once(campaign.version_root / "framework_close_snapshot.json", snapshot)
        transition = {"patch_id": patch_id, "parent_state": state, "next_version": next_version,
                      "active_workspace": staged["workspace"], "scale_completed": bool(complete)}
        write_json(transition_path, transition)
        manager.activate(patch_id)
        write_json(attempt_path, {**attempt, "status": "activated", "patch_id": patch_id})
        return _complete_transition(campaign, transition)
    except Exception as exc:
        failure = {**attempt, "status": "rejected", "patch_id": patch_id, "error": str(exc)[:4000], "retained_previous_version": version}
        write_json(attempt_path, failure)
        write_json(campaign.version_root / "framework_failures" / (patch_id + ".json"), failure)
        campaign._state(phase="framework_patch_rejected", blocker={"kind": "framework_patch_rejected", "patch_id": patch_id, "error": str(exc)[:4000], "retry_next_turn": True})
        campaign.event("framework_patch_rejected", patch_id=patch_id, error=str(exc)[:1500])
        return campaign.status()

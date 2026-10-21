"""Stage framework changes in retained copies; activate only verified bytes.

This guard is outside model-patch scope. It never edits an evaluator, changes an
acceptance result, deletes an old version or executes a model-provided command.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import time

from quanta_agents.research_kernel.store import digest, exclusive_lock, serial, write_json
from .resources import CostLedger

COMPONENTS = {"implementation", "scheduler", "data_interface", "operator", "combination"}
PROTECTED_NAMES = {"protocol.py", "acceptance.py", "oracle.py", "reference.py", "revisions.py", "framework_host.py", "model.py", "resources.py", "__init__.py"}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sources(root):
    roots = ("src", "tests", "scripts", "experiment_traces/meta_ashare_revision18/src")
    return {p.relative_to(root).as_posix(): _hash(p) for base in roots for p in (root / base).rglob("*")
            if p.is_file() and p.suffix in {".py", ".json", ".yaml", ".toml"} and "__pycache__" not in p.parts}


def _meaningful_python(content):
    tree = ast.parse(content)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.dump(tree, include_attributes=False)


class RevisionManager:
    def __init__(self, workspace, artifacts, frozen_paths=(), *, allowed_paths=None, cost_ledger=None):
        self.workspace, self.root = Path(workspace).resolve(), Path(artifacts).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.patches = self.root / "framework_patches"
        self.patches.mkdir(exist_ok=True)
        self.frozen_paths = {str(Path(p).resolve()): _hash(p) for p in frozen_paths}
        self.allowed_paths = set(allowed_paths) if allowed_paths is not None else None
        self.cost = cost_ledger or CostLedger(self.root)
        self.active_path = self.root / "active_framework.json"

    def _allowed(self, value):
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ":" in value or any(part in {".", ".."} for part in path.parts):
            raise ValueError("Patch path must stay inside the isolated workspace")
        if path.suffix != ".py" or path.name in PROTECTED_NAMES or any(word in path.name.lower() for word in ("acceptance", "oracle", "protocol", "reference")):
            raise ValueError("Frozen acceptance and guard files are outside patch scope")
        scoped = value.startswith("src/quanta_agents/factor_campaign/") or value.startswith("tests/test_factor_campaign_")
        if not scoped or (self.allowed_paths is not None and value not in self.allowed_paths):
            raise ValueError("Only explicitly allowed new research implementation may be patched")
        target = (self.workspace / value).resolve()
        if not target.is_relative_to(self.workspace) or str(target) in self.frozen_paths:
            raise ValueError("Frozen target or path escape")
        return value

    def _folder(self, patch_id):
        if not isinstance(patch_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", patch_id):
            raise ValueError("Safe stable patch ID required")
        return self.patches / patch_id

    def stage(self, patch_id, changes, *, component, hypothesis, falsifier, evidence_ids, next_version, parent_version="V10A"):
        if component not in COMPONENTS or not hypothesis or not falsifier or not evidence_ids:
            raise ValueError("A substantive framework change requires a diagnosis, falsifier and evidence")
        if not re.fullmatch(r"V[1-9][0-9]*A", next_version) or int(next_version[1:-1]) != int(parent_version[1:-1]) + 1:
            raise ValueError("Framework versions must advance one version from the actual parent")
        if isinstance(changes, list):
            if len({row["path"] for row in changes}) != len(changes):
                raise ValueError("Duplicate patch path")
            changes = {row["path"]: row["content"] for row in changes}
        if not isinstance(changes, dict) or not changes or len(changes) > 24:
            raise ValueError("Bounded nonempty replacement-file patch required")
        for path, content in changes.items():
            self._allowed(path)
            if not isinstance(content, str) or len(content.encode()) > 300000:
                raise ValueError("Patch content must be bounded UTF-8 source")
            compile(content, path, "exec")
        program_changes = [path for path in changes if path.startswith("src/") and
                           (not (self.workspace / path).exists() or _meaningful_python(changes[path]) != _meaningful_python((self.workspace / path).read_text(encoding="utf-8")))]
        if not program_changes:
            raise ValueError("Tests, comments or an unchanged formula do not make a framework version")
        manifest = {"patch_id": patch_id, "component": component, "hypothesis": hypothesis, "falsifier": falsifier,
                    "evidence_ids": list(evidence_ids), "parent_version": parent_version, "next_version": next_version,
                    "source_workspace": str(self.workspace), "frozen_paths": self.frozen_paths,
                    "changes_sha256": {p: hashlib.sha256(c.encode()).hexdigest() for p, c in changes.items()}}
        folder = self._folder(patch_id)
        if folder.exists():
            if (folder / "manifest.json").exists():
                previous = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                if previous["request"] != manifest:
                    raise ValueError("Patch ID cannot be reused for changed patch bytes")
                return previous
            # Preserve incomplete copies, then stage a fresh copy of the same
            # exact proposal. Never delete or silently overwrite failed work.
            if (folder / "staging_request.json").exists() and json.loads((folder / "staging_request.json").read_text(encoding="utf-8")) != manifest:
                raise ValueError("Incomplete stage has a different registered request")
            if (folder / "proposed_changes.json").exists() and json.loads((folder / "proposed_changes.json").read_text(encoding="utf-8")) != changes:
                raise ValueError("Incomplete stage has different retained patch bytes")
        else:
            folder.mkdir()
        write_json(folder / "staging_request.json", manifest)
        # Preserve proposal even if copying or later tests fail.
        write_json(folder / "proposed_changes.json", changes)
        stage = folder / "workspace"
        number = 1
        while stage.exists():
            number += 1
            stage = folder / ("workspace_attempt_" + str(number))
        stage.mkdir()
        before = _sources(self.workspace)
        for base in ("src", "tests", "scripts", "experiment_traces/meta_ashare_revision18/src"):
            source = self.workspace / base
            if source.exists():
                shutil.copytree(source, stage / base, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for filename in ("pyproject.toml", "requirements-meta.txt"):
            if (self.workspace / filename).is_file():
                shutil.copy2(self.workspace / filename, stage / filename)
        for path, content in changes.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        result = {"request": manifest, "status": "staged", "workspace": str(stage), "before": before,
                  "after": _sources(stage), "created_unix": time.time(), "financial_success_claim": False}
        write_json(folder / "manifest.json", result)
        return result

    def _intact(self, manifest):
        if _sources(Path(manifest["workspace"])) != manifest["after"]:
            raise ValueError("Staged source bytes changed after patch registration")
        for path, expected in manifest["request"]["frozen_paths"].items():
            if not Path(path).is_file() or _hash(path) != expected:
                raise ValueError("Frozen acceptance source changed; campaign must block")
        expected = {**manifest["before"], **manifest["request"]["changes_sha256"]}
        if manifest["after"] != expected:
            raise ValueError("Patch changed files beyond declared permitted targets")

    def verify(self, patch_id, *, test_runner, independent_auditor):
        """Callbacks are trusted host code, never executable model instructions.

        test_runner(workspace) -> {passed, evidence...}; independent_auditor(
        workspace, manifest, tests) -> {approved, independent, evidence_id,...}.
        Both reports and source hashes remain reviewable at activation.
        """
        folder = self._folder(patch_id)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        report = {"patch_id": patch_id, "status": "rejected", "tests": None, "audit": None,
                  "manifest_sha256": _hash(folder / "manifest.json")}
        try:
            self._intact(manifest)
            with self.cost.measure("patch." + patch_id + ".tests", kind="patch_tests"):
                report["tests"] = test_runner(Path(manifest["workspace"]))
            self._intact(manifest)
            if report["tests"].get("passed") is not True:
                raise ValueError("Targeted tests failed")
            report["audit"] = independent_auditor(Path(manifest["workspace"]), manifest, report["tests"])
            self._intact(manifest)
            audit = report["audit"]
            if audit.get("approved") is not True or audit.get("independent") is not True or not audit.get("evidence_id"):
                raise ValueError("Independent audit approval with evidence is required")
            report["status"] = "verified"
            report["verified_sources_sha256"] = digest(manifest["after"])
        except Exception as exc:
            report["error"] = str(exc)[:4000]
        write_json(folder / ("verification_" + str(time.time_ns()) + ".json"), report)
        write_json(folder / "latest_verification.json", report)
        return report

    def activate(self, patch_id, *, smoke_test=None):
        folder = self._folder(patch_id)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads((folder / "latest_verification.json").read_text(encoding="utf-8"))
        self._intact(manifest)
        if report.get("status") != "verified" or report.get("manifest_sha256") != _hash(folder / "manifest.json") or report.get("verified_sources_sha256") != digest(manifest["after"]):
            raise ValueError("Only the independently verified exact patch may activate")
        with exclusive_lock(self.root / "framework_activation.lock"):
            previous = json.loads(self.active_path.read_text(encoding="utf-8")) if self.active_path.exists() else {
                "version": manifest["request"]["parent_version"], "workspace": str(self.workspace)}
            if previous.get("patch_id") == patch_id:
                return previous
            if previous["version"] != manifest["request"]["parent_version"]:
                raise ValueError("Active parent version differs from reviewed patch parent")
            active = {"version": manifest["request"]["next_version"], "workspace": manifest["workspace"],
                      "patch_id": patch_id, "verified_sources_sha256": digest(manifest["after"])}
            write_json(folder / "previous_active.json", previous)
            write_json(self.active_path, active)
            try:
                if smoke_test is not None and smoke_test(Path(active["workspace"])) is not True:
                    raise ValueError("Activation smoke test failed")
                self._intact(manifest)
            except BaseException as exc:
                write_json(self.active_path, previous)
                write_json(folder / "rollback.json", {"reason": str(exc), "restored": previous, "retained_failed_patch": True})
                raise
            write_json(folder / "activation.json", active)
            return active

    def rollback(self, patch_id, *, reason):
        folder = self._folder(patch_id)
        with exclusive_lock(self.root / "framework_activation.lock"):
            active = json.loads(self.active_path.read_text(encoding="utf-8"))
            if active.get("patch_id") != patch_id:
                raise ValueError("Rollback must name the active patch")
            previous = json.loads((folder / "previous_active.json").read_text(encoding="utf-8"))
            write_json(self.active_path, previous)
            write_json(folder / "rollback.json", {"reason": reason, "restored": previous, "retained_failed_patch": True})
            return previous

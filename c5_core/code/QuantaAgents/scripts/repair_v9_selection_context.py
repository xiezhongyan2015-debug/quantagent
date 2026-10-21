"""One explicit V9.0.0 -> V9.0.1 evidence-preserving continuation migration.

The initial context-omission rejection stays closed and immutable. No model or
account executes here. Its two calls and 15 accounts carry into the denominator.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import zipfile
import hashlib

from quanta_agents.meta_v9.study import V9Study, VERSION, read, once, sha, source_files


def migrate(original, target, archive, verification):
    original, target = original.resolve(), target.resolve()
    if original == target or (target / "source_pins.json").exists() or (target / "context_repair.json").exists():
        raise ValueError("A distinct, unstarted continuation directory is required")
    closed, report = read(original / "closed.json"), read(original / "development_report.json")
    checked = read(verification)
    pins = read(original / "source_pins.json")
    with zipfile.ZipFile(archive) as z:
        for i, (name, expected) in enumerate(pins.items()):
            if hashlib.sha256(z.read(f"{i:03d}/{Path(name).name}")).hexdigest() != expected:
                raise ValueError("Initial implementation archive mismatch")
    if (closed["status"] != "closed" or report["final_fit_status"] != "fitted"
            or checked["engineering_and_artifact_verification"] != "passed"
            or checked["completed_accounts"] != 15 or len(checked["model_calls"]) != 2):
        raise ValueError("Expected fully retained initial study is required")
    # Only study presentation/version files may differ. Numerical/account code
    # must stay byte-for-byte equal to the implementation that produced evidence.
    allowed = {"study.py", "__init__.py"}
    for p in source_files():
        if not (p.parent.name == "meta_v9" and p.name in allowed) and pins.get(str(p)) != sha(p):
            raise ValueError("Numerical source changed; evidence reuse is not admitted: " + str(p))
    cfg = read(original / "config.json")
    original_id = cfg["study_id"]
    cfg.update(version=VERSION, study_id=original_id + "_context_repair")
    cfg["budget"]["max_model_calls"] = 4
    cfg["continuation_of"] = str(original)
    cfg["budget_amendment"] = "One additional model call to repair omitted final fit eligibility; all original calls/accounts count; no new parameter search"
    study = V9Study(target)
    study.initialize(cfg, cfg["library_snapshot"]["path"], cfg["data"])
    def copy_tree(source, destination):
        for p in source.rglob("*"):
            dest = destination / p.relative_to(source)
            if p.is_file() and dest.exists() and sha(p) != sha(dest):
                raise ValueError("Partial import differs: " + str(dest))
        shutil.copytree(source, destination, dirs_exist_ok=True)
    for name in ("assets", "panel_cache", "development", "account_starts"):
        if (original / name).exists():
            copy_tree(original / name, target / name)
    for name in ("early_fit.json", "declaration.json", "development_report.json", "candidate_strategies.json"):
        shutil.copy2(original / name, target / name)
    copy_tree(original / "model_calls" / "declare", target / "model_calls" / "declare")
    copy_tree(original / "model_calls" / "select", target / "model_calls" / "initial_select")
    prior = target / "prior_context_failure"
    prior.mkdir(exist_ok=True)
    for name in ("selection.json", "closed.json", "events.jsonl", "source_pins.json", "started.json"):
        if (original / name).exists():
            shutil.copy2(original / name, prior / name)
    inherited = {str(p.relative_to(original)): sha(p) for p in original.rglob("*") if p.is_file()
                 and (p.parts[len(original.parts)] in {"development", "model_calls", "account_starts"}
                      or p.name in {"development_report.json", "early_fit.json", "selection.json", "closed.json"})}
    amendment = {"reason": "Selection packet omitted successful final_fit status and available strategy specifications",
        "initial_study": str(original), "initial_study_id": original_id,
        "initial_source_bundle": str(archive.resolve()), "initial_source_bundle_sha256": sha(archive),
        "initial_verification": str(verification.resolve()), "initial_verification_sha256": sha(verification),
        "inherited_model_calls": 2, "inherited_accounts": 15, "new_model_call_allowance": 2,
        "total_model_call_limit": 4, "total_account_limit": 18, "numeric_code_unchanged": True,
        "inherited_source_files": inherited, "new_search_or_parameter_changes": False,
        "already_exposed_development_evidence": True, "initial_rejection_preserved": True}
    once(target / "context_repair.json", amendment)
    study.project.record_trial(cfg["study_id"], "inherited_evidence", "evidence_import",
        {"original_study": original_id, "accounts": 15, "model_calls": 2}, status="imported",
        evidence={"path": str(target / "context_repair.json"), "sha256": sha(target / "context_repair.json")})
    return {"root": str(target), "version": VERSION, "inherited_accounts": 15, "inherited_calls": 2}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("original", "target", "archive", "verification"):
        p.add_argument("--" + name, required=True, type=Path)
    a = p.parse_args()
    print(migrate(a.original, a.target, a.archive, a.verification))


if __name__ == "__main__":
    main()

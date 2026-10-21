"""Read-only release evidence extraction; never rerun a model or account."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def summarize(root):
    from quanta_agents.meta_v7.controller import V7ResearchKernel
    from quanta_agents.meta_v6.gateway import verify_saved_completion
    from quanta_agents.research_kernel.store import write_json
    root = Path(root).resolve()
    kernel = V7ResearchKernel(root / "research_study")
    status = kernel.status()
    if kernel.pending() or any(r["status"] in {"started", "needs_recovery", "verified"} for r in kernel.store.rows("SELECT status FROM model_calls")):
        raise ValueError("Study still active or has an unresolved model receipt")
    calls, totals = [], {}
    for row in kernel.store.rows("SELECT * FROM model_calls ORDER BY created"):
        receipt = verify_saved_completion(row["directory"])
        if receipt["runtime_identity"].get("verified") is not True:
            raise ValueError("Unverified original model receipt")
        action = receipt["response"]
        for key, value in receipt.get("usage", {}).items():
            if type(value) is int:
                totals[key] = totals.get(key, 0) + value
        calls.append({"id": row["id"], "status": row["status"], "model": receipt["model"], "effort": receipt["effort"],
                      "verified": True, "action": action["action"], "reason": action["reason"],
                      "usage": receipt.get("usage", {}), "directory": row["directory"]})
    runs = []
    for row in kernel.store.rows("SELECT * FROM runs ORDER BY rowid"):
        value = {k: row[k] for k in ("id", "status", "executions", "evidence_id", "artifact_dir", "error")}
        value["strategy"] = json.loads(row["spec"])["strategy"]
        if row["status"] == "completed":
            kernel._verify_artifacts(row["artifact_dir"], row["artifact_manifest_sha256"])
            report = kernel.store.evidence(row["evidence_id"], max_bytes=1000000, limit=100)["value"]
            value.update({k: report[k] for k in ("summary", "annual", "execution")})
        runs.append(value)
    validation = []
    for row in kernel.store.rows("SELECT * FROM validation_jobs ORDER BY rowid"):
        validation.append({"id": row["id"], "status": row["status"], "evidence_id": row["evidence_id"],
            "review": json.loads(row["review"]) if row["review"] else None,
            "report": kernel.store.evidence(row["evidence_id"], max_bytes=2000000, limit=100)["value"] if row["evidence_id"] else None})
        for candidate in (validation[-1]["report"] or {}).get("candidates", []):
            for phase in ("train", "validation"):
                artifact = (candidate.get(phase) or {}).get("raw_artifacts")
                if not artifact:
                    raise ValueError("Validation raw account retention missing")
                manifest = Path(artifact["manifest_path"]).resolve()
                if not manifest.is_relative_to(kernel.root / "validation_accounts"):
                    raise ValueError("Validation artifact escaped the study")
                if hashlib.sha256(manifest.read_bytes()).hexdigest() != artifact["manifest_sha256"]:
                    raise ValueError("Validation manifest digest differs")
                for proof in json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]:
                    path = Path(proof["path"]).resolve()
                    if not path.is_relative_to(manifest.parent) or hashlib.sha256(path.read_bytes()).hexdigest() != proof["sha256"]:
                        raise ValueError("Validation raw artifact digest differs")
    suites = ET.parse(root / "pytest_release.xml").getroot()
    testing = {key: sum(int(s.get(key, "0")) for s in suites.iter("testsuite")) for key in ("tests", "failures", "errors", "skipped")}
    reviews = [json.loads(r["review"]) for r in kernel.store.rows("SELECT review FROM batch_reviews")]
    source_paths = list((ROOT / "src/quanta_agents/meta_v7").glob("*.py")) + list((ROOT / "tests").glob("test_meta_v7_*.py"))
    source_paths += [ROOT / "scripts" / name for name in ("run_research_v7.py", "accept_meta_v7.py", "summarize_meta_v7.py")]
    proof = lambda path: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    result = {"version": "meta_v7.0.0", "study_status": status, "tests": testing, "calls": calls,
        "model_usage_totals": totals, "runs": runs, "reviews": reviews, "validation": validation,
        "project_ledger": kernel.project.summary(), "asset_counts": {}, "release_sources": [proof(p) for p in source_paths],
        "numeric_acceptance": json.loads((root / "numeric_parity_001/supervisor_receipt.json").read_text(encoding="utf-8")),
        "representation_pilot": proof(root / "pilot_representation_001/pilot_report.json"),
        "context_repairs": [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*context_repair.json"))],
        "claims": {"factor_before_portfolio_verified": bool(status["factor_jobs"]) and bool(runs),
            "two_reviewed_rounds": len(reviews) >= 2, "explicit_close": status["stopped"],
            "validation_finished_and_reviewed": bool(validation) and all(r["status"] == "completed" and r["review"] for r in validation),
            "independent_holdout": False, "profitability_proven": False, "model_upper_bound_proven": False,
            "all_factor_catalogue_entries_executable": False, "numeric_2025_read": False}}
    for asset in kernel.assets.list(limit=1000):
        key = asset.get("status", "unknown")
        result["asset_counts"][key] = result["asset_counts"].get(key, 0) + 1
    output = root / "release_acceptance.json"
    if output.exists():
        raise ValueError("Release acceptance already exists; preserve it and choose another output root")
    write_json(output, result)
    return {"path": str(output), "tests": testing, "claims": result["claims"], "calls": len(calls), "usage": totals}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    print(json.dumps(summarize(parser.parse_args().root), ensure_ascii=False))

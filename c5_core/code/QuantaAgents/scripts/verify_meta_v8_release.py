"""Read-only verification of a frozen pilot and its explicitly retained correction."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from quanta_agents.meta_v6.gateway import verify_saved_completion
from quanta_agents.research_kernel.store import serial, write_json


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verify(output):
    read = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
    intent, result = read(output/"intent.json"), read(output/"report.json")
    correction = read(output/"post_pilot_contract_correction.json")
    expected_change = ROOT/"scripts/validate_meta_v8.py"
    if Path(correction["source_path"]) != expected_change:
        raise ValueError("Unexpected correction scope")
    if correction["before_sha256"] != intent["pins"][str(expected_change)] or correction["after_sha256"] != sha(expected_change.read_bytes()):
        raise ValueError("Correction does not bind the frozen and current source")
    with zipfile.ZipFile(output/"frozen_execution_bundle.zip") as bundle:
        for filename, expected in intent["pins"].items():
            path = Path(filename)
            if sha(bundle.read(path.relative_to(ROOT).as_posix())) != expected:
                raise ValueError("Frozen bundle pin mismatch: "+filename)
            if path != expected_change and sha(path.read_bytes()) != expected:
                raise ValueError("Unexpected live source/input change: "+filename)
        for name in ("intent.json", "report.json"):
            if bundle.read((output/name).relative_to(ROOT).as_posix()) != (output/name).read_bytes():
                raise ValueError("Original frozen report or intent changed")
    calls = []
    for cell in intent["cells"]:
        folder = output/"cells"/cell["cell"]
        receipt = verify_saved_completion(folder/"call")
        if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or not receipt.get("runtime_identity", {}).get("verified"):
            raise ValueError("Saved local model identity not verified")
        row = next(r for r in result["cells"] if r["cell"] == cell["cell"])
        if row["response"] != receipt["response"] or row["usage"] != receipt["usage"]:
            raise ValueError("Report differs from original response/usage")
        if (folder/"call/prompt.txt").read_text(encoding="utf-8") != (folder/"frozen_prompt.txt").read_text(encoding="utf-8"):
            raise ValueError("Prompt differs from frozen input")
        calls.append({"cell": cell["cell"], "status": row["status"], "local_identity_verified": True,
                      "response_and_usage_unchanged": True})
    answer = {"status": "verified_with_explicit_post_pilot_protocol_correction", "calls": calls,
              "frozen_pins_verified": len(intent["pins"]), "new_model_calls": 0,
              "production_v8_source_unchanged_since_pilot": True,
              "real_revision_comparison": "not_evaluable_due_to_incomplete_pilot_path_contract",
              "original_invalid_outcome_retained": True, "financial_success": False}
    write_json(output/"final_verification.json", answer)
    return answer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"output/research/meta_v8_20260909/model_pilot_001")
    args = parser.parse_args()
    print(serial(verify(args.output.resolve())))

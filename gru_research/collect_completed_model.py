"""Read-only evidence collector for one completed fixed-label training run.

This never trains, infers, reads label arrays or test scores, or rewrites outputs.
It records declared dataset hashes separately from earlier actual-byte audits.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from datetime import datetime, timezone


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--seed", type=int, choices=(11, 22, 33, 44, 55), required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path.home())
    parser.add_argument("--expected-collector-sha256", required=True)
    args = parser.parse_args()
    collector_sha = digest(Path(__file__).read_bytes())
    assert collector_sha == args.expected_collector_sha256, "collector source changed"
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "validation_receipts/prospective_features_addon_manifest.json").read_bytes())
    expected = dict(manifest["source_guards_sha256"])
    expected["gru_research/prospective_features.py"] = manifest["files_sha256"]["gru_research/prospective_features.py"]

    def guards():
        actual = {name: digest((root / name).read_bytes()) for name in expected}
        assert actual == expected, "frozen source changed"
        return actual

    source_hashes = guards()
    meta_bytes = (args.dataset / "meta.json").read_bytes()
    meta = json.loads(meta_bytes)
    fingerprint = digest(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode())
    config = json.loads((root / "gru_research/config.json").read_bytes())
    formal = root / "outputs/formal"
    folder = formal / f"fixed/label_{args.label}/seed_{args.seed}"
    result_bytes = (folder / "result.json").read_bytes()
    result = json.loads(result_bytes)
    signature = {"dataset_fingerprint": fingerprint, "config": config,
                 "seed": args.seed, "label": args.label, "variant": "full"}
    assert result["complete"] is True and result["real_data"] is True
    assert result["signature"] == signature
    payload = {"formal/" + str((folder / "result.json").relative_to(formal)): result_bytes}
    for name in ("epochs.jsonl", "best.pt", "best_validation.json", "best_validation_sections.json"):
        path = folder / name
        payload["formal/" + str(path.relative_to(formal))] = path.read_bytes()
    best_bytes = payload["formal/" + str((folder / "best.pt").relative_to(formal))]
    assert digest(best_bytes) == result["best_checkpoint_sha256"]

    import torch
    from gru_research.model import PlanGRU
    torch.set_num_threads(1)
    state = torch.load(io.BytesIO(best_bytes), map_location="cpu", weights_only=False)
    assert state["signature"] == signature
    assert state["epoch"] + 1 == state["best_epoch"] + 1 == result["best_epoch"]
    assert state["best_ic"] == result["best_validation_ic"]
    aliases = {"hour": "hour24", "daily": "daily16", "factors": "factors", "fund": "fund16", "ages": "ages2"}
    groups = {key: list(meta["groups"][key] if key in meta["groups"] else meta["groups"][alias])
              for key, alias in aliases.items()}
    assert state["groups"] == groups and state["n_industries"] == meta["n_industries"]
    model = PlanGRU(groups, state["n_industries"], "full")
    model.load_state_dict(state["model"], strict=True)
    assert all(bool(torch.isfinite(value).all()) for value in state["model"].values())

    runs = []
    for path in sorted(formal.glob("fixed/label_*/seed_*/epochs.jsonl")):
        data = payload.get("formal/" + str(path.relative_to(formal)))
        if data is None:
            data = path.read_bytes()
        partial = bool(data and not data.endswith(b"\n"))
        lines = data.splitlines()
        events = [json.loads(line) for line in (lines[:-1] if partial else lines) if line.strip()]
        events = [event for event in events if event.get("event") == "epoch"]
        result_path = path.with_name("result.json")
        complete = result_path.is_file() and json.loads(result_path.read_bytes()).get("complete") is True
        runs.append({"path": str(path.relative_to(formal)), "actual_completed_epoch_events": len(events),
                     "last_event": events[-1] if events else None, "complete": complete,
                     "partial_tail_line": partial})
    payload["remote_formal_progress.log"] = (root / "outputs/slurm-405900.log").read_bytes()
    assert guards() == source_hashes
    assert digest(Path(__file__).read_bytes()) == collector_sha
    assert (args.dataset / "meta.json").read_bytes() == meta_bytes
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt = {"observed_utc": stamp, "new_completed_model": str(folder.relative_to(formal)),
               "new_completed_result": result, "actual_server_load": {
                   "seed": args.seed, "label": args.label, "best_sha256": digest(best_bytes),
                   "signature_matches_result": True, "epoch_matches_result": True,
                   "best_validation_ic_matches_result": True, "groups_match_dataset_meta": True,
                   "strict_state_dict_load_passed": True, "all_model_tensors_finite": True,
                   "model_tensor_count": len(state["model"]),
                   "parameter_count": sum(value.numel() for value in model.parameters()),
                   "inference_performed": False}, "torch_version": torch.__version__,
               "collector_sha256": collector_sha, "source_guards_sha256": source_hashes,
               "source_guards_verified_before_and_after": len(source_hashes),
               "dataset_meta_sha256": digest(meta_bytes), "dataset_files_declared_sha256": meta["files_sha256"],
               "dataset_bytes_rehashed_this_check": False, "runs": runs,
               "completed_fixed_models": sum(run["complete"] for run in runs),
               "actual_epoch_events": sum(run["actual_completed_epoch_events"] for run in runs),
               "artifact_sha256": {name: digest(data) for name, data in payload.items()},
               "squeue": subprocess.check_output(["squeue", "-j", "405900,405950", "-h", "-o", "%i %T %M %R"], text=True),
               "test_or_forward_results_read": False, "full_PLAN_complete": False}
    payload[f"formal_completed_model_receipt_{stamp}.json"] = json.dumps(receipt, indent=2).encode()
    output = args.output_dir / f"gru_completed_snapshot_{stamp}.tar.gz"
    with tarfile.open(output, "x:gz") as archive:
        for name, data in payload.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    print(json.dumps({"archive": output.name, "bytes": output.stat().st_size,
                      "sha256": digest(output.read_bytes()), "completed_models": receipt["completed_fixed_models"],
                      "actual_epoch_events": receipt["actual_epoch_events"], "new_model": receipt["new_completed_model"],
                      "guards": len(source_hashes), "squeue": receipt["squeue"]}))


if __name__ == "__main__":
    main()

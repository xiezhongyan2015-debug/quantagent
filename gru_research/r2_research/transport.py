"""Copy the exact actual R2 small causal materials; never original large archives.

This does not create causal approval. It only preserves reviewed bytes and an
explicit original-identity→relative-path mapping for an eventual UI upload.
"""
import argparse
import json
from pathlib import Path
from .protocol import require, sha
from .causal_gate import MAX_SMALL_BYTES


def prepare(receipt_path, expected_sha256, output):
    path, output = Path(receipt_path), Path(output)
    blob = path.read_bytes()
    require(sha(blob) == expected_sha256, "root-reviewed actual causal receipt SHA differs")
    doc = json.loads(blob)
    refs = doc["evidence_artifacts"]
    require(refs and len({r["path"] for r in refs}) == len(refs), "invalid closed evidence roster")
    output.mkdir(parents=True, exist_ok=False)
    receipt_relative = "causal_receipt_original.json"
    with (output / receipt_relative).open("xb") as handle:
        handle.write(blob)
    files = {}
    for index, ref in enumerate(refs):
        p = Path(ref["path"])
        require(p.is_file() and p.stat().st_size <= MAX_SMALL_BYTES, "causal material missing or large; no archive copying")
        data = p.read_bytes()
        require(sha(data) == ref["sha256"] and len(data) == ref["bytes"], "actual original small material changed")
        relative = f"materials/{index:04d}_{ref['sha256'][:16]}.original"
        destination = output / relative
        destination.parent.mkdir(exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(data)
        files[ref["path"]] = {"relative_path": relative, "sha256": ref["sha256"], "bytes": len(data)}
    manifest = {"schema_version": 1, "closed_material_mapping": True,
                "receipt": {"original_identity_path": str(path), "relative_path": receipt_relative,
                            "sha256": expected_sha256, "bytes": len(blob)}, "files": files,
                "large_original_archives_copied_or_rehashed": False,
                "original_receipt_rewritten": False, "causal_permission_created": False}
    target = output / "evidence_manifest.json"
    with target.open("x") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return {"evidence_directory": str(output), "manifest_sha256": sha(target.read_bytes()),
            "small_material_count": len(files), "actual_training_started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-receipt-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.receipt, args.expected_receipt_sha256, args.output)))


if __name__ == "__main__":
    main()

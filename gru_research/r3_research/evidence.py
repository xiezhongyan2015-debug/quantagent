"""Bounded small-material verification; no Torch, arrays, or checkpoint imports."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .protocol import (BASELINE_SHA256, PROTOCOL_SHA256, REGISTRATION_SHA256,
                       R2_BINDING, SEEDS, FOLDS, canonical, require, sha)

SOURCES = tuple("gru_research/r3_research/" + name for name in
                ("__init__.py", "protocol.py", "protocol_v1.json", "evidence.py", "runner.py"))
SMALL_PER_FILE = 8 * 1024 * 1024
SMALL_TOTAL = 64 * 1024 * 1024
BASELINE_TOTAL = 16 * 1024 * 1024
BASELINE_NAMES = ("identity.json", "result.json", "outer_attempt.json", "outer_result.json",
                  "epochs.jsonl", "best_inner_summary.json", "best_inner_sections.json")
REMOTE_STAT_BASIS_SHA256 = "9ea51de4249289a8396e21c7f66059400ed275499e9f9e69476267c04152a803"
DATA_STAT_NAMES = ("X.npy", "labels.npy", "members.npy", "hour_complete.npy", "industry.npy",
                   "times.npy", "earliest_execution_times.npy", "label_end_times.npy")


def stat(path):
    p = Path(path)
    s = p.stat()
    require(p.is_file(), "small source is not a regular file")
    return {"path": str(p), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns,
            "inode": s.st_ino, "device": s.st_dev}


def child(root, relative):
    require(isinstance(relative, str), "relative material path required")
    p, root = Path(relative), Path(root).resolve()
    require(not p.is_absolute() and p.parts and ".." not in p.parts, "unsafe relative material path")
    actual = (root / p).resolve()
    require(root in actual.parents, "material escaped its explicit root")
    return actual


class SmallReader:
    def __init__(self):
        self.cache, self.refs, self.observations, self.bytes = {}, {}, [], 0

    def read(self, path, expected, size=None, cache=True):
        path = Path(path).resolve()
        require(isinstance(expected, str) and len(expected) == 64
                and all(c in "0123456789abcdef" for c in expected), "actual out-of-band SHA required")
        if cache and str(path) in self.cache:
            ref = self.refs[str(path)]
            require(ref["sha256"] == expected and (size is None or ref["bytes"] == size), "cache identity alias")
            require(stat(path) == ref["STAT_after"], "cached small source STAT changed")
            return self.cache[str(path)]
        before = stat(path)
        n = before["bytes"]
        require(0 <= n <= SMALL_PER_FILE and self.bytes + n <= SMALL_TOTAL, "small-file physical-read budget exceeded")
        require(size is None or type(size) is int and size == n, "pre-read expected size differs")
        chunks, consumed = [], 0
        with path.open("rb") as stream:
            while consumed < n:
                part = stream.read(min(65536, n - consumed))
                require(part, "short small-file read")
                chunks.append(part)
                consumed += len(part)
                self.bytes += len(part)
            require(stream.read(1) == b"", "small source grew; EOF not verified")
        blob, after = b"".join(chunks), stat(path)
        require(before == after and sha(blob) == expected, "small source STAT/SHA changed")
        ref = {"path": str(path), "bytes": n, "sha256": expected, "STAT_before": before,
               "STAT_after": after, "EOF_verified": True}
        self.observations.append(ref)
        if cache:
            self.cache[str(path)], self.refs[str(path)] = blob, ref
        return blob

    def json(self, path, expected, size=None):
        return json.loads(self.read(path, expected, size))

    def unchanged_stats(self):
        require(all(stat(path) == ref["STAT_after"] for path, ref in self.refs.items()),
                "a cached source/evidence/baseline STAT changed")


def source_guard(reader, root, manifest, expected, protocol, final=False):
    raw = reader.read(manifest, expected, cache=not final)
    doc = json.loads(raw)
    require(doc.get("schema_version") == 1 and doc.get("protocol_sha256") == PROTOCOL_SHA256
            and set(doc.get("files_sha256", {})) == set(SOURCES), "exact new R3 runtime seal required")
    wanted = {**protocol["original27_source_sha256"], **doc["files_sha256"]}
    require(len(protocol["original27_source_sha256"]) == 27, "original27 source map incomplete")
    for relative, h in wanted.items():
        reader.read(child(root, relative), h, cache=not final)
    return {"manifest_sha256": expected, "files_sha256": doc["files_sha256"],
            "original27_source_sha256": protocol["original27_source_sha256"]}


def baseline_roster(reader, path, root):
    doc = reader.json(path, BASELINE_SHA256)
    require(doc.get("original_source_root") == str(Path(root).resolve())
            and doc.get("original_run_directory") == str(Path(root).resolve() / "outputs/r2_lr_chrono_v1_20261002"),
            "baseline original same-server root/run differs")
    wanted = {(f["id"], s) for f in FOLDS for s in SEEDS}
    rows = doc.get("baseline_control_pairs")
    require(isinstance(rows, list) and len(rows) == 15
            and {(r.get("fold"), r.get("seed")) for r in rows} == wanted, "exact baseline15 required")
    paths, size = [], 0
    for row in rows:
        require(row.get("arm") == "control" and row.get("training_label") == 3 and row.get("variant") == "pv"
                and set(row.get("products", {})) == set(BASELINE_NAMES) | {"best_inner.pt"}, "wrong baseline role")
        directory = Path(doc["original_run_directory"]) / "diagnostic" / row["fold"] / "control" / ("seed_%s" % row["seed"])
        for name, ref in row["products"].items():
            require(ref["path"] == str(directory / name) and type(ref["bytes"]) is int
                    and ref["bytes"] > 0 and isinstance(ref["sha256"], str) and len(ref["sha256"]) == 64,
                    "closed baseline product identity differs")
            if name == "best_inner.pt":
                continue  # Descriptor only. Never STAT/read/load the baseline checkpoint.
            current = stat(ref["path"])
            require(current["bytes"] == ref["bytes"] <= SMALL_PER_FILE, "baseline small-file preSTAT size differs")
            paths.append(ref["path"])
            size += ref["bytes"]
    require(len(paths) == len(set(paths)) == 105 and size <= BASELINE_TOTAL,
            "baseline105 unique small products exceed budget")
    return doc


def read_baseline_once(reader, doc):
    """Only the explicit105 original small products; no CP reads or new forward."""
    output = {}
    for row in doc["baseline_control_pairs"]:
        documents, hashes = {}, {}
        for name in BASELINE_NAMES:
            ref = row["products"][name]
            blob = reader.read(ref["path"], ref["sha256"], ref["bytes"])
            documents[name] = [json.loads(line) for line in blob.splitlines()] if name == "epochs.jsonl" else json.loads(blob)
            hashes[name] = ref["sha256"]
        require(documents["result.json"]["best_inner_checkpoint_sha256"]
                == row["products"]["best_inner.pt"]["sha256"], "baseline CP descriptor differs")
        documents["_hashes"] = hashes
        output[row["fold"], row["seed"]] = documents
    return output


def execution_gate(reader, args, protocol, seal):
    """A new explicit LABEL4 permission is mandatory; old LABEL3 receipt is insufficient."""
    original = protocol["dataset_and_source_inheritance"]
    registration = reader.json(args.registration, REGISTRATION_SHA256)
    require(registration.get("new_runtime_preparation_allowed") is True
            and registration.get("status") == "EXACT_PROPOSAL_REGISTERED_IMPLEMENTATION_PREPARATION_ALLOWED_EXECUTION_PENDING"
            and registration["original27_source_hashes_unchanged"] == protocol["original27_source_sha256"], "root registration differs")
    meta = reader.json(Path(args.dataset) / "meta.json", original["meta_sha256"])
    require(sha(canonical(meta)) == original["canonical_metadata_fingerprint"], "actual original metadata fingerprint differs")
    directory = Path(args.data_reuse_evidence_directory)
    mapping = reader.json(directory / "evidence_manifest.json", args.expected_data_reuse_evidence_manifest_sha256)
    require(mapping.get("schema_version") == 1 and mapping.get("closed_material_mapping") is True, "closed label4 evidence transport map required")
    entry = mapping["receipt"]
    require(entry["sha256"] == args.expected_data_reuse_evidence_sha256, "data evidence receipt alias")
    data = reader.json(child(directory, entry["relative_path"]), entry["sha256"], entry["bytes"])
    require(data.get("schema_version") == "R3_LABEL4_NUMERICAL_DATA_REUSE_EVIDENCE_V1"
            and data.get("status") == "PREPARED_LABEL4_NUMERICAL_DATA_REUSE_EVIDENCE_NOT_EXECUTION_PERMISSION"
            and data.get("r3_protocol_sha256") == PROTOCOL_SHA256, "wrong label4-specific data evidence")
    require(data.get("training_label") == 4 and data.get("loss_index") == 3 and data.get("variant") == "pv"
            and data.get("original_meta_sha256") == original["meta_sha256"]
            and data.get("original_metadata_fingerprint") == original["canonical_metadata_fingerprint"]
            and data.get("original_source27_sha256") == protocol["original27_source_sha256"]
            and data.get("original_files_sha256") == meta["files_sha256"], "label4 data/source/clock/maturity identities differ")
    require(data.get("label4_training_permission") is False and data.get("evidence_is_new_numeric_run") is False
            and data.get("historical_realized_inputs_only") is True
            and data.get("full_end_to_end_causality_proven") is False
            and data.get("vendor_arrival_PIT_QFQ_certified") is False, "numerical inheritance scope was broadened")
    references = data.get("actual_evidence_refs")
    require(isinstance(references, list) and references and len(references) <= 32, "closed actual small evidence references missing")
    require(len({r["path"] for r in references}) == len(references)
            and set(mapping.get("files", {})) == {r["path"] for r in references}, "evidence map missing/extra identity")
    relative = [entry["relative_path"]] + [x["relative_path"] for x in mapping["files"].values()]
    require(len(set(relative)) == len(relative), "distinct proof identities aliased")
    for ref in references:
        transported = mapping["files"][ref["path"]]
        require(all(transported[k] == ref[k] for k in ("bytes", "sha256")), "evidence transport changed identity")
        reader.read(child(directory, transported["relative_path"]), ref["sha256"], ref["bytes"])
    permission = reader.json(args.permission, args.expected_permission_sha256)
    require(permission.get("schema_version") == "ROOT_R3_PV_LABEL4_QUALIFIED_RESEARCH_PERMISSION_V1"
            and permission.get("execution_allowed") is True and permission.get("root_accepted") is True
            and permission.get("scope") == "PV_ONLY_LABEL4_3FOLDS_5SEEDS_20CAP", "new root LABEL4 permission absent")
    expected = {"protocol_sha256": PROTOCOL_SHA256, "new_source_manifest_sha256": seal["manifest_sha256"],
        "registration_sha256": REGISTRATION_SHA256, "baseline_lineage_sha256": BASELINE_SHA256,
        "data_reuse_evidence_sha256": args.expected_data_reuse_evidence_sha256,
        "data_reuse_evidence_manifest_sha256": args.expected_data_reuse_evidence_manifest_sha256,
        "original_meta_sha256": original["meta_sha256"],
        "original_metadata_fingerprint": original["canonical_metadata_fingerprint"],
        "original_source27_sha256": protocol["original27_source_sha256"],
        "original_source_root": str(args.root.resolve()), "original_dataset_directory": str(args.dataset.resolve()),
        "new_run_directory": str(args.output.resolve()), "training_label": 4, "loss_index": 3, "variant": "pv", "fit_cap": 20}
    require(all(permission.get(k) == v for k, v in expected.items()), "root permission actual source/data/run identity differs")
    require(permission.get("loss_index3_label4_shape_maturity_purge_reviewed") is True
            and permission.get("same_R2_control_development_cohorts_reviewed") is True
            and permission.get("historical_exposure_declared") is True
            and permission.get("virgin_OOS") is False and permission.get("full_PLAN_complete") is False
            and permission.get("allissuer_PIT_arrival_QFQ_clearance") is False, "qualified label4 review or limitations missing")
    require("diagnostics" in permission.get("admitted_stages", []), "diagnostics permission missing")
    pins, basis_ref = permission.get("remote_consumed_input_stat_pins"), permission.get("remote_consumed_input_stat_basis")
    require(isinstance(pins, dict) and set(pins) == set(DATA_STAT_NAMES)
            and isinstance(basis_ref, dict) and basis_ref.get("sha256") == REMOTE_STAT_BASIS_SHA256,
            "actual eight remote input STAT pins and original remote basis required")
    preflight_ref = permission.get("actual_remote_dataset_runtime_preflight")
    require(isinstance(preflight_ref, dict), "actual remote metadata-only preflight reference required")
    preflight = reader.json(preflight_ref["path"], preflight_ref["sha256"], preflight_ref["bytes"])
    require(preflight.get("schema_version") == "R3_LABEL4_ACTUAL_REMOTE_DATASET_STAT_AND_RUNTIME_PREFLIGHT_V1"
            and preflight.get("status") == "PASS_ACTUAL_REMOTE_STAT8_META_RUNTIME_NO_ARRAY_BODY_READ"
            and preflight.get("remote_consumed_input_stat_pins") == pins
            and preflight.get("remote_consumed_input_stat_after") == pins
            and preflight.get("meta", {}).get("sha256") == original["meta_sha256"]
            and preflight.get("whole_array_hashes_remeasured") is False
            and preflight.get("array_header_or_body_reads") == 0, "actual remote preflight identity/scope differs")
    import sys
    runtime_pin = preflight["runtime"]["actual_stat"]
    require(str(Path(sys.executable).resolve()) == runtime_pin["path"]
            and stat(runtime_pin["path"]) == runtime_pin, "actual pinned interpreter STAT changed")
    basis = reader.json(basis_ref["path"], basis_ref["sha256"], basis_ref["bytes"])
    before, after = basis["large_array_stat_guards_before"], basis["large_array_stat_guards_after"]
    require(before == after and set(before) == {Path(n).stem for n in DATA_STAT_NAMES}, "original remote STAT basis differs")
    for name in DATA_STAT_NAMES:
        pin, prior = pins[name], before[Path(name).stem]
        require(set(pin) == {"path", "bytes", "mtime_ns", "inode", "device"}
                and pin["path"] == str(args.dataset.resolve() / name)
                and prior["path"] == pin["path"] and prior.get("whole_file_hashed") is False
                and all(type(pin[k]) is int for k in ("bytes", "mtime_ns", "inode", "device"))
                and all(prior[k] == pin[k] for k in ("bytes", "mtime_ns"))
                and stat(pin["path"]) == pin, "remote realized input STAT differs; no fresh whole-body SHA claim")
    return meta, permission


def stage_admission(reader, args, binding):
    if args.stage == "diagnostics":
        return None
    doc = reader.json(args.stage_admission, args.expected_stage_admission_sha256)
    require(doc.get("schema_version") == "ROOT_ACTUAL_R3_STAGE_ADMISSION_V1"
            and doc.get("execution_allowed") is True and doc.get("root_accepted") is True
            and doc.get("stage") == args.stage and doc.get("run_identity") == binding
            and doc.get("original_run_directory") == str(args.output.resolve()), "actual root stage admission absent")
    require(doc.get("actual_candidate_inner_complete") == 15 and doc.get("actual_candidate_outer_once_complete") == 15
            and doc.get("candidate15_finite_parameters_verified") is True, "complete/finite15 root proof missing")
    ref = doc.get("actual_candidate15_CPU_finite_receipt")
    require(isinstance(ref, dict), "real CPU15 receipt identity required")
    reader.read(ref["path"], ref["sha256"], ref["bytes"])
    if args.stage in ("final", "freeze", "evaluate"):
        require(doc.get("candidate_selected") is True and isinstance(doc.get("selection_sha256"), str)
                and isinstance(doc.get("budget_sha256"), str), "new finals require actual winning decision/budget")
    if args.stage in ("freeze", "evaluate"):
        require(doc.get("actual_final_complete") == 5 and doc.get("new_final5_finite_parameters_verified") is True,
                "all five actual finite final weights required")
        ref = doc.get("actual_final5_CPU_finite_receipt")
        require(isinstance(ref, dict), "actual final5 CPU receipt identity absent")
        reader.read(ref["path"], ref["sha256"], ref["bytes"])
    return doc

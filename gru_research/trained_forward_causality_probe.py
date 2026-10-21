"""Prepared-only by default; one real assembled-X/top5 forward probe on CPU.

Explicit execution tests only the frozen PanelDataset/model layer. It cannot
certify minute aggregation, all-member normalization, issuer PIT arrival or a
new provider. Original arrays/checkpoints are never written. No metric/IC,
optimizer, training, ranking, scheduler or source fetching is called.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import traceback

META_SHA = "2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849"
DATA_FP = "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4"
FREEZE_SHA = "363eb9cb6ccdb613e8f5907c8e7331c550998df9acb708665d64e1e14233484d"
SOURCE_MANIFEST_SHA = "5c1e9d8e147a0178737a2ea850163e26cbfa4d12f99cb7269ec2d100ee200400"
LOCAL_SAMPLE = "2024-06-18T10:30:00+08:00"
ROSTER = [
    ("pv_seed22", "ablations/pv/seed_22/best.pt", "pv", 3, 22),
    ("fixed_label4_seed33", "fixed/label_4/seed_33/best.pt", "full", 4, 33),
    ("fixed_label3_seed33", "fixed/label_3/seed_33/best.pt", "full", 3, 33),
    ("fixed_label3_seed11", "fixed/label_3/seed_11/best.pt", "full", 3, 11),
    ("pv_seed11", "ablations/pv/seed_11/best.pt", "pv", 3, 11),
]


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity(path, max_bytes=20*1024**2):
    p = Path(path)
    require(p.is_file() and p.stat().st_size <= max_bytes, "missing or out-of-scope hash input: " + str(p))
    digest = hashlib.sha256()
    size = 0
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
            size += len(block)
    return {"path": str(p.resolve()), "bytes": size, "sha256": digest.hexdigest()}


def write_json_exclusive(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


class FutureOnlyArray:
    """Virtual future values; every read copies, and original mmap stays readonly."""
    def __init__(self, base, cutoff, role):
        self.base, self.cutoff, self.role = base, int(cutoff), role
        self.shape, self.dtype, self.ndim = base.shape, base.dtype, base.ndim
        self.reads = []

    def _alter(self, values):
        if self.role in ("members", "hour_complete"):
            return np.logical_not(values)
        constant = 999.0 if self.role == "X" else -7331.0
        return np.full_like(values, constant)

    def __getitem__(self, key):
        time_key = key[0] if isinstance(key, tuple) else key
        require(isinstance(time_key, (int, np.integer, slice)), "unsupported time-index form in frozen batch")
        indices = np.arange(self.shape[0], dtype=np.int64)[time_key]
        values = np.array(self.base[key], copy=True)
        future = np.asarray(indices) > self.cutoff
        if np.ndim(indices) == 0:
            if bool(future):
                values = self._alter(values)
        else:
            values[future] = self._alter(values[future])
        flat = np.asarray(indices).reshape(-1)
        self.reads.append({"role": self.role, "minimum_time_index": int(flat.min()) if len(flat) else None,
                           "maximum_time_index": int(flat.max()) if len(flat) else None,
                           "future_time_rows_requested": int(np.count_nonzero(future))})
        return values

    def __array__(self, dtype=None, copy=None):
        # Only the small bool completeness matrix is materialized for causal
        # prefix sums. Materializing entire future X or labels is prohibited.
        require(self.role == "hour_complete", "whole-array materialization forbidden: " + self.role)
        values = np.array(self.base, copy=True)
        values[self.cutoff+1:] = np.logical_not(values[self.cutoff+1:])
        return values.astype(dtype, copy=False) if dtype is not None else values

    def __setitem__(self, key, value):
        raise TypeError("read-only future proxy")


def stat_guard(dataset):
    result = {}
    for name in ("X", "labels", "members", "hour_complete", "industry", "times", "earliest_execution_times", "label_end_times"):
        p = dataset / (name + ".npy")
        s = p.stat()
        result[name] = {"path": str(p), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns,
                        "whole_file_hashed": False}
    return result


def run(args):
    global np
    project, dataset, formal, output = [Path(v).resolve() for v in (args.project_root, args.dataset, args.formal_root, args.output)]
    require(not output.exists(), "append-only attempt directory already exists")
    require(output != project and not output.is_relative_to(dataset) and not output.is_relative_to(formal), "output overlaps original data/formal namespace")
    manifest_identity = identity(args.manifest, max_bytes=200000)
    require(manifest_identity["sha256"] == args.manifest_sha, "prepared manifest hash differs")
    manifest = json.loads(Path(args.manifest).read_bytes())
    require(manifest["sample_local_time"] == LOCAL_SAMPLE, "fixed date/time cannot be selected adaptively")
    expected_roster = [{"id": name, "relative_path": relative, "variant": variant, "label": label, "seed": seed} for name,relative,variant,label,seed in ROSTER]
    require([{k: row[k] for k in expected_roster[0]} for row in manifest["checkpoints"]] == expected_roster, "five preselected checkpoint roster differs")
    require(identity(__file__)["sha256"] == manifest["program_sha256"], "actual executing program differs from prepared source")
    require(identity(dataset / "meta.json")["sha256"] == META_SHA, "frozen metadata bytes differ")
    meta = json.loads((dataset / "meta.json").read_bytes())
    require(hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(",", ":")).encode()).hexdigest() == DATA_FP, "metadata fingerprint differs")
    require(identity(formal / "freeze.json")["sha256"] == FREEZE_SHA, "original40 freeze bytes differ")
    freeze = json.loads((formal / "freeze.json").read_bytes())
    config = json.loads((project / "gru_research/config.json").read_bytes())
    require(freeze["dataset_fingerprint"] == DATA_FP and freeze["config"] == config, "freeze dataset/config mismatch")
    require(config["sequence_length"] == 80 and config["min_stocks"] == 100 and config["dtype"] == "float32", "frozen window/cohort/dtype policy differs")
    # Caller binds the actual public guard-document path. It may be carried in
    # the isolated preparation capsule; no remote basename/path inference.
    source_manifest_path = Path(args.source_manifest).resolve()
    require(identity(source_manifest_path)["sha256"] == SOURCE_MANIFEST_SHA, "original19 manifest differs")
    original = json.loads(source_manifest_path.read_bytes())
    source19 = dict(original["source_guards_sha256"])
    source19["gru_research/prospective_features.py"] = original["files_sha256"]["gru_research/prospective_features.py"]
    require(source19 == manifest["source19_sha256"] and len(source19) == 19, "prepared source19 map differs")
    guard_paths = {str(project / rel): digest for rel,digest in source19.items()}
    guard_paths[str(project / "gru_research/__init__.py")] = manifest["package_init_sha256"]
    guard_paths.update({str(dataset/"meta.json"): META_SHA, str(formal/"freeze.json"): FREEZE_SHA,
                        str(source_manifest_path): SOURCE_MANIFEST_SHA})
    for name in ("symbols.json", "times.npy", "earliest_execution_times.npy", "label_end_times.npy"):
        guard_paths[str(dataset/name)] = meta["files_sha256"][name]
    for entry in manifest["checkpoints"]:
        p = formal / entry["relative_path"]
        matches = [value for key,value in freeze["checkpoint_hashes"].items() if key.endswith("/"+entry["relative_path"])]
        require(matches == [entry["sha256"]], "checkpoint does not match exact original freeze identity")
        require(p.stat().st_size == entry["bytes"], "checkpoint size differs")
        guard_paths[str(p)] = entry["sha256"]
    require(sum(x["bytes"] for x in manifest["checkpoints"]) <= 20*1024**2, "five-checkpoint hash budget exceeded")
    before = {path: identity(path) for path in guard_paths}
    require(all(before[p]["sha256"] == digest for p,digest in guard_paths.items()), "actual source/checkpoint hash mismatch")
    array_before = stat_guard(dataset)
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version":1, "status":"RUNNING_SINGLE_ASSEMBLED_X_MODEL_LAYER_AUDIT",
              "started_utc":datetime.now(timezone.utc).isoformat(), "manifest":manifest_identity,
              "executing_program":identity(__file__), "fixed_sample_local":LOCAL_SAMPLE,
              "original_small_source_checkpoint_guards_before":before, "large_array_stat_guards_before":array_before,
              "models":[], "accepted_training_fit_count_changed":0, "full_PLAN_complete":False,
              "layer_scope":"Original assembled X, PanelDataset past80 batch/eligibility and five frozen FP32 model forwards only",
              "not_proven":["240-minute cleaning and upstream future-quality handling", "all122fields recomputation and full-member normalization", "precise issuer PIT release/feed arrival and provider revisions", "full prospective60-session/generalization/performance"]}
    error = None
    try:
        # A separate CPU process cannot claim or use the ongoing training GPU.
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        sys.dont_write_bytecode = True
        import numpy as imported_numpy
        np = imported_numpy
        import torch
        require(not torch.cuda.is_available(), "CPU-only probe unexpectedly exposes CUDA")
        torch.set_num_threads(1)
        torch.use_deterministic_algorithms(True)
        require(not any(name == "gru_research" or name.startswith("gru_research.") for name in sys.modules), "foreign/preloaded research package cannot be reused")
        sys.path.insert(0, str(project))
        train = importlib.import_module("gru_research.train")
        package = importlib.import_module("gru_research")
        imported = {"train":train, "model":importlib.import_module("gru_research.model"),
                    "evaluate":importlib.import_module("gru_research.evaluate"), "package":package}
        expected = {"train":project/"gru_research/train.py", "model":project/"gru_research/model.py",
                    "evaluate":project/"gru_research/evaluate.py", "package":project/"gru_research/__init__.py"}
        for name,module in imported.items():
            require(Path(module.__file__).resolve() == expected[name], "actual import checkout mismatch: "+name)
            require(identity(module.__file__)["sha256"] == guard_paths[str(expected[name])], "actual imported source hash mismatch")
        train.seed_everything(20261002)
        panel = train.PanelDataset(dataset)
        require(panel.fingerprint == DATA_FP and panel.X.dtype == np.float32, "actual panel/dtype mismatch")
        require((panel.T,panel.U,panel.F)==(8476,1178,122),"frozen actual panel dimensions differ")
        sample_ns = int(datetime.fromisoformat(LOCAL_SAMPLE).timestamp())*10**9
        matches = np.flatnonzero(panel.times == sample_ns)
        require(len(matches) == 1, "fixed real sample time missing/duplicated; no fallback")
        t = int(matches[0])
        require(79 <= t < panel.T-1, "no complete past window or future row")
        current_mask = panel.eligible_mask(t, 80, require_labels=False).copy()
        require(int(current_mask.sum()) >= 100, "fixed sample has fewer than100 genuinely eligible members; no adaptive resampling")
        for role in ("X", "labels", "members", "hour_complete", "industry"):
            require(not getattr(panel,role).flags.writeable, "original mmap is not readonly")
        original_batch = panel.batch([t], "cpu", config, require_labels=False)
        virtual = copy.copy(panel)
        proxies = {role:FutureOnlyArray(getattr(panel,role),t,role) for role in ("X", "labels", "members", "hour_complete")}
        for role,proxy in proxies.items():
            setattr(virtual,role,proxy)
        nonvacuity = {}
        for role,proxy in proxies.items():
            key = (t+1,0,0) if role in ("X","labels") else (t+1,0)
            old_value = np.asarray(getattr(panel,role)[key])
            new_value = np.asarray(proxy[key])
            require(not np.array_equal(old_value,new_value,equal_nan=True), "future mutation vacuous: "+role)
            nonvacuity[role] = True
        # The original class computes a whole boolean cumulative matrix. Rebuild
        # that same causal prefix on the virtual future completeness changes;
        # merely retaining the original cache would make the test too weak.
        altered_complete = np.asarray(proxies["hour_complete"], dtype=np.bool_)
        cumulative = np.cumsum(~altered_complete,axis=0,dtype=np.int32)
        virtual._incomplete_prefix = np.concatenate([np.zeros((1,panel.U),dtype=np.int32),cumulative])
        virtual._eligibility_cache = {}
        virtual._split_reports = {}
        require(np.array_equal(virtual._incomplete_prefix[:t+2],panel._incomplete_prefix[:t+2]), "future completeness changed a past prefix sum")
        for proxy in proxies.values():
            proxy.reads.clear()
        virtual_mask = virtual.eligible_mask(t,80,require_labels=False)
        require(np.array_equal(current_mask,virtual_mask), "real current member/80hour scoring mask changed")
        future_batch = virtual.batch([t],"cpu",config,require_labels=False)
        require(original_batch[-1] == future_batch[-1], "actual current cohort counts changed")
        for i in range(4):
            # torch.equal treats equal NaNs as unequal. Comparing dtype/shape
            # and C-order bytes checks original missing payloads exactly too.
            require(original_batch[i].shape == future_batch[i].shape and original_batch[i].dtype == future_batch[i].dtype
                    and original_batch[i].numpy().tobytes() == future_batch[i].numpy().tobytes(),
                    "actual batch tensor including missingness changed at index"+str(i))
        require(all(row["future_time_rows_requested"] == 0 for proxy in proxies.values() for row in proxy.reads), "frozen batch read future input/label/member rows")
        input_path = output/"actual_input_tensor_equality.npz"
        with input_path.open("xb") as stream:
            np.savez(stream, signal_time=np.array([sample_ns],dtype=np.int64),time_index=np.array([t]),
                     mask_before=current_mask,mask_after=virtual_mask,
                     industry_before=original_batch[1].numpy(),industry_after=future_batch[1].numpy(),
                     slots_before=original_batch[2].numpy(),slots_after=future_batch[2].numpy())
        result.update({"actual_time_index":t,"current_real_eligible_members":int(current_mask.sum()),
                       "original_panel_shape":list(panel.X.shape),"past_window_start_index":t+1-80,
                       "input_tensors_and_missingness_bitwise_equal":True,
                       "current_member_complete80_scoring_eligibility_bitwise_equal":True,
                       "virtual_complete_prefix_recomputed_and_past_identical":True,
                       "future_mutations_demonstrably_nonvacuous":nonvacuity,
                       "batch_read_traces":{role:proxy.reads for role,proxy in proxies.items()},
                       "actual_input_equality_artifact":identity(input_path,max_bytes=60*1024**2),
                       "actual_X_tensor_shape":list(original_batch[0].shape),
                       "actual_X_tensor_dtype":str(original_batch[0].dtype),
                       "actual_X_tensor_bytes_compared":original_batch[0].numel()*original_batch[0].element_size(),
                       "X_before_tensor_sha256":hashlib.sha256(original_batch[0].numpy().tobytes()).hexdigest(),
                       "X_after_tensor_sha256":hashlib.sha256(future_batch[0].numpy().tobytes()).hexdigest(),
                       "large_input_X_tensor_bytes_saved":False,
                       "imports":{name:identity(module.__file__) for name,module in imported.items()},
                       "runtime":{"python":sys.version.split()[0],"numpy":np.__version__,"torch":torch.__version__,
                                  "device":"cpu","FP32":True,"deterministic_algorithms":True,"torch_threads":1,
                                  "CUDA_hidden_for_this_process":True}})
        for entry in manifest["checkpoints"]:
            state = train.checkpoint_load(formal/entry["relative_path"],"cpu")
            signature = {"dataset_fingerprint":DATA_FP,"config":config,"seed":entry["seed"],
                         "label":entry["label"],"variant":entry["variant"]}
            require(state["signature"] == signature, "checkpoint signature differs")
            require(state["groups"] == panel.groups and state["n_industries"] == panel.n_industries, "checkpoint shape/group metadata differs")
            require(all(torch.is_tensor(v) and v.dtype==torch.float32 and v.device.type=="cpu" and bool(torch.isfinite(v).all()) for v in state["model"].values()), "checkpoint model tensors not finite CPU FP32")
            model = train.PlanGRU(state["groups"],state["n_industries"],entry["variant"])
            model.load_state_dict(state["model"],strict=True)
            require(len(state["model"])==26 and sum(p.numel() for p in model.parameters())==202481,"frozen model state/parameter dimensions differ")
            model.eval()
            require(not model.training, "model is not in eval mode")
            with torch.inference_mode():
                score_before = model(*original_batch[:3]).cpu()
                score_after = model(*future_batch[:3]).cpu()
            require(score_before.dtype==score_after.dtype==torch.float32 and score_before.shape==(int(current_mask.sum()),), "score dtype/shape differs")
            require(bool(torch.isfinite(score_before).all()) and bool(torch.isfinite(score_after).all()), "nonfinite predictions")
            require(torch.equal(score_before,score_after), "trained prediction changed under isolated future perturbation")
            score_path = output/(entry["id"]+"_forward_equality.npz")
            with score_path.open("xb") as stream:
                np.savez(stream,score_before=score_before.numpy(),score_after=score_after.numpy())
            result["models"].append({"id":entry["id"],"checkpoint_sha256":entry["sha256"],"checkpoint_bytes":entry["bytes"],
                                      "variant":entry["variant"],"label":entry["label"],"seed":entry["seed"],
                                      "strict_load":True,"FP32_deterministic_eval":True,"predictions_bitwise_equal":True,
                                      "absolute_maximum_prediction_difference":0.0,
                                      "prediction_count":int(current_mask.sum()),"score_equality_artifact":identity(score_path)})
            del model,state,score_before,score_after
        require(len(result["models"])==5,"partial top5 audit cannot pass")
        result["status"] = "PASS_ONE_REAL_2024_WINDOW_ASSEMBLED_X_LAYER_AND_ALL_FIVE_TRAINED_FP32_FORWARDS"
    except Exception as exc:
        error = exc
        result["status"] = "FAIL_OR_PARTIAL_NO_CAUSALITY_CLEARANCE_NO_AUTOMATIC_RETRY"
        result["failure"] = {"type":type(exc).__name__,"reason":str(exc)}
        with (output/"actual_failure_traceback.txt").open("x") as stream:
            stream.write(traceback.format_exc())
    finally:
        try:
            after = {path:identity(path) for path in guard_paths}
            array_after = stat_guard(dataset)
            result["original_small_source_checkpoint_guards_after"] = after
            result["large_array_stat_guards_after"] = array_after
            require(after==before and array_after==array_before,"original bytes/stat changed during forward audit")
            result["original_guards_unchanged_before_after"] = True
        except Exception as exc:
            error = error or exc
            result["status"] = "FAIL_OR_PARTIAL_ORIGINAL_IDENTITY_GUARD_NO_CLEARANCE"
            result["guard_failure"] = {"type":type(exc).__name__,"reason":str(exc)}
        result["ended_utc"] = datetime.now(timezone.utc).isoformat()
        result["large_X_or_original_minute_archives_rehashed"] = False
        result["IC_RankIC_trading_returns_checkpoint_metrics_inspected_or_computed"] = False
        result["optimizer_training_test_panel_scoring_model_selection_or_server_job_management_called"] = False
        write_json_exclusive(output/"actual_forward_causality_receipt_v1.json",result)
    print(json.dumps({"receipt":identity(output/"actual_forward_causality_receipt_v1.json"),
                      "status":result["status"],"completed_models":len(result["models"]),
                      "new_training_fits":0,"full_PLAN_complete":False},ensure_ascii=False))
    if error is not None:
        raise RuntimeError("single actual audit failed; preserve output and do not automatically retry") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--project-root")
    parser.add_argument("--dataset")
    parser.add_argument("--formal-root")
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha")
    parser.add_argument("--source-manifest")
    parser.add_argument("--output")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status":"PREPARED_ONLY_NOT_EXECUTED","sample_local":LOCAL_SAMPLE,
                          "fixed_models":len(ROSTER),"Torch_or_data_imported":False,
                          "new_training_fits":0,"full_PLAN_complete":False}))
        return
    require(all(getattr(args,key) for key in ("project_root","dataset","formal_root","manifest","manifest_sha","source_manifest","output")), "explicit seven runtime bindings required")
    require(re.fullmatch("[a-f0-9]{64}",args.manifest_sha) is not None,"invalid expected manifest SHA")
    run(args)


if __name__ == "__main__":
    main()

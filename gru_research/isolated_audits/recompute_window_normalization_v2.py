"""Stream one real past80 raw-feature normalization window and join Top5 proof.

No original minute/slow formula regeneration, Torch, scores, IC or job control.
Existing sources/V1/R2/original arrays are readonly. Missing/exact-bit mismatch
rejects without tolerance or adaptive time/model changes.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "gru_research/artifacts"
TOP5 = ART / "top5_actual_forward_causality_20261002T113238Z"
SAMPLE = "2024-06-18T10:30:00+08:00"
META_SHA = "2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849"
META_FP = "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4"
SOURCE_DOC_SHA = "5c1e9d8e147a0178737a2ea850163e26cbfa4d12f99cb7269ec2d100ee200400"
HELPER_SHA = "e34f82f92763a67fac857e84a63df85bdaef861e73213a6b803b8919e9e6ec3f"
TOP5_SHA = "9ea51de4249289a8396e21c7f66059400ed275499e9f9e69476267c04152a803"
TOP5_ROOT_SHA = "fcab6c2f744dec28818d8d0b27cabb21dd65580954905910699dda4725bdeb9b"


def require(test, reason):
    if not test:
        raise ValueError(reason)


def ident(p, cap=20*1024**2):
    p = Path(p)
    require(p.is_file() and p.stat().st_size <= cap, "small identity missing/over budget: " + str(p))
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda:stream.read(1024**2),b""):
            digest.update(block)
    return {"path":str(p),"bytes":p.stat().st_size,"sha256":digest.hexdigest()}


def stat(p):
    p = Path(p); s = p.stat()
    return {"path":str(p),"bytes":s.st_size,"mtime_ns":s.st_mtime_ns,"whole_file_hashed_by_this_audit":False}


def write_json(p, obj):
    with Path(p).open("x") as f:
        f.write(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+"\n")


def body_hash(array):
    require(array.flags.c_contiguous, "body hash requires explicit original batch C order")
    return hashlib.sha256(memoryview(array).cast("B")).hexdigest()


def fitted_hash(stats):
    digest = hashlib.sha256()
    for name in ("count","lo","hi","mean","std"):
        a = stats[name]
        digest.update(name.encode()+str(a.dtype).encode()+str(a.shape).encode())
        digest.update(memoryview(a).cast("B"))
    return digest.hexdigest()


class WindowProxy:
    """Read only explicitly bounded rows, changing strictly future rows."""
    def __init__(self, base, first, cutoff, role, future=False):
        self.base,self.first,self.cutoff,self.role,self.future = base,first,cutoff,role,future
        self.reads = []

    def row(self, index):
        require(self.first <= index <= self.cutoff+1, "past80/future witness read bound exceeded")
        result = np.array(self.base[index],copy=True)
        changed = self.future and index > self.cutoff
        if changed:
            result = np.logical_not(result) if self.role=="members" else np.full_like(result,625000.0)
        self.reads.append({"index":int(index),"bytes":int(result.nbytes),"future_modified":bool(changed)})
        return result


def run(output):
    global np
    output = Path(output).resolve()
    allowed = ROOT/"gru_research/trading_data/window_normalization_audit_v2_20261002"
    require(output.is_relative_to(allowed) and not output.exists(),"new append-only V2 namespace required")
    output.mkdir(parents=True,exist_ok=False)
    result = {"schema_version":2,"status":"RUNNING","fixed_sample_local":SAMPLE,
              "started_utc":datetime.now(timezone.utc).isoformat(),"new_training_fits":0,"full_PLAN_complete":False,
              "scope":"One real past80 retained-generated-H24/slow98, historical-full-member normalization, original batch body and inherited five trained forward invariance",
              "not_proven":["Original240minute cleaning or H24/slow98 formulas recalculated from underlying source",
                             "issuer publication/feed-arrival or vendor revision PIT",
                             "all-time/all-source/full-chain causality or truefuture60 generalization"],
              "case_results":[],"Torch_model_forward_IC_scores_training_network_or_server_actions":False}
    before,large_before = {},{}
    error = None
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0,str(ROOT/"gru_research/.deps"))
        import numpy as numpy
        np = numpy
        dataset = ART/"dataset"
        doc_path = ART/"prospective_features_addon_manifest.json"
        require(ident(doc_path)["sha256"]==SOURCE_DOC_SHA,"old source19 document mismatch")
        doc = json.loads(doc_path.read_bytes())
        source19 = dict(doc["source_guards_sha256"])
        source19["gru_research/prospective_features.py"] = doc["files_sha256"]["gru_research/prospective_features.py"]
        require(len(source19)==19,"original19 roster mismatch")
        small = {ROOT/p:sha for p,sha in source19.items()}
        small[doc_path] = SOURCE_DOC_SHA
        small[dataset/"meta.json"] = META_SHA
        meta = json.loads((dataset/"meta.json").read_bytes())
        require(hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(",",":")).encode()).hexdigest()==META_FP,"canonical frozen meta mismatch")
        for name in ("times.npy","symbols.json","feature_names.json","minute_feature_metadata.json","slow_manifest.json","slow_factor_selection.json"):
            small[dataset/name] = meta["files_sha256"][name]
        helper_path = ROOT/"gru_research/isolated_audits/recompute_fixed_normalization_v1.py"
        small[helper_path] = HELPER_SHA
        top5_path = TOP5/"audit_output/actual_forward_causality_receipt_v1.json"
        root_proof_path = TOP5/"root_independent_actual_forward_acceptance_v1.json"
        small[top5_path] = TOP5_SHA; small[root_proof_path] = TOP5_ROOT_SHA
        v1_path = ROOT/"gru_research/trading_data/fixed_normalization_audit_20261002/attempt1/actual_normalization_causality_receipt_v1.json"
        small[v1_path] = "311b4f3358a534b57360300db478466506086fd839e0dcca1a7eb2bbe774a533"
        top5 = json.loads(top5_path.read_bytes())
        mask_path = TOP5/"audit_output/actual_input_tensor_equality.npz"
        small[mask_path] = top5["actual_input_equality_artifact"]["sha256"]
        for p,sha in small.items():
            before[str(p)] = ident(p)
            require(before[str(p)]["sha256"]==sha,"actual small source/proof identity mismatch: "+str(p))
        root_proof = json.loads(root_proof_path.read_bytes())
        require(root_proof["actual_runtime_receipt"]["sha256"]==TOP5_SHA and root_proof["job_exit"]==0
                and root_proof["original_guards_before_after_exact"] is True,"inherited actual trained-forward root acceptance mismatch")
        require(len(root_proof["saved_NPZ_inputs_and_all5_scores_independently_compared"])==5
                and all(x["actual_saved_scores_finite_FP32_bitwise_equal"] for x in root_proof["saved_NPZ_inputs_and_all5_scores_independently_compared"]),"five actual inherited forwards not accepted")
        # Import only the sealed new V1 helper definitions, never its CLI/run.
        spec = importlib.util.spec_from_file_location("sealed_normalization_helper_v1",helper_path)
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper); helper.np = np
        normalize,function_proof = helper.extract_normalizer(ROOT/"gru_research/assemble.py")
        with (output/"original_normalize_exact_function.txt").open("x") as stream:
            stream.write(function_proof.pop("exact_function_text")+"\n")
        minute = json.loads((dataset/"minute_feature_metadata.json").read_bytes())
        slow = json.loads((dataset/"slow_manifest.json").read_bytes())
        names = list(minute["hour_features"])+list(slow["daily16"])+list(slow["factors"])+list(slow["fund16"])+list(slow["ages2"])
        require(names==meta["features"]==json.loads((dataset/"feature_names.json").read_bytes()) and len(names)==122,"exact field order mismatch")
        cursor = 0
        for group,fields in (("hour",minute["hour_features"]),("daily",slow["daily16"]),("factors",slow["factors"]),("fund",slow["fund16"]),("ages",slow["ages2"])):
            require(meta["groups"][group]==list(range(cursor,cursor+len(fields))),"exact field index partition mismatch")
            cursor += len(fields)
        require(len(minute["hour_features"])==24 and cursor==122,"H24/slow98 schema mismatch")
        require(json.loads((dataset/"symbols.json").read_bytes())==minute["union_symbols"],"historical union order differs")
        paths = {"hour":ART/"minute_hour_features.npy","slow":dataset/"slow_raw_by_day.npy","members":ART/"minute_members.npy",
                 "dataset_members":dataset/"members.npy","X":dataset/"X.npy"}
        large_before = {role:stat(p) for role,p in paths.items()}
        arrays = {role:np.load(p,mmap_mode="r",allow_pickle=False) for role,p in paths.items()}
        times = np.load(dataset/"times.npy",mmap_mode="r",allow_pickle=False)
        require(all(not a.flags.writeable for a in arrays.values()),"original mmap is writable")
        require(arrays["hour"].shape==(8476,1178,24) and arrays["slow"].shape==(2119,1178,98) and arrays["X"].shape==(8476,1178,122),"raw/panel exact dimensions differ")
        require(all(arrays[k].dtype==np.float32 for k in ("hour","slow","X")),"raw/window FP32 serialization differs")
        sample_ns = int(datetime.fromisoformat(SAMPLE).timestamp())*10**9
        found = np.flatnonzero(times==sample_ns)
        require(len(found)==1,"fixed time missing/ambiguous; no fallback")
        t = int(found[0]); start = t+1-int(meta["sequence_length"]); d = t//4
        require(meta["sequence_length"]==80 and start>=0 and t+1<len(times),"actual80 window/future witness unavailable")
        window_indices = np.arange(start,t+1,dtype=np.int64)
        require(len(window_indices)==80 and np.all(np.diff(times[window_indices])>0),"real80 timestamp order differs")
        require(top5["actual_time_index"]==t and top5["past_window_start_index"]==start,"inherited actual window identity differs")
        with np.load(mask_path,allow_pickle=False) as artifact:
            mask = artifact["mask_before"].copy()
            require(helper.exact_array(mask,artifact["mask_after"]) and int(artifact["time_index"][0])==t
                    and int(artifact["signal_time"][0])==sample_ns,"original batch-mask proof differs")
        require(mask.dtype==np.bool_ and mask.shape==(1178,) and int(mask.sum())==449,"original actual current449 mask differs")
        current_members = np.array(arrays["members"][t],copy=True)
        require(not (mask & ~current_members).any() and int(current_members.sum())==500,"actual500/449 relationship differs")
        batch_shape = (int(mask.sum()),80,122)
        require(top5["actual_X_tensor_shape"]==list(batch_shape) and top5["actual_X_tensor_dtype"]=="torch.float32"
                and top5["X_before_tensor_sha256"]==top5["X_after_tensor_sha256"],"original server batch body declaration differs")
        tensor = np.empty(batch_shape,dtype=np.float32,order="C")
        require(tensor.nbytes==top5["actual_X_tensor_bytes_compared"],"original tensor byte budget differs")
        baseline_proxy = {"hour":WindowProxy(arrays["hour"],start,t,"hour"),"slow":WindowProxy(arrays["slow"],start//4,d,"slow"),
                          "members":WindowProxy(arrays["members"],start,t,"members")}
        future_proxy = {"hour":WindowProxy(arrays["hour"],start,t,"hour",True),"slow":WindowProxy(arrays["slow"],start//4,d,"slow",True),
                        "members":WindowProxy(arrays["members"],start,t,"members",True)}
        future_witness = {}
        for role,proxy in future_proxy.items():
            index = (d if role=="slow" else t)+1
            original = np.array(arrays[role][index],copy=True); mutated = proxy.row(index)
            changed = int(np.count_nonzero(~((original==mutated)|(np.isnan(original)&np.isnan(mutated)))))
            require(changed>0,"combined future perturbation vacuous")
            future_witness[role] = {"index":index,"changed_cells":changed,"original_future_row_SHA":body_hash(original),"changed_future_row_SHA":body_hash(mutated)}
            proxy.reads.clear()
        result.update({"actual_time_index":t,"actual_window_start_index":start,"actual_window_end_index":t,"actual_window_length":80,
                       "actual_window_times_ns":[int(x) for x in times[window_indices]],"current_full_members":500,"current_complete80_subset":449,
                       "normalization_reference":"each row actual full historical members, not fixed449 eligible subset",
                       "exact_feature_names":names,"groups":meta["groups"],"normalizer":function_proof,
                       "original_top5_server_proof":before[str(top5_path)],"original_top5_root_acceptance":before[str(root_proof_path)],
                       "combined_future_counterfactual_witness":future_witness,"row_proofs":[],
                       "raw_identity_limit":"Retained generated raw hour/day have current row body SHAs and largeSTAT guards; independent preexisting raw whole-file SHA unavailable in selected metadata.",
                       "inherited_large_SHA_not_rehashed":{"original_X":meta["files_sha256"]["X.npy"],"members":meta["files_sha256"]["members.npy"],"original_slow_parquet_not_decoded":slow["raw_sha256"]}})
        fullwindow = hashlib.sha256(); originalwindow = hashlib.sha256()
        stats_matrix = {name:np.empty((80,122),dtype=np.int64 if name=="count" else np.float64) for name in ("count","lo","hi","mean","std")}
        for offset,i in enumerate(window_indices):
            i = int(i); day = i//4
            require(datetime.fromtimestamp(int(times[i])//10**9,timezone.utc).astimezone(datetime.fromisoformat(SAMPLE).tzinfo).strftime("%Y-%m-%d")==minute["calendar_dates"][day],"real timestamp/day grid differs")
            h = baseline_proxy["hour"].row(i); s = baseline_proxy["slow"].row(day); pool = baseline_proxy["members"].row(i)
            day_pool = np.array(arrays["members"][4*day],copy=True)
            panel_pool = np.array(arrays["dataset_members"][i],copy=True)
            require(helper.exact_array(pool,day_pool) and helper.exact_array(pool,panel_pool) and 498<=int(pool.sum())<=502,"historical intraday/day/panel member reference differs")
            rebuilt,fitted = helper.combine(normalize,h,s,pool)
            original_X = np.array(arrays["X"][i],copy=True)
            comparison = helper.compare(rebuilt,original_X)
            fullwindow.update(memoryview(rebuilt).cast("B")); originalwindow.update(memoryview(original_X).cast("B"))
            tensor[:,offset,:] = rebuilt[mask]
            for name in stats_matrix:stats_matrix[name][offset] = fitted[name]
            result["row_proofs"].append({"offset":offset,"time_index":i,"day_index":day,"signal_time_ns":int(times[i]),
                                         "full_historical_member_count":int(pool.sum()),"members_row_SHA":body_hash(pool),
                                         "raw_hour24_row_SHA":body_hash(h),"raw_slow98_day_row_SHA":body_hash(s),
                                         "fitstats_SHA":fitted_hash(fitted),"rebuilt122_row_body_SHA":body_hash(rebuilt),
                                         "originalX122_row_body_SHA":body_hash(original_X),"comparison":comparison})
            require(comparison["all_value_and_NaN_equivalent"] and comparison["complete_row_bytes_identical_including_NaN_payloads"],"actual past row differs from originalX; preserve difference, no tolerance")
        baseline_window_sha = fullwindow.hexdigest(); original_window_sha = originalwindow.hexdigest()
        baseline_batch_sha = body_hash(tensor)
        require(baseline_window_sha==original_window_sha,"whole80 streamed row bodies differ")
        require(baseline_batch_sha==top5["X_before_tensor_sha256"]==top5["X_after_tensor_sha256"],"raw-rebuilt449x80x122 batch body differs from actual server input")
        batch_path = output/"real_rebuilt_original_batch449_80_122.npy"
        with batch_path.open("xb") as f:np.save(f,tensor,allow_pickle=False)
        stats_path = output/"baseline80_fitstats.npz"
        with stats_path.open("xb") as f:np.savez(f,**stats_matrix)
        result.update({"baseline_streamed_all1178x80x122_SHA":baseline_window_sha,"original_X_streamed_all1178x80x122_SHA":original_window_sha,
                       "baseline_batch_body_SHA":baseline_batch_sha,"server_before_and_after_batch_body_SHA":top5["X_before_tensor_sha256"],
                       "actual_batch_shape":list(batch_shape),"actual_batch_body_bytes":tensor.nbytes,
                       "reconstructed_batch_artifact":ident(batch_path),"baseline_fitstat_artifact":ident(stats_path)})
        case_hash = hashlib.sha256(); all_stats_same = True
        # No cache: re-extract and refit all 80 rows using the isolated future
        # proxies. The same single small tensor buffer is reused after saving.
        case_rows = []
        for offset,i in enumerate(window_indices):
            i = int(i); day = i//4
            h = future_proxy["hour"].row(i); s = future_proxy["slow"].row(day); pool = future_proxy["members"].row(i)
            normalized,fitted = helper.combine(normalize,h,s,pool)
            expected = result["row_proofs"][offset]
            require(body_hash(h)==expected["raw_hour24_row_SHA"] and body_hash(s)==expected["raw_slow98_day_row_SHA"]
                    and body_hash(pool)==expected["members_row_SHA"],"future proxy changed a past raw/reference row")
            same_stats = fitted_hash(fitted)==expected["fitstats_SHA"]
            all_stats_same &= same_stats
            row_SHA = body_hash(normalized)
            case_rows.append({"time_index":i,"fitstats_SHA":fitted_hash(fitted),"normalized_row_body_SHA":row_SHA,
                              "fitstats_exact":bool(same_stats),"row_body_exact":row_SHA==expected["rebuilt122_row_body_SHA"]})
            require(same_stats and row_SHA==expected["rebuilt122_row_body_SHA"],"future-only proxy changed past fitted statistics/output")
            case_hash.update(memoryview(normalized).cast("B")); tensor[:,offset,:] = normalized[mask]
        case_window_sha = case_hash.hexdigest(); case_batch_sha = body_hash(tensor)
        require(case_window_sha==baseline_window_sha and case_batch_sha==baseline_batch_sha,"future case80window/batch body differs")
        future_batch_path = output/"future_case_rebuilt_original_batch449_80_122.npy"
        with future_batch_path.open("xb") as f:np.save(f,tensor,allow_pickle=False)
        result["case_results"].append({"case":"combined_only_future_hour_slowday_members","PASS":True,
                                        "all80_statistics_refit_and_exact":bool(all_stats_same),"actual_row_results":case_rows,
                                        "all1178x80x122_streamed_body_SHA":case_window_sha,"batch449x80x122_body_SHA":case_batch_sha,
                                        "batch_artifact":ident(future_batch_path),"current_and_past_read_traces":{k:v.reads for k,v in future_proxy.items()}})
        require(all(not item["future_modified"] for proxy in future_proxy.values() for item in proxy.reads),"past-window construction read modified future rows")
        result["status"] = "PASS_LOCAL_RETAINED_RAW_TO_FULL_MEMBER_NORMALIZED_REAL80_TO_EXACT_ORIGINAL_BATCH_AND_INHERITED_TOP5_FORWARD_CHAIN"
        result["joined_original_five_actual_forward_evidence_not_rerun"] = root_proof["saved_NPZ_inputs_and_all5_scores_independently_compared"]
        result["distinct_read_budget"] = {"hour_rows":80,"slow_day_rows":len(set(int(i)//4 for i in window_indices)),"original_X_rows":80,
                                         "future_witness_rows_per_raw_role":1,"whole_original_arrays_or_slow_parquet_body_read":False,
                                         "no_large_80window_stack_materialized":True,"single449x80x122_tensor_buffer_bytes":tensor.nbytes}
        result["runtime"] = {"python":sys.version.split()[0],"numpy":np.__version__,"device":"numpy_CPU_no_Torch"}
    except Exception as exc:
        error = exc
        result["status"] = "FAIL_OR_REJECT_NO_LOCAL80WINDOW_CHAIN_CLEARANCE"
        result["failure"] = {"type":type(exc).__name__,"reason":str(exc)}
        with (output/"actual_failure_traceback.txt").open("x") as f:f.write(traceback.format_exc())
    finally:
        try:
            after = {path:ident(path) for path in before};large_after = {role:stat(value["path"]) for role,value in large_before.items()}
            require(after==before and large_after==large_before,"original smallSHA or largeSTAT guard changed")
            result.update({"original_small_SHA_before":before,"original_small_SHA_after":after,"largeSTAT_before":large_before,
                           "largeSTAT_after":large_after,"original_guards_unchanged":True})
        except Exception as exc:
            error = error or exc;result["status"] = "FAIL_OR_REJECT_SOURCE_GUARD_CHANGED";result["guard_failure"] = str(exc)
        result["peak_process_RSS_bytes"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        result["peak_RSS_units"] = "macOS ru_maxrss bytes"
        if result["peak_process_RSS_bytes"]>=200*1024**2:
            error = error or ValueError("200MiB process memory exceeded");result["status"] = "FAIL_OR_REJECT_MEMORY_BUDGET"
        result["finished_utc"] = datetime.now(timezone.utc).isoformat();result["script"] = ident(__file__)
        write_json(output/"actual_window_normalization_receipt_v2.json",result)
    print(json.dumps({"status":result["status"],"receipt":ident(output/"actual_window_normalization_receipt_v2.json"),
                      "peak_RSS_bytes":result["peak_process_RSS_bytes"],"new_training_fits":0,"full_PLAN_complete":False},ensure_ascii=False))
    if error:
        raise RuntimeError("preserve failed actualV2 and do not automatically rerun") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true");parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status":"PREPARED_ONLY_NO_REAL80_AUDIT","sample":SAMPLE}));return
    require(args.output is not None,"explicit isolated append-only output required")
    run(args.output)


if __name__=="__main__":
    main()

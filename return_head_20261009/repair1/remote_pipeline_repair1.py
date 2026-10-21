"""Restore original seed/FP32 inference settings; retain the frozen stage-A entry.

Economic specification, inputs, ridge formula and head fit remain in byte-frozen
remote_pipeline.py. This independent entry wraps only its model loader, restores
the exact original trainer's seed_everything(), and checks three future rank
snapshots before any head fit. Rank snapshots contain no future return labels.
"""
from __future__ import annotations

import gc
import importlib.util
from pathlib import Path
import sys

import numpy as np

_spec = importlib.util.spec_from_file_location("return_head_frozen_original_pipeline",
    Path(__file__).with_name("remote_pipeline.py"))
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)
_ORIGINAL_RUN = base.run
_ORIGINAL_LOADER = base.load_frozen_model
_ORIGINAL_FULL_PANEL = base.safe_full_panel
ORIGINAL_ENTRY_SHA256 = "e75faf5486874580142ad4a9d6c8715d3451beb5c22de78d02daf6eaa2c72fbb"
EXPECTED_FLAGS = dict(cudnn_deterministic=True, cudnn_benchmark=False,
    cuda_matmul_allow_tf32=False, cudnn_allow_tf32=False)


def runtime_flags():
    import torch
    return dict(cudnn_deterministic=bool(torch.backends.cudnn.deterministic),
        cudnn_benchmark=bool(torch.backends.cudnn.benchmark),
        cuda_matmul_allow_tf32=bool(torch.backends.cuda.matmul.allow_tf32),
        cudnn_allow_tf32=bool(torch.backends.cudnn.allow_tf32))


def restore_original_precision(train, seed, device):
    import torch
    before=runtime_flags()
    # This exact verified original method was called by evaluation.load_model.
    # It was omitted in the stopped first stage-A entry; no new precision policy
    # or model parameter is selected here.
    train.seed_everything(seed)
    after=runtime_flags()
    if after!=EXPECTED_FLAGS:
        raise ValueError("Original seed_everything FP32/cuDNN flags were not restored")
    return dict(seed=seed,method="gru_research.train.seed_everything",
        flags_before_reset=before,flags_for_inference=after,
        torch_version=str(torch.__version__),cuda_version=torch.version.cuda,
        cudnn_version=torch.backends.cudnn.version(),device=str(device),
        gpu_name=torch.cuda.get_device_name(device) if str(device).startswith("cuda") else None,
        deterministic_algorithms_enabled=bool(torch.are_deterministic_algorithms_enabled()))


def pre_fit_rank_checks(model,panel,infer,adapter_class,old_raw,old_mask,seed,device):
    """First, Jun16 replacement, last: original vs adapter vs frozen raw rank."""
    indices=infer.visible_sections(panel)
    swap=np.flatnonzero(panel.days[indices]==base.training.day_number("2025-06-16"))
    if len(swap)!=1:
        raise ValueError("Required2025-06-16 replacement snapshot is missing/duplicate")
    rows=[0,int(swap[0]),len(indices)-1]
    if len(set(rows))!=3:
        raise ValueError("Three distinct pre-fit future rank check dates required")
    seed_index=base.SEEDS.index(seed)
    if old_raw.shape!=(5,len(indices),panel.U) or old_mask.shape!=(len(indices),panel.U):
        raise ValueError("Frozen raw-rank/mask array alignment changed")
    before=base.state_hash(model)
    adapter=adapter_class(model,target_unit="arithmetic_label3").to(device).eval()
    records=[]
    try:
        for row in rows:
            t=int(indices[row])
            direct,direct_mask=infer.predict_visible(model,panel,t,device,80)
            _,adapter_rank,adapter_mask=base.extract_section(adapter,panel,t,device,infer)
            expected=old_raw[seed_index,row]
            mask=old_mask[row]
            if not np.array_equal(direct_mask,mask) or not np.array_equal(adapter_mask,mask):
                raise ValueError("Pre-fit rank visible identity check failed")
            if not np.array_equal(np.isfinite(expected),mask):
                raise ValueError("Old frozen raw-rank eligibility differs from its mask")
            if not np.array_equal(direct[mask],adapter_rank[mask]):
                raise ValueError("Adapter changed original rank arithmetic before head fit")
            direct_error=float(np.max(np.abs(direct[mask].astype(np.float64)-expected[mask].astype(np.float64))))
            adapter_error=float(np.max(np.abs(adapter_rank[mask].astype(np.float64)-expected[mask].astype(np.float64))))
            if max(direct_error,adapter_error)>base.ABSOLUTE_TOLERANCE:
                raise ValueError("Pre-fit original raw-rank replay exceeds1e-6 for seed "+str(seed)+" row "+str(row))
            records.append(dict(seed=seed,row=row,signal_time_ns=int(panel.times[t]),
                date=str(np.datetime64(int(panel.days[t]),"D")),visible_stocks=int(mask.sum()),
                original_vs_frozen_max_abs_error=direct_error,
                adapter_vs_frozen_max_abs_error=adapter_error,
                original_vs_adapter_exact=True,absolute_tolerance=base.ABSOLUTE_TOLERANCE))
        after=base.state_hash(model)
        if before!=after or base.state_hash(adapter.rank_model)!=before:
            raise ValueError("Pre-fit check changed original shared parameters")
    finally:
        del adapter
        gc.collect()
    return dict(status="passed",seed=seed,snapshots=records,
        shared_state_hash_before=before,shared_state_hash_after=after,
        future_label_values_read=False,future_label_end_values_read=False,
        selection="fixed first/2025-06-16/last; score-only checks before head fit")


def run(args):
    if base.sha(base.__file__)!=ORIGINAL_ENTRY_SHA256:
        raise ValueError("Keep original stopped/frozen stage-A entry byte-identical")
    output=Path(args.output_root).resolve()
    context={};precision={};checks={}
    spec=importlib.util.spec_from_file_location("return_head_repair1_adapter",args.adapter_source)
    adapter_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter_module)

    def captured_full_panel(*params,**kwargs):
        result=_ORIGINAL_FULL_PANEL(*params,**kwargs)
        context["panel"],context["infer"]=result[0],params[2]
        return result

    def repaired_loader(train,resolver,inventory,frozen,seed,device):
        precision[str(seed)]=restore_original_precision(train,seed,device)
        # No model weights/architecture/data/output-scale changes are made.
        model,path=_ORIGINAL_LOADER(train,resolver,inventory,frozen,seed,device)
        if "old_raw" not in context:
            old_dir=Path(args.original_visible_signals_dir)
            with np.load(old_dir/"seed_scores.npz",allow_pickle=False) as archive:
                context["old_raw"]=archive["raw_scores"].copy()
            with np.load(old_dir/"mask.npz",allow_pickle=False) as archive:
                context["old_mask"]=archive["mask"].copy()
        # Save precision evidence even if the score-only pre-fit guard stops.
        base.write(output/"runtime_precision_repair1.json",dict(status="checking",per_seed=precision))
        checks[str(seed)]=pre_fit_rank_checks(model,context["panel"],context["infer"],
            adapter_module.RankReturnAdapter,context["old_raw"],context["old_mask"],seed,device)
        base.write(output/"pre_fit_rank_check_repair1.json",dict(status="passed_so_far",per_seed=checks))
        print("REPAIR1_PRE_FIT_RANK_PASSED",seed,3,"snapshots",flush=True)
        return model,path

    original_loader,original_panel=base.load_frozen_model,base.safe_full_panel
    base.load_frozen_model=repaired_loader;base.safe_full_panel=captured_full_panel
    try:
        manifest=_ORIGINAL_RUN(args)
    finally:
        base.load_frozen_model=original_loader;base.safe_full_panel=original_panel
    if tuple(sorted(map(int,checks)))!=base.SEEDS or tuple(sorted(map(int,precision)))!=base.SEEDS:
        raise ValueError("All five seeds require original precision and pre-fit evidence")
    runtime_receipt=dict(status="complete",repair="restore omitted original seed_everything; no economic specification change",
        per_seed=precision,original_entry_sha256=ORIGINAL_ENTRY_SHA256,
        entry_filename=Path(__file__).name,entry_sha256=base.sha(__file__))
    pre_fit_receipt=dict(status="passed",per_seed=checks,shared_parameters_updated=False,
                        future_label_values_read=False,future_label_end_values_read=False)
    base.write(output/"runtime_precision_repair1.json",runtime_receipt)
    base.write(output/"pre_fit_rank_check_repair1.json",pre_fit_receipt)
    manifest["entry_filename"]=Path(__file__).name
    manifest["repair1_runtime"]=runtime_receipt
    manifest["inference"]["runtime_precision"]=runtime_receipt
    manifest["inference"]["pre_fit_rank_check"]=pre_fit_receipt
    manifest["new_source_sha256"]["remote_pipeline_original.py"]=ORIGINAL_ENTRY_SHA256
    manifest["new_source_sha256"]["remote_pipeline.py"]=base.sha(__file__) # logical entry compatibility
    manifest["new_source_sha256"][Path(__file__).name]=base.sha(__file__)
    signal_path=output/"signals/manifest.json"
    signal_manifest=base.read(signal_path)
    signal_manifest["entry_filename"]=Path(__file__).name
    signal_manifest["inference"]=manifest["inference"]
    base.write(signal_path,signal_manifest)
    for relative in ("signals/manifest.json","runtime_precision_repair1.json","pre_fit_rank_check_repair1.json"):
        path=output/relative
        manifest["files"][relative]=dict(sha256=base.sha(path),bytes=path.stat().st_size)
    base.write(output/"manifest.json",manifest)
    return manifest


if __name__=="__main__":
    # Keep original CLI/error-save semantics, while selecting the repaired entry.
    base.run=run
    base.main()

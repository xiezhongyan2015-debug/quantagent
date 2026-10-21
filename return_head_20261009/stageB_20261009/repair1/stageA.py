"""One fixed, frozen-encoder arithmetic-label3 head experiment on existing assets.

This entry does not contact a server. The authorized caller executes it on the
CUHK host with explicit local asset paths. No source checkpoint/dataset is
modified. Original ResearchPanel restricts label access to pre2025; the future
panel instead has the original infer_signals header-only label guards.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gc
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import time
import traceback

import numpy as np

if __package__:
    from . import training
else:
    spec = importlib.util.spec_from_file_location("return_head_training", Path(__file__).with_name("training.py"))
    training = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = training
    spec.loader.exec_module(training)

SEEDS = (11, 22, 33, 44, 55)
SLOT = 2
ABSOLUTE_TOLERANCE = 1e-6
SIGNAL_KIND = "arithmetic_label3_return_proxy"
ENSEMBLE_METHOD = "equal_mean_return_units_no_section_normalization"
# Entry receipt from the inventory-verified archived evaluation_manifest.json.
EVALUATION_SOURCE_SHA256 = "08786e99a1ee93faa4d9f6509d2db5fe2d7bd36039d0e85a13747071a44b4353"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8*1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf8")
    temporary.replace(path)


def recorded_project_root(visible_manifest):
    descriptor = PurePosixPath(visible_manifest["lineage"]["descriptor"])
    positions = [i for i, part in enumerate(descriptor.parts) if part == "outputs"]
    if len(positions) != 1:
        raise ValueError("One recorded project outputs ancestor required")
    return PurePosixPath(*descriptor.parts[:positions[0]])


class AssetResolver:
    """Explicit, receipt-preserving relocation; never silently substitute data."""
    def __init__(self, original_root, visible_manifest, asset_map=None):
        self.recorded_root = str(recorded_project_root(visible_manifest))
        self.actual_root = str(Path(original_root).resolve())
        self.mapping = dict(asset_map or {})
        if self.recorded_root in self.mapping and str(Path(self.mapping[self.recorded_root]).resolve()) != self.actual_root:
            raise ValueError("--original-root conflicts with explicit relocation map")
        self.mapping[self.recorded_root] = self.actual_root
        if not all(isinstance(k, str) and isinstance(v, str) and k and v for k,v in self.mapping.items()):
            raise ValueError("Asset map must map explicit original absolute paths/prefixes to actual paths")

    def path(self, recorded):
        value = str(recorded)
        for prefix in sorted(self.mapping, key=len, reverse=True):
            if value == prefix or value.startswith(prefix.rstrip("/")+"/"):
                suffix = value[len(prefix):].lstrip("/")
                return (Path(self.mapping[prefix])/suffix).resolve()
        return Path(value).resolve()

    def relative_spec(self, descriptor_path, spec):
        path = Path(spec["path"])
        if not path.is_absolute():
            return (Path(descriptor_path).parent/path).resolve()
        return self.path(path)


@contextmanager
def relocated_research_paths(overlay, resolver):
    """Original ResearchPanel method, only its external filename resolver adapted.

    JSON contents and every numeric/feature/hash contract are unchanged. The
    scope and read-only mmap checks in the original method still apply.
    """
    with overlay._INITIALIZATION_LOCK:
        original = overlay._path
        overlay._path = lambda base, value: resolver.path(original(base, value))
        try:
            yield
        finally:
            overlay._path = original


def load_array_spec(resolver, descriptor_path, spec, *, dtype, ndim):
    path = resolver.relative_spec(descriptor_path, spec)
    if sha(path) != spec["sha256"]:
        raise ValueError("Frozen replacement array SHA changed: "+str(path))
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if array.dtype != np.dtype(dtype) or array.ndim != ndim or list(array.shape) != spec["shape"]:
        raise ValueError("Frozen array shape/dtype changed: "+str(path))
    return array


def safe_full_panel(train, overlay, infer, evaluation, resolver, frozen):
    """Same full patch4 OverlayX, explicit relocation and forbidden label values."""
    line = frozen["lineage"]
    descriptor_path = resolver.path(line["descriptor"])
    training_path = resolver.path(line["training_descriptor"]["path"])
    if sha(descriptor_path) != line["descriptor_sha256"] or sha(training_path) != line["training_descriptor"]["sha256"]:
        raise ValueError("Recorded full/training patch4 descriptor changed")
    descriptor, training_descriptor = read(descriptor_path), read(training_path)
    dataset = resolver.path(descriptor["source_dataset"])
    if sha(dataset/"meta.json") != line["source_meta_sha256"]:
        raise ValueError("Original source metadata changed")
    meta = read(dataset/"meta.json")
    expected = dict(descriptor=line["training_descriptor"]["path"],
        descriptor_sha256=line["training_descriptor"]["sha256"],
        fingerprint=line["training_dataset_fingerprint"],
        feature_names=line["feature_names"], groups=line["groups"])
    # Check original recorded lineage before filename relocation; no JSON edits.
    evaluation.check_descriptor(descriptor, expected, training_descriptor, meta)
    if descriptor["evaluation_fingerprint"] != line["evaluation_fingerprint"] or descriptor["mode"] != "patch4":
        raise ValueError("Original complete patch4 evaluation fingerprint required")
    actual_inputs = infer.verify_inputs(dataset, meta)
    if actual_inputs != frozen["input_source_sha256"]:
        raise ValueError("Visible-pool original input receipts differ")
    with infer.block_label_values(train, overlay._INITIALIZATION_LOCK):
        base = train.PanelDataset(dataset, enforce_audit=True)
    if not isinstance(base.labels, infer.LabelHeaderOnly) or not isinstance(base.label_end_times, infer.LabelHeaderOnly):
        raise ValueError("Future full panel must contain only label headers")
    if base.fingerprint != line["source_canonical_fingerprint"]:
        raise ValueError("Frozen source canonical fingerprint changed")
    dates = load_array_spec(resolver, descriptor_path, descriptor["dates_day_number"], dtype="int64", ndim=1)
    replacement = load_array_spec(resolver, descriptor_path, descriptor["replacement_daily"], dtype="float32", ndim=3)
    old_dates = load_array_spec(resolver, training_path, training_descriptor["dates_day_number"], dtype="int64", ndim=1)
    old_replacement = load_array_spec(resolver, training_path, training_descriptor["replacement_daily"], dtype="float32", ndim=3)
    if not np.array_equal(dates, np.unique(base.days)) or not np.array_equal(old_dates, dates[:len(old_dates)]):
        raise ValueError("Exact frozen original full/pre2025 calendar required")
    if old_replacement.shape != replacement[:len(old_dates)].shape:
        raise ValueError("Pre2025 replacement prefix shape changed")
    for begin in range(0, len(old_dates), 32):
        stop = min(begin+32, len(old_dates))
        if old_replacement[begin:stop].tobytes() != replacement[begin:stop].tobytes():
            raise ValueError("Original trained-on patch4 prefix changed")
    for begin in range(0, len(dates), 32):
        if np.isinf(replacement[begin:begin+32]).any():
            raise FloatingPointError("Infinite frozen patch4 replacement value")
    names, groups, keep, output_keep, positions = overlay.feature_layout(meta,
        descriptor["factor_columns"], "patch4", descriptor["groups"])
    if names != descriptor["feature_names"] or len(names) != 122 or replacement.shape != (len(dates), base.U, len(positions)):
        raise ValueError("Original full122 patch4 feature identity required")
    base.X = overlay.OverlayX(base.X, replacement, np.searchsorted(dates, base.days),
                             keep, output_keep, positions, len(names))
    base.F, base.groups = len(names), groups
    return base, dataset, actual_inputs, descriptor_path, training_path


def state_hash(model):
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        value = tensor.detach().cpu().contiguous().numpy()
        digest.update(name.encode()+b"\0"+str(value.dtype).encode()+b"\0")
        digest.update(np.asarray(value.shape,dtype=np.int64).tobytes())
        digest.update(value.tobytes())
    return digest.hexdigest()


def load_frozen_model(train, resolver, inventory, frozen, seed, device):
    import torch
    recorded = frozen["checkpoints"][str(seed)]
    inv = next(row for row in inventory["seed_inventory"] if row["seed"] == seed)
    if recorded["sha256"] != inv["checkpoint_sha256"] or recorded["path"] != inv["remote_checkpoint_path"]:
        raise ValueError("Inventory and original visible checkpoint identity differ")
    path = resolver.path(recorded["path"])
    if sha(path) != recorded["sha256"]:
        raise ValueError("Exact frozen seed checkpoint SHA required")
    state = torch.load(path, map_location="cpu", weights_only=False)
    if state["signature"] != recorded["signature"] or state["signature"]["variant"] != "full" or state["signature"]["label"] != 3:
        raise ValueError("Original frozen rank label3 signature changed")
    if sorted(i for values in state["groups"].values() for i in values) != list(range(122)):
        raise ValueError("Original full122 checkpoint required")
    model = train.PlanGRU(state["groups"], int(state["n_industries"]), "full")
    model.load_state_dict(state["model"], strict=True)
    model.to(device).eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model, path


def extract_section(adapter, panel, t, device, infer):
    mask = np.asarray(panel.eligible_mask(int(t), 80, require_labels=False),dtype=np.bool_)
    stock_positions = np.flatnonzero(mask)
    features, ranks = [], []
    for begin in range(0, len(stock_positions), infer.CHUNK):
        stock = stock_positions[begin:begin+infer.CHUNK]
        h, rank = training.extract_visible_features(adapter,
            *infer.inference_tensors(panel, int(t), stock, device, 80))
        features.append(h); ranks.append(rank)
    h = np.concatenate(features) if features else np.empty((0,128),np.float32)
    rank = np.full(panel.U, np.nan, np.float32)
    if ranks:
        rank[stock_positions] = np.concatenate(ranks)
    if h.shape != (int(mask.sum()),128) or not np.array_equal(np.isfinite(rank), mask):
        raise ValueError("Frozen visible feature pool differs from rank identities")
    return h, rank, mask


def predict_all_sections(adapter, fit, panel, indices, device, infer, *, stage=None, seed=None, progress_started=None):
    mu = np.full((len(indices),panel.U),np.nan,np.float32)
    rank = np.full_like(mu,np.nan)
    mask = np.zeros(mu.shape,np.bool_)
    for row,t in enumerate(indices):
        h, rank[row], mask[row] = extract_section(adapter,panel,int(t),device,infer)
        mu[row,mask[row]] = fit.predict(h).astype(np.float32)
        if stage is not None and ((row+1)%100==0 or row+1==len(indices)):
            print("RETURN_HEAD_PROGRESS",stage,seed,row+1,len(indices),
                  round(time.monotonic()-progress_started,3),flush=True)
    if not np.array_equal(np.isfinite(mu), mask):
        raise ValueError("Return-head output must cover every visible stock without label filtering")
    return mu,rank,mask


def average_return_units(per_seed, mask):
    if per_seed.shape != (5,*mask.shape) or not all(np.array_equal(np.isfinite(row),mask) for row in per_seed):
        raise ValueError("Exactly five return-unit heads on identical visible cohorts required")
    result = np.full(mask.shape,np.nan,np.float32)
    result[mask] = per_seed[:,mask].mean(axis=0,dtype=np.float64).astype(np.float32)
    return result


def validation_metrics(prediction, raw_rank, target, visible, mature, min_stocks=100):
    records = []
    weighted_mse = weighted_mae = mean_prediction = mean_target = 0.0
    ep2 = ey2 = epy = 0.0
    def corr(a,b):
        ac,bc = a-a.mean(),b-b.mean()
        denominator = np.sqrt(ac@ac*(bc@bc))
        return float(ac@bc/denominator) if denominator > 1e-15 else None
    def ranks(values):
        order=np.argsort(values,kind="stable");result=np.empty(len(values),np.float64)
        begin=0
        while begin<len(order):
            end=begin+1
            while end<len(order) and values[order[end]]==values[order[begin]]:end+=1
            result[order[begin:end]]=(begin+end-1)/2
            begin=end
        return result
    for row in range(len(visible)):
        valid=visible[row]&np.isfinite(target[row])&np.isfinite(prediction[row])&bool(mature[row])
        n=int(valid.sum())
        if n<min_stocks:continue
        p,y,r=prediction[row,valid].astype(np.float64),target[row,valid].astype(np.float64),raw_rank[row,valid].astype(np.float64)
        mse=float(np.mean((p-y)**2));mae=float(np.mean(np.abs(p-y)))
        records.append(dict(row=row,fitting_pairs=n,mse=mse,mae=mae,
            pearson_ic=corr(p,y),rank_ic=corr(ranks(p),ranks(y)),
            original_rank_ic=corr(ranks(r),ranks(y))))
        weighted_mse+=mse;weighted_mae+=mae;mean_prediction+=float(p.mean());mean_target+=float(y.mean())
        ep2+=float(np.mean(p*p));ey2+=float(np.mean(y*y));epy+=float(np.mean(p*y))
    n=len(records)
    if not n:raise ValueError("No mature finite2024 validation cross sections")
    mp,my=mean_prediction/n,mean_target/n
    vp,vy=ep2/n-mp*mp,ey2/n-my*my
    covariance=epy/n-mp*my
    slope=covariance/vp if vp>1e-18 else None
    def mean_field(key):
        values=[r[key] for r in records if r[key] is not None]
        return float(np.mean(values)) if values else None
    return dict(sections=n,finite_mature_pairs=sum(r["fitting_pairs"] for r in records),
        weighted_mse=weighted_mse/n,weighted_mae=weighted_mae/n,
        mean_prediction=mp,mean_target=my,prediction_std=np.sqrt(max(vp,0)),target_std=np.sqrt(max(vy,0)),
        calibration_slope=slope,calibration_intercept=(my-slope*mp) if slope is not None else None,
        calibration_used_to_adjust_prediction=False,pearson_ic=mean_field("pearson_ic"),
        rank_ic=mean_field("rank_ic"),original_rank_ic=mean_field("original_rank_ic"),records=records,
        sample_weight="equal mature timestamps; equal finite visible stocks within timestamp")


def run(args):
    import torch
    started=time.monotonic()
    output=Path(args.output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use independent empty output directory; do not resume/splice old results")
    output.mkdir(parents=True,exist_ok=True)
    status=dict(status="running",stage="checking_inputs",completed_seeds=[],elapsed_seconds=0.0,
                host_execution="caller-authorized CUHK; no network access by this program")
    write(output/"RUN_STATUS.json",status)
    def set_stage(stage, seed=None):
        status["stage"]=stage
        if seed is not None:status["seed"]=seed
        status["elapsed_seconds"]=time.monotonic()-started
        write(output/"RUN_STATUS.json",status)
        print("RETURN_HEAD_STAGE",stage,seed,round(status["elapsed_seconds"],3),flush=True)
    inventory,frozen=read(args.inventory),read(args.visible_manifest)
    if frozen["status"]!="complete" or tuple(frozen["seeds"])!=SEEDS or frozen["training_label_transform"]!="qlib_rank_linear":
        raise ValueError("Complete original five-seed rank signal manifest required")
    resolver=AssetResolver(args.original_root,frozen,read(args.asset_map) if args.asset_map else None)
    sys.path.insert(0,resolver.actual_root)
    source_receipts={}
    for identity in ("gru_research/model.py","gru_research/train.py","gru_research/evaluate.py","cs_factor_repair_20261007/panel.py"):
        source_path=Path(resolver.actual_root)/identity
        actual=sha(source_path)
        if actual!=frozen["source_sha256"][identity]:raise ValueError("Original verified method source changed: "+identity)
        source_receipts[identity]=actual
    infer_path=Path(resolver.actual_root)/"gru_index_enhancement_20261008/infer_signals.py"
    if sha(infer_path)!=frozen["entry_sha256"]:raise ValueError("Original safe inference source changed")
    source_receipts["gru_index_enhancement_20261008/infer_signals.py"]=sha(infer_path)
    evaluation_path=Path(resolver.actual_root)/"ranklabel_future_evaluation_20261008/evaluate.py"
    if sha(evaluation_path)!=EVALUATION_SOURCE_SHA256:raise ValueError("Original archived full-period evaluation source changed")
    source_receipts["ranklabel_future_evaluation_20261008/evaluate.py"]=sha(evaluation_path)
    train=importlib.import_module("gru_research.train")
    overlay=importlib.import_module("cs_factor_repair_20261007.panel")
    evaluation=importlib.import_module("ranklabel_future_evaluation_20261008.evaluate")
    infer=importlib.import_module("gru_index_enhancement_20261008.infer_signals")
    for module,identity in ((train,"gru_research/train.py"),(overlay,"cs_factor_repair_20261007/panel.py"),
                            (evaluation,"ranklabel_future_evaluation_20261008/evaluate.py"),
                            (infer,"gru_index_enhancement_20261008/infer_signals.py")):
        if Path(module.__file__).resolve()!=(Path(resolver.actual_root)/identity).resolve():
            raise ValueError("Imported module from a different asset root: "+identity)
    adapter_spec=importlib.util.spec_from_file_location("return_head_adapter",args.adapter_source)
    adapter_module=importlib.util.module_from_spec(adapter_spec);adapter_spec.loader.exec_module(adapter_module)
    device=args.device
    if device.startswith("cuda") and not torch.cuda.is_available():raise RuntimeError("CUDA unavailable; no silent CPU fallback")
    torch.set_num_threads(2)
    if device.startswith("cuda"):
        torch.cuda.init();torch.cuda.set_device(torch.device(device))
    full_panel,dataset,actual_inputs,full_descriptor,training_descriptor=safe_full_panel(
        train,overlay,infer,evaluation,resolver,frozen)
    with relocated_research_paths(overlay,resolver):
        research_panel=overlay.ResearchPanel.load(train,training_descriptor)
    if research_panel.fingerprint!=frozen["lineage"]["training_dataset_fingerprint"]:
        raise ValueError("Original scoped patch4 training fingerprint changed")
    if research_panel.groups!=full_panel.groups or research_panel.symbols!=full_panel.symbols:
        raise ValueError("Pre2025/full patch4 stock and feature identities differ")
    config=frozen["checkpoints"]["11"]["signature"]["config"]
    if config["embargo_trading_days"]!=2 or config["sequence_length"]!=80 or config["min_stocks"]!=100:
        raise ValueError("Original fixed cohort controls required")
    train_window=training.training_window(research_panel.trading_days)
    val_window=training.training_window(research_panel.trading_days,start="2024-01-01",end="2025-01-01")
    train_indices=research_panel.split_indices("train",config,require_labels=False)
    train_indices=train_indices[research_panel.slots[train_indices]==SLOT]
    val_indices=np.flatnonzero((research_panel.days>=training.day_number("2024-01-01")) &
                              (research_panel.days<training.day_number("2025-01-01")) & (research_panel.slots==SLOT))
    future_indices=infer.visible_sections(full_panel)
    if (len(future_indices),int((full_panel.days[future_indices]<training.day_number("2026-01-01")).sum()))!=(420,243):
        raise ValueError("All original243/177 future dates must survive without label cuts")
    original_dir=Path(args.original_visible_signals_dir).resolve()
    for name,receipt in frozen["files"].items():
        if sha(original_dir/name)!=receipt["sha256"]:raise ValueError("Uploaded frozen visible numeric artifact changed: "+name)
    with np.load(original_dir/"scores.npz",allow_pickle=False) as a:
        old_scores=a["scores"].copy();old_dates=a["dates"].copy();old_times=a["signal_time_ns"].copy();old_symbols=a["symbols"].copy()
    with np.load(original_dir/"mask.npz",allow_pickle=False) as a:old_mask=a["mask"].copy()
    with np.load(original_dir/"seed_scores.npz",allow_pickle=False) as a:old_rank=a["raw_scores"].copy()
    symbols=np.asarray(full_panel.symbols,dtype=np.str_)
    if not np.array_equal(old_dates,full_panel.days[future_indices]) or not np.array_equal(old_times,full_panel.times[future_indices]) or not np.array_equal(old_symbols,symbols):
        raise ValueError("Frozen future dates/timestamps/stock ordering changed")
    protocol=dict(stage="A_frozen_encoder_only",seeds=list(SEEDS),training_interval=["2019-01-01","2024-01-01"],
        validation_interval=["2024-01-01","2025-01-01"],signal_slot=2,signal_local_time="14:00",
        hidden_size=128,additional_parameters_per_seed=129,target_unit="arithmetic_label3",
        target_formula="expm1(raw log y3) per realized outcome",target_is_gross=True,
        ensemble_method=ENSEMBLE_METHOD,ridge_multiplier=training.RIDGE_MULTIPLIER,
        train_window=train_window.as_dict(),validation_window=val_window.as_dict(),
        backbone_and_rank_frozen=True,shared_finetuning=False,parameter_search=False,
        training_labels_only_pre2024=True,validation_labels_only2024=True,
        validation2024_independent=False,years2025_2026_independent=False,
        optimizer_return_interpretation="label3 arithmetic gross return proxy; slot2 exits next13:01--13:05, portfolio replans14:00",
        horizon_mismatch_resolved=False,new_minutes_used=False,old_execution_unchanged=True)
    write(output/"protocol.json",protocol)
    set_stage("preparing_pre2025_labels")
    # The label-bearing object is the original historical-only ResearchPanel.
    # Label arrays in full_panel remain unreadable for the entire experiment.
    val_ends=np.asarray(research_panel.label_end_times[val_indices,2],np.int64)
    val_entries=np.asarray(research_panel.earliest_execution_times[val_indices],np.int64)
    val_times=np.asarray(research_panel.times[val_indices],np.int64)
    val_mature=(val_entries>val_times)&(val_ends>val_entries)&(val_ends<val_window.cutoff_ns)
    raw_y3=np.full((len(val_indices),full_panel.U),np.nan,np.float32)
    # Keep all visible predictions, but never read validation outcomes whose
    # exit reaches the embargo or crosses into2025. This is pair qualification,
    # not prediction-universe construction.
    for row in np.flatnonzero(val_mature):
        raw_y3[row]=np.asarray(research_panel.labels[int(val_indices[row]),:,2],np.float32)
    with np.errstate(over="ignore",invalid="ignore"):arithmetic_y3=np.expm1(raw_y3.astype(np.float64))
    val_mu=np.full((5,len(val_indices),full_panel.U),np.nan,np.float32)
    val_rank=np.full_like(val_mu,np.nan);val_masks=[]
    future_mu=np.full((5,len(future_indices),full_panel.U),np.nan,np.float32)
    future_rank=np.full_like(future_mu,np.nan);future_masks=[]
    heads={};state_before={};state_after={};seed_errors={};resources=[]
    training_pair_digest=hashlib.sha256();seen_label_rows={}
    for seed_index,seed in enumerate(SEEDS):
        tick=time.monotonic()
        set_stage("loading_frozen_checkpoint",seed)
        model,checkpoint_path=load_frozen_model(train,resolver,inventory,frozen,seed,device)
        state_before[str(seed)]=state_hash(model)
        adapter=adapter_module.RankReturnAdapter(model,target_unit="arithmetic_label3").to(device).eval()
        if sum(p.numel() for p in adapter.parameters() if p.requires_grad)!=129:
            raise ValueError("Only129 return-head parameters may be trainable")
        def batches():
            for count,t in enumerate(train_indices,1):
                t=int(t)
                if not training.day_number("2019-01-01")<=int(research_panel.days[t])<train_window.cutoff_day:
                    raise ValueError("Training label access outside frozen interval")
                h,_,mask=extract_section(adapter,research_panel,t,device,infer)
                y=np.asarray(research_panel.labels[t,mask,2],np.float32)
                end=int(research_panel.label_end_times[t,2]);entry=int(research_panel.earliest_execution_times[t])
                payload=np.asarray([int(research_panel.times[t]),end,entry],np.int64).tobytes()+mask.tobytes()+y.tobytes()
                row_sha=hashlib.sha256(payload).hexdigest()
                if t in seen_label_rows and seen_label_rows[t]!=row_sha:raise ValueError("Training labels changed across frozen seeds")
                if t not in seen_label_rows:
                    seen_label_rows[t]=row_sha;training_pair_digest.update(payload)
                yield dict(h=h,raw_y3=y,signal_time_ns=int(research_panel.times[t]),
                    label_end_time_ns=end,earliest_execution_time_ns=entry)
                if count%100==0 or count==len(train_indices):
                    print("RETURN_HEAD_PROGRESS","train2019_2023",seed,count,len(train_indices),
                          round(time.monotonic()-started,3),flush=True)
        set_stage("fitting_return_head",seed)
        fit=training.fit_ridge_from_batches(batches(),window=train_window)
        head_path=output/f"heads/seed{seed}_return_head.npz"
        receipt=training.save_return_head(head_path,fit,lineage=dict(seed=seed,
            frozen_checkpoint_sha256=frozen["checkpoints"][str(seed)]["sha256"],checkpoint_path_actual=str(checkpoint_path),
            model_source_sha256=source_receipts["gru_research/model.py"],training_descriptor_sha256=sha(training_descriptor),
            validation_not_used_for_head_fitting=True))
        fitted_reload=training.load_return_head(head_path)
        training.install_return_head(adapter,fitted_reload)
        heads[str(seed)]=dict(path=head_path.relative_to(output).as_posix(),sha256=receipt["sha256"],
            added_parameters=129,training_sections=fit.metadata["training_sections"],fitting_pairs=fit.metadata["fitting_pairs"],
            weighted_train_mse=fit.metadata["weighted_train_mse"])
        set_stage("validation2024",seed)
        val_mu[seed_index],val_rank[seed_index],v_mask=predict_all_sections(adapter,fitted_reload,research_panel,val_indices,device,infer,
            stage="validation2024",seed=seed,progress_started=started)
        val_masks.append(v_mask)
        set_stage("inference2025_2026",seed)
        future_mu[seed_index],future_rank[seed_index],f_mask=predict_all_sections(adapter,fitted_reload,full_panel,future_indices,device,infer,
            stage="inference2025_2026",seed=seed,progress_started=started)
        future_masks.append(f_mask)
        if not np.array_equal(f_mask,old_mask):raise ValueError("Future visible mask differs from frozen original scores")
        error=float(np.max(np.abs(future_rank[seed_index,old_mask].astype(np.float64)-old_rank[seed_index,old_mask].astype(np.float64))))
        seed_errors[str(seed)]=error
        if error>ABSOLUTE_TOLERANCE:raise ValueError("Old rank raw replay differs beyond1e-6 for seed "+str(seed))
        state_after[str(seed)]=state_hash(adapter.rank_model)
        if state_after[str(seed)]!=state_before[str(seed)]:raise ValueError("Shared backbone or original rank head changed")
        if sha(checkpoint_path)!=frozen["checkpoints"][str(seed)]["sha256"]:raise ValueError("Original checkpoint changed during execution")
        resources.append(dict(seed=seed,seconds=time.monotonic()-tick))
        status["completed_seeds"].append(seed);status["elapsed_seconds"]=time.monotonic()-started
        set_stage("seed_complete",seed)
        print("RETURN_HEAD_SEED_COMPLETE",seed,json.dumps(heads[str(seed)]),flush=True)
        del adapter,model;gc.collect()
        if device.startswith("cuda"):torch.cuda.empty_cache()
    set_stage("assembling_and_hashing_artifacts")
    if not all(np.array_equal(val_masks[0],m) for m in val_masks) or not all(np.array_equal(old_mask,m) for m in future_masks):
        raise ValueError("Prediction universe changed between seeds")
    val_mask=val_masks[0]
    ensemble_val=average_return_units(val_mu,val_mask)
    ensemble_future=average_return_units(future_mu,old_mask)
    rank_scores,_,_=infer.ensemble(future_rank,old_mask)
    ensemble_error=float(np.max(np.abs(rank_scores[old_mask].astype(np.float64)-old_scores[old_mask].astype(np.float64))))
    if ensemble_error>ABSOLUTE_TOLERANCE:raise ValueError("Original zscore/equal-average rank ensemble replay changed")
    rank_replay=dict(compared_visible_values=int(old_mask.sum())*5,mask_equal=True,
        max_abs_error=max(seed_errors.values()),absolute_tolerance=ABSOLUTE_TOLERANCE,
        per_seed_errors=seed_errors,original_rank_ensemble_max_abs_error=ensemble_error,
        shared_state_hash_before=state_before,shared_state_hash_after=state_after)
    (output/"validation").mkdir();(output/"signals").mkdir()
    np.savez_compressed(output/"validation/validation2024.npz",expected_returns=val_mu,
        ensemble_expected_return=ensemble_val,rank_scores=val_rank,visible_mask=val_mask,
        raw_y3=raw_y3,arithmetic_y3=arithmetic_y3,label_end_time_ns=val_ends,
        earliest_execution_time_ns=val_entries,signal_time_ns=val_times,dates=research_panel.days[val_indices],
        symbols=symbols,seeds=np.asarray(SEEDS),mature_section_mask=val_mature)
    metrics={str(seed):validation_metrics(val_mu[i],val_rank[i],arithmetic_y3,val_mask,val_mature)
             for i,seed in enumerate(SEEDS)}
    # Raw rank only provides a reference ranking; returns are never standardized.
    original_rank_val_ensemble,_,_=infer.ensemble(val_rank,val_mask)
    metrics["ensemble"]=validation_metrics(ensemble_val,original_rank_val_ensemble,arithmetic_y3,val_mask,val_mature)
    write(output/"validation/metrics.json",metrics)
    dates=full_panel.days[future_indices];times=full_panel.times[future_indices]
    membership=np.stack([np.asarray(full_panel.members[int(t)],np.bool_) for t in future_indices])
    np.savez_compressed(output/"signals/scores.npz",scores=ensemble_future,ensemble_raw_scores=ensemble_future,
        dates=dates,signal_time_ns=times,symbols=symbols)
    np.savez_compressed(output/"signals/mask.npz",mask=old_mask,membership=membership,dates=dates,signal_time_ns=times,symbols=symbols)
    np.savez_compressed(output/"signals/seed_scores.npz",raw_scores=future_mu,seeds=np.asarray(SEEDS),dates=dates,symbols=symbols)
    np.savez_compressed(output/"signals/rank_replay.npz",raw_rank_scores=future_rank,rank_ensemble=rank_scores,
        dates=dates,signal_time_ns=times,symbols=symbols,seeds=np.asarray(SEEDS))
    inference=dict(label_values_read=False,label_end_values_read=False,future_label_values_read=False,
        future_label_end_values_read=False,guard_type="original infer_signals.LabelHeaderOnly",
        future_dates=420,days2025=243,days2026=177,rank_replay=rank_replay,
        label_finite_filter=False,label_maturity_filter=False,embargo_filter=False,
        prediction_transform="none",mask_source="exact original frozen visible mask")
    signal_manifest=dict(status="complete",model="original B0/full/patch4 plus frozen-encoder arithmetic label3 return head",
        seeds=list(SEEDS),signal_kind=SIGNAL_KIND,target_unit="arithmetic_label3",ensemble_method=ENSEMBLE_METHOD,
        label_values_read=False,label_end_values_read=False,signal_slot=2,signal_local_time="14:00",
        source_sha256=source_receipts,checkpoints=frozen["checkpoints"],heads=heads,inference=inference,
        files={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted((output/"signals").glob("*.npz"))})
    write(output/"signals/manifest.json",signal_manifest)
    validation=dict(path="validation/validation2024.npz",metrics="validation/metrics.json",window=val_window.as_dict(),
        label_values_read=True,label_end_values_read=True,label_read_scope="2024 only",
        raw_outcomes_read_only_mature=True,max_read_label_end_time_ns=int(val_ends[val_mature].max()),
        validation_independent=False,used_to_fit_or_select_return_head=False,
        future_filter_affects_prediction_pool=False,sections=len(val_indices),mature_sections=int(val_mature.sum()))
    manifest=dict(status="complete",model=signal_manifest["model"],signal_kind=SIGNAL_KIND,
        seeds=list(SEEDS),target_unit="arithmetic_label3",ensemble_method=ENSEMBLE_METHOD,
        training_performed=True,shared_parameters_updated=False,rank_head_updated=False,
        protocol=protocol,heads=heads,validation=validation,inference=inference,
        training=dict(label_values_read=True,label_end_values_read=True,label_read_scope="2019--2023 only; original scoped ResearchPanel",
            label3_pairs_sha256=training_pair_digest.hexdigest(),unique_sections_read=len(seen_label_rows),window=train_window.as_dict()),
        source_sha256=source_receipts,input_source_sha256=actual_inputs,
        source_labels_sha256_receipts_only=frozen["label_source_sha256_receipts_only"],
        labels_full_payload_rehashed=False,full_descriptor_sha256=sha(full_descriptor),
        training_descriptor_sha256=sha(training_descriptor),inventory_sha256=sha(args.inventory),
        original_visible_manifest_sha256=sha(args.visible_manifest),asset_resolution=resolver.mapping,
        new_source_sha256={"remote_pipeline.py":sha(__file__),"training.py":sha(training.__file__),"adapter.py":sha(args.adapter_source)},
        resources=resources,total_seconds=time.monotonic()-started,
        limitations=["Original2024 checkpoint selection means2024 validation is not independent",
            "Label3 slot2 exits next13:01--13:05; daily14:00 portfolio holding-horizon mismatch remains",
            "Expected returns are gross proxies without an official same-window benchmark adjustment",
            "Upstream whole_bad visibility is unchanged and not globally certified",
            "2025/2026 are previously exposed research years; no future labels read for inference"],
        files={p.relative_to(output).as_posix():dict(sha256=sha(p),bytes=p.stat().st_size)
               for p in sorted(output.rglob("*")) if p.is_file() and p not in (output/"RUN_STATUS.json",output/"manifest.json")})
    write(output/"manifest.json",manifest)
    write(output/"RUN_STATUS.json",dict(status="complete",completed_seeds=list(SEEDS),seconds=time.monotonic()-started))
    print("RETURN_HEAD_EXPERIMENT_COMPLETE",json.dumps(dict(output=str(output),seconds=manifest["total_seconds"],rank_replay=rank_replay)),flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root",type=Path,required=True)
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--inventory",type=Path,required=True)
    parser.add_argument("--visible-manifest",type=Path,required=True)
    parser.add_argument("--original-visible-signals-dir",type=Path,required=True)
    parser.add_argument("--adapter-source",type=Path,required=True)
    parser.add_argument("--asset-map",type=Path)
    parser.add_argument("--device",default="cuda:0")
    args=parser.parse_args()
    try:
        run(args)
    except Exception as error:
        output=Path(args.output_root)
        if output.exists() and not isinstance(error,FileExistsError):
            write(output/"STOP_EVIDENCE.json",dict(status="stopped",error_type=type(error).__name__,error=str(error),
                traceback=traceback.format_exc(),constraints_relaxed=False,shared_finetuning=False))
            write(output/"RUN_STATUS.json",dict(status="stopped",error_type=type(error).__name__,error=str(error)))
        raise


if __name__=="__main__":
    main()

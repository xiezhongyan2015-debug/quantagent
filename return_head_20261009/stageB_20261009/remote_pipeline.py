"""Stage B: one small shared-layer adaptation; original stage A stays read-only."""
import argparse
import gc
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import traceback
import numpy as np
import torch
import stageA as base
import training
import shared_training as shared
from adapter import RankReturnAdapter

ROOT=Path(__file__).resolve().parent

def extract(adapter,panel,t,device,infer):
    """All visible stocks, without label filters; works with a frozen stage-B encoder."""
    mask=np.asarray(panel.eligible_mask(int(t),80,require_labels=False),bool)
    stocks=np.flatnonzero(mask); features=[]; rank=np.full(panel.U,np.nan,np.float32)
    adapter.eval()
    for start in range(0,len(stocks),infer.CHUNK):
        st=stocks[start:start+infer.CHUNK]; captured=[]
        hook=adapter.rank_model.head.register_forward_pre_hook(lambda module,args:captured.append(args[0].detach()))
        try:
            with torch.inference_mode(): out=adapter(*infer.inference_tensors(panel,int(t),st,device,80))
        finally: hook.remove()
        if len(captured)!=1: raise ValueError('One final original128 representation required')
        features.append(captured[0].cpu().numpy().copy())
        rank[st]=out['rank_score'].cpu().numpy()
    h=np.concatenate(features) if features else np.empty((0,128),np.float32)
    if h.shape!=(int(mask.sum()),128) or not np.isfinite(h).all() or not np.array_equal(np.isfinite(rank),mask):
        raise ValueError('Visible prediction universe/nonfinite representation changed')
    return h,rank,mask

def predict(adapter,fit,panel,indices,device,infer):
    mu=np.full((len(indices),panel.U),np.nan,np.float32); rank=np.full_like(mu,np.nan)
    masks=np.zeros(mu.shape,bool)
    for row,t in enumerate(indices):
        h,rank[row],masks[row]=extract(adapter,panel,int(t),device,infer)
        mu[row,masks[row]]=fit.predict(h).astype(np.float32)
    return mu,rank,masks

def frozen_subset_hash(model,names):
    h=hashlib.sha256()
    for name,p in sorted(model.named_parameters()):
        if name not in names: h.update(name.encode()+p.detach().cpu().numpy().tobytes())
    return h.hexdigest()

def run(args):
    start=time.monotonic(); out=Path(args.output_root).resolve()
    if out.exists() and any(out.iterdir()): raise FileExistsError('New empty output required; no resume or splice')
    out.mkdir(parents=True,exist_ok=True)
    protocol=base.read(ROOT/'protocol.json'); a=Path(args.stageA_output).resolve()
    am=base.read(a/'manifest.json')
    if am.get('status')!='complete' or am.get('shared_parameters_updated') is not False:
        raise ValueError('Completed unchanged-encoder stage A required')
    for name,receipt in am['files'].items():
        p=(a/name).resolve()
        if not p.is_relative_to(a) or base.sha(p)!=receipt['sha256']: raise ValueError('Stage A input changed: '+name)
    frozen=base.read(args.visible_manifest); inventory=base.read(args.inventory)
    if frozen['status']!='complete' or frozen['seeds']!=protocol['seeds']: raise ValueError('Frozen original five seeds')
    resolver=base.AssetResolver(args.original_root,frozen)
    sys.path.insert(0,resolver.actual_root)
    for name,h in am['source_sha256'].items():
        if base.sha(Path(resolver.actual_root)/name)!=h: raise ValueError('Original method source changed: '+name)
    train=importlib.import_module('gru_research.train'); overlay=importlib.import_module('cs_factor_repair_20261007.panel')
    infer=importlib.import_module('gru_index_enhancement_20261008.infer_signals')
    evaluation=importlib.import_module('ranklabel_future_evaluation_20261008.evaluate')
    if not torch.cuda.is_available() and args.device.startswith('cuda'): raise ValueError('CUDA required')
    torch.set_num_threads(2)
    full,dataset,inputs,full_descriptor,train_descriptor=base.safe_full_panel(train,overlay,infer,evaluation,resolver,frozen)
    with base.relocated_research_paths(overlay,resolver): historical=overlay.ResearchPanel.load(train,train_descriptor)
    if historical.fingerprint!=frozen['lineage']['training_dataset_fingerprint'] or historical.symbols!=full.symbols:
        raise ValueError('Original historical scoped panel changed')
    config=frozen['checkpoints']['11']['signature']['config']
    window=training.training_window(historical.trading_days)
    ti=historical.split_indices('train',config,require_labels=False)
    ti=ti[historical.slots[ti]==2]
    vi=np.flatnonzero((historical.days>=training.day_number('2024-01-01')) &
        (historical.days<training.day_number('2025-01-01'))&(historical.slots==2))
    fi=infer.visible_sections(full)
    if len(fi)!=420: raise ValueError('Original420 signal sections required')
    old_dir=Path(args.original_visible_signals_dir)
    for name,receipt in frozen['files'].items():
        if base.sha(old_dir/name)!=receipt['sha256']: raise ValueError('Original signal artifact changed')
    with np.load(old_dir/'mask.npz',allow_pickle=False) as z: oldmask=z['mask'].copy()
    with np.load(old_dir/'seed_scores.npz',allow_pickle=False) as z: oldraw=z['raw_scores'].copy()
    with np.load(old_dir/'scores.npz',allow_pickle=False) as z:
        for name,arr in [('dates',full.days[fi]),('signal_time_ns',full.times[fi]),('symbols',np.asarray(full.symbols))]:
            if not np.array_equal(z[name],arr): raise ValueError('Original signal identity changed')
    state={'status':'running','stage':'loading','completed_seeds':[]}
    def status(stage,seed=None):
        state.update(stage=stage,seconds=time.monotonic()-start)
        if seed is not None: state['seed']=seed
        base.write(out/'RUN_STATUS.json',state); print('STAGE_B',stage,seed,flush=True)
    base.write(out/'protocol.json',protocol)
    val_mu=np.full((5,len(vi),full.U),np.nan,np.float32); val_rank=np.full_like(val_mu,np.nan)
    future_mu=np.full((5,len(fi),full.U),np.nan,np.float32); future_rank=np.full_like(future_mu,np.nan)
    guards={}; head_receipts={}; checkpoint_receipts={}; masks=[]; future_masks=[]
    for si,seed in enumerate(base.SEEDS):
        status('loading_seed',seed); train.seed_everything(seed)
        flags=dict(cudnn_deterministic=bool(torch.backends.cudnn.deterministic),cudnn_benchmark=bool(torch.backends.cudnn.benchmark),
            cuda_matmul_allow_tf32=bool(torch.backends.cuda.matmul.allow_tf32),cudnn_allow_tf32=bool(torch.backends.cudnn.allow_tf32))
        if flags!=dict(cudnn_deterministic=True,cudnn_benchmark=False,cuda_matmul_allow_tf32=False,cudnn_allow_tf32=False):
            raise ValueError('Original deterministic FP32 flags required')
        model,cp=base.load_frozen_model(train,resolver,inventory,frozen,seed,args.device)
        adapter=RankReturnAdapter(model,target_unit='arithmetic_label3').to(args.device).eval()
        oldfit=training.load_return_head(a/am['heads'][str(seed)]['path'])
        training.install_return_head(adapter,oldfit)
        pref=[]
        swap=np.flatnonzero(full.days[fi]==training.day_number('2025-06-16'))
        if len(swap)!=1: raise ValueError('Replacement date missing')
        for row in [0,int(swap[0]),len(fi)-1]:
            _,rank,mask=extract(adapter,full,int(fi[row]),args.device,infer)
            error=float(np.abs(rank[mask].astype(float)-oldraw[si,row,mask].astype(float)).max())
            if not np.array_equal(mask,oldmask[row]) or error>1e-6: raise ValueError('Pre-fit original rank replay failed')
            pref.append(dict(row=row,max_abs_error=error))
        status('train_only_statistics',seed); records=[]; pairhash=hashlib.sha256(); rv=[]; ym=[]; y2=[]
        for t in ti:
            t=int(t)
            if not window.start_day<=int(historical.days[t])<window.cutoff_day: raise ValueError('Training scope')
            h,rank,mask=extract(adapter,historical,t,args.device,infer)
            raw=np.asarray(historical.labels[t,mask,2],np.float32)
            end=int(historical.label_end_times[t,2]); entry=int(historical.earliest_execution_times[t]); stamp=int(historical.times[t])
            pairhash.update(np.asarray([stamp,end,entry],np.int64).tobytes()+mask.tobytes()+raw.tobytes())
            with np.errstate(over='ignore',invalid='ignore'): y=np.expm1(raw.astype(np.float64))
            fit=np.isfinite(y)&np.isfinite(raw)&(entry>stamp)&(end>entry)&(end<window.cutoff_ns)
            if len(raw)<100 or fit.sum()<100: continue
            ym.append(float(y[fit].mean())); y2.append(float(np.square(y[fit]).mean()))
            rv.append(float(np.var(rank[mask].astype(float))))
            records.append(dict(t=t,mask=mask,y=y,fit=fit,raw=raw,rank=rank[mask],end=end,entry=entry,stamp=stamp))
        if pairhash.hexdigest()!=am['training']['label3_pairs_sha256']: raise ValueError('Original stage-A label pair digest changed')
        return_scale2=float(np.mean(y2)-np.mean(ym)**2); rank_scale2=float(np.mean(rv))
        if len(records)!=1211 or sum(int(r['fit'].sum()) for r in records)!=569784: raise ValueError('Frozen fitting cohort changed')
        names=shared.enable_top_gru(adapter); frozen_before=frozen_subset_hash(adapter.rank_model,names)
        teacher_hash=base.state_hash(adapter.teacher); opt=shared.optimizer(adapter); losses=[]
        status('one_epoch_shared_adaptation',seed)
        for i,r in enumerate(records):
            tensors=infer.inference_tensors(historical,r['t'],np.flatnonzero(r['mask']),args.device,80)
            loss=shared.train_section(adapter,opt,tensors,torch.as_tensor(r['y'],dtype=torch.float32,device=args.device),
                torch.as_tensor(r['fit'],device=args.device),return_scale2,rank_scale2,
                torch.as_tensor(r['rank'],device=args.device))
            losses.append(dict(date=str(np.datetime64(int(historical.days[r['t']]),'D')),**loss))
            if (i+1)%100==0:
                state['sections_completed']=i+1; status('one_epoch_shared_adaptation',seed)
        for p in adapter.parameters(): p.requires_grad_(False)
        adapter.eval(); status('final_ridge_and_train_rank_gate',seed); drift=[]
        def batches():
            for r in records:
                h,rank,mask=extract(adapter,historical,r['t'],args.device,infer)
                if not np.array_equal(mask,r['mask']): raise ValueError('Final fitting mask changed')
                drift.append(float(np.mean(np.square(rank[mask].astype(float)-r['rank'].astype(float)))))
                yield dict(h=h,raw_y3=r['raw'],signal_time_ns=r['stamp'],label_end_time_ns=r['end'],earliest_execution_time_ns=r['entry'])
        fit=training.fit_ridge_from_batches(batches(),window=window)
        normalized_drift=float(np.sqrt(np.mean(drift)/rank_scale2))
        trust=shared.project_trust(adapter)
        if frozen_subset_hash(adapter.rank_model,names)!=frozen_before or base.state_hash(adapter.teacher)!=teacher_hash:
            raise ValueError('Frozen rank head/other original parameters or teacher changed')
        guard=dict(pre_fit_original_rank=pref,runtime_flags=flags,training_sections=len(records),fitting_pairs=569784,
            pair_digest=pairhash.hexdigest(),return_variance=return_scale2,teacher_within_section_rank_variance=rank_scale2,
            normalized_train_rank_rms_drift=normalized_drift,maximum_allowed_rank_drift=shared.MAX_TRAIN_RANK_DRIFT,
            relative_shared_displacement=trust,original_frozen_subset_unchanged=True,teacher_unchanged=True)
        guards[str(seed)]=guard; base.write(out/'training/guards.json',guards)
        base.write(out/f'training/seed{seed}_steps.json',losses)
        if normalized_drift>shared.MAX_TRAIN_RANK_DRIFT: raise ValueError('Train-only rank preservation gate failed; no retune')
        with torch.no_grad():
            adapter.return_head.weight.copy_(torch.as_tensor(fit.weight,dtype=torch.float32,device=args.device).reshape(1,128))
            adapter.return_head.bias.copy_(torch.as_tensor([fit.bias],dtype=torch.float32,device=args.device))
        head=out/f'heads/seed{seed}_return_head.npz'; head.parent.mkdir(exist_ok=True)
        metadata=dict(fit.metadata,stage=protocol['stage'],shared_parameters_updated=True,rank_head_updated=False,
            original_checkpoint_sha256=frozen['checkpoints'][str(seed)]['sha256'])
        np.savez_compressed(head,weight=fit.weight.astype(np.float32),bias=np.asarray(fit.bias,np.float32),
            metadata_utf8=np.frombuffer(json.dumps(metadata,sort_keys=True).encode(),np.uint8))
        ck=out/f'checkpoints/seed{seed}_dual_head.pt'; ck.parent.mkdir(exist_ok=True)
        torch.save(dict(state_dict=adapter.serving_state_dict(),target_unit='arithmetic_label3',seed=seed,
            original_signature=frozen['checkpoints'][str(seed)]['signature'],protocol_sha256=base.sha(ROOT/'protocol.json')),ck)
        head_receipts[str(seed)]=dict(path=head.relative_to(out).as_posix(),sha256=base.sha(head),added_parameters=129,
            shared_parameters_updated=True,training_sections=1211,fitting_pairs=569784)
        checkpoint_receipts[str(seed)]=dict(path=ck.relative_to(out).as_posix(),sha256=base.sha(ck),teacher_included=False)
        status('validation2024_descriptive_only',seed)
        val_mu[si],val_rank[si],vm=predict(adapter,fit,historical,vi,args.device,infer); masks.append(vm)
        status('future_label_blocked_inference',seed)
        future_mu[si],future_rank[si],fm=predict(adapter,fit,full,fi,args.device,infer); future_masks.append(fm)
        if not np.array_equal(fm,oldmask): raise ValueError('Original visible future cohort changed')
        if base.sha(cp)!=frozen['checkpoints'][str(seed)]['sha256']: raise ValueError('Original checkpoint modified')
        state['completed_seeds'].append(seed); status('seed_complete',seed)
        del model,adapter,opt,records; gc.collect(); torch.cuda.empty_cache()
    if not all(np.array_equal(masks[0],m) for m in masks): raise ValueError('Validation universe differs between seeds')
    status('assemble_frozen_exports')
    # Validation outcomes are accessed only after the fixed candidate is trained.
    vw=training.training_window(historical.trading_days,start='2024-01-01',end='2025-01-01')
    ve=np.asarray(historical.label_end_times[vi,2],np.int64); ent=np.asarray(historical.earliest_execution_times[vi],np.int64)
    stamps=np.asarray(historical.times[vi],np.int64); mature=(ent>stamps)&(ve>ent)&(ve<vw.cutoff_ns)
    raw=np.full((len(vi),full.U),np.nan,np.float32)
    for row in np.flatnonzero(mature): raw[row]=np.asarray(historical.labels[int(vi[row]),:,2],np.float32)
    target=np.expm1(raw.astype(float)); vm=masks[0]; vmean=base.average_return_units(val_mu,vm)
    fmean=base.average_return_units(future_mu,oldmask); vr,_,_=infer.ensemble(val_rank,vm)
    metrics={str(s):base.validation_metrics(val_mu[i],val_rank[i],target,vm,mature) for i,s in enumerate(base.SEEDS)}
    metrics['ensemble']=base.validation_metrics(vmean,vr,target,vm,mature)
    (out/'validation').mkdir(); (out/'signals').mkdir(); symbols=np.asarray(full.symbols,dtype=np.str_)
    np.savez_compressed(out/'validation/validation2024.npz',expected_returns=val_mu,ensemble_expected_return=vmean,
        rank_scores=val_rank,visible_mask=vm,raw_y3=raw,arithmetic_y3=target,label_end_time_ns=ve,
        earliest_execution_time_ns=ent,signal_time_ns=stamps,dates=historical.days[vi],symbols=symbols,
        seeds=np.asarray(base.SEEDS),mature_section_mask=mature)
    base.write(out/'validation/metrics.json',metrics)
    dates=full.days[fi]; times=full.times[fi]
    np.savez_compressed(out/'signals/scores.npz',scores=fmean,ensemble_raw_scores=fmean,dates=dates,signal_time_ns=times,symbols=symbols)
    membership=np.stack([np.asarray(full.members[int(t)],bool) for t in fi])
    np.savez_compressed(out/'signals/mask.npz',mask=oldmask,membership=membership,dates=dates,signal_time_ns=times,symbols=symbols)
    np.savez_compressed(out/'signals/seed_scores.npz',raw_scores=future_mu,seeds=np.asarray(base.SEEDS),dates=dates,symbols=symbols)
    np.savez_compressed(out/'signals/rank_outputs.npz',raw_rank_scores=future_rank,dates=dates,signal_time_ns=times,symbols=symbols)
    inference=dict(label_values_read=False,label_end_values_read=False,future_label_values_read=False,
        future_label_end_values_read=False,guard_type='original infer_signals.LabelHeaderOnly',future_dates=420,
        visible_mask_equal_original=True,label_finite_filter=False,label_maturity_filter=False,
        future_rank_drift_is_measured_not_parameter_selection=True,
        future_rank_rms_difference=float(np.sqrt(np.mean(np.square(future_rank[:,oldmask].astype(float)-oldraw[:,oldmask].astype(float))))))
    sm=dict(status='complete',model='B0/full/patch4 conservative second-layer adaptation plus arithmetic label3 head',
        seeds=list(base.SEEDS),signal_kind=base.SIGNAL_KIND,target_unit='arithmetic_label3',ensemble_method=base.ENSEMBLE_METHOD,
        label_values_read=False,label_end_values_read=False,signal_slot=2,signal_local_time='14:00',
        checkpoints=checkpoint_receipts,original_checkpoints=frozen['checkpoints'],heads=head_receipts,inference=inference,
        source_sha256=am['source_sha256'],files={p.name:dict(sha256=base.sha(p),bytes=p.stat().st_size) for p in (out/'signals').glob('*.npz')})
    base.write(out/'signals/manifest.json',sm)
    manifest=dict(status='complete',stage=protocol['stage'],training_performed=True,shared_parameters_updated=True,
        rank_head_updated=False,hyperparameter_search=False,seeds=list(base.SEEDS),protocol=protocol,heads=head_receipts,
        checkpoints=checkpoint_receipts,training_guards=guards,inference=inference,
        stageA_manifest_sha256=base.sha(a/'manifest.json'),input_source_sha256=inputs,
        source_sha256=am['source_sha256'],training_label_scope='2019--2023 only with original two-session maturity embargo',
        validation=dict(independent=False,used_for_fit_or_selection=False,mature_sections=int(mature.sum())),
        original_visible_manifest_sha256=base.sha(args.visible_manifest),seconds=time.monotonic()-start,
        new_source_sha256={p.name:base.sha(p) for p in ROOT.glob('*.py')},
        files={p.relative_to(out).as_posix():dict(sha256=base.sha(p),bytes=p.stat().st_size) for p in out.rglob('*')
            if p.is_file() and p.name not in ['RUN_STATUS.json','manifest.json']})
    # The nested signals manifest is also a required artifact.
    p=out/'signals/manifest.json'; manifest['files']['signals/manifest.json']=dict(sha256=base.sha(p),bytes=p.stat().st_size)
    base.write(out/'manifest.json',manifest); state['status']='complete'; status('complete')
    return manifest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['original-root','output-root','inventory','visible-manifest','original-visible-signals-dir','stageA-output']:
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--device',default='cuda:0'); args=p.parse_args()
    try: run(args)
    except Exception as error:
        out=Path(args.output_root)
        if out.exists() and not isinstance(error,FileExistsError):
            base.write(out/'STOP_EVIDENCE.json',dict(status='stopped',error_type=type(error).__name__,error=str(error),
                traceback=traceback.format_exc(),retry_or_hyperparameter_change=False))
            base.write(out/'RUN_STATUS.json',dict(status='stopped',error=str(error)))
        raise

if __name__=='__main__': main()

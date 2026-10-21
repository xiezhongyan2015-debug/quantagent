"""Independently compare real stage-B serving checkpoints to frozen originals.

No datasets, returns or validation labels are loaded. Checks parameter scope,
relative trust caps, old rank head identity, extra129 parameters, teacher removal,
head/checkpoint coherence, and all manifest file hashes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import torch

ALLOWED={'gru.weight_ih_l1','gru.weight_hh_l1','gru.bias_ih_l1','gru.bias_hh_l1'}
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''): h.update(b)
    return h.hexdigest()
def read(p): return json.loads(Path(p).read_text(encoding='utf8'))
def run(output,frozen_manifest,destination):
    output=Path(output).resolve(); m=read(output/'manifest.json'); old=read(frozen_manifest)
    if m['status']!='complete' or m['rank_head_updated'] is not False: raise ValueError('Complete fixed-rank-head training required')
    files={}
    for name,r in m['files'].items():
        p=(output/name).resolve()
        if not p.is_relative_to(output) or sha(p)!=r['sha256'] or p.stat().st_size!=r['bytes']:
            raise ValueError('Artifact hash/size mismatch: '+name)
        files[name]=r['sha256']
    rows=[]
    for seed in [11,22,33,44,55]:
        orig_path=Path(old['checkpoints'][str(seed)]['path'])
        if sha(orig_path)!=old['checkpoints'][str(seed)]['sha256']: raise ValueError('Original checkpoint changed')
        orig=torch.load(orig_path,map_location='cpu',weights_only=False)
        serving=torch.load(output/m['checkpoints'][str(seed)]['path'],map_location='cpu',weights_only=False)
        if serving['original_signature']!=orig['signature']: raise ValueError('Original signature altered')
        values=serving['state_dict']
        if any(n.startswith('teacher.') for n in values): raise ValueError('Teacher exported into serving model')
        selected={n[len('rank_model.'):]:v for n,v in values.items() if n.startswith('rank_model.')}
        if set(selected)!=set(orig['model']): raise ValueError('Encoder/readout structure changed')
        changed=[]; ratios={}
        for name,b in orig['model'].items():
            v=selected[name]
            if v.dtype!=b.dtype or v.shape!=b.shape or not bool(torch.isfinite(v).all()): raise ValueError('Tensor identity/nonfinite')
            if name in ALLOWED:
                relative=float((v.double()-b.double()).norm()/b.double().norm())
                if relative>.005+2e-7: raise ValueError('Per-tensor trust cap exceeded')
                ratios[name]=relative
                if not torch.equal(v,b): changed.append(name)
            elif not torch.equal(v,b): raise ValueError('Frozen tensor changed: '+name)
        if set(values)-{'rank_model.'+n for n in selected}!={'return_head.weight','return_head.bias'}:
            raise ValueError('Unexpected extra serving modules')
        extra=sum(values[n].numel() for n in ['return_head.weight','return_head.bias'])
        if extra!=129: raise ValueError('Only129 additional serving parameters allowed')
        with np.load(output/m['heads'][str(seed)]['path'],allow_pickle=False) as h:
            if not np.array_equal(h['weight'],values['return_head.weight'].numpy().reshape(-1)):
                raise ValueError('NPZ readout and checkpoint weights differ')
            if not np.array_equal(h['bias'],values['return_head.bias'].numpy().reshape(())):
                raise ValueError('NPZ readout and checkpoint intercept differ')
            meta=json.loads(h['metadata_utf8'].tobytes().decode())
            if meta['shared_parameters_updated'] is not True or meta['rank_head_updated'] is not False:
                raise ValueError('Head metadata falsifies shared-update scope')
        rows.append(dict(seed=seed,changed_original_tensors=changed,relative_displacement=ratios,
            nonselected_original_tensors_identical=True,rank_head_identical=True,teacher_exported=False,
            additional_serving_parameters=extra,original_serving_parameter_count=sum(v.numel() for v in selected.values()),
            fitting_pairs=meta['fitting_pairs'],training_sections=meta['training_sections']))
    result=dict(status='passed_independent_model_and_hash_audit',seeds=rows,files_verified=len(files),
        manifest_sha256=sha(output/'manifest.json'),original_manifest_sha256=sha(frozen_manifest),
        validation_or_future_outcomes_read=False,model_or_checkpoint_modified=False,
        audit_source_sha256=sha(__file__),file_sha256=files)
    destination=Path(destination)
    if destination.exists(): raise FileExistsError('Preserve previous independent model audit')
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes((json.dumps(result,indent=2,ensure_ascii=False)+'\n').encode())
    print(json.dumps({k:v for k,v in result.items() if k!='file_sha256'},ensure_ascii=False))
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--frozen-manifest',required=True);p.add_argument('--destination',required=True)
    a=p.parse_args();run(a.output,a.frozen_manifest,a.destination)

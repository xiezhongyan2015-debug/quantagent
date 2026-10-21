"""Prepared fixed35 R2 checkpoint provenance/finite audit; never infer or train.

Default prints metadata only. Execute requires a root-pinned closed native
manifest, all30 inner/once-outer completions, all5 fixed final fits and freeze.
Each of35 selected checkpoint products is physically read once, SHA/STAT bound,
then weights_only CPU loaded from those retained bytes. No pickle fallback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import zipfile

PROTOCOL_SHA = 'e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f'
RUNTIME_MANIFEST_SHA = '8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809'
META_SHA = '2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849'
META_FP = '6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4'
SEEDS = (11,22,33,44,55)
ARMS = {'control':3e-4,'lower_lr':1e-4}
FOLDS = ('2021','2022','2023')
CHUNK = 65536
BINDING_KEYS = ('protocol_sha256','new_source_manifest_sha256','causal_receipt_sha256',
                'causal_transport_manifest_sha256','dataset_fingerprint')
HEADER_FIELDS = set(BINDING_KEYS) | {'fold','arm','seed','checkpoint_role','checkpoint_sha256',
                 'inner_result_sha256','stage','complete','outer_attempt_sha256',
                 'outer_is_virgin_OOS','used_for_training_or_checkpoint','eligible_sections_expected'}


def require(ok, message):
    if not ok: raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def stat(path):
    p=Path(path);s=p.stat()
    return {'path':str(p.resolve()),'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'inode':s.st_ino,'device':s.st_dev}


def deadline_check(deadline):
    require(time.monotonic()<deadline,'bounded CPU audit deadline; preserve attempt, no automatic retry')


def read_bound(ref, cap, deadline, observations):
    p=Path(ref['path']);before=stat(p)
    require(type(ref['bytes']) is int and 0<ref['bytes']<=cap and before['bytes']==ref['bytes'],'closed file size budget')
    record={'path':str(p.resolve()),'bytes_read':0,'expected_sha256':ref['sha256'],
            'STAT_before':before,'single_physical_pass':True,'full_SHA_and_EOF_verified':False}
    observations.append(record);h=hashlib.sha256();parts=[]
    try:
        with p.open('rb') as handle:
            remaining=ref['bytes']
            while remaining:
                deadline_check(deadline);part=handle.read(min(CHUNK,remaining));require(part,'truncated bounded artifact')
                h.update(part);parts.append(part);record['bytes_read']+=len(part);remaining-=len(part)
            deadline_check(deadline);require(handle.read(1)==b'','source grew beyond pinned byte length')
        record['actual_sha256']=h.hexdigest();record['STAT_after']=stat(p)
        require(h.hexdigest()==ref['sha256'] and record['STAT_after']==before,'same-read SHA/STAT identity differs')
        record['full_SHA_and_EOF_verified']=True
        return b''.join(parts)
    finally:
        record['partial_or_complete_read_sha256']=h.hexdigest()


def json_doc(ref, deadline, observations):
    return json.loads(read_bound(ref,4*1024**2,deadline,observations))


def outer_header_only(body):
    """Project top-level provenance; never decode records or summary values.

    The skipped source bytes are SHA-bound native producer output, not a score
    table. Lexical scanning checks quote/container balance and does not parse
    ICs, RankICs, predictions, or select a model.
    """
    s=body.decode('utf-8');decoder=json.JSONDecoder();i=0;result={};seen=set()
    def whitespace(pos):
        while pos<len(s) and s[pos].isspace():pos+=1
        return pos
    i=whitespace(i);require(i<len(s) and s[i]=='{','outer native object required');i+=1;after_comma=False
    while True:
        i=whitespace(i)
        if i<len(s) and s[i]=='}':require(not after_comma,'outer trailing comma');i+=1;break
        key,end=decoder.raw_decode(s,i);require(type(key) is str and key not in seen,'duplicate outer top-level key')
        seen.add(key);i=whitespace(end);require(i<len(s) and s[i]==':','outer header colon missing');i=whitespace(i+1)
        start=i;stack=[];quoted=False;escaped=False
        while i<len(s):
            c=s[i]
            if quoted:
                if escaped:escaped=False
                elif c=='\\':escaped=True
                elif c=='"':quoted=False
            else:
                if c=='"':quoted=True
                elif c in '[{':stack.append(c)
                elif c in ']}':
                    if not stack:break
                    require((stack.pop(),c) in (('[',']'),('{','}')),'outer nested delimiter mismatch')
                elif c==',' and not stack:break
            i+=1
        require(not quoted and not stack and i>start,'outer value truncated/unbalanced')
        if key in HEADER_FIELDS:result[key]=json.loads(s[start:i])
        i=whitespace(i);require(i<len(s),'outer object unterminated')
        if s[i]==',':i+=1;after_comma=True
        elif s[i]=='}':i+=1;break
        else:raise ValueError('outer top-level delimiter missing')
    require(whitespace(i)==len(s) and {'records','summary'}<=seen,'outer trailing bytes or missing original observation fields')
    return result


def publish(path, doc):
    p=Path(path);partial=p.with_name(p.name+'.partial')
    with partial.open('xb') as f:f.write((json.dumps(doc,indent=2,allow_nan=False,ensure_ascii=False)+'\n').encode())
    os.replace(partial,p)


def prepared():
    return {'status':'PREPARED_ONLY_ALL35_FREEZE_AND_NATIVE_SHA_MANIFEST_REQUIRED',
            'schema_version':'PREPARED_R2_FIXED35_CPU_CHECKPOINT_AUDIT_V1',
            'roster':{'diagnostic_best_inner':30,'final_fixed_last':5},
            'checkpoint_body_reads':0,'Torch_imports':0,'forward_or_training':False,
            'outer_metric_values_decoded':False,'weights_only':True,'pickle_fallback':False,
            'automatic_retry':False,'full_PLAN_complete':False,'economic_acceptance':False}


def gate(contract, deadline, observations):
    root=Path(contract['original_source_root']).resolve();run=Path(contract['actual_native_run_directory']).resolve()
    original=Path(contract['original_run_directory'])
    require(original.is_absolute() and str(original)==contract['original_run_directory'],'explicit original path identity required')
    refs=contract['source_refs'];require(set(refs)=={'protocol','runtime_manifest','meta','config'},'exact small source roles')
    protocol=json_doc(refs['protocol'],deadline,observations);runtime=json_doc(refs['runtime_manifest'],deadline,observations)
    meta=json_doc(refs['meta'],deadline,observations);config=json_doc(refs['config'],deadline,observations)
    require(refs['protocol']['sha256']==PROTOCOL_SHA and refs['runtime_manifest']['sha256']==RUNTIME_MANIFEST_SHA
            and refs['meta']['sha256']==META_SHA and sha(canonical(meta))==META_FP,'original protocol/source/meta pins differ')
    expected_sources={**protocol['original_dataset']['original_source19_sha256'],**runtime['files_sha256']}
    require(len(expected_sources)==27 and set(contract['source27'])==set(expected_sources),'original19 + runtime8 closed source roster required')
    for name,digest in expected_sources.items():
        ref=contract['source27'][name]
        require(ref['sha256']==digest and str(Path(ref['path']).resolve())==str(root/name),'actual source root/relative identity differs')
        read_bound(ref,4*1024**2,deadline,observations)
    native=contract['native_files'];required={'run_identity.json','freeze.json','selection.json','budget.json'}
    plans=[{'fold':f,'arm':a,'seed':s} for f in FOLDS for a in ARMS for s in SEEDS]
    for p in plans:
        d=f"diagnostic/{p['fold']}/{p['arm']}/seed_{p['seed']}"
        required.update(d+'/'+n for n in ('identity.json','result.json','epochs.jsonl','outer_attempt.json',
                                         'outer_result.json','best_inner_summary.json','best_inner_sections.json'))
    for seed in SEEDS:required.update(f'final/seed_{seed}/'+n for n in ('identity.json','result.json','epochs.jsonl'))
    require(set(native)==required,'all30 diagnostics+onceouter+all5 final exact native small roster required')
    for relative,ref in native.items():
        require(str(Path(ref['path']).resolve())==str(run/relative) and ref['identity_path']==str(original/relative),
                'native copy-to-original exact relative mapping differs')
    completion=json_doc(contract['root_completion_receipt'],deadline,observations)
    require(completion['status']=='COMPLETE_R2_30_DIAGNOSTICS_5_FINAL_FREEZE_NATIVE_EVIDENCE_BOUND'
            and completion['diagnostic_complete_count']==30 and completion['outer_once_complete_count']==30
            and completion['final_complete_count']==5 and completion['native_files_sha256']=={k:v['sha256'] for k,v in native.items()},
            'root out-of-band actual native completeness receipt missing/different')
    binding=json_doc(native['run_identity.json'],deadline,observations)
    require(set(binding)==set(BINDING_KEYS) and binding['protocol_sha256']==PROTOCOL_SHA
            and binding['new_source_manifest_sha256']==RUNTIME_MANIFEST_SHA and binding['dataset_fingerprint']==META_FP
            and binding['causal_receipt_sha256']=='95370519b5974c6724cef641506eeb113b4d33fc76fdb575182f7783fe2ca3cc'
            and binding['causal_transport_manifest_sha256']=='685c131667613b4a6d7fc2ff49713d889faadd3590ebaf986e624bd1b718dc9c',
            'actual R2 original runtime signature differs')
    frozen=json_doc(native['freeze.json'],deadline,observations);selected=json_doc(native['selection.json'],deadline,observations)
    budget=json_doc(native['budget.json'],deadline,observations)
    require(all(frozen.get(k)==v for k,v in binding.items()) and frozen['seeds']==list(SEEDS)
            and frozen['checkpoint_role']=='fixed_last_epoch' and frozen['variant']=='pv' and frozen['training_label']==3
            and isinstance(frozen['frozen_at_utc'],str),'actual final freeze incomplete/wrong role')
    core={k:v for k,v in frozen.items() if k not in ('freeze_id','frozen_at_utc')}
    require(frozen['freeze_id']==sha(canonical(core))[:24] and frozen['selection_sha256']==native['selection.json']['sha256']
            and frozen['budget_sha256']==native['budget.json']['sha256'],'freeze content/selection/budget SHA differs')
    arm=selected['selected_arm'];require(arm in ARMS and selected['selected_learning_rate']==ARMS[arm]
            and all(selected.get(k)==v for k,v in binding.items()),'existing selection identity differs; audit cannot choose')
    expected_decisions={f"diagnostic/{p['fold']}/{p['arm']}/seed_{p['seed']}/{n}" for p in plans for n in ('result.json','outer_result.json')}
    require(set(selected['diagnostic_evidence_sha256'])==expected_decisions
            and all(selected['diagnostic_evidence_sha256'][k]==native[k]['sha256'] for k in expected_decisions),
            'selection not bound to all60 actual decision files')
    entries=[];inner_epochs=[]
    for p in plans:
        d=f"diagnostic/{p['fold']}/{p['arm']}/seed_{p['seed']}";fold=next(x for x in protocol['folds'] if x['id']==p['fold'])
        signature={**binding,'config':{**config,'learning_rate':ARMS[p['arm']]},'seed':p['seed'],'label':3,'variant':'pv',
                   'stage':'diagnostic','arm':p['arm'],'fold':p['fold'],'fit_period':fold['fit'],'inner_period':fold['inner']}
        identity=json_doc(native[d+'/identity.json'],deadline,observations);result=json_doc(native[d+'/result.json'],deadline,observations)
        require(identity==signature and result['signature']==signature and result['complete'] is True
                and all(result.get(k)==v for k,v in signature.items()),'actual diagnostic signature/complete differs')
        raw=read_bound(native[d+'/epochs.jsonl'],4*1024**2,deadline,observations)
        epochs=[json.loads(line) for line in raw.decode().splitlines()];require([x['epoch'] for x in epochs]==list(range(1,len(epochs)+1))
                and result['epochs_completed']==len(epochs) and 1<=len(epochs)<=40,'diagnostic actual epoch continuity/budget differs')
        e=result['best_inner_epoch'];require(type(e) is int and 1<=e<=len(epochs)
                and type(result['best_inner_IC']) in (float,int) and math.isfinite(result['best_inner_IC'])
                and epochs[e-1]['inner_y4_IC']==result['best_inner_IC']
                and all(x['phase']=='r2_diagnostic' and all(x[k]==v for k,v in p.items()) for x in epochs),
                'actual best inner epoch role/metric differs')
        require(len(epochs)==40 or epochs[-1]['bad_epochs']==8,'diagnostic completion missing actual stop boundary')
        require(result['epoch_log_sha256']==native[d+'/epochs.jsonl']['sha256']
                and result['inner_records_sha256']==native[d+'/best_inner_sections.json']['sha256']
                and result['inner_summary_sha256']==native[d+'/best_inner_summary.json']['sha256'],'diagnostic original report hashes differ')
        for n in ('best_inner_sections.json','best_inner_summary.json'):read_bound(native[d+'/'+n],4*1024**2,deadline,observations)
        attempt=json_doc(native[d+'/outer_attempt.json'],deadline,observations)
        outer=outer_header_only(read_bound(native[d+'/outer_result.json'],4*1024**2,deadline,observations))
        expected={**binding,**p,'checkpoint_role':'inner_best','checkpoint_sha256':result['best_inner_checkpoint_sha256'],
                  'inner_result_sha256':native[d+'/result.json']['sha256']}
        require(all(outer.get(k)==v for k,v in expected.items()) and all(attempt.get(k)==v for k,v in expected.items())
                and outer['complete'] is True and outer['stage']=='diagnostic_outer'
                and attempt['stage']=='diagnostic_outer_attempt' and outer['outer_attempt_sha256']==native[d+'/outer_attempt.json']['sha256']
                and outer['outer_is_virgin_OOS'] is False and outer['used_for_training_or_checkpoint'] is False,
                'actual once-outer source/header incomplete or wrong role')
        require(type(outer['eligible_sections_expected']) is int and outer['eligible_sections_expected']>0,
                'outer complete header lacks real section population')
        entries.append({'relative_path':d+'/best_inner.pt','signature':signature,'checkpoint_sha256':result['best_inner_checkpoint_sha256'],
                        'expected_epoch_zero_based':e-1,'expected_best_ic':result['best_inner_IC'],'expected_checkpoint_role':'inner_best'})
        if p['arm']==arm:inner_epochs.append(e)
    derived={**binding,'epochs':max(1,min(40,int(statistics.median(inner_epochs)))),'selected_arm':arm,
             'inner_best_epochs':inner_epochs,'derivation':'integer median of fifteen chosen-arm inner best epochs',
             'outer_or_2024_2025_2026_used_for_budget':False,'selection_sha256':native['selection.json']['sha256']}
    require(len(inner_epochs)==15 and budget==derived,'existing fixed budget not original selected15 median')
    expected_frozen={str(original/f'final/seed_{s}/last.pt') for s in SEEDS}
    require(set(frozen['checkpoint_hashes'])==expected_frozen,'freeze does not contain exact five original final paths')
    for seed in SEEDS:
        d=f'final/seed_{seed}'
        signature={**binding,'config':{**config,'learning_rate':ARMS[arm]},'seed':seed,'label':3,'variant':'pv','stage':'final','arm':arm,
            'fit_period':['2019-01-01','2024-01-01'],'fixed_epoch_budget':budget['epochs'],'checkpoint_role':'fixed_last_epoch',
            'selection_sha256':native['selection.json']['sha256'],'budget_sha256':native['budget.json']['sha256']}
        identity=json_doc(native[d+'/identity.json'],deadline,observations);result=json_doc(native[d+'/result.json'],deadline,observations)
        require(identity==signature and result['signature']==signature and result['complete'] is True
                and result['epochs_completed']==budget['epochs'] and all(result.get(k)==v for k,v in signature.items())
                and result['2024_used_for_training_or_checkpoint_selection'] is False,'five final actual identity/budget incomplete')
        epochs=[json.loads(x) for x in read_bound(native[d+'/epochs.jsonl'],4*1024**2,deadline,observations).decode().splitlines()]
        require(result['epoch_log_sha256']==native[d+'/epochs.jsonl']['sha256']
                and [x['epoch'] for x in epochs]==list(range(1,budget['epochs']+1))
                and all(x['phase']=='r2_final' and x['arm']==arm and x['seed']==seed
                        and x['validation_read'] is False and x['learning_rate']==ARMS[arm] for x in epochs),
                'final fixed-last/constant-LR/no-validation original trace differs')
        require(result['last_checkpoint_sha256']==frozen['checkpoint_hashes'][str(original/(d+'/last.pt'))],'actual final checkpoint not frozen')
        entries.append({'relative_path':d+'/last.pt','signature':signature,'checkpoint_sha256':result['last_checkpoint_sha256'],
                        'expected_epoch_zero_based':budget['epochs']-1,'expected_epoch_record':epochs[-1],
                        'expected_checkpoint_role':'fixed_last_epoch'})
    cprefs=contract['checkpoints'];require(set(cprefs)=={e['relative_path'] for e in entries} and len(entries)==35,'fixed35 checkpoint roster incomplete/duplicate')
    for e in entries:
        ref=cprefs[e['relative_path']]
        require(ref['sha256']==e['checkpoint_sha256'] and ref['identity_path']==str(original/e['relative_path'])
                and str(Path(ref['path']).resolve())==str(run/e['relative_path']) and type(ref['bytes']) is int
                and 0<ref['bytes']<=8*1024**2 and stat(ref['path'])['bytes']==ref['bytes'],
                'native checkpoint original mapping/SHA/STAT-size differs')
    require(completion['checkpoint_hashes']=={k:v['sha256'] for k,v in cprefs.items()}
            and completion['freeze_sha256']==native['freeze.json']['sha256'],'root native actual CP/freeze map differs')
    require(sum(v['bytes'] for v in cprefs.values())<=160*1024**2,'total checkpoint source-body budget exceeded')
    require(sum(x['bytes_read'] for x in observations)<=128*1024**2,'small completion/source byte budget exceeded')
    return entries,meta,frozen


def execute(args):
    started=time.monotonic();deadline=started+600;observations=[];completed=[];failure=None
    require(type(args.contract_sha256) is str and type(args.source_sha256) is str,'explicit out-of-band pins required')
    contract_ref={'path':str(args.contract),'bytes':args.contract.stat().st_size,'sha256':args.contract_sha256}
    contract=json_doc(contract_ref,deadline,observations)
    require(contract['schema_version']=='PREPARED_R2_FIXED35_CPU_CHECKPOINT_AUDIT_V1'
            and contract['execution_allowed'] is True,'prepared/incomplete contract cannot execute')
    require(contract['source']['path']==str(Path(__file__).resolve()) and contract['source']['sha256']==args.source_sha256,
            'reviewed auditor source pin differs')
    read_bound(contract['source'],1024**2,deadline,observations)
    require(str(args.output.resolve())==contract['exact_new_output_directory'],'exclusive new output namespace differs')
    args.output.mkdir(parents=True,exist_ok=False)
    marker={'PID':os.getpid(),'started_utc':now(),'contract_sha256':args.contract_sha256,'source_sha256':args.source_sha256,
            'status':'STARTED_ONCE_FIXED35_AUDIT_NO_RETRY','forward_or_training':False}
    publish(args.output/'EXECUTION_STARTED_v1.json',marker)
    try:
        entries,meta,frozen=gate(contract,deadline,observations)
        # All35 real completions and freeze identities precede Torch import/CP IO.
        sys.path.insert(0,str(Path(contract['original_source_root']).resolve()))
        import torch
        import numpy as np
        from gru_research.model import PlanGRU
        require(Path(sys.modules[PlanGRU.__module__].__file__).resolve()==Path(contract['original_source_root']).resolve()/'gru_research/model.py',
                'PlanGRU import not the exact guarded original implementation')
        torch.set_num_threads(1)
        require(hasattr(torch.serialization,'get_unsafe_globals_in_checkpoint'),'Torch lacks explicit unsafe-global preinspection; no load fallback')
        numpy_reconstruct=np._core.multiarray._reconstruct if hasattr(np,'_core') else np.core.multiarray._reconstruct
        safe=[np.ndarray,np.dtype,type(np.dtype('uint32')),
              (numpy_reconstruct,'numpy.core.multiarray._reconstruct'),(numpy_reconstruct,'numpy._core.multiarray._reconstruct')]
        allowed={'numpy.core.multiarray._reconstruct','numpy._core.multiarray._reconstruct','numpy.ndarray','numpy.dtype','numpy.dtypes.UInt32DType'}
        for e in entries:
            deadline_check(deadline);ref=contract['checkpoints'][e['relative_path']]
            body=read_bound(ref,8*1024**2,deadline,observations)
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                infos=archive.infolist()
                require(0<len(infos)<=256 and len({x.filename for x in infos})==len(infos)
                        and sum(x.file_size for x in infos)<=64*1024**2
                        and all(x.file_size>=0 and not x.flag_bits&1 for x in infos),
                        'checkpoint ZIP member/inflation metadata budget exceeded')
            unsafe=set(torch.serialization.get_unsafe_globals_in_checkpoint(io.BytesIO(body)))
            if e['expected_checkpoint_role']=='inner_best':require(not unsafe,'diagnostic best checkpoint unexpectedly contains RNG/global objects')
            require(unsafe<=allowed,'checkpoint has unregistered globals; no arbitrary allowlist or False fallback')
            with torch.serialization.safe_globals(safe):state=torch.load(io.BytesIO(body),map_location='cpu',weights_only=True)
            require(type(state) is dict and state['signature']==e['signature'] and type(state['epoch']) is int
                    and state['epoch']==e['expected_epoch_zero_based'] and state['groups']==meta['groups']
                    and type(state['n_industries']) is int and state['n_industries']==meta['n_industries'],
                    'checkpoint internal exact signature/epoch/groups/industry differs')
            if e['expected_checkpoint_role']=='inner_best':require(state['best_ic']==e['expected_best_ic'],'inner-selected internal metric differs')
            else:
                require(state['epoch_record']==e['expected_epoch_record'],'fixed-last internal event differs from actual last epoch')
                rng=state['rng']['numpy']
                require(isinstance(rng,tuple) and len(rng)==5 and rng[0]=='MT19937'
                        and isinstance(rng[1],np.ndarray) and rng[1].dtype==np.dtype('uint32') and rng[1].shape==(624,)
                        and type(rng[2]) is int and 0<=rng[2]<=624 and type(rng[3]) is int and rng[3] in (0,1)
                        and type(rng[4]) is float and math.isfinite(rng[4]),
                        'loaded final NumPy object is not original bounded MT19937 RNG serialization')
            weights=state['model'];require(isinstance(weights,dict) and len(weights)==26,'model tensor roster differs')
            tensors=[]
            for name,x in weights.items():
                require(type(name) is str and isinstance(x,torch.Tensor) and x.device.type=='cpu' and x.dtype==torch.float32
                        and x.layout==torch.strided and torch.isfinite(x).all().item(),'nonfinite/nonFP32/nonCPU model tensor')
                tensors.append({'name':name,'shape':list(x.shape),'dtype':str(x.dtype),'elements':x.numel(),'finite':True})
            network=PlanGRU(meta['groups'],meta['n_industries'],'pv');network.load_state_dict(weights,strict=True)
            require(sum(x.numel() for x in network.parameters())==202481,'exact PlanGRU parameter count differs')
            require(stat(ref['path'])==observations[-1]['STAT_before'],'checkpoint mutated during CPU load')
            record={'relative_path':e['relative_path'],'original_identity_path':ref['identity_path'],'actual_sha256':ref['sha256'],
                    'checkpoint_role':e['expected_checkpoint_role'],'epoch_zero_based':state['epoch'],
                    'signature_exact':True,'groups_exact':True,'strict_state_dict':True,'parameter_count':202481,
                    'FP32_finite_model_tensor_count':26,'model_tensors':tensors,'weights_only':True,
                    'unsafe_globals_declared':sorted(unsafe),'extra_checkpoint_fields_preserved_in_original_only':sorted(set(state)-{'signature','model','epoch','groups','n_industries'}),
                    'original_NumPy_RNG_serialization_shape_checked':e['expected_checkpoint_role']=='fixed_last_epoch',
                    'optimizer_rng_finiteness_or_training_resume_certified':False,'forward_calls':0}
            publish(args.output/f'checkpoint_{len(completed)+1:02d}.json',record);completed.append(record)
            del state,network,weights,body
        require(len(completed)==35,'all35 loads not complete')
        # Rehash only small code/root native completion metadata. No checkpoint
        # second body pass, and no score/model/array inference.
        for ref in [contract['source'],*contract['source_refs'].values(),*contract['source27'].values(),contract['root_completion_receipt']]:
            read_bound(ref,4*1024**2,deadline,observations)
        for ref in [*contract['native_files'].values(),*contract['checkpoints'].values()]:
            matching=next(o for o in observations if o['path']==str(Path(ref['path']).resolve()))
            require(stat(ref['path'])==matching['STAT_before'],'native closed evidence STAT changed after audit')
        result={'status':'PASS_ACTUAL_FIXED35_CPU_WEIGHTS_ONLY_FINITE_PROVENANCE_QUALIFIED',
                'completed_diagnostic_best_inner':30,'completed_final_fixed_last':5,'freeze_id':frozen['freeze_id'],
                'freeze_sha256':contract['native_files']['freeze.json']['sha256'],'torch_version':torch.__version__,
                'numpy_version':np.__version__,'all35_internal_signatures_and_strict_shapes_match':True}
    except BaseException as exc:
        failure={'type':type(exc).__name__,'message':str(exc)}
        result={'status':'FAILED_FIXED35_AUDIT_PRESERVED_NO_RETRY','failure':failure,'completed_loads':len(completed),
                'all35_checkpoint_audit_complete':False,'freeze_acceptance':False}
    result.update(schema_version=1,source_sha256=args.source_sha256,contract_sha256=args.contract_sha256,marker=marker,
        finished_utc=now(),elapsed_seconds=time.monotonic()-started,actual_reads=observations,completed_checkpoints=completed,
        actual_CP_physical_passes=sum(o['path'].endswith('.pt') for o in observations),weights_only=True,pickle_fallback=False,
        outer_metric_values_decoded=False,model_forward_calls=0,additional_fits=0,selection_recomputed=False,
        actual_35_completeness_precedes_Torch_and_CP_reads=True,model_reuse_or_training_permission=False,
        optimizer_rng_state_semantics_certified=False,source_19_and_8_mutated=False,
        historical_evaluation_is_virgin_OOS=False,full_PLAN_complete=False,all_PLAN_acceptance_complete=False,economic_acceptance=False,
        internal_deadline_is_cooperative_only=True,automatic_retry=False)
    publish(args.output/'actual_fixed35_cpu_checkpoint_audit_receipt_v1.json',result)
    print(json.dumps({'status':result['status'],'completed_loads':len(completed),'receipt':str(args.output/'actual_fixed35_cpu_checkpoint_audit_receipt_v1.json')}),flush=True)
    return 1 if failure else 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true')
    p.add_argument('--contract',type=Path);p.add_argument('--contract-sha256');p.add_argument('--source-sha256');p.add_argument('--output',type=Path)
    a=p.parse_args()
    if not a.execute:print(json.dumps(prepared(),sort_keys=True));return
    require(all((a.contract,a.contract_sha256,a.source_sha256,a.output)),'explicit actual pins/paths required')
    try:code=execute(a)
    except BaseException as exc:
        try:
            marker_path=a.output/'EXECUTION_STARTED_v1.json';receipt=a.output/'actual_fixed35_cpu_checkpoint_audit_receipt_v1.json'
            marker=json.loads(marker_path.read_text()) if marker_path.exists() else None
            if (marker and marker.get('PID')==os.getpid() and marker.get('source_sha256')==a.source_sha256
                    and marker.get('contract_sha256')==a.contract_sha256 and not receipt.exists()):
                publish(receipt,{'status':'FAILED_POST_MARKER_AUDIT_PRESERVED_NO_RETRY','marker':marker,
                    'failure':{'type':type(exc).__name__,'message':str(exc)},'partial_read_counts_unknown':True,
                    'all35_checkpoint_audit_complete':False,'model_forward_calls':0,'additional_fits':0,'automatic_retry':False,
                    'full_PLAN_complete':False,'economic_acceptance':False})
        except BaseException as secondary:print(json.dumps({'primary_failure':str(exc),'receipt_failure':str(secondary),'partial_evidence_preserved':True}),file=sys.stderr,flush=True)
        raise
    raise SystemExit(code)


if __name__=='__main__':main()

"""Prepared same-host R2 fixed229 native/fixed35 checkpoint identity capture.

No result copies, tensor loaders, inference, training, or metric selection.
Default prints a static contract without filesystem or environment inspection.
An explicit future root contract and owned capture are required for execution.
All small-file and checkpoint STAT budgets precede native body reads; complete
native provenance gates precede the first checkpoint body read. Checkpoints are
streamed once for SHA/EOF/STAT only, and discarded without deserialization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

PROTOCOL_SHA = 'e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f'
RUNTIME_MANIFEST_SHA = '8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809'
META_SHA = '2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849'
META_FP = '6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4'
AUDITOR_METADATA_SOURCE_SHA = '70c7b53c2b7f1c69507d63156fb326f1ad3cd5cdb5dfbe1ce0d2c6ced8aa46a6'
SEEDS = (11,22,33,44,55)
ARMS = {'control':3e-4,'lower_lr':1e-4}
FOLDS = ('2021','2022','2023')
CHUNK = 65536
SMALL_FILE_CAP = 4*1024**2
SMALL_TOTAL_CAP = 128*1024**2
CP_FILE_CAP = 8*1024**2
CP_TOTAL_CAP = 160*1024**2
CONTRACT_SCHEMA = 'PREPARED_R2_NATIVE35_CAPTURE_V1'
RECEIPT_NAME = 'actual_r2_native35_capture_receipt_v1.json'
BINDING_KEYS = ('protocol_sha256','new_source_manifest_sha256','causal_receipt_sha256',
                'causal_transport_manifest_sha256','dataset_fingerprint')
HEADER_FIELDS = set(BINDING_KEYS) | {'fold','arm','seed','checkpoint_role','checkpoint_sha256',
                 'inner_result_sha256','stage','complete','outer_attempt_sha256',
                 'outer_is_virgin_OOS','used_for_training_or_checkpoint','eligible_sections_expected'}
_CACHE = {}
_CACHE_REFS = {}
_CACHE_HITS = 0


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
    require(p.is_file() and not p.is_symlink(),'explicit regular file required; no symlink aliases')
    return {'path':str(p.resolve()),'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'inode':s.st_ino,'device':s.st_dev}


def deadline_check(deadline):
    require(time.monotonic()<deadline,'cooperative 600-second capture limit; preserve attempt, no retry')


def explicit_path(value):
    require(type(value) is str,'path must be a literal absolute string')
    p=Path(value)
    require(p.is_absolute() and str(p)==value and str(p.resolve())==value,'canonical original path identity required')
    return p


def digest_valid(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def read_product(ref, cap, deadline, observations, retain):
    """One bounded physical pass, with a cached body only for small metadata."""
    deadline_check(deadline);p=explicit_path(ref['path']);before=stat(p)
    require(type(ref['bytes']) is int and 0<ref['bytes']<=cap and before['bytes']==ref['bytes'],
            'closed file size budget differs')
    if 'source_STAT_before' in ref:require(before==ref['source_STAT_before'],'file changed after all-size preflight')
    expected=ref.get('sha256');require(expected is None or digest_valid(expected),'invalid expected SHA')
    key=str(p);require(all(o['path']!=key for o in observations),'second physical read prohibited')
    record={'path':key,'bytes_read':0,'expected_sha256':expected,'source_STAT_before':before,
            'single_physical_source_pass':True,'EOF_verified':False,'retained_small_metadata':retain}
    observations.append(record);h=hashlib.sha256();parts=[]
    try:
        with p.open('rb') as handle:
            remaining=ref['bytes']
            while remaining:
                deadline_check(deadline);part=handle.read(min(CHUNK,remaining));require(part,'truncated bounded artifact')
                h.update(part);record['bytes_read']+=len(part);remaining-=len(part)
                if retain:parts.append(part)
            deadline_check(deadline);require(handle.read(1)==b'','source grew beyond preflight length')
            record['EOF_verified']=True
        record['sha256']=h.hexdigest();record['source_STAT_after']=stat(p)
        require(record['source_STAT_after']==before and (expected is None or record['sha256']==expected),
                'same-read SHA/STAT identity differs')
        result={'path':key,'identity_path':ref.get('identity_path',key),'bytes':ref['bytes'],
                'sha256':record['sha256'],'source_STAT_before':before,'source_STAT_after':record['source_STAT_after'],
                'EOF_verified':True,'single_physical_source_pass':True}
        if retain:
            _CACHE[key]=b''.join(parts);_CACHE_REFS[key]=result
        return result
    finally:
        record['partial_or_complete_read_sha256']=h.hexdigest()


def read_bound(ref, cap, deadline, observations):
    """Source-provenance gate reads an already same-pass-bound small cache."""
    global _CACHE_HITS
    deadline_check(deadline);key=str(explicit_path(ref['path']))
    require(key in _CACHE and key in _CACHE_REFS,'metadata gate cannot initiate a source body read')
    measured=_CACHE_REFS[key]
    require(type(ref['bytes']) is int and 0<ref['bytes']<=cap and ref['bytes']==measured['bytes']
            and ref['sha256']==measured['sha256'] and measured['EOF_verified'] is True,
            'cache identity differs from declared source')
    _CACHE_HITS+=1
    return _CACHE[key]


def json_doc(ref, deadline, observations):
    return json.loads(read_bound(ref,SMALL_FILE_CAP,deadline,observations))


def publish(path, doc):
    p=Path(path);require(not p.exists(),'receipt cannot replace an existing original')
    partial=p.with_name(p.name+'.partial')
    with partial.open('xb') as f:f.write((json.dumps(doc,indent=2,allow_nan=False,ensure_ascii=False)+'\n').encode())
    os.replace(partial,p)


def fixed_paths(run):
    native={n:{'path':str(run/n),'identity_path':str(run/n)}
            for n in ('run_identity.json','freeze.json','selection.json','budget.json')}
    cp={}
    for fold in FOLDS:
        for arm in ARMS:
            for seed in SEEDS:
                d=f'diagnostic/{fold}/{arm}/seed_{seed}'
                for name in ('identity.json','result.json','epochs.jsonl','outer_attempt.json',
                             'outer_result.json','best_inner_summary.json','best_inner_sections.json'):
                    relative=d+'/'+name;native[relative]={'path':str(run/relative),'identity_path':str(run/relative)}
                relative=d+'/best_inner.pt';cp[relative]={'path':str(run/relative),'identity_path':str(run/relative)}
    for seed in SEEDS:
        d=f'final/seed_{seed}'
        for name in ('identity.json','result.json','epochs.jsonl'):
            relative=d+'/'+name;native[relative]={'path':str(run/relative),'identity_path':str(run/relative)}
        relative=d+'/last.pt';cp[relative]={'path':str(run/relative),'identity_path':str(run/relative)}
    require(len(native)==229 and len(cp)==35,'fixed roster construction differs')
    return native,cp


def prepared():
    return {'schema_version':CONTRACT_SCHEMA,'status':'PREPARED_ONLY_ACTUAL35_FREEZE_AND_ROOT_OWNED_CAPTURE_PENDING',
            'execution_allowed':False,'native_roster_count':229,'checkpoint_roster_count':35,
            'actual_native_file_reads':0,'actual_checkpoint_body_reads':0,'all35_complete':False,
            'native_copy_count':0,'checkpoint_copy_count':0,'Torch_imports':0,'NumPy_imports':0,
            'outer_metric_values_decoded':False,'checkpoint_parameters_loaded':False,
            'additional_fits':0,'model_forward_calls':0,'selection_recomputed':False,
            'small_total_cap_bytes':SMALL_TOTAL_CAP,'small_per_file_cap_bytes':SMALL_FILE_CAP,
            'checkpoint_total_cap_bytes':CP_TOTAL_CAP,'checkpoint_per_file_cap_bytes':CP_FILE_CAP,
            'cooperative_limit_seconds':600,'actual_owned_capture_gate_ready':False,'automatic_retry':False,
            'root_accepted':False,'all35_finite_parameters_verified':False,
            'historical_evaluation_is_virgin_OOS':False,'full_PLAN_complete':False,'economic_acceptance':False}


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

def metadata_gate(contract, deadline, observations):
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
    require(sum(v['bytes'] for v in cprefs.values())<=160*1024**2,'total checkpoint source-body budget exceeded')
    require(sum(x['bytes_read'] for x in observations)<=128*1024**2,'small completion/source byte budget exceeded')
    return entries,meta,frozen


def owned_capture_gate(contract):
    """Check the explicit root-owned workflow, not adversarial attestation."""
    owned=contract['required_owned_capture']
    require(set(owned)=={'schema_version','capture_source_sha256','child_hard_deadline_seconds'}
            and owned['schema_version']=='NATIVE35_OWNED_CAPTURE_LAUNCH_V1'
            and digest_valid(owned['capture_source_sha256'])
            and type(owned['child_hard_deadline_seconds']) is int
            and owned['child_hard_deadline_seconds']==660,'future reviewed owned capture binding required')
    require(os.environ.get('GRU_NATIVE35_CAPTURE_SOURCE_SHA256')==owned['capture_source_sha256']
            and os.environ.get('GRU_NATIVE35_CAPTURE_PARENT_PID')==str(os.getppid())
            and os.getpid()==os.getpgrp() and os.getpgid(os.getppid())!=os.getpgrp(),
            'explicit separate child process group and owned parent environment required')


def declared_sources(contract, root):
    refs=contract['source_refs'];sources=contract['source27']
    require(set(refs)=={'protocol','runtime_manifest','meta','config'} and len(sources)==27,
            'exact source metadata roles/27 source roster required')
    expected_ref_paths={
        'protocol':str(root/'gru_research/r2_research/protocol_v1.json'),
        'runtime_manifest':str(root/'validation_receipts/r2_source_manifest_v1.json'),
        'meta':'/data/users/s1155284034/gru_hourly_20261001/dataset/meta.json',
        'config':str(root/'gru_research/config.json')}
    for role,ref in refs.items():
        require(ref['path']==expected_ref_paths[role],'actual deployed metadata path differs')
    for name,ref in sources.items():
        relative=Path(name)
        require(type(name) is str and not relative.is_absolute() and '..' not in relative.parts
                and str(relative)==name and ref['path']==str(root/relative),
                'source27 sealed relative path differs')
    unique={}
    for ref in [*refs.values(),*sources.values()]:
        p=explicit_path(ref['path']);require(digest_valid(ref['sha256'])
                and type(ref['bytes']) is int and 0<ref['bytes']<=SMALL_FILE_CAP,'small sealed source identity incomplete')
        key=str(p)
        if key in unique:
            require((ref['bytes'],ref['sha256'])==(unique[key]['bytes'],unique[key]['sha256']),
                    'aliased source metadata declarations differ')
        else:unique[key]=dict(ref)
    return unique


def all_size_preflight(small, native, checkpoints, already_read, deadline, stat_observations):
    """STAT every fixed input and sum all budgets before native bodies."""
    seen=set();small_total=already_read;cp_total=0
    for kind,products,cap in (('source',small,SMALL_FILE_CAP),('native',native,SMALL_FILE_CAP),
                              ('checkpoint',checkpoints,CP_FILE_CAP)):
        for key,ref in products.items():
            deadline_check(deadline);p=explicit_path(ref['path']);path=str(p)
            require(path not in seen,'different native/source/checkpoint products alias one file')
            seen.add(path);observed=stat(p)
            stat_observations.append({'kind':kind,'relative_or_source_key':key,'source_STAT_before':observed})
            require(0<observed['bytes']<=cap,'pre-body product size budget exceeded')
            if 'bytes' in ref:require(ref['bytes']==observed['bytes'],'presealed small-source STAT size differs')
            ref['bytes']=observed['bytes'];ref['source_STAT_before']=observed
            if kind=='checkpoint':cp_total+=observed['bytes']
            else:small_total+=observed['bytes']
    require(small_total<=SMALL_TOTAL_CAP and cp_total<=CP_TOTAL_CAP,
            'all-STAT aggregate budget exceeded before native body reads')
    return {'source_and_native_plus_contract_and_self_bytes':small_total,'checkpoint_bytes':cp_total,
            'source_unique_files':len(small),'native_small_files':len(native),'checkpoints':len(checkpoints),
            'all_sizes_checked_before_native_body_reads':True,'all_sizes_checked_before_checkpoint_body_reads':True}


def checkpoint_expected_hashes(native, checkpoints, deadline, observations):
    """Take expected hashes from actual results; never decode a checkpoint."""
    for relative,ref in checkpoints.items():
        parent=str(Path(relative).parent);result=json_doc(native[parent+'/result.json'],deadline,observations)
        field='best_inner_checkpoint_sha256' if relative.endswith('/best_inner.pt') else 'last_checkpoint_sha256'
        require(digest_valid(result.get(field)),'actual result has no exact selected checkpoint hash')
        ref['sha256']=result[field]


def source_view(ref):
    measured=_CACHE_REFS[ref['path']]
    return dict(measured)


def execute(args):
    started=time.monotonic();deadline=started+600;observations=[];marker=None;failure=None
    stat_observations=[]
    native={};checkpoints={};small={};completed={};measured_native={};preflight=None;binding=None;freeze_digest=None
    metadata_complete=False;owned_verified=False;contract=None;frozen=None;entries=[]
    require(digest_valid(args.contract_sha256) and digest_valid(args.source_sha256),'explicit out-of-band pins required')
    contract_path=explicit_path(str(args.contract));self_path=Path(__file__).resolve()
    contract_ref={'path':str(contract_path),'bytes':stat(contract_path)['bytes'],'sha256':args.contract_sha256}
    read_product(contract_ref,SMALL_FILE_CAP,deadline,observations,True)
    contract=json_doc(contract_ref,deadline,observations)
    require(contract['schema_version']==CONTRACT_SCHEMA and contract['execution_allowed'] is True,
            'prepared contract must refuse execution')
    require(contract['source']['path']==str(self_path) and contract['source']['sha256']==args.source_sha256,
            'reviewed collector source identity differs')
    read_product(contract['source'],1024**2,deadline,observations,True)
    owned_capture_gate(contract);owned_verified=True
    root=explicit_path(contract['original_source_root']);run=explicit_path(contract['original_run_directory'])
    require(str(root)=='/home/users/s1155284034/gru_hourly_20261001'
            and str(run)==str(root/'outputs/r2_lr_chrono_v1_20261002')
            and contract['actual_native_run_directory']==str(run),'fixed same-host original input identities differ')
    expected_output=root/'outputs/r2_native35_capture_v1/attempt1'
    require(str(args.output)==str(expected_output) and str(args.output.resolve())==str(expected_output)
            and contract['exact_new_output_directory']==str(expected_output),'unique fixed new output namespace differs')
    require(digest_valid(contract['expected_run_identity_sha256']) and digest_valid(contract['expected_freeze_sha256']),
            'future real run_identity/freeze out-of-band pins required')
    args.output.mkdir(parents=True,exist_ok=False)
    marker={'PID':os.getpid(),'PGID':os.getpgrp(),'parent_PID':os.getppid(),'started_utc':now(),
            'contract_sha256':args.contract_sha256,'source_sha256':args.source_sha256,
            'status':'STARTED_ONCE_NATIVE229_CP35_IDENTITY_CAPTURE_NO_RETRY'}
    publish(args.output/'EXECUTION_STARTED_v1.json',marker)
    try:
        small=declared_sources(contract,root);native,checkpoints=fixed_paths(run)
        require(set(contract['native_relative_paths'])==set(native) and len(contract['native_relative_paths'])==229
                and set(contract['checkpoint_relative_paths'])==set(checkpoints)
                and len(contract['checkpoint_relative_paths'])==35,'predeclared fixed roster differs')
        native['run_identity.json']['sha256']=contract['expected_run_identity_sha256']
        native['freeze.json']['sha256']=contract['expected_freeze_sha256']
        preflight=all_size_preflight(small,native,checkpoints,sum(o['bytes_read'] for o in observations),deadline,stat_observations)
        for ref in small.values():read_product(ref,SMALL_FILE_CAP,deadline,observations,True)
        # Outer reports and best-inner reports are retained as opaque bytes.
        # Only outer_header_only below projects the provenance header.
        for relative,ref in native.items():
            measured_native[relative]=read_product(ref,SMALL_FILE_CAP,deadline,observations,True)
            measured_native[relative]['role']=('diagnostic_' if relative.startswith('diagnostic/')
                                              else 'final_' if relative.startswith('final/') else 'root_')+Path(relative).name
        contract_for_gate={**contract,'native_files':measured_native,'checkpoints':checkpoints}
        checkpoint_expected_hashes(measured_native,checkpoints,deadline,observations)
        entries,meta,frozen=metadata_gate(contract_for_gate,deadline,observations)
        metadata_complete=True
        binding=json_doc(measured_native['run_identity.json'],deadline,observations)
        freeze_digest=measured_native['freeze.json']['sha256']
        # Only after all229 actual native signatures, all30 once-outer attempts,
        # all5 fixed-final results and the actual freeze pass can CP I/O begin.
        for entry in entries:
            relative=entry['relative_path'];ref=checkpoints[relative]
            measured=read_product(ref,CP_FILE_CAP,deadline,observations,False)
            measured.update(checkpoint_role=entry['expected_checkpoint_role'],
                            role='diagnostic_best_inner_checkpoint' if relative.endswith('/best_inner.pt') else 'final_fixed_last_checkpoint',
                            expected_epoch_zero_based=entry['expected_epoch_zero_based'],
                            expected_signature=entry['signature'],internal_signature_observed=False,
                            checkpoint_parameters_loaded=False)
            completed[relative]=measured
        require(len(completed)==35,'all35 checkpoint identity reads incomplete')
        for observation in observations:
            deadline_check(deadline)
            after=stat(observation['path']);observation['final_STAT']=after
            require(after==observation['source_STAT_before'],'input identity changed during capture')
        result={'status':'COMPLETE_FIXED35_NATIVE_CAPTURE_NOT_ROOT_ACCEPTANCE','generation_complete':True,
                'metadata_completed':{'diagnostic':30,'outer_once':30,'final':5},
                'metadata_provenance_complete':True,'checkpoint_identity_count':35,
                'source_refs':{k:source_view(v) for k,v in contract['source_refs'].items()},
                'source27':{k:source_view(v) for k,v in contract['source27'].items()},
                'source':source_view(contract['source']),'contract':source_view(contract_ref)}
    except BaseException as exc:
        failure={'type':type(exc).__name__,'message':str(exc)}
        result={'status':'FAILED_NATIVE35_CAPTURE_PRESERVED_NO_RETRY','generation_complete':False,'failure':failure,
                'metadata_completed':{'diagnostic':30 if metadata_complete else None,
                                      'outer_once':30 if metadata_complete else None,'final':5 if metadata_complete else None},
                'metadata_provenance_complete':metadata_complete,'checkpoint_identity_count':len(completed)}
    result.update(schema_version='ACTUAL_R2_NATIVE35_CAPTURE_V1',source_sha256=args.source_sha256,
        contract_sha256=args.contract_sha256,marker=marker,started_utc=marker['started_utc'],finished_utc=now(),
        elapsed_seconds=time.monotonic()-started,original_source_root=str(root),original_run_directory=str(run),
        actual_native_run_directory=str(run),native_files=measured_native,checkpoints=completed,run_identity=binding,
        freeze_sha256=freeze_digest,freeze_id=frozen['freeze_id'] if frozen else None,
        all_size_preflight=preflight,actual_STAT_preflight_observations=stat_observations,
        actual_reads=observations,metadata_cache_hits=_CACHE_HITS,
        actual_native_physical_source_passes=sum(o['path'].endswith(('.json','.jsonl')) and o['path'].startswith(str(run)+'/') for o in observations),
        actual_checkpoint_physical_source_passes=sum(o['path'].endswith('.pt') for o in observations),
        actual_source_bytes_read=sum(o['bytes_read'] for o in observations),
        checkpoint_body_bytes_read=sum(o['bytes_read'] for o in observations if o['path'].endswith('.pt')),
        source27_and_native_body_reads_are_single_pass=True,small_metadata_retained_in_memory=True,
        checkpoint_bodies_retained=False,native_copy_count=0,checkpoint_copy_count=0,Torch_imports=0,NumPy_imports=0,
        outer_metric_values_decoded=False,checkpoint_parameters_loaded=False,all35_finite_parameters_verified=False,
        root_accepted=False,model_forward_calls=0,additional_fits=0,selection_recomputed=False,
        actual_35_metadata_completeness_precedes_checkpoint_reads=metadata_complete,
        owned_capture_workflow_checked=owned_verified,owned_capture_is_not_adversarial_attestation=True,
        internal_deadline_is_cooperative_only=True,hard_deadline_delegated_to_reviewed_owned_capture=True,
        original_19_and_runtime_8_mutated=False,model_reuse_or_training_permission=False,
        historical_evaluation_is_virgin_OOS=False,full_PLAN_complete=False,all_PLAN_acceptance_complete=False,
        economic_acceptance=False,automatic_retry=False)
    publish(args.output/RECEIPT_NAME,result)
    print(json.dumps({'status':result['status'],'checkpoint_identity_count':len(completed),
                      'receipt':str(args.output/RECEIPT_NAME)}),flush=True)
    return 1 if failure else 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true')
    p.add_argument('--contract',type=Path);p.add_argument('--contract-sha256');p.add_argument('--source-sha256');p.add_argument('--output',type=Path)
    a=p.parse_args()
    if not a.execute:print(json.dumps(prepared(),sort_keys=True));return
    require(all((a.contract,a.contract_sha256,a.source_sha256,a.output)),'explicit future actual pins/paths required')
    try:code=execute(a)
    except BaseException as exc:
        try:
            marker_path=a.output/'EXECUTION_STARTED_v1.json';receipt=a.output/RECEIPT_NAME
            marker=json.loads(marker_path.read_text()) if marker_path.exists() else None
            if (marker and marker.get('PID')==os.getpid() and marker.get('source_sha256')==a.source_sha256
                    and marker.get('contract_sha256')==a.contract_sha256 and not receipt.exists()):
                publish(receipt,{'schema_version':'ACTUAL_R2_NATIVE35_CAPTURE_V1',
                    'status':'FAILED_POST_MARKER_NATIVE35_CAPTURE_PRESERVED_NO_RETRY','marker':marker,
                    'failure':{'type':type(exc).__name__,'message':str(exc)},'partial_read_counts_unknown':True,
                    'generation_complete':False,'root_accepted':False,'all35_finite_parameters_verified':False,
                    'model_forward_calls':0,'additional_fits':0,'automatic_retry':False,
                    'full_PLAN_complete':False,'all_PLAN_acceptance_complete':False,'economic_acceptance':False})
        except BaseException as secondary:
            print(json.dumps({'primary_failure':str(exc),'receipt_failure':str(secondary),
                              'partial_evidence_preserved':True}),file=sys.stderr,flush=True)
        raise
    raise SystemExit(code)


if __name__=='__main__':main()

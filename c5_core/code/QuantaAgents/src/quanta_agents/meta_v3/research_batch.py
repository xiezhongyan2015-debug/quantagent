"""Bounded enumeration, durable candidate reservations and saved-only recovery.

Scan cells count declared expression matrices plus the raw calendar replay,
not physical disk reads or independent hypotheses. All variants are retained.
"""
import ast
from copy import deepcopy
from decimal import Decimal
import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .ledger import need,digest,worker_lease
from .research_iteration import executable_program_hash,account_attribution,public_diagnosis
from .kernel import ROOT

ACTIONS=('register_batch','execute_batch','inspect_batch')
PLACEHOLDER=re.compile(r'\{\{([A-Za-z][A-Za-z0-9_]{0,39})\}\}')


def policy(case):
    value=case.get('batch_policy')
    need(type(value) is dict and set(value)=={'version','max_candidates_total','max_scan_cells_total','max_wall_seconds','output_stop_threshold_bytes','field_semantics'},'exact controller batch policy required')
    need(value['version']=='bounded_batch_v1','batch policy version')
    for key,maximum in [('max_candidates_total',64),('max_scan_cells_total',2097152),('max_wall_seconds',7200),('output_stop_threshold_bytes',268435456)]:
        need(type(value[key]) is int and 0<value[key]<=maximum,'batch bound: '+key)
    fields={f['name']:f['unit'] for f in case['decision_fixture']['fields']};semantics=value['field_semantics']
    need(type(semantics) is dict and set(semantics)==set(fields),'all field semantics must be frozen')
    for field,meta in semantics.items():
        need(type(meta) is dict and set(meta)=={'unit','raw_precision','digit_derivation'},'exact field semantics')
        need(meta['unit']==fields[field] and type(meta['raw_precision']) is str and 0<len(meta['raw_precision'])<=500,'field unit/precision declaration')
        if meta['digit_derivation'] is not None:
            d=meta['digit_derivation']
            need(type(d) is dict and set(d)=={'source_unit','minimum_increment','integer_conversion','source_evidence_sha256','base','place','width'},'exact digit provenance')
            need(type(d['base']) is int and 2<=d['base']<=16 and type(d['place']) is int and 0<=d['place']<=6,'digit base/place bounds')
            need(type(d['width']) is int and 1<=d['width']<=3,'digit width bound')
            need(Decimal(str(d['minimum_increment'])).is_finite() and Decimal(str(d['minimum_increment']))>0,'digit increment')
            need(d['integer_conversion']=='exact_decimal_no_rounding' and meta['raw_precision']!='unknown','digit conversion cannot infer lost precision')
            need(d['source_evidence_sha256'] in {p['sha256'] for p in case.get('evidence_sources',[])},'digit derivation requires pinned source evidence')
    return value


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def tree_bytes(folder):
    total=0
    for path in folder.rglob('*'):
        try:
            if path.is_file():total+=path.stat().st_size
        except FileNotFoundError:pass # Atomic worker renames; the next poll sees the final name.
    return total


def plans(root):return [(p.parent,read(p)) for p in sorted(root.glob('*/registration.json'))]


def unresolved(root):
    return any(not (p.parent/'receipt.json').exists() for p in root.glob('*/candidates/*/intent.json'))


def reconcile_registry(root,case_hash,save):
    """Normalize committed outcomes across every batch before deduplication."""
    for folder,registration in plans(root):
        if registration['case_hash']!=case_hash:continue
        for row in registration['candidates']:
            work=folder/'candidates'/row['candidate_id']
            if (work/'intent.json').exists() and (work/'worker_result.json').exists():
                need(read(work/'intent.json')['source_pins']==registration['source_pins'],'cross-batch worker source drift')
                _receipt(work,row,case_hash,save)


def find_recorded(root,case,executable):
    for folder,registration in plans(root):
        if registration['case_hash']!=digest(case):continue
        for row in registration['candidates']:
            if row['executable_program_hash']==executable:
                return {'batch_id':folder.name,'candidate_id':row['candidate_id'],'registration_hash':digest(registration)}
    return None


def check_ordinary_budget(root,case,count,other_scan_cells=0):
    limits=policy(case);registered=plans(root)
    need(count+sum(p['reserved_candidates'] for _,p in registered)<=limits['max_candidates_total'],'task expanded candidate budget exhausted')
    stockdays=len(case['decision_fixture']['codes'])*len(case['decision_fixture']['calendar'])
    need(count*stockdays*6+other_scan_cells+sum(p['reserved_scan_cells'] for _,p in registered)<=limits['max_scan_cells_total'],'task scan-cell budget exhausted')


def complexity(program):
    expressions=[f['expression'] for f in program['factors']]+[program['target_weight_expression']]
    nodes=[n for e in expressions for n in ast.walk(ast.parse(e,mode='eval'))]
    return {'expressions':len(expressions),'ast_nodes':len(nodes),'comparisons':sum(len(n.ops) for n in nodes if isinstance(n,ast.Compare)),
            'boolean_operators':sum(isinstance(n,(ast.BoolOp,ast.BitAnd,ast.BitOr)) for n in nodes),
            'function_calls':sum(isinstance(n,ast.Call) for n in nodes)}


def register(folder,args,case,validate,save,*,ordinary_candidates=0,other_scan_cells=0,other_comparisons=0):
    limits=policy(case)
    need(type(args) is dict and set(args)=={'families','comparisons','selection_rule','stop_rule'},'exact batch declaration')
    need(type(args['selection_rule']) is str and 0<len(args['selection_rule'])<=2000,'frozen selection rule')
    need(args['stop_rule'] in ('continue_settled_failures','stop_on_first_failure'),'frozen stop rule')
    families=args['families'];need(type(families) is list and 1<=len(families)<=4,'one to four candidate families')
    required={'id','priority','origin','mechanism_status','mechanism','role','program_template','parameters','digit_fields'}
    ids=[];requested=0
    for family in families:
        need(type(family) is dict and set(family)==required,'exact family fields')
        fid=family['id'];need(type(fid) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,39}',fid) and fid not in ids,'family identity');ids.append(fid)
        need(type(family['priority']) is int and 1<=family['priority']<=4,'family priority')
        need(family['origin'] in ('model_intuition','fixed','random') and family['mechanism_status'] in ('hypothesis','unknown_anomaly'),'family provenance/unknown mechanism')
        need(type(family['mechanism']) is str and 0<len(family['mechanism'])<=1500,'bounded mechanism or explicit unknown')
        need(family['role'] in ('original','single_condition','combination','alternative','baseline'),'family role')
        params=family['parameters'];need(type(params) is dict and len(params)<=4,'four parameter axes maximum')
        for name,values in params.items():
            need(re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,39}',name) and type(values) is list and 1<=len(values)<=8,'bounded parameter axis')
            need(all(type(v) in (int,float) and math.isfinite(v) for v in values),'numeric finite parameters only; no researcher code')
        requested+=math.prod(len(v) for v in params.values())
    need(requested<=32,'expanded batch exceeds32 candidates before computation')
    comparisons=args['comparisons'];need(type(comparisons) is list and len(comparisons)<=16,'comparison bound')
    for item in comparisons:
        need(type(item) is dict and set(item)=={'kind','families','prediction'},'exact comparison declaration')
        need(item['kind'] in ('incremental_condition','parameter_neighborhood','alternative_digits','alternative_explanation','generator_control'),'comparison kind')
        need(type(item['families']) is list and 1<=len(item['families'])<=4 and all(f in ids for f in item['families']),'comparison families')
        need(type(item['prediction']) is str and 0<len(item['prediction'])<=1500,'distinguishing prediction')
    rows=[];stockdays=len(case['decision_fixture']['codes'])*len(case['decision_fixture']['calendar']);total_cells=0
    for family in sorted(families,key=lambda f:(f['priority'],ids.index(f['id']))):
        template=family['program_template'];need(type(template) is dict,'strategy template object')
        params=family['parameters'];names=list(params)
        for combination in itertools.product(*(params[n] for n in names)):
            values=dict(zip(names,combination));program=deepcopy(template);error=None;compiled=None;cost=None
            try:
                expressions=[f['expression'] for f in program['factors']]+[program['target_weight_expression']]
                used={m.group(1) for expression in expressions for m in PLACEHOLDER.finditer(expression)}
                need(used==set(names),'each parameter must occur in a controlled expression; no hidden axes')
                substitute=lambda e:PLACEHOLDER.sub(lambda m:'('+json.dumps(values[m.group(1)],allow_nan=False)+')',e)
                for factor in program['factors']:factor['expression']=substitute(factor['expression'])
                program['target_weight_expression']=substitute(program['target_weight_expression'])
                compiled=validate(program,public_field_contract=case['decision_fixture']['fields']);cost=complexity(program)
                used_fields={n.id for e in [f['expression'] for f in program['factors']]+[program['target_weight_expression']] for n in ast.walk(ast.parse(e,mode='eval')) if isinstance(n,ast.Name)}
                digits={name for name,m in limits['field_semantics'].items() if m['digit_derivation'] is not None and name in used_fields}
                need(type(family['digit_fields']) is list and set(family['digit_fields'])==digits,'all used digit fields must be declared')
                if digits:
                    kinds={c['kind'] for c in comparisons if family['id'] in c['families']}
                    need({'alternative_digits','alternative_explanation'}<=kinds,'digit families require registered digit and market-structure alternatives')
            except (ValueError,TypeError,KeyError,SyntaxError) as exc:error=type(exc).__name__+': '+str(exc)[:2000]
            expression_count=len(program.get('factors',[]))+1 if type(program.get('factors')) is list else 1
            cells=stockdays*(expression_count+1);total_cells+=cells
            rows.append({'candidate_id':f'c{len(rows)+1:03d}','family_id':family['id'],'parameters':values,'program':program,
                'program_hash':digest(program),'executable_program_hash':executable_program_hash(program) if compiled and error is None else None,
                'validation_error':error,'complexity':cost,'reserved_expression_and_execution_scan_cells':cells,
                'parameter_axis_count':len(names),'parameter_grid_size':math.prod(len(v) for v in params.values())})
    prior=plans(folder.parent)
    reserved=sum(p['reserved_candidates'] for _,p in prior)+ordinary_candidates
    cells=sum(p['reserved_scan_cells'] for _,p in prior)+ordinary_candidates*stockdays*6+other_scan_cells
    need(reserved+len(rows)<=limits['max_candidates_total'],'task expanded candidate budget exhausted')
    need(cells+total_cells<=limits['max_scan_cells_total'],'task scan-cell budget exhausted')
    from .runtime import source_pins
    result={'kind':'frozen_research_batch_v1','declaration':args,'case_hash':digest(case),'policy':limits,'source_pins':source_pins(),
        'initial_cash':case['initial_cash'],'data_and_cost_contract_hash':digest(case),'candidates':rows,
        'reserved_candidates':len(rows),'reserved_scan_cells':total_cells,'ordinary_candidates_before_registration':ordinary_candidates,
        'other_scan_cells_before_registration':other_scan_cells,'other_comparisons_before_registration':other_comparisons,
        'scan_definition':'stockdays*(number_of_factor_and_target_expressions+one_raw_execution_calendar); reservations are not physical IO or independent samples',
        'new_model_calls':0,'independent_research_samples':0,'formal_target_success':False}
    save(folder/'registration.json',result)
    return result


def _receipt(folder,row,case_hash,save):
    path=folder/'worker_result.json'
    if not path.exists():return None
    result=read(path);intent=read(folder/'intent.json')
    need(intent['case_hash']==case_hash and digest(intent['case'])==case_hash and intent['program_hash']==row['program_hash'] and
         digest(intent['program'])==row['program_hash'],'candidate input identity drift')
    need(result['request_hash']==digest(intent) and result['artifact_hash']==digest(result['artifact']) and
         digest(result['artifact']['program'])==row['program_hash'],'candidate result binding drift')
    artifact=result['artifact'];raw=artifact.get('raw') or {}
    summary=account_attribution(row['candidate_id'],artifact,intent['case']['decision_fixture']['calendar'])
    public=public_diagnosis({'accounts':[summary],'pairs':[]})['accounts'][0]
    receipt={'candidate_id':row['candidate_id'],'status':'completed' if raw.get('status')=='completed_mechanical' else 'failed',
        'program_hash':row['program_hash'],'executable_program_hash':row['executable_program_hash'],
        'artifact_hash':result['artifact_hash'],'worker_result_hash':digest(result),'account':public,
        'raw_status':raw.get('status'),'error':raw.get('error') or artifact.get('workbench',{}).get('error'),
        'observed_scan_completion':{'targets_saved':(folder/'workbench/targets.json').is_file(),
            'raw_result_saved':artifact.get('raw') is not None,'valued_calendar_sessions':summary.get('saved_cash_days',0),
            'full_calendar_completed':summary['complete_account'],
            'note':'Exact saved phase/session observations; not physical IO counts. Unresolved dispatches have unknown computation completion.'}}
    final=folder/'receipt.json'
    if final.exists():need(read(final)==receipt,'saved candidate receipt drift')
    else:save(final,receipt)
    return receipt


def run(folder,registration,case,save,*,deadline_epoch,source_pins,execute_new=True,ordinary_evidence=()):
    need(read(folder/'registration.json')==registration and digest(case)==registration['case_hash'],'batch registration/case drift')
    need(registration['source_pins']==source_pins,'batch registration source drift')
    deadline=min(deadline_epoch,time.time()+registration['policy']['max_wall_seconds'])
    start=folder/'run_intent.json'
    if start.exists():deadline=min(deadline,read(start)['deadline_epoch'])
    elif execute_new:
        save(start,{'registration_hash':digest(registration),'deadline_epoch':deadline,'source_pins':source_pins})
    if start.exists():need(read(start)['source_pins']==source_pins and read(start)['registration_hash']==digest(registration),'batch source pins changed')
    reconcile_registry(folder.parent,registration['case_hash'],save)
    rows=[];stop='unresolved_candidate' if unresolved(folder.parent) else None;known={}
    for eid,artifact in ordinary_evidence:
        known[executable_program_hash(artifact['program'])]={'ordinary_evidence_id':eid,'artifact_hash':digest(artifact)}
    # Across batches, exact rules are reused only within the same frozen case.
    for other,plan in plans(folder.parent):
        if other==folder or plan['case_hash']!=registration['case_hash']:continue
        for candidate in plan['candidates']:
            receipt=other/'candidates'/candidate['candidate_id']/'receipt.json'
            if receipt.exists():known[candidate['executable_program_hash']]={'batch_id':other.name,'candidate_id':candidate['candidate_id'],'receipt_hash':digest(read(receipt))}
    for row in registration['candidates']:
        work=folder/'candidates'/row['candidate_id'];work.mkdir(parents=True,exist_ok=True)
        result=_receipt(work,row,registration['case_hash'],save) if (work/'intent.json').exists() else None
        if result is None and (work/'disposition.json').exists():result=read(work/'disposition.json')
        if result is None and (work/'intent.json').exists():
            result={'candidate_id':row['candidate_id'],'status':'unknown','error':'dispatch exists without committed worker result; saved-only reconciliation required'};stop='unresolved_candidate'
        if result is None:
            if stop or time.time()>=deadline or not execute_new:
                result={'candidate_id':row['candidate_id'],'status':'not_started','reason':stop or ('saved_only_recovery' if not execute_new else 'deadline')}
            elif row['validation_error']:
                result={'candidate_id':row['candidate_id'],'status':'invalid','error':row['validation_error']};save(work/'disposition.json',result)
            elif row['executable_program_hash'] in known:
                reference=known[row['executable_program_hash']]
                if 'ordinary_evidence_id' in reference:
                    cache=folder/'ordinary_evidence'/reference['ordinary_evidence_id']/'artifact.json'
                    if not cache.exists():save(cache,next(a for eid,a in ordinary_evidence if eid==reference['ordinary_evidence_id']))
                result={'candidate_id':row['candidate_id'],'status':'reused','reference':reference};save(work/'disposition.json',result)
            else:
                intent={'case':case,'case_hash':digest(case),'program':row['program'],'program_hash':row['program_hash'],
                    'source_pins':source_pins,'reserved_scan_cells':row['reserved_expression_and_execution_scan_cells'],'deadline_epoch':deadline}
                save(work/'intent.json',intent)
                env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP','COMSPEC','PATHEXT','USERPROFILE','APPDATA','LOCALAPPDATA','NUMBER_OF_PROCESSORS')}
                env['PYTHONPATH']=str(ROOT/'src')
                with (work/'worker.log').open('xb') as log:
                    process=subprocess.Popen([sys.executable,'-m','quanta_agents.meta_v3.batch_worker','--request',str((work/'intent.json').resolve())],
                        cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    import psutil
                    try:created=psutil.Process(process.pid).create_time()
                    except psutil.Error:created=None
                    save(work/'dispatch.json',{'pid':process.pid,'create_time':created,'started_at':time.time()})
                    killed_reason=None
                    while process.poll() is None:
                        used=tree_bytes(folder)
                        if time.time()>=deadline or used>=registration['policy']['output_stop_threshold_bytes']:
                            killed_reason='deadline' if time.time()>=deadline else 'output_bytes';process.kill();process.wait();break
                        try:process.wait(timeout=.25)
                        except subprocess.TimeoutExpired:pass
                    save(work/'process_exit.json',{'exit_code':process.returncode,'killed_reason':killed_reason})
                result=_receipt(work,row,registration['case_hash'],save)
                if result is None:
                    result={'candidate_id':row['candidate_id'],'status':'unknown','error':'worker stopped without committed result; no automatic retry'};stop='unresolved_candidate'
        if result['status'] in ('completed','failed'):
            known[row['executable_program_hash']]={'batch_id':folder.name,'candidate_id':row['candidate_id'],'receipt_hash':digest(result)}
        if result['status'] in ('failed','invalid') and registration['declaration']['stop_rule']=='stop_on_first_failure':stop=stop or 'registered_failure_stop'
        used=tree_bytes(folder)
        if used>=registration['policy']['output_stop_threshold_bytes']:stop=stop or 'output_bytes'
        rows.append({**result,'family_id':row['family_id'],'parameters':row['parameters'],'complexity':row['complexity']})
    return {'kind':'batch_execution_evidence_v1','batch_id':folder.name,'registration_hash':digest(registration),'candidates':rows,
        'reserved_candidates':registration['reserved_candidates'],'reserved_scan_cells':registration['reserved_scan_cells'],
        'started_candidates':sum((folder/'candidates'/r['candidate_id']/'intent.json').exists() for r in registration['candidates']),
        'dispatch_reserved_scan_cells':sum(r['reserved_expression_and_execution_scan_cells'] for r in registration['candidates'] if (folder/'candidates'/r['candidate_id']/'intent.json').exists()),
        'scan_accounting_note':'Expanded candidates and dispatch reservations are exact. Each candidate receipt reports saved completion phases and valued sessions; interrupted work has unknown actual scan completion. Physical disk-read counts are not inferred.',
        'output_guard':'0.25second polling stop threshold, not an OS-enforced hard byte cap; kernel per-candidate persistence bounds remain active',
        'stop_reason':stop,'new_model_calls_inside_batch':0,'complete_summary':True,'formal_target_success':False,
        'interpretation':'All candidates including failures/unknowns/not-started/reused are returned in frozen order. No best-only selection, significance or OOS claim.'}


def artifact_for(folder,registration,candidate_id,save):
    candidates={r['candidate_id']:r for r in registration['candidates']};need(candidate_id in candidates,'unknown batch candidate')
    row=candidates[candidate_id];work=folder/'candidates'/candidate_id
    receipt=_receipt(work,row,registration['case_hash'],save) if (work/'intent.json').exists() else None
    if receipt is None and (work/'disposition.json').exists():
        d=read(work/'disposition.json');need(d['status']=='reused','candidate has no executed artifact')
        ref=d['reference']
        if 'ordinary_evidence_id' in ref:
            artifact=read(folder/'ordinary_evidence'/ref['ordinary_evidence_id']/'artifact.json')
            need(digest(artifact)==ref['artifact_hash'] and executable_program_hash(artifact['program'])==row['executable_program_hash'],'ordinary reused artifact drift')
            return artifact
        other=folder.parent/ref['batch_id'];other_plan=read(other/'registration.json')
        need(other_plan['case_hash']==registration['case_hash'],'cross-case reuse forbidden')
        need(digest(read(other/'candidates'/ref['candidate_id']/'receipt.json'))==ref['receipt_hash'],'reused receipt drift')
        return artifact_for(other,other_plan,ref['candidate_id'],save)
    need(receipt is not None,'candidate execution unresolved or not started')
    return read(work/'worker_result.json')['artifact']

"""Read only fixed metadata sources; never follow a market/source path in JSON."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.formal_preflight import current_proposal, evaluate, digest

BASE = ROOT/'docs/research/meta_framework_v4_handoff/formal_preflight'
SOURCES = {
    'tasks':ROOT/'src/quanta_agents/meta/ashare_tasks.py',
    'benchmark':ROOT/'docs/META_ASHARE_BENCHMARK_CASES.md',
    'protocol':ROOT/'docs/META_ASHARE_PROTOCOL_DRAFT.md',
    'v4':ROOT/'docs/META_FRAMEWORK_V4_IMPLEMENTATION_PROMPT_20260908.md',
    'prior_admission':ROOT/'docs/research/meta_framework_v4_handoff/formal_admission_002.json',
    'legacy_manifest':ROOT/'experiment_traces/meta_ashare_v2/meta-20260906-001003-9b47f3/manifest.json',
    'exposure':ROOT/'experiment_traces/meta_ashare_v2/meta-20260906-001003-9b47f3/evaluation_exposure.json',
    'market_manifest':Path('/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025/manifest.json'),
}
IMPLEMENTATIONS = ('architecture_contract.py','runtime.py','controller_plan.py','context.py',
    'registered_producer.py','generator_controls.py','research_batch.py','batch_worker.py')


def load_current_metadata():
    pins, raw = [], {}
    for key,path in SOURCES.items():
        body = path.read_bytes()
        if len(body)>2000000: raise ValueError('bounded metadata source too large: '+key)
        pins.append({'role':key,'path':str(path),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)})
        raw[key] = body.decode('utf-8-sig')
    tasks_node = next(n.value for n in ast.parse(raw['tasks']).body
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TASKS' for t in n.targets))
    tasks = ast.literal_eval(tasks_node)  # No case adapter construction or imports.
    legacy = json.loads(raw['legacy_manifest'])['case']
    exposure = json.loads(raw['exposure'])
    prior = json.loads(raw['prior_admission'])
    market = json.loads(raw['market_manifest'])
    implementation_pins = {}
    for name in IMPLEMENTATIONS:
        path=ROOT/'src/quanta_agents/meta_v3'/name
        body=path.read_bytes()
        checksum=hashlib.sha256(body).hexdigest()
        implementation_pins[str(path.relative_to(ROOT))]=checksum
        pins.append({'role':'architecture_source','path':str(path),'sha256':checksum,'bytes':len(body)})
    manifest_pin=next(p for p in pins if p['role']=='legacy_manifest')['sha256']
    cases=[]
    for key,definition in tasks.items():
        exposure_class=('exposed_development' if key in ('price_repair','relative_strength','volume_expansion') else 'unreviewed_definition')
        cases.append({'key':key,'scientific_case_id':definition['case_id'],'family':definition['family'],
            'role':'real_research','definition_hash':digest({'task':definition['task'],'baseline':definition['baseline']}),
            'exposure_class':exposure_class,
            'input_binding':{'task_sha256':digest(definition['task']),'initial_strategy_sha256':digest(definition['baseline']),
                'manifest_sha256':manifest_pin,'calendar_sha256':legacy['calendar_sha256'],
                'membership_sha256':legacy['membership_sha256'],'data_snapshot_id':legacy['datahash'],'split':'final'}})
    # The original negative-control card is evidence of a definition, not a sixth real task.
    null_card=raw['benchmark'].split('### N01',1)[1].split('\n## ',1)[0]
    negative={'key':'N01','scientific_case_id':'benchmark_draft_N01','family':'negative_control',
        'role':'negative_control','definition_hash':digest(null_card),'exposure_class':'negative_control_definition',
        'input_binding':dict(cases[0]['input_binding'],task_sha256=digest(null_card),initial_strategy_sha256=None)}
    cases.append(negative)
    metadata={'cases':cases,'implementation_pins':implementation_pins,'periods':legacy['splits'],
        'market_identity_scope':legacy['snapshot_hash_method'],
        'market_manifest_summary':{k:market[k] for k in ('actual_date_range','stock_file_count','row_count','available_fields')},
        'legacy_execution_scope':{'scope':legacy['scope'],'capacity_verified':legacy['capacity_verified'],
            'limitations':legacy['limitations'],'final_sealed_metadata_only':legacy['final_sealed']},
        'observed_exposure':exposure,'prior_case_exposure_audit':prior['case_denominator_correction']['exposure_evidence']}
    return metadata,pins


def proposal_path(value):
    path=Path(value).resolve()
    # This CLI intentionally has no arbitrary data/case/source reader. Optional
    # input is a small local metadata proposal in its dedicated new directory.
    if path.parent!=BASE.resolve() or not path.name.startswith('candidate_') or path.suffix!='.json':
        raise ValueError('proposal must be formal_preflight/candidate_*.json; source references are never followed')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',help='Optional candidate metadata only; never a case value file')
    parser.add_argument('--label',default='current_001')
    args=parser.parse_args()
    if not args.label.replace('_','').isalnum() or len(args.label)>60:raise ValueError('bounded output label')
    path=proposal_path(args.candidate) if args.candidate else None
    metadata,pins=load_current_metadata()
    if path:
        body=path.read_bytes()
        if len(body)>2000000:raise ValueError('bounded candidate metadata')
        proposal=json.loads(body)
        pins.append({'role':'candidate_proposal','path':str(path),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)})
    else:proposal=current_proposal(metadata)
    result=evaluate(metadata,proposal)
    result.update(observed_at=datetime.now(timezone.utc).isoformat(),source_inputs=pins,
        source_case_catalog=metadata['cases'],policy_references={
            'financial_and_batch':'docs/META_FRAMEWORK_V4_IMPLEMENTATION_PROMPT_20260908.md:146-151',
            'statistics':'docs/META_ASHARE_PROTOCOL_DRAFT.md:97-127',
            'five_plus_control':'docs/META_ASHARE_BENCHMARK_CASES.md:7-20'})
    result['checker_source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(__file__),ROOT/'src/quanta_agents/meta_v3/formal_preflight.py')}
    for item in pins:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('metadata changed during preflight: '+item['role'])
    result['input_hashes_unchanged']=True
    BASE.mkdir(parents=True,exist_ok=True)
    out=BASE/('report_'+args.label+'.json')
    candidate=BASE/('candidate_'+args.label+'.json')
    if out.exists() or (not path and candidate.exists()):raise ValueError('append-only output already exists')
    if not path:
        with candidate.open('x',encoding='utf-8') as f:json.dump(proposal,f,ensure_ascii=False,indent=2,sort_keys=True)
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,sort_keys=True)
    print(json.dumps({'report':str(out.relative_to(ROOT)),'status':result['status'],
        'counts':result['counts'],'case_denominator':result['gates'][0]['observed'],
        'new_research_calls':0,'new_registered_slots':0,'sealed_value_reads':0},ensure_ascii=True))


if __name__=='__main__':main()

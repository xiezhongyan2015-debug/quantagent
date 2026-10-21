"""Metadata-first input admission. No source byte is read by this module.

The controller declares paths and partitions explicitly. These declarations
are auditable authorization, not proof of file content or historical arrival.
"""
from datetime import date
from pathlib import Path

from .ledger import need,digest
from .kernel import module


def preflight_case(case):
    from .conditional_risk_derivation import validate_case_metadata
    validate_case_metadata(case)
    from .archive_codec import case_policy
    from .target_schedule import validate_case_policy
    validate_case_policy(case)
    case_policy(case)
    if 'report_policy' in case:
        need(case['report_policy'] == {'version': 'claim_support_v1'}, 'unknown frozen report policy')
    if 'experiment_design_policy' in case:
        from .experiment_design import VERSION
        need(case['experiment_design_policy'] == {'version': VERSION}, 'unknown frozen experiment design policy')
    need(case.get('research_class') in ('real_saved_development','synthetic_calibration'),'research class not admitted')
    backend=case.get('execution_backend')
    need(backend in (None,'v3_streamed_001','v3_l2_cash_001','v3_structural_001'),'unknown execution backend')
    if 'batch_policy' in case:
        from .research_batch import policy
        policy(case)
    if case['research_class']=='synthetic_calibration':
        need(backend is None,'synthetic scope backend mismatch')
        module('meta.factor_strategy_program').fixture_axes(case['decision_fixture'])
    else:
        from .real_program import matrices
        matrices(case['decision_fixture'])
        if backend=='v3_l2_cash_001':
            from .l2_saved_execution import validate_case
            validate_case(case)
        else:
            need(case['decision_fixture']['kind']=='exposed_real_decision_table','daily source/backend mismatch')
            if backend == 'v3_structural_001':
                from .suspension_market import SuspensionMarket
                bindings = case['raw_source_bindings']
                SuspensionMarket(bindings['market_schedule'], case['decision_fixture']['codes'],
                    case['decision_fixture']['calendar'], bindings.get('corporate_actions', []))


def source_paths(case):
    paths={str(Path(p['path']).resolve()):'evidence' for p in case.get('evidence_sources',[])}
    for artifact in case['raw_source_bindings']['source_artifacts']:
        for key in ('rows_file','manifest_file'):
            paths[str((Path(artifact['root'])/artifact[key]).resolve())]='market'
    if case.get('execution_backend') == 'v3_structural_001':
        for episode in case['raw_source_bindings']['market_schedule']['episodes']:
            for key in ('document_path', 'review_path'):
                path = str(Path(episode[key]).resolve())
                need(paths.get(path) != 'market', 'halt notice cannot alias a market data file')
                paths[path] = 'historical_notice'
    return paths


def admit_paths(case,declarations,*,engineering_allowed=False):
    expected=source_paths(case)
    need(type(declarations) is list and len(declarations)<=256,'explicit bounded source path admissions required')
    admitted={}
    for item in declarations:
        need(type(item) is dict and set(item)=={'path','role','partition','content_date_range'},'exact source admission fields')
        path=str(Path(item['path']).resolve())
        need(path in expected and path not in admitted,'unrequested or duplicate source path admission')
        role,partition,span=item['role'],item['partition'],item['content_date_range']
        if role=='engineering':
            need(engineering_allowed and partition=='generated_fixture' and span is None,'engineering source cannot masquerade as market proof')
        else:
            need(type(span) is list and len(span)==2 and all(type(x) is str and date.fromisoformat(x).isoformat()==x for x in span)
                 and span[0]<=span[1]<=date.today().isoformat(),'source content date range required')
            if role=='development_market_data':
                allowed=(partition=='exposed_2017_2021' and '2017-01-01'<=span[0]<=span[1]<='2021-12-31') or (
                    partition=='exposed_l2_2026' and case['decision_fixture']['kind']=='exposed_l2_decision_table' and span[0]>='2026-01-01')
                need(allowed,'market source partition not admitted; sealed data cannot be read to check its hash')
            else:
                need(role=='historical_notice' and partition=='public_disclosure' and expected[path]!='market',
                     'notice admission cannot authorize raw market files')
        admitted[path]=item
    need(set(admitted)==set(expected),'every new source path requires explicit admission before byte reads')
    for path in expected:
        source=Path(path)
        need(source.is_file() and source.stat().st_size<=128*1024**2,'bounded existing source required before content verification')
    return {'kind':'metadata_first_source_admission_v1','declarations':declarations,
            'source_path_set_hash':digest(sorted(expected)),'content_verified':False}


def substantive_change(parent,child,kind,requested_fields=None):
    """Kind-specific observable change; prose descriptions never renew budget."""
    a,b=parent['decision_fixture'],child['decision_fixture']
    field_key=lambda r:(r['session'],r['symbol'],r['field'])
    new_fields=sorted({x['name'] for x in b['fields']}-{x['name'] for x in a['fields']})
    new_codes=sorted(set(b['codes'])-set(a['codes']));new_days=sorted(set(b['calendar'])-set(a['calendar']))
    old_rows={field_key(r):{k:v for k,v in r.items() if k!='source_evidence_id'} for r in a['field_rows']}
    new_rows={field_key(r):{k:v for k,v in r.items() if k!='source_evidence_id'} for r in b['field_rows']}
    new_coordinates=set(new_rows)-set(old_rows)
    old_fields={f['name']:f['unit'].strip() for f in a['fields']};child_fields={f['name']:f['unit'].strip() for f in b['fields']}
    fields_changed=bool(new_coordinates)
    if kind=='data':
        need(fields_changed,'data extension has no substantive field/observation addition; metadata-only renewal refused')
        need(all(name in child_fields and child_fields[name]==unit for name,unit in old_fields.items()),
             'unit/field semantic correction requires explicit correction review, not extension renewal')
        need(all(key in new_rows and new_rows[key]==row for key,row in old_rows.items()),
             'existing data correction or deletion is not an additive extension')
        need(any(new_rows[key]['value'] is not None for key in new_coordinates),'new data delivery contains no observed values')
        if requested_fields is not None:
            need(type(requested_fields) is list and requested_fields and set(requested_fields)<={key[2] for key in new_coordinates},
                 'requested field deliverables absent from actual additions')
    elif kind=='universe':
        need(new_codes and set(a['codes'])<=set(b['codes']),'universe extension must actually add supported symbols; no return-based replacement')
    elif kind=='tool':
        from .research_iteration import action_limits,NEW_ACTIONS
        from .research_tools import LIMITS
        before,after=action_limits(parent,LIMITS),action_limits(child,LIMITS)
        added=[k for k in NEW_ACTIONS if not before[k] and after[k]]
        need(added,'tool extension must admit an implemented new action; quota-only renewal refused')
        return {'kind':kind,'new_actions':added}
    else:
        need(False,'benchmark or new asset extension requires a supported dedicated adapter before ready admission')
    return {'kind':kind,'added_fields':new_fields,'added_symbols':new_codes,'added_sessions':new_days,
            'new_observation_coordinates':len(new_coordinates),'requested_fields_verified':requested_fields,
            'decision_content_changed':fields_changed,'request_semantic_match':'Controller must explain how actual changes satisfy the saved request; no NLP truth claim'}

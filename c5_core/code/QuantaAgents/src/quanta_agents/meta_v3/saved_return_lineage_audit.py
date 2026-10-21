"""Fixed read-only audit of an already exposed archive; no strategy execution.

This independent book cannot release formal data or reuse the consumed account
audit. CONSUMED commits before this backend opens bound input bytes. Preparation
has already inspected this exposed case; this is not a first-exposure claim.
"""
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time

from .kernel import ROOT
from .ledger import digest, need, serial

VERSION = 'saved_return_lineage_audit_v1'
SUBJECT = 'year_inputs_001/sh600004/2019/return_lineage_v1'
BOOK = ROOT / 'experiment_traces/meta_framework_v4/input_lineage_audits.sqlite3'
YEAR = 'experiment_traces/meta_framework_v3/year_inputs_001/'
ORIGIN = 'experiment_traces/meta_development_raw_coverage_v13/attempts/20260906T184719050829Z/'
SOURCES = {
    YEAR+'case.json': ('dee62e885fca97588334cf7f1d8ef97a37c90a425a523e6537b36442ef1ef776', 'exposed_case', 4*1024**2),
    YEAR+'preparation_plan.json': ('52ec9c89e2deed54f8caaf9c08d9972a5b0c11f7b2c4fb5bae6330adadcee3b0', 'metadata', 1024**2),
    YEAR+'selected_rows.json': ('e2ae35c519f9b3db17d00ff7263e2d9e8e6354a7bbdef570c0db4d8cf8a7c9c0', 'selected_2019', 2*1024**2),
    YEAR+'reference_audit.json': ('fdcda0ed04a404f634a19dc5e13a84224a1da5c844b9f6052c2edab33970ff44', 'saved_review', 2*1024**2),
    YEAR+'cash_action_manifest.json': ('bec24394e4597c5e6d4c1d99ddc8567cf816c96efe6914c4342c8bd3b21d7e2f', 'saved_review', 2*1024**2),
    'docs/research/meta_framework_v3_handoff/baiyun_2019_scope_admission_001.md': ('167820e68996bc55ec32f26e4aba8a1d9bc04151a55c20c53fcb1ed81d998c2b', 'saved_review', 1024**2),
    'experiment_traces/meta_framework_v3/company_action_evidence_002/baiyun_implementation.pdf': ('4296ecb245390903a59b5d945390959bb0fb623569c47214f83d642806ddf3bd', 'document_identity_only', 16*1024**2),
    'experiment_traces/meta_framework_v3/company_action_evidence_001/sh600004.pdf': ('7fac0eb9db9a149453e39ceeb5e47d3e43ea00798c20511fd0148e7351b47d1d', 'document_identity_only', 32*1024**2),
    'experiment_traces/meta_framework_v3/company_action_evidence_002/baiyun_implementation_receipt.json': ('2e11c4d8203c8355f197090e15b301e03ff35adc921d722ecfacab5f4e5ec5d1', 'metadata', 1024**2),
    ORIGIN+'plan.json': ('b927d358b108ff3ee55ff0dc9f4d055762ed72d1edda7b63ee17f9a137240f79', 'metadata', 2*1024**2),
    ORIGIN+'codes/sh600004/source_manifest.json': ('eeca95b6575083e8df9ead290798a60016c9f6b6d05aa02864d3bc75f29e5b36', 'metadata', 1024**2),
    ORIGIN+'codes/sh600004/rows.json.gz': ('464e962a0eb88cbbfd30ceb2e0bc8d7d72484fcfd845f88ef32de1db94233081', 'physical_exposed_2017_2021_archive', 2*1024**2),
}


def code_pins():
    from .runtime import source_pins
    pins = source_pins()
    path = ROOT / 'scripts/run_v4_saved_return_lineage.py'
    pins[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return pins


def metadata_manifest():
    sources = [{'path': str(ROOT / p), 'relative_path': p, 'sha256': info[0],
                'role': info[1], 'max_bytes': info[2]} for p, info in SOURCES.items()]
    return {'version': VERSION, 'subject': SUBJECT, 'sources': sources,
        'physical_market_partition': 'exposed_2017_2021', 'physical_market_date_range': ['2017-01-01','2021-12-31'],
        'selected_symbol': 'sh600004', 'selected_date_range': ['2019-01-02','2019-12-31'],
        'selected_sessions': 244, 'explicit_previous_anchor': '2018-12-28',
        'max_expanded_archive_bytes': 8*1024**2, 'max_archive_rows': 2000,
        'original_csv_reads_allowed': False, 'strategy_execution_allowed': False,
        'formal_release_allowed': False, 'prior_preparation_exposure': True}


def validate_manifest(manifest):
    need(manifest == metadata_manifest(), 'fixed exposed input scope changed; formal/sealed release unsupported')
    for entry in manifest['sources']:
        path = Path(entry['path'])
        need(path.is_absolute() and path.resolve() == path and not path.is_symlink()
             and path.is_file() and path.stat().st_size <= entry['max_bytes'], 'bounded unredirected source metadata required')
    need(sum(Path(e['path']).stat().st_size for e in manifest['sources']) <= 64*1024**2, 'total source bytes exceed bound')


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='') as stream:
        stream.write(serial(value)); stream.flush()
        import os
        os.fsync(stream.fileno())


def _event(db, phase, payload):
    row = db.execute('SELECT ordinal,event_hash FROM events WHERE subject=? ORDER BY ordinal DESC LIMIT 1', (SUBJECT,)).fetchone()
    ordinal, previous = (row[0]+1,row[1]) if row else (1,None)
    body = {'subject':SUBJECT,'ordinal':ordinal,'previous_hash':previous,'phase':phase,'payload':payload}
    db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(SUBJECT,ordinal,serial(body),previous,digest(body)))


def _consume(manifest, output, pins, as_of):
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(BOOK) as db:
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('CREATE TABLE IF NOT EXISTS audits(subject TEXT PRIMARY KEY, manifest TEXT, manifest_hash TEXT, output TEXT, state TEXT, receipt TEXT); CREATE TABLE IF NOT EXISTS events(subject TEXT, ordinal INTEGER, body TEXT, previous_hash TEXT, event_hash TEXT, PRIMARY KEY(subject,ordinal));')
        db.execute('BEGIN IMMEDIATE')
        need(db.execute('SELECT 1 FROM audits WHERE subject=?',(SUBJECT,)).fetchone() is None,
             'original lineage audit already consumed; aliases and redispatch forbidden')
        frozen = {'scope':manifest,'code_pins':pins,'as_of':as_of,'output':str(output)}
        db.execute('INSERT INTO audits VALUES(?,?,?,?,?,NULL)',(SUBJECT,serial(frozen),digest(frozen),str(output),'CONSUMED'))
        _event(db,'CONSUMED',{'manifest_sha256':digest(frozen),'before_backend_input_bytes':True})
    return frozen


def _settle(state, receipt):
    need(state in ('COMPLETE','INCOMPLETE_TERMINAL') and receipt.get('subject')==SUBJECT
         and receipt.get('formal_target_success') is False, 'invalid lineage terminal receipt')
    with sqlite3.connect(BOOK) as db:
        db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
        need(db.execute('SELECT state FROM audits WHERE subject=?',(SUBJECT,)).fetchone() == ('CONSUMED',), 'lineage audit no longer unresolved')
        db.execute('UPDATE audits SET state=?,receipt=? WHERE subject=?',(state,serial(receipt),SUBJECT))
        _event(db,state,{'receipt_sha256':digest(receipt)})


def status():
    need(BOOK.is_file(), 'lineage audit book not created')
    with sqlite3.connect(BOOK.resolve().as_uri()+'?mode=ro', uri=True) as db:
        row=db.execute('SELECT manifest,manifest_hash,state,receipt,output FROM audits WHERE subject=?',(SUBJECT,)).fetchone()
        need(row is not None, 'lineage audit not consumed')
        frozen=json.loads(row[0]); need(digest(frozen)==row[1], 'frozen lineage identity changed')
        need(frozen['scope']==metadata_manifest(), 'frozen lineage scope changed')
        need(row[4]==frozen['output'], 'frozen lineage output binding changed')
        previous=None; phases=[]
        for ordinal, body_text, prior, event_hash in db.execute('SELECT ordinal,body,previous_hash,event_hash FROM events WHERE subject=? ORDER BY ordinal',(SUBJECT,)):
            body=json.loads(body_text)
            need(ordinal==len(phases)+1 and prior==previous and body['previous_hash']==previous
                 and body['ordinal']==ordinal and body['subject']==SUBJECT and digest(body)==event_hash, 'lineage event chain changed')
            phases.append(body); previous=event_hash
        need(phases and phases[0]['phase']=='CONSUMED' and phases[0]['payload']['manifest_sha256']==row[1], 'initial lineage consumption missing')
        receipt=json.loads(row[3]) if row[3] else None
        if receipt is not None:
            need(receipt.get('subject')==SUBJECT and receipt.get('formal_target_success') is False,
                 'lineage receipt scope or success claim changed')
        need((row[2]=='CONSUMED' and len(phases)==1 and receipt is None) or
             (row[2] in ('COMPLETE','INCOMPLETE_TERMINAL') and len(phases)==2 and phases[-1]['phase']==row[2]
              and phases[-1]['payload']['receipt_sha256']==digest(receipt)), 'lineage terminal binding changed')
        return {'subject':SUBJECT,'state':row[2],'manifest':frozen,'manifest_sha256':row[1],
                'receipt':receipt,'event_count':len(phases),'event_head':previous,'formal_target_success':False}


def _read_inputs(manifest):
    validate_manifest(manifest)
    blobs, inventory = {}, []
    for entry in manifest['sources']:
        path=Path(entry['path'])
        with path.open('rb') as stream: data=stream.read(entry['max_bytes']+1)
        need(len(data)<=entry['max_bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'], 'bound source changed')
        blobs[entry['relative_path']]=data
        inventory.append({**entry,'bytes_read':len(data)})
    return blobs, inventory


def analyze_saved_inputs(manifest, *, as_of):
    from .causal_return_lineage import analyze
    blobs, inventory = _read_inputs(manifest)
    read=lambda name: json.loads(blobs[name].decode('utf-8-sig'))
    case, prep = read(YEAR+'case.json'), read(YEAR+'preparation_plan.json')
    selected, action_manifest = read(YEAR+'selected_rows.json'), read(YEAR+'cash_action_manifest.json')
    days=case['decision_fixture']['calendar']; codes=case['decision_fixture']['codes']
    need(codes==['sh600004'] and days==prep['calendar'] and len(days)==244
         and days[0]=='2019-01-02' and days[-1]=='2019-12-31' and prep['previous_reference_day']=='2018-12-28', 'original case calendar or prior anchor changed')
    need(case['research_class']=='real_saved_development' and case['execution_backend']=='v3_streamed_001'
         and Decimal(case['initial_cash'])==Decimal('1000000'), 'original real development scope changed')
    with gzip.GzipFile(fileobj=io.BytesIO(blobs[ORIGIN+'codes/sh600004/rows.json.gz'])) as stream:
        expanded=stream.read(manifest['max_expanded_archive_bytes']+1)
    need(len(expanded)<=manifest['max_expanded_archive_bytes'], 'archive expansion exceeds admitted bound')
    origin=json.loads(expanded)
    need(type(origin) is list and len(origin)<=2000, 'physical archive row bound')
    need(all(r['code']=='sh600004' and '2017-01-01'<=r['date']<='2021-12-31' for r in origin), 'physical archive exceeds admitted partition')
    origin_by_day={r['date']:r for r in origin}
    need(len(origin_by_day)==len(origin), 'duplicate archive date')
    need([r['date'] for r in selected]==days and all(r==origin_by_day[r['date']] for r in selected), 'saved selected rows do not match original archived rows')
    anchor=origin_by_day[prep['previous_reference_day']]
    source_hash=SOURCES[ORIGIN+'codes/sh600004/rows.json.gz'][0]
    def price(row):
        return {'session':row['date'],'symbol':row['code'],'close':row['raw_price_text']['raw_close'],
                'reference_previous_close':row['raw_price_text']['raw_prev_close'],
                'source_id':digest({'archive_sha256':source_hash,'coordinate':[row['date'],row['code']],'row':row})}
    action=case['raw_source_bindings']['corporate_actions'][0]
    need(case['raw_source_bindings']['corporate_actions']==[action_manifest['announcement']], 'saved action identity differs')
    need(action['rights_issue'] is False and action['stock_distribution_kind']=='none'
         and Decimal(action['stock_distribution_per_share'])==0, 'cash-only source adapter cannot omit a stock/rights action')
    normalized_action={'action_id':action['action_id'],'symbol':action['symbol'],'kind':'cash',
        'announcement_date':action['announcement_date'],'record_date':action['record_date'],'ex_date':action['ex_date'],
        'payment_date':action['cash_payment_date'],'listing_date':None,
        'available_at':action_manifest['announcement_simulated_available_at'],'availability_basis':'declared_simulated',
        'share_increment_per_original_share':'0','gross_cash_per_original_share':action['gross_cash_per_share'],
        'basis':'per_original_pre_ex_share','status':'implementation','source_id':action['source_sha256']}
    scope_id=SOURCES['docs/research/meta_framework_v3_handoff/baiyun_2019_scope_admission_001.md'][0]
    coverage=[{'session':day,'symbol':'sh600004','status':'unknown','source_id':scope_id} for day in days]
    arguments={'calendar':days,'symbols':codes,'prices':[price(r) for r in selected],
        'actions':[normalized_action],'coverage':coverage,'previous_anchors':[price(anchor)],
        'input_kind':'caller_bound_exposed_development','as_of':as_of}
    result=analyze(**arguments)
    fields=[]; source_by_day={r['date']:r for r in selected}
    for row in case['decision_fixture']['field_rows']:
        key='raw_close' if row['field']=='close' else row['field']
        expected=source_by_day[row['session']][key]
        fields.append({'session':row['session'],'symbol':row['symbol'],'field':row['field'],
                       'equal_to_selected_source':Decimal(str(row['value']))==Decimal(str(expected))})
    origin_manifest=read(ORIGIN+'codes/sh600004/source_manifest.json')
    return {'scope':manifest,'source_inventory':inventory,'normalized_inputs':arguments,'lineage':result,
        'selected_origin_exact_rows':len(selected),'explicit_previous_anchor':price(anchor),
        'physical_archive_rows_read':len(origin),'physical_archive_expanded_bytes':len(expanded),
        'old_decision_field_checks':fields,'old_decision_field_matches':sum(r['equal_to_selected_source'] for r in fields),
        'old_action_manifest_source_flags':{k:action_manifest[k] for k in ('company_action_coverage_complete','historical_announcement_available_at_observed','execution_valid')},
        'original_archive_manifest':origin_manifest,
        'byte_binding_verified':True,'selected_rows_origin_binding_verified':True,
        'raw_price_origin_authenticated':False,'historical_arrival_verified':False,'complete_announcement_inventory_verified':False,
        'raw_csv_opened':False,'account_executed':False,'strategy_executed':False,'formal_target_success':False,
        'limitations':['Archived prices and the selected snapshot agree byte-for-row; original vendor collection and full CSV origin are not authenticated.',
                       'Only saved issuer facts and retrospective scope are numerically applied; original PDF bytes are identity-checked, not independently re-reviewed here.',
                       'An adjusted vendor reference close is never the prior actual close. Explicit original archive row supplies the initial pre-close anchor.',
                       'Diagnostic values conditional on saved actions are not qualified causal inputs, historical execution or a new strategy result.']}


def run(output):
    output=Path(output).absolute()
    need(not output.exists() and output.parent.is_dir() and output.parent.resolve()==output.parent,
         'new unredirected audit output required')
    scope=metadata_manifest(); validate_manifest(scope)
    pins=code_pins(); as_of=datetime.now(timezone.utc).isoformat()
    frozen=_consume(scope,output,pins,as_of)
    started,cpu=time.perf_counter(),time.process_time()
    try:
        output.mkdir(); _save(output/'intent.json',frozen)
        need(code_pins()==pins, 'lineage audit code changed before input read')
        report=analyze_saved_inputs(scope,as_of=as_of)
        need(code_pins()==pins, 'lineage audit code changed during read')
        _save(output/'report.json',report)
        receipt={'subject':SUBJECT,'report_sha256':digest(report),'report_file_sha256':hashlib.sha256((output/'report.json').read_bytes()).hexdigest(),
                 'wall_ms':round((time.perf_counter()-started)*1000,3),'controller_cpu_ms':round((time.process_time()-cpu)*1000,3),
                 'strategy_executed':False,'formal_target_success':False}
        _save(output/'receipt.json',receipt); _settle('COMPLETE',receipt)
    except Exception as exc:
        error={'subject':SUBJECT,'type':type(exc).__name__,'message':str(exc),'formal_target_success':False,'original_consumption_preserved':True}
        if output.is_dir() and not (output/'failure.json').exists(): _save(output/'failure.json',error)
        _settle('INCOMPLETE_TERMINAL',error)
        raise
    return status()

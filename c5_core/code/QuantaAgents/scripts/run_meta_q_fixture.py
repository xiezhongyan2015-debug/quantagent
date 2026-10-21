"""No-model end-to-end Q controller fixture on the frozen development data.

Every attempt has a new isolated ledger. Production history and held-out prices
are untouched. Actual research model requests are forbidden by fixture mode.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
import psutil

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'experiment_traces/meta_ashare_revision6'
EXPECTED={
    'snapshot_id':'dac2319409e2b2860014e773a091a5955837b7483e45ae09177e5e1db994146f',
    'development_datahash':'0e9a0455bd8019859baed6037d374e67515f143fceba4faf0cea6bfe7e7fd6e1',
    'loaded_through':'2021-12-31',
}
SCHEDULE=['diagnose','backtest','diagnose','backtest','backtest','submit']


def save(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.path.insert(0,str(STAGE/'src'))
    from quanta_agents.meta.runtime import Engine
    from quanta_agents.meta.server import StateLock
    parent=ROOT/'experiment_traces/meta_q_fixture'
    parent.mkdir(exist_ok=True)
    output=parent/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(exist_ok=False)
    lock=StateLock(output)
    engine=Engine(output)
    before=engine.source_manifest()
    save(output/'fixture_plan.json',{'case':'relative_strength','data_snapshot':EXPECTED,
        'mode':'fixture','action_schedule':SCHEDULE,'submission_policy':'previously_evaluated',
        'include_execution_diagnostics':True,'source_manifest':before,
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'model_calls_permitted':0,'scope':'controller_and_data_integration_not_architecture_research'})
    started=time.monotonic()
    process=psutil.Process()
    samples=[]
    # Fixture-only instrumentation: preserve the traceback otherwise reduced to
    # a concise error by the durable controller, without changing its behavior.
    from quanta_agents.meta.ashare_case import AShareCase
    def instrument(original):
        def wrapped(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except Exception:
                with (output/'case_tracebacks.txt').open('a',encoding='utf-8') as stream:
                    traceback.print_exc(file=stream)
                raise
        return wrapped
    for method in ('_load','evaluate','diagnose'):
        setattr(AShareCase,method,instrument(getattr(AShareCase,method)))
    try:
        run_id=engine.create('fixture',case='ashare',case_config={'task':'relative_strength','include_execution_diagnostics':True},
            expected_data_snapshot=EXPECTED,research_config={'calls_per_architecture':6,'only_architecture':'baseline',
                'evaluation_split':'development','action_schedule':SCHEDULE,'submission_policy':'previously_evaluated'})
        print(json.dumps({'run_id':run_id,'root':str(output),'mode':'fixture','real_model_calls':0}),flush=True)
        last_print=0
        while engine.worker and engine.worker.is_alive():
            engine.worker.join(timeout=2)
            elapsed=time.monotonic()-started
            memory=process.memory_info()
            samples.append({'elapsed_seconds':elapsed,'rss_bytes':memory.rss,
                'private_bytes':memory.private,'peak_private_bytes':memory.peak_pagefile,
                'available_physical_bytes':psutil.virtual_memory().available})
            if elapsed-last_print>30:
                r=engine.get(run_id)
                print(json.dumps({'phase':r['phase'],'status':r['status'],'elapsed_seconds':round(elapsed,1),'model_calls':0,
                    'private_mb':round(memory.private/2**20),'peak_private_mb':round(memory.peak_pagefile/2**20),
                    'system_available_mb':round(samples[-1]['available_physical_bytes']/2**20)}),flush=True)
                last_print=elapsed
            if elapsed>900:
                engine.control(run_id,'cancel','Offline integration exceeded its 15 minute accident bound')
                raise RuntimeError('Fixture exceeded fixed 15 minute bound; no model retry')
        r=engine.get(run_id)
        if r['status']!='completed': raise AssertionError(r.get('last_error') or r['status'])
        assert len(r['calls'])==6 and all(c['status']=='completed' and c['receipt']['verification']=='fixture_no_model' for c in r['calls'])
        records=r['research']['baseline']
        assert [v['response']['action'] for v in records]==SCHEDULE
        assert r['usage']['total_tokens']==0 and not r['final_opened'] and not r['confirmation_opened']
        assert records[-1]['submission_evidence_step']
        reports=[a for a in r['artifacts'] if a['name'].endswith('_execution_diagnostics.json')]
        assert len(reports)==4 # initial evidence plus three backtest slots; submit reuses prior evidence.
        for item in reports:
            path=output/run_id/item['name']
            assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
            content=json.loads(path.read_text(encoding='utf-8'))
            assert content['identity']['source_hash']==before['hash'] and content['identity']['datahash']==EXPECTED['development_datahash']
            assert content['identity']['split']=='development' and content['integrity']['status']=='reconciled'
        assert engine.source_manifest()==before
        save(output/'fixture_receipt.json',{'status':'passed','run_id':run_id,'source_hash':before['hash'],
            'fixture_calls':6,'research_model_calls':0,'tokens':r['usage']['total_tokens'],
            'elapsed_seconds':time.monotonic()-started,'diagnostic_artifacts':reports,
            'final_opened':False,'confirmation_opened':False,'execution_valid':False,'formal_target_success':False,
            'research_questions':'Does the frozen controller schedule, diagnostic artifact and tested-only submission path work? This fixture cannot assess Astra research quality.'})
        print(json.dumps({'status':'passed','root':str(output),'fixture_calls':6,'model_calls':0,'elapsed_seconds':round(time.monotonic()-started,2)}),flush=True)
    except Exception as exc:
        save(output/'failure.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'elapsed_seconds':time.monotonic()-started,
            'model_calls':0,'source_before':before['hash'],'source_after':engine.source_manifest()['hash']})
        raise
    finally:
        save(output/'memory_samples.json',samples)
        engine.close();lock.close()


if __name__=='__main__': main()

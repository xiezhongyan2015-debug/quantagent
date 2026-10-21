"""Run a finite number of remaining native-source batches, one process at a time.

Completed, pinned diagnostics are reused. A child failure stops this run and
preserves the original error output; no source conflict triggers raw admission.
"""
import argparse
import subprocess
from . import bounded_tail_pipeline as p

ROOT=p.BASE/'public_source_probes/additional_native_inventory_20261009/remaining_historical_native_matrix_full_scope_bounded'
STEPS=[
    ('acquire_remaining_historical_native_batch_bounded','receipt.json'),
    ('verify_remaining_historical_native_batch_daily_bounded','independent_daily_diagnostic.json'),
    ('query_remaining_historical_native_batch_coarse_bounded','coarse_reference_diagnostic.json'),
    ('verify_remaining_historical_native_batch_coarse_bounded','independent_coarse_verification.json'),
    ('qualify_remaining_native_batch_original_minutes_bounded','original_minute_and_cash_qualification.json'),
]

def completed(path):
    if not path.exists():return False
    j=p.json.loads(path.read_text())
    if path.name=='receipt.json':return j.get('status')=='complete_remaining_historical_native_batch_no_admission'
    if path.name=='coarse_reference_diagnostic.json':return j.get('status')=='complete_remaining_historical_native_batch_coarse_diagnostic_no_admission'
    return j.get('passed') is True

def verify_batch(dest):
    ac=p.json.loads((dest/'receipt.json').read_text());di=p.json.loads((dest/'independent_daily_diagnostic.json').read_text())
    co=p.json.loads((dest/'independent_coarse_verification.json').read_text());q=p.json.loads((dest/'original_minute_and_cash_qualification.json').read_text())
    assert di['passed'] and co['passed'] and q['passed']
    assert di['audited_acquisition_sha256']==q['source_acquisition_sha256']==p.sha(dest/'receipt.json')
    assert co['audited_coarse_diagnostic_sha256']==p.sha(dest/'coarse_reference_diagnostic.json')
    assert co['audited_native_daily_diagnostic_sha256']==p.sha(dest/'independent_daily_diagnostic.json')
    assert q['independent_coarse_proof_sha256']==p.sha(dest/'independent_coarse_verification.json')
    for r in ac['results']:
        assert p.identity(r['range_path'])==r['range_verified_identity'] and p.identity(r['selected_path'])==r['selected_verified_identity']
    assert not ac['pending_ranges'] and q['candidate_hours_admitted']==0
    return dict(batch=dest.name,stocks=len({r['code'] for r in ac['results']}),stockdays=len(ac['actual_requested_stockdays']),actual_missing_hours=ac['actual_missing_hours'],new_network_bytes=ac['new_network_bytes'],daily_positive_pass_hours=di['counts'].get('daily_positive_pass_hours',0),strict_coarse_pass_hours=co['counts'].get('strict_coarse_pass_hours',0),original_minute_price_conflict_hours=q['counts'].get('original_minute_price_conflict_hours',0),source_amount_conflict_hours=sum(bool(r['additional_cash_feasibility_failed_minute_indices']) for r in q['records']),candidate_hours_admitted=0)

def main(first,count):
    lock=p.locked('run_remaining_historical_native_batches')
    plan=p.json.loads((ROOT/'scope.json').read_text());total=(len(plan['stocks'])+7)//8
    assert 1<=first<=total and 1<=count<=4
    dest=ROOT/f'run_{first:03d}_{min(total,first+count-1):03d}';dest.mkdir(exist_ok=True)
    op=dest/'receipt.json';assert not op.exists()
    receipt=dict(status='running_serial_bounded_public_native_batches',started_utc=p.utc(),source_code_sha256=p.sha(__file__),full_scope_plan_sha256=p.sha(ROOT/'scope.json'),first_batch=first,max_batches=count,maximum_concurrent_child_processes=1,manager_pid=p.os.getpid(),steps=[],completed_batches=[],candidate_hours_admitted=0,training_started=False)
    def save():receipt.update(updated_utc=p.utc());p.atomic_json(op,receipt)
    save();child=None
    try:
        for number in range(first,min(total+1,first+count)):
            folder=ROOT/f'batch_{number:03d}'
            for module,filename in STEPS:
                artifact=folder/filename
                if completed(artifact):
                    receipt['steps'].append(dict(batch=number,module=module,status='completed_artifact_reused',path=str(artifact),sha256=p.sha(artifact)));save();continue
                if artifact.exists():raise RuntimeError(f'Incomplete retained step requires guarded recovery before restarting: {artifact}')
                print(p.json.dumps(dict(batch=number,stage=module,status='starting')),flush=True)
                child=subprocess.Popen([p.sys.executable,'-B','-m','gru_beta_neutral_20261008.'+module,'--batch',str(number)],cwd=p.ROOT)
                step=dict(batch=number,module=module,status='running',pid=child.pid,started_utc=p.utc());receipt['steps'].append(step);save()
                code=child.wait();child=None;step.update(finished_utc=p.utc(),exit_code=code,status='complete' if code==0 else 'failed_terminal');save()
                if code!=0:raise RuntimeError(f'Child step failed; retained diagnostic output preserved: {module} batch{number} exit{code}')
                assert completed(artifact);step.update(path=str(artifact),sha256=p.sha(artifact));save()
            verified=verify_batch(folder);receipt['completed_batches'].append(verified);save()
            print(p.json.dumps(dict(status='batch_independently_checked_without_raw_admission',**verified)),flush=True)
        receipt.update(status='complete_finite_serial_native_batch_diagnostics',finished_utc=p.utc())
    except BaseException as e:
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=8)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        receipt.update(status='failed_terminal_preserved',finished_utc=p.utc(),error_type=type(e).__name__,error=str(e),error_stack=p.traceback.format_exc())
        raise
    finally:save();lock.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--first-batch',type=int,required=True);parser.add_argument('--max-batches',type=int,default=1);args=parser.parse_args();main(args.first_batch,args.max_batches)

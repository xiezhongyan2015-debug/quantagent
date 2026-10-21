"""Finish current local evidence work and independently qualify staged minutes.

Wait for the confirmed live predecessor; continue only from terminal evidence.
No recurrence, no raw admission, no model training and no user interface calls.
"""
import importlib
from . import bounded_tail_pipeline as p
from .acquire_sep22_native_archive_bounded import DEST as ARCHIVE
from .stage_sep22_native_millisecond_hours_bounded import DEST as STAGE


def main():
    lock=p.locked('sep22_millisecond_staging_workflow')
    state_path=ARCHIVE/'minute_staging_workflow.json'
    state=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='waiting_for_current_native_evidence_worker',steps=[],raw_panel_modified=False,training_started=False,popups=False)
    p.atomic_json(state_path,state)
    try:
        with (p.OUT/'sep22_evidence_workflow.lock').open('a') as predecessor:
            p.fcntl.flock(predecessor,p.fcntl.LOCK_EX)
        path=ARCHIVE/'acquisition_and_extraction.json';r=p.json.loads(path.read_text())
        if r['status']!='complete_published_digest_crc_and_union_members_verified':
            state['status']='bounded_v2_resume_of_confirmed_terminal_incomplete_response';p.atomic_json(state_path,state)
            importlib.import_module('.resume_sep22_native_archive_bounded_v2',__package__).main()
        r=p.json.loads(path.read_text());assert r['status']=='complete_published_digest_crc_and_union_members_verified'
        for module,output in [
            ('verify_sep22_streamed_native_members',ARCHIVE/'independent_member_verification.json'),
            ('pair_sep22_trade_milliseconds_bounded',p.OUT/'millisecond_pairing_sep22/receipt.json'),
            ('verify_sep22_intrasecond_trade_order_bounded',p.OUT/'millisecond_pairing_sep22/intrasecond_order_verification.json'),
            ('stage_sep22_native_millisecond_hours_bounded',STAGE/'receipt.json'),
            ('verify_sep22_native_millisecond_staging_bounded',STAGE/'independent_verification.json')]:
            state['status']=module;p.atomic_json(state_path,state)
            if not output.exists():importlib.import_module('.'+module,__package__).main()
            obj=p.json.loads(output.read_text())
            assert obj['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008'/(module+'.py'))
            if module=='verify_sep22_streamed_native_members':assert obj['passed'] and obj['audited_receipt_sha256']==p.sha(path)
            if module=='pair_sep22_trade_milliseconds_bounded':assert obj['all_rows_price_quantity_and_floor_clock_exact'] and obj['stockdays_checked']==624
            if module=='verify_sep22_intrasecond_trade_order_bounded':assert obj['minute_open_changed_by_intrasecond_order']==obj['minute_close_changed_by_intrasecond_order']==obj['intrasecond_event_inversions']==0
            if module=='verify_sep22_native_millisecond_staging_bounded':assert obj['passed'] and obj['audited_receipt_sha256']==p.sha(STAGE/'receipt.json')
            state['steps'].append(dict(module=module,path=str(output),sha256=p.sha(output)));p.atomic_json(state_path,state)
        state.update(status='complete307actual_ms_hours_staged_and_independently_verified_pending_raw128_merge',finished_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(state_path,state)
    except Exception as exc:
        state.update(status='failure_retained_before_admission',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc(),finished_utc=p.utc());p.atomic_json(state_path,state);raise
    finally:lock.close()


if __name__=='__main__':main()

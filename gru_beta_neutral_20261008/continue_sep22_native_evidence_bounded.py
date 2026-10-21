"""Finish this active acquisition and run dependent bounded evidence checks.

This is a local worker for the current goal, not a recurring automation. Each
downstream step waits for its actual dependencies. No raw admission or training.
"""
import importlib
from . import bounded_tail_pipeline as p
from .acquire_sep22_native_archive_bounded import DEST


def main():
    workflow_lock=p.locked('sep22_evidence_workflow')
    state_path=DEST/'dependent_evidence_workflow.json'
    state=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='waiting_for_current_download',
        raw_candidate_modified=False,training_started=False,steps=[],popups=False)
    p.atomic_json(state_path,state)
    try:
        # The active download holds this lock until extraction or a retained
        # bounded failure. Waiting here avoids simultaneously running transfers.
        with (p.OUT/'sep22_native_archive.lock').open('a') as archive_lock:
            p.fcntl.flock(archive_lock,p.fcntl.LOCK_EX)
        receipt=DEST/'acquisition_and_extraction.json'
        r=p.json.loads(receipt.read_text())
        if r['status']!='complete_published_digest_crc_and_union_members_verified':
            state['status']='resuming_retained_archive_once';p.atomic_json(state_path,state)
            importlib.import_module('.acquire_sep22_native_archive_bounded',__package__).main()
        r=p.json.loads(receipt.read_text());assert r['status']=='complete_published_digest_crc_and_union_members_verified'
        for module,output in [
            ('verify_sep22_streamed_native_members',DEST/'independent_member_verification.json'),
            ('pair_sep22_trade_milliseconds_bounded',p.OUT/'millisecond_pairing_sep22/receipt.json'),
            ('verify_sep22_intrasecond_trade_order_bounded',p.OUT/'millisecond_pairing_sep22/intrasecond_order_verification.json')]:
            state['status']=module;p.atomic_json(state_path,state)
            if not output.exists():importlib.import_module('.'+module,__package__).main()
            obj=p.json.loads(output.read_text())
            if module=='verify_sep22_streamed_native_members':assert obj['passed'] and obj['audited_receipt_sha256']==p.sha(receipt)
            if module=='pair_sep22_trade_milliseconds_bounded':assert obj['all_rows_price_quantity_and_floor_clock_exact'] and obj['stockdays_checked']==624
            if module=='verify_sep22_intrasecond_trade_order_bounded':assert obj['minute_open_changed_by_intrasecond_order']==obj['minute_close_changed_by_intrasecond_order']==obj['intrasecond_event_inversions']==0
            state['steps'].append(dict(module=module,path=str(output),sha256=p.sha(output)));p.atomic_json(state_path,state)
        state.update(status='complete_native_evidence_pending_new_hour_qualification',finished_utc=p.utc(),
            archive_bytes=r['received_total_bytes'],source_pairs=r['complete_event_snapshot_pairs'],
            peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(state_path,state)
        print(p.json.dumps(dict(status=state['status'],raw_candidate_modified=False,steps=len(state['steps']))),flush=True)
    except Exception as exc:
        state.update(status='failure_retained_before_admission',error_type=type(exc).__name__,error=str(exc),
            traceback=p.traceback.format_exc(),finished_utc=p.utc());p.atomic_json(state_path,state);raise
    finally:workflow_lock.close()


if __name__=='__main__':main()

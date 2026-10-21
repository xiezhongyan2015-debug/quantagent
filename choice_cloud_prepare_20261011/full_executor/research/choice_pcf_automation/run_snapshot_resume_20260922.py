"""Resume the exact reconciled journal after repairing the snapshot lag timer."""
import copy
import datetime as dt
import sys
from decimal import Decimal
import run_cash_continuation_20260922 as previous
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import read_json, digest, now_cn, lock

session=previous.session
SOURCE=previous.DEST
DEST=SOURCE.parent/'snapshot_resume_20260922'


def deadline(now,immediate=False):
    a=read_json(DEST/'authorization.json')
    start=dt.datetime.fromisoformat(a['authorized_at']);end=dt.datetime.fromisoformat(a['expires_at'])
    if (not immediate or now.date().isoformat()!=session.DAY or start.tzinfo is None or end.tzinfo is None
            or not dt.timedelta(0)<end-start<=dt.timedelta(minutes=15) or not start<=now<=end
            or now.time()>=dt.time(14,50) or a.get('simulation_only') is not True
            or a.get('user_instruction')!='账户数据快照超过 90 秒未能核对一致  是什么意思？解决下'
            or a.get('excluded_cash_cny')!='395.66'):
        raise TradeError('Snapshot repair authorization invalid/expired')
    return end


def inherited_state(require_fresh=True):
    previous.validate_source()
    original=previous.original_initial_state()
    approval=read_json(DEST/'authorization.json')
    for name,expected in approval['source_sha256'].items():
        if digest(read_json(SOURCE/name))!=expected:raise TradeError('Reconciled source journal changed')
    state=read_json(SOURCE/'private_state.json');result=read_json(SOURCE/'result_redacted.json')
    proof=read_json(SOURCE/'independent_readonly_audit.json')
    if (result['status']!='stopped_review_required' or result['reason']!='Snapshots failed to converge within 90 seconds'
            or result['unresolved_intents'] or result['pending'] or Decimal(result['frozen_cny'])
            or proof['issues'] or proof['pending'] or Decimal(proof['frozen_cny']) or proof['reconciliation_passes']!=2
            or proof['submitted']!=len(state['attempts']) or result['submitted']!=len(state['attempts'])
            or any(not x.get('oid') or not x.get('terminal') or x['state'] not in ('4','7','8','9') for x in state['attempts'])):
        raise TradeError('Source must have all exact orders reconciled and terminal')
    if require_fresh and not 0<=(now_cn()-dt.datetime.fromisoformat(proof['checked_at'])).total_seconds()<=600:
        raise TradeError('Fresh independent two-pass audit required')
    for key in ('day','pcf_sha256','baseline','historical','targets','carried_reserve','baseline_security_reserve',
                'capital_limit','max_security_value','max_order_value','target_fraction','submission_spacing_seconds'):
        if state[key]!=original[key]:raise TradeError('Original plan, ledger baseline or limits changed')
    if state['cash_quarantine']['amount']!='395.66':raise TradeError('Cash exclusion changed')
    if (SOURCE/'STOP').exists():raise TradeError('Previous STOP active')
    state['snapshot_resume']=dict(source=SOURCE.name,inherited_attempts=len(state['attempts']),
                                 source_audit_at=proof['checked_at'],repair='Reset snapshot lag on every successful account audit')
    return state


class ResumeWatcher(previous.VerifiedWatcher):
    def audit(self):
        result=super().audit()
        if not getattr(self,'resume_verified',False):
            self.sleep(5)
            if self.path.with_name('STOP').exists() or self.clock().date().isoformat()!=session.DAY:
                raise TradeError('Resume preflight stopped/expired')
            result=super().audit()
            self.state['snapshot_resume']['preflight_verified_at']=self.clock().isoformat()
            self.resume_verified=True
            self.flush()
        return result


def report(watcher,status,reason=None):
    r=previous.report(watcher,status,reason)
    r.update(continuation_of=SOURCE.name,snapshot_resume=watcher.state['snapshot_resume'],
             newly_submitted_this_run=len(watcher.state['attempts'])-watcher.state['snapshot_resume']['inherited_attempts'],
             last_snapshot_mismatch=watcher.state.get('last_snapshot_mismatch'))
    return r


def main():
    if sys.argv[1:] not in (['--execute-approved-simulation'],['--audit-readonly']):raise TradeError('Explicit mode required')
    execute=sys.argv[1]=='--execute-approved-simulation'
    if execute:
        deadline(now_cn(),True)
        if (DEST/'launch_attempt.json').exists():raise TradeError('Resume already attempted; never replay')
    with lock(previous.SOURCE/'watch.lock'),lock(SOURCE/'watch.lock'):
        state=inherited_state(require_fresh=execute)
        session.DEST=DEST
        session.initial_state=lambda:copy.deepcopy(state)
        session.Adapter=previous.QuarantinedAdapter
        session.Watcher=ResumeWatcher
        session.report=report
        session.launch_deadline=deadline
        if execute:session.execute(immediate=True)
        else:session.readonly(False)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__,flush=True)
        raise SystemExit(1)

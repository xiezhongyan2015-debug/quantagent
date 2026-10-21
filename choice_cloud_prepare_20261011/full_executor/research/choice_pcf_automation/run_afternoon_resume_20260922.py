"""Resume the audited morning simulation journal under fresh user authorization."""
import copy
import datetime as dt
import sys
from contextlib import ExitStack
from decimal import Decimal

import run_snapshot_resume_20260922 as previous
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import digest, lock, now_cn, read_json

session = previous.session
SOURCE = previous.DEST
DEST = SOURCE.parent / 'afternoon_resume_20260922'


def deadline(now, immediate=False):
    approval = read_json(DEST / 'authorization.json')
    start = dt.datetime.fromisoformat(approval['authorized_at'])
    end = dt.datetime.fromisoformat(approval['expires_at'])
    if (not immediate or now.date().isoformat() != session.DAY
            or start.tzinfo is None or end.tzinfo is None
            or not dt.timedelta(0) < end - start <= dt.timedelta(minutes=15)
            or not start <= now <= end or not dt.time(13, 5) <= now.time() < dt.time(14, 50)
            or approval.get('simulation_only') is not True
            or approval.get('user_instruction') != '恢复执行'
            or approval.get('excluded_cash_cny') != '395.66'):
        raise TradeError('Afternoon authorization invalid or expired')
    return end


def inherited_state():
    original = previous.inherited_state(require_fresh=False)
    approval = read_json(DEST / 'authorization.json')
    for name, expected in approval['source_sha256'].items():
        if digest(read_json(SOURCE / name)) != expected:
            raise TradeError('Audited morning evidence changed')
    state = read_json(SOURCE / 'private_state.json')
    proof = read_json(SOURCE / 'independent_readonly_audit.json')
    if (proof['issues'] or proof['pending'] or Decimal(proof['frozen_cny'])
            or proof['reconciliation_passes'] != 2
            or proof['submitted'] != len(state['attempts'])
            or not 0 <= (now_cn() - dt.datetime.fromisoformat(proof['checked_at'])).total_seconds() <= 600
            or any(not a.get('oid') or not a.get('terminal') or a['state'] not in ('4', '7', '8', '9')
                   for a in state['attempts'])):
        raise TradeError('Fresh clean two-pass audit of every inherited order required')
    for key in ('day', 'pcf_sha256', 'baseline', 'historical', 'targets', 'carried_reserve',
                'baseline_security_reserve', 'capital_limit', 'max_security_value', 'max_order_value',
                'target_fraction', 'submission_spacing_seconds', 'cash_quarantine', 'padding_ticks',
                'max_attempts_per_code', 'blocked_codes'):
        if state[key] != original[key]:
            raise TradeError('Original plan or limits changed: ' + key)
    prior = state['attempts'][:len(original['attempts'])]
    if prior != original['attempts']:
        raise TradeError('Inherited order history changed')
    for directory in (previous.previous.SOURCE, previous.SOURCE, SOURCE):
        if (directory / 'STOP').exists():
            raise TradeError('Prior STOP is active')
    state['afternoon_resume'] = dict(source=SOURCE.name, inherited_attempts=len(state['attempts']),
                                   source_audit_at=proof['checked_at'])
    return state


def report(watcher, status, reason=None):
    result = previous.report(watcher, status, reason)
    result.update(continuation_of=SOURCE.name, afternoon_resume=watcher.state['afternoon_resume'],
                  newly_submitted_this_run=len(watcher.state['attempts']) - watcher.state['afternoon_resume']['inherited_attempts'])
    return result


def main():
    if sys.argv[1:] == ['--audit-readonly']:
        session.DEST = DEST
        session.Adapter = previous.previous.QuarantinedAdapter
        return session.readonly(False)
    if sys.argv[1:] not in (['--validate-local'], ['--execute-approved-simulation']):
        raise TradeError('Explicit mode required')
    deadline(now_cn(), True)
    state = inherited_state()
    if sys.argv[1:] == ['--validate-local']:
        print('Validated exact inherited journal, fresh audit and unchanged simulation limits')
        return
    if (DEST / 'launch_attempt.json').exists():
        raise TradeError('Afternoon execution already attempted; never replay')
    with ExitStack() as locks:
        for directory in (previous.previous.SOURCE, previous.SOURCE, SOURCE):
            locks.enter_context(lock(directory / 'watch.lock'))
        session.DEST = DEST
        session.initial_state = lambda: copy.deepcopy(state)
        session.Adapter = previous.previous.QuarantinedAdapter
        session.Watcher = previous.ResumeWatcher
        session.report = report
        session.launch_deadline = deadline
        session.execute(immediate=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, TradeError) else type(error).__name__, flush=True)
        raise SystemExit(1)

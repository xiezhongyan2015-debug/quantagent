"""One separately authorized continuation; quarantine CNY395.66, preserve prior ledger.

Configure the existing session runner in this dedicated process. Its order,
authentication, completion and close-time controls remain in force.
"""
import copy
import datetime as dt
import json
import sys
from decimal import Decimal

import run_session_20260922 as session
from watch_engine import Watcher, save
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import read_json, digest, now_cn, lock

SOURCE = session.DEST
DEST = SOURCE.parent / 'cash_continuation_20260922'
EXCLUDED = Decimal('395.66')
original_initial_state = session.initial_state
original_report = session.report


def quarantine(raw):
    result = copy.deepcopy(raw)
    cash = Decimal(str(result['balance']['availBalance']))
    if not cash.is_finite() or cash < EXCLUDED:
        raise TradeError('Insufficient account cash for quarantined adjustment')
    result['balance']['availBalance'] = str(cash - EXCLUDED)
    return result


class QuarantinedAdapter(session.Adapter):
    def read(self):
        return quarantine(super().read())


class VerifiedWatcher(Watcher):
    def audit(self):
        result = super().audit()
        if not self.state.get('continuation_preflight_verified_at'):
            if self.state['attempts']:
                raise TradeError('Continuation must verify before any new order')
            self.sleep(5)
            if self.path.with_name('STOP').exists() or self.clock().date().isoformat() != session.DAY:
                raise TradeError('Continuation preflight stopped/expired')
            result = super().audit()
            self.state['continuation_preflight_verified_at'] = self.clock().isoformat()
        self.state['last_audit']['account_cash_before_quarantine'] = str(Decimal(result['cash']) + EXCLUDED)
        self.state['last_audit']['excluded_cash_cny'] = str(EXCLUDED)
        self.flush()
        return result


def report(watcher, status, reason=None):
    result = original_report(watcher, status, reason)
    result.update(excluded_cash_cny=str(EXCLUDED), cash_adjustment_status='source_unverified_user_authorized_exclusion',
                  continuation_of=str(SOURCE.name), continuation_preflight_verified_at=watcher.state.get('continuation_preflight_verified_at'),
                  spendable_cash_cny=watcher.state.get('last_audit', {}).get('cash'),
                  account_cash_cny=watcher.state.get('last_audit', {}).get('account_cash_before_quarantine'))
    return result


def approval_deadline(now, immediate=False):
    approval = read_json(DEST / 'authorization.json')
    start = dt.datetime.fromisoformat(approval['authorized_at'])
    end = dt.datetime.fromisoformat(approval['expires_at'])
    if (not immediate or now.date().isoformat() != session.DAY or start.tzinfo is None or end.tzinfo is None
            or not dt.timedelta(0) < end-start <= dt.timedelta(minutes=15) or not start <= now <= end
            or now.time() >= dt.time(14,50) or approval.get('execution_date') != session.DAY
            or approval.get('user_instruction') != '允许，尽快开始交易'
            or approval.get('excluded_cash_cny') != str(EXCLUDED) or approval.get('simulation_only') is not True):
        raise TradeError('Continuation authorization invalid/expired')
    return end


def validate_source():
    approval = read_json(DEST / 'authorization.json')
    for name, expected in approval['source_sha256'].items():
        if digest(read_json(SOURCE / name)) != expected:
            raise TradeError('Original stopped-session evidence changed')
    old = read_json(SOURCE / 'private_state.json')
    result = read_json(SOURCE / 'result_redacted.json')
    trace = [json.loads(line) for line in (SOURCE / 'http_trace_redacted.jsonl').read_text(encoding='utf-8').splitlines() if line]
    if (old['attempts'] or result['submitted'] != 0 or result['unresolved_intents'] != 0
            or result['status'] != 'stopped_review_required' or not result['trading_writes_suspended']
            or result['reason'] != 'Cash/deal/freeze snapshots disagree' or not trace
            or any(row['method'] != 'GET' for row in trace)):
        raise TradeError('Previous execution is not a verified zero-write failure')
    if (SOURCE / 'STOP').exists():
        raise TradeError('Original STOP remains active')


def configure():
    # Read and validate the original sealed PCF, baseline and limits before
    # redirecting runtime output. Never edit the original seal or cash baseline.
    validate_source()
    state = original_initial_state()
    state['cash_quarantine'] = dict(amount=str(EXCLUDED), status='source_unverified',
                                    authorization=str(DEST / 'authorization.json'), source_session=SOURCE.name)
    session.DEST = DEST
    session.initial_state = lambda: copy.deepcopy(state)
    session.Adapter = QuarantinedAdapter
    session.Watcher = VerifiedWatcher
    session.report = report
    session.launch_deadline = approval_deadline


def main():
    if sys.argv[1:] not in (['--execute-approved-simulation'], ['--audit-readonly']):
        raise TradeError('Explicit simulation execution or read-only audit required')
    if sys.argv[1] == '--execute-approved-simulation':
        approval_deadline(now_cn(), True)
        if (DEST / 'launch_attempt.json').exists():
            raise TradeError('Continuation already attempted; never replay')
    with lock(SOURCE / 'watch.lock'):
        configure()
        if sys.argv[1] == '--audit-readonly':
            session.readonly(False)
        else:
            session.execute(immediate=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, TradeError) else type(error).__name__, flush=True)
        raise SystemExit(1)

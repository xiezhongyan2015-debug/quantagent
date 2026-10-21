"""Read-only per-security stall detection. Never queries an account or trades.

Called by the existing one-minute alert dispatcher; failed notifications stay
in its durable outbox. All writes are watchdog state or notification records.
"""
import copy
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

CN = dt.timezone(dt.timedelta(hours=8))
LIMIT_SECONDS = 600
REMINDER_SECONDS = 7200   # 2026-10-14: was 1800; one alert at 10 min, then at most every 2 trading hours
STALE_ALERT_SECONDS = 600   # 2026-10-14: a single slow audit is not an alert; QuantMonitor covers a stuck executor
FRESH_SECONDS = 120
BASE = Path('C:/ChoiceBridge/executor/research/choice_pcf_automation')
CALENDAR = Path('C:/ChoiceBridge/quant/current/实盘日更/config/calendar_2026.json')
ACTIVE = {'watching', 'formal_rebalancing', 'position_topup_planned', 'maintaining_position_floor'}
FINISHED = {'stopped_review_required', 'closed', 'completed_targets', 'completed_pilot', 'stopped', 'handed_off'}


def moment(text):
    value = dt.datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError('Watchdog timestamps must include timezone')
    return value.astimezone(CN)


def trading_seconds(start, end):
    if start.date() != end.date() or end < start:
        return 0
    seconds = 0
    for lo, hi in [(dt.time(9, 30), dt.time(11, 30)), (dt.time(13), dt.time(15))]:
        a = dt.datetime.combine(start.date(), lo, CN)
        b = dt.datetime.combine(start.date(), hi, CN)
        seconds += max(0, (min(end, b) - max(start, a)).total_seconds())
    return seconds


def in_session(now):
    return dt.time(9, 30) <= now.time() < dt.time(11, 30) or dt.time(13) <= now.time() < dt.time(15)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def candidates(ledger, status):
    audit = ledger['last_audit']
    held = audit['positions']
    pending = {x['code'] for x in audit.get('pending', {}).values()}
    attempts = {}
    for item in ledger.get('attempts', []):
        attempts[item['order']['code']] = item
    result = {}
    # Derive actual gaps from the dated ledger; don't trust an old deferred list.
    for code, target in ledger['targets'].items():
        quantity, owned = target['quantity'], held.get(code, 0)
        if type(quantity) is not int or type(owned) is not int or min(quantity, owned) < 0:
            raise ValueError('Invalid integer holdings or target')
        delta = quantity - owned
        if not delta or code in pending:
            continue
        reason = status.get('deferred', {}).get(code) or ledger.get('deferred', {}).get(code)
        if not reason and delta < 0:
            available = audit.get('sellable', {}).get(code, 0)
            if available < abs(delta) or (abs(delta) % 100 and available % 100 != owned % 100):
                reason = 'T+1或零股可卖数量受限，等待原执行器规则放行'
        last = attempts.get(code, {})
        if not reason and last:
            if last.get('state') == '8' and last.get('terminal') and last.get('filled') == 0:
                reason = '零成交撤单后尚未取得执行进展，等待账户核验及原风控放行'
            elif last.get('state') == '9':
                reason = '原委托被拒，按原规则不自动重报'
            elif last.get('state') in ('7', 'UNKNOWN', 'SENDING') or not last.get('oid'):
                reason = '原委托部分成交或回执待核验，按原规则等待'
        if not reason:
            continue  # ordinary untouched orders are not classified as blocked
        mark = Decimal(str(audit.get('position_mark_values', {}).get(code, '0')))
        price = mark / owned if owned and mark > 0 else Decimal(str(target['reference_price']))
        if not price.is_finite() or price <= 0:
            raise ValueError('Missing positive price for stalled security')
        result[code] = dict(code=code, target=quantity, gap=delta, reason=str(reason)[:180],
                            estimated_cny=str((abs(delta)*price).quantize(Decimal('.01'))))
    return result


def observe(previous, ledger, status, now):
    """Pure state transition. No original ledger/status mutation or I/O."""
    day = now.date().isoformat()
    old = copy.deepcopy(previous) if previous.get('day') == day else dict(day=day, entries={})
    if ledger.get('day') != day or status.get('execution_date') != day:
        raise ValueError('Execution day differs')
    for stamp in [status['at'], ledger['last_audit']['checked_at']]:
        if not 0 <= (now-moment(stamp)).total_seconds() <= FRESH_SECONDS:
            raise ValueError('Executor or account audit file is stale')
    if not in_session(now) or status.get('status') not in ACTIVE:
        # Preserve observations, but don't count global pauses as blocked time.
        for entry in old.get('entries', {}).values():
            entry['last_seen'] = now.isoformat()
            entry['countable'] = False
        old.update(at=now.isoformat(), status=status.get('status'))
        return old, []
    new = candidates(ledger, status)
    entries, notices = {}, []
    for code, row in new.items():
        before = old.get('entries', {}).get(code)
        same = before and before['target'] == row['target'] and before['gap']*row['gap'] > 0
        progress = same and abs(row['gap']) < abs(before['gap'])
        if not same or progress:
            if progress and before.get('alerted'):
                notices.append(dict(key=f"progress:{day}:{code}:{before['first_at']}:{row['gap']}",
                    text=f"ℹ️ {day} {code} 已有执行进展，剩余差额{row['gap']}股；尚未确认全部完成。"))
            entry = dict(row, first_at=now.isoformat(), seconds=0, alerted=False, last_alert_seconds=0)
        else:
            entry = dict(before, **row)
            elapsed = (now-moment(before['last_seen'])).total_seconds()
            # Don't count a polling outage or lunch as known continuous failure.
            if before.get('countable') and 0 <= elapsed <= FRESH_SECONDS:
                entry['seconds'] += trading_seconds(moment(before['last_seen']), now)
        entry.update(last_seen=now.isoformat(), countable=True)
        if (entry['seconds'] >= LIMIT_SECONDS and
                (not entry['alerted'] or entry['seconds']-entry['last_alert_seconds'] >= REMINDER_SECONDS)):
            number = 1 if not entry['alerted'] else int(entry.get('notice_number', 1))+1
            notices.append(dict(key=f"stall:{day}:{code}:{entry['first_at']}:{number}",
                text=f"⚠️ {day} {code} 单股持续受限约{int(entry['seconds']//60)}个交易分钟，"
                     f"剩余{'买入' if row['gap']>0 else '卖出'}{abs(row['gap'])}股，估值约{row['estimated_cny']}元。"
                     f"最近记录原因：{row['reason']}。保留原策略与风控；本告警不暂停其他合规交易。"))
            entry.update(alerted=True, last_alert_seconds=entry['seconds'], notice_number=number)
        entries[code] = entry
    for code, before in old.get('entries', {}).items():
        if code not in new and before.get('alerted'):
            owned = ledger['last_audit']['positions'].get(code, 0)
            target = ledger['targets'].get(code, {}).get('quantity', owned)
            text = '目标差额已完成' if target == owned else '已离开持续受限状态，仍需核验剩余委托或持仓'
            notices.append(dict(key=f"cleared:{day}:{code}:{before['first_at']}",
                                text=f'ℹ️ {day} {code} {text}。'))
    return dict(schema=1, day=day, at=now.isoformat(), status=status['status'], entries=entries), notices


def run_once(now=None):
    import corporate_action_runtime as ca
    from console_runtime import active_session, latest_session, mutex
    now = now or ca.clocks()
    day = now.date().isoformat()
    if day not in read(CALENDAR) or not in_session(now):
        return 0
    folder = ca.root() / 'symbol_watchdog'
    path = folder / 'state.json'
    try:
        with mutex(folder / 'watchdog.lock'):
            run = active_session() or latest_session()
            if run is None:
                return 0
            ledger = read(run/'private_state.json')
            status = read(run/'status_redacted.json')
            if ledger.get('day') != day:
                return 0
            if status.get('status') in FINISHED:
                # The executor has ended for the day (QuantMonitor and the executor itself report stops); nothing to watch.
                ca.save(folder/'health.json', dict(at=now.isoformat(), day=day, ok=True, run=str(run), note='executor finished: ' + str(status.get('status'))))
                return 0
            if not (ledger.get('last_audit') or {}).get('checked_at'):
                # Session start: the ledger exists before its first audit (2026-10-14 09:30 KeyError).
                ca.save(folder/'health.json', dict(at=now.isoformat(), day=day, ok=True, run=str(run), note='waiting for first audit'))
                return 0
            # Active tomorrow's appointment must not be interpreted as today's stall.
            previous = read(path) if path.exists() else dict(day=day, entries={})
            updated, notices = observe(previous, ledger, status, now)
            # Persist only after every notice is durable. Retried batch keys deduplicate.
            for offset in range(0, len(notices), 8):
                group = notices[offset:offset+8]
                key = hashlib.sha256(json.dumps([x['key'] for x in group]).encode()).hexdigest()
                ca.queue_notice('symbol_stall', '\n\n'.join(x['text'] for x in group), 'symbols:'+key)
            ca.save(path, updated)
            ca.save(folder/'health.json', dict(at=now.isoformat(), day=day, ok=True,
                                             run=str(run), tracked=len(updated['entries']), notices=len(notices)))
            return len(notices)
    except Exception as error:
        # A watchdog problem never raises into execution or blocks notification dispatch.
        reason = str(error)[:180] if isinstance(error, ValueError) else type(error).__name__
        if reason == 'Executor or account audit file is stale':
            try: last = read(folder/'health.json')
            except (OSError, ValueError): last = {}
            since = last.get('stale_since') if last.get('day') == day and last.get('stale_since') else now.isoformat()
            ca.save(folder/'health.json', dict(at=now.isoformat(), day=day, ok=False, reason=reason, stale_since=since))
            if trading_seconds(moment(since), now) < STALE_ALERT_SECONDS:
                return 0
        ca.queue_notice('symbol_watchdog_failed',
            f'⚠️ {day} 单股持续受限监控暂不可用：{reason}。请检查监控日志；本监控未更改或暂停交易。',
            f'symbol-watchdog-health:{day}:{reason}')
        if reason != 'Executor or account audit file is stale':
            ca.save(folder/'health.json', dict(at=now.isoformat(), day=day, ok=False, reason=reason))
        return 1

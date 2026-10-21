"""Read-only, unambiguous recovery of a lost simulation submission receipt.

Absence is never proof of rejection, and this module never submits an order.
"""
import copy
import datetime as dt
from console_core import dec, reconcile, intent_fingerprint, isolated_attempts, expired_receipts
from quanta_agents.factor_lab_a.pcf_automation import digest
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending, number
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import CN


class SubmissionReceiptPending(TradeError):
    pass


class ReceiptAbsent(FillSnapshotPending):
    pass


def unresolved(state):
    return [a for a in state['attempts'] if not a.get('oid')]


def submission_window(attempt, trace=()):
    start=dt.datetime.fromisoformat(attempt['submitted_at'])
    if start.tzinfo is None:raise TradeError('提交时间缺少时区，不能自动关联回执')
    end=attempt.get('submit_failed_at')
    if end:
        end=dt.datetime.fromisoformat(end)
    else:
        # Older journals did not save the timeout's completion time. A subsequent
        # serialized GET begins only after the failed POST has returned.
        matches=[i for i,e in enumerate(trace) if e.get('method')=='POST' and e.get('endpoint')=='AguTrade/SpoOrder'
                 and abs((dt.datetime.fromisoformat(e['at'])-start).total_seconds())<=2]
        if len(matches)!=1 or matches[0]+1>=len(trace):
            raise TradeError('缺少完整提交时间证据，不能自动关联回执')
        following=trace[matches[0]+1]
        if following.get('method')!='GET':raise TradeError('提交后日志不连续，不能自动关联回执')
        end=dt.datetime.fromisoformat(following['at'])
    if end.tzinfo is None or not 0<=(end-start).total_seconds()<=60:
        raise TradeError('提交时间窗口异常，不能自动关联回执')
    return start,end


def candidate(raw,state,config,trace=()):
    missing=unresolved(state)
    if len(missing)!=1:raise TradeError('存在多笔结果不明委托，不能自动关联回执')
    intent=missing[0]
    if intent.get('state') not in ('SENDING','UNKNOWN'):raise TradeError('未知的未确认委托状态')
    start,end=submission_window(intent,trace)
    if start.astimezone(CN).date().isoformat()!=state['day']:raise TradeError('未确认委托日期不一致')
    known=set(state.get('historical',{}))|set(state.get('prior',{}))|{a['oid'] for a in state['attempts'] if a.get('oid')}
    new=[row for row in raw['orders'] if str(row['orderId']) not in known
         and str(row['orderDate']).replace('-','')>=state['day'].replace('-','')]
    if not new:
        raise ReceiptAbsent(f"尚未查到 {intent['order']['code']} 的服务器委托编号；不把无记录当成失败，不自动重发")
    if len(new)!=1:raise TradeError('发现多笔未关联委托，禁止按相似订单猜测回执')
    row=new[0];order=intent['order'];oid=str(row['orderId'])
    if not oid.strip():raise TradeError('服务器返回空委托编号')
    if (str(row['orderDate'])!=state['day'] or normalize_code(str(row['secCode']))!=order['code']
            or number(row['drt'],True)!=order.get('side',1) or number(row['orderCount'],True)!=order['quantity']
            or dec(row['orderPrice'])!=dec(order['price']) or number(row['orderType'],True)!=1):
        raise TradeError('未关联委托的日期、证券、方向、数量、限价或类型不符，不能自动接回')
    try:server=dt.datetime.fromisoformat(str(row['orderDate'])+'T'+str(row['orderTime'])).replace(tzinfo=CN)
    except (TypeError,ValueError):raise TradeError('服务器委托时间格式异常，不能自动接回') from None
    # Server times have one-second precision; observed normal receipts lag the
    # local submit clock by 1–2 seconds. Never match outside this narrow window.
    if not start-dt.timedelta(seconds=3)<=server<=end+dt.timedelta(seconds=3):
        raise TradeError('服务器委托时间不在本次提交窗口内，不能自动接回')
    proposed=copy.deepcopy(state)
    target=unresolved(proposed)[0];target.update(oid=oid,state='ACK')
    audit=reconcile(raw,proposed,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'])
    for attempt in proposed['attempts']:
        attempt.update(audit['states'][attempt['oid']])
    return proposed,audit,oid


def isolation_candidate(raw,state,config,now,trace=()):
    """Verify everything except one absent receipt; do not declare it failed."""
    if not config.get('isolate_unknown_orders'):raise TradeError('未启用未知委托隔离')
    missing=unresolved(state)
    if len(missing)!=1:raise TradeError('仅允许隔离一笔未确认买入委托')
    intent=missing[0];start,end=submission_window(intent,trace)
    if now.astimezone(CN).date().isoformat()!=state['day'] or (now-start).total_seconds()<90:
        raise FillSnapshotPending('提交未满90秒或已跨日，暂不隔离未确认委托')
    if intent['order'].get('side',1)!=1:raise TradeError('未确认卖出委托不能自动隔离后续跑')
    try:candidate(raw,state,config,trace)
    except ReceiptAbsent:pass
    else:raise TradeError('服务器已有候选委托，必须核对回执，不能按缺失隔离')
    known=copy.deepcopy(state);known['attempts']=[a for a in known['attempts'] if a.get('oid')]
    audit=reconcile(raw,known,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'])
    if audit['pending'] or dec(audit['frozen']):raise FillSnapshotPending('其他已知委托尚未结清，暂不隔离未确认委托')
    if dec(audit['cash'])<dec(intent['reserve']):raise TradeError('可用资金不足以完整保留未确认委托预算')
    if audit['cash_drift_requires_confirmation']:raise FillSnapshotPending('现金差异尚未确认，不能同时隔离未确认委托')
    stable={k:audit[k] for k in ('positions','states','cash','daily_paid','frozen','cash_drift_cny')}
    return audit,stable


def isolate(state,audit,first_hash,second_hash,at):
    proposed=copy.deepcopy(state);intent=unresolved(proposed)[0]
    intent['isolation']=dict(day=state['day'],at=at,verified_snapshots=2,intent_sha256=intent_fingerprint(intent),
        reserve_cny=intent['reserve'],first_snapshot_sha256=first_hash,second_snapshot_sha256=second_hash,
        meaning='Unconfirmed, not failed. Retain full reserve and skip this security for the rest of the day.')
    for attempt in proposed['attempts']:
        if attempt.get('oid'):attempt.update(audit['states'][attempt['oid']])
    proposed['blocked_receipt_symbols']=sorted(set(proposed.get('blocked_receipt_symbols',[]))|{intent['order']['code']})
    return proposed


def expire_previous_day(state,config,observations,now,trace=()):
    """Close one expired DAY intent only after two fresh full account reads.

    This is a local resolution, not proof that submission failed. No order is
    resubmitted. Future audits must keep querying history for a delayed receipt.
    """
    if not config.get('isolate_unknown_orders'):raise TradeError('未启用未知委托隔离')
    missing=unresolved(state)
    if len(missing)!=1 or len(isolated_attempts(state))!=1:
        raise TradeError('跨日自动核验仅处理一笔已有完整隔离证据的买入委托')
    intent=missing[0];start,_=submission_window(intent,trace)
    if (now.tzinfo is None or now.astimezone(CN).date().isoformat()<=state['day']
            or start.astimezone(CN).date().isoformat()!=state['day']):
        raise TradeError('尚未跨过原委托交易日，不能按日内申报到期处理')
    if intent['order']['code'][:4] not in ('SH60','SH68','SZ00','SZ30'):
        raise TradeError('仅支持普通沪深股票日内限价委托的跨日核验')
    if len(observations)!=2:raise TradeError('跨日解除预约需要两轮完整账户核验')
    proposed=copy.deepcopy(state)
    proposed['attempts']=[a for a in proposed['attempts'] if a.get('oid')]
    checks=[];audits=[];proofs=[];finished=[]
    for observation in observations:
        raw=observation['raw'];begin=dt.datetime.fromisoformat(observation['started_at'])
        end=dt.datetime.fromisoformat(observation['finished_at'])
        if (begin.tzinfo is None or end.tzinfo is None or not begin<=end<=now
                or (now-begin).total_seconds()>120
                or begin.astimezone(CN).date().isoformat()!=now.astimezone(CN).date().isoformat()
                or raw.get('history_complete') is not True):
            raise TradeError('跨日核验快照过期、跨日或历史查询不完整')
        try:candidate(raw,state,config,trace)
        except ReceiptAbsent:pass
        else:raise TradeError('发现服务器候选委托，必须接回核验，不能按到期无记录归档')
        audit=reconcile(raw,proposed,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'])
        if audit['pending'] or dec(audit['frozen']):raise TradeError('已知委托或冻结资金未结清')
        if audit['cash_drift_requires_confirmation']:raise TradeError('现金差异尚未确认，不能同时解除未知委托预约')
        checks.append(dict(account={k:audit[k] for k in ('positions','sellable','states','cash','daily_paid','frozen','cash_drift_cny')},
                           order_ids=sorted(str(r['orderId']) for r in raw['orders'])))
        audits.append(audit);finished.append(end)
        proofs.append(dict(started_at=begin.isoformat(),finished_at=end.isoformat(),sha256=digest(raw)))
    if not 5<=(finished[1]-finished[0]).total_seconds()<=120 or checks[0]!=checks[1]:
        raise TradeError('两轮跨日账户核验不一致或未间隔至少5秒')
    proof=dict(intent_sha256=intent_fingerprint(intent),source_sha256=digest(state),snapshots=proofs,
        seen_order_ids=checks[-1]['order_ids'],released_reserve_cny=intent['reserve'],
        rule='A-share limit DAY order; prior trading date ended; two complete reconciled reads',
        meaning='Acceptance remains unknown. No observed order or fill after day expiry. Never replay the old intent.')
    proposed.setdefault('expired_unacknowledged',[]).append(dict(day=state['day'],at=now.isoformat(),
        classification='EXPIRED_UNOBSERVED',intent=copy.deepcopy(intent),proof=proof))
    for attempt in proposed['attempts']:attempt.update(audits[-1]['states'][attempt['oid']])
    proposed['last_audit']=dict(audits[-1],checked_at=now.isoformat())
    proposed['ack_recovery']=dict(phase='expired_unobserved',at=now.isoformat(),code=intent['order']['code'],
        reason='跨日两轮完整核验无新增委托或成交，原日内委托已到期；解除预约，保留原始未知回执和历史追查')
    expired_receipts(proposed)
    return proposed

"""Date-independent strategy and reconciliation rules for the local simulation console.

No credentials, network requests, subprocesses or order writes in this module.
"""
import copy
import datetime as dt
import math
from decimal import Decimal
from pathlib import Path
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending, number
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import read_json, digest, CN
import position_floor as floor

BASE_DIR=ROOT/'research/choice_pcf_automation'
HOME=BASE_DIR/'console'
TERMINAL={'4','7','8','9'}
MAX_CASH_DRIFT_CNY=5000
DEFAULTS=dict(version=1,account_mode='competition_simulation_only',strategy='pilot_one_lot',
    target_fraction=.92,capital_limit=12000000,max_security_value=500000,max_order_value=10000,
    price_guard=.03,spacing_seconds=5,allow_sells=False,prefer_sealed_plan=False,external_file='',external_script='',
    calendar_path=str(BASE_DIR/'daily_public_pcf/calendar.json'),calendar_valid_through='2026-12-30',
    excluded_cash_cny='0.00',cash_drift_tolerance_cny=.03,isolate_unknown_orders=False,fill_timeout_seconds=60,max_attempts_per_code=1,execution_date='2026-10-12',pilot_symbol='',max_new_orders=1,pilot_shares=100,pilot_total_cap_cny=10000,
    membership_path=str(BASE_DIR/'daily_public_pcf/csi500_members.json'),
    position_floor=dict(floor.POLICY_DEFAULTS))


def dec(value):
    try:v=Decimal(str(value))
    except Exception:raise TradeError('数值格式不正确') from None
    if not v.is_finite():raise TradeError('参数不能包含NaN或无穷值')
    return v


def config_checked(value):
    c=dict(DEFAULTS,**value)
    if c['account_mode']!='competition_simulation_only':raise TradeError('仅支持原Choice比赛模拟账户')
    if c['strategy'] not in ('pilot_one_lot','pilot_then_targets','pcf_563030','target_file','local_script'):raise TradeError('未知策略类型')
    for k in ('target_fraction','capital_limit','max_security_value','max_order_value','price_guard','spacing_seconds'):
        c[k]=float(dec(c[k]))
    if not 0<c['target_fraction']<=1 or not 0<c['price_guard']<=.20:raise TradeError('仓位须在0–100%，价格保护须在0–20%')
    if not 0<c['max_order_value']<=c['max_security_value']<=c['capital_limit']:raise TradeError('须满足：单笔上限≤单股上限≤总投入上限')
    if not 5<=c['spacing_seconds']<=3600:raise TradeError('委托间隔至少5秒')
    if type(c['allow_sells']) is not bool or type(c['prefer_sealed_plan']) is not bool:raise TradeError('卖出和封存计划开关必须为布尔值')
    if type(c['isolate_unknown_orders']) is not bool:raise TradeError('未确认委托隔离开关必须为布尔值')
    if dt.date.fromisoformat(c['calendar_valid_through']).isoformat()!=c['calendar_valid_through']:raise TradeError('日历截止日期格式错误')
    if dec(c['excluded_cash_cny'])!=0:raise TradeError('新云机使用已核验的新账本，隔离金额固定为0')
    c['cash_drift_tolerance_cny']=float(dec(c['cash_drift_tolerance_cny']))
    if not Decimal('.03')<=dec(c['cash_drift_tolerance_cny'])<=MAX_CASH_DRIFT_CNY:raise TradeError('现金差异容忍须在0.03–5000元，不能放宽持仓或订单核验')
    if c['fill_timeout_seconds']!=60 or c['max_attempts_per_code']!=1:raise TradeError('试单固定60秒挂单保护、最多一次提交')
    if c['strategy']=='target_file' and not c['external_file']:raise TradeError('请选择目标持仓JSON文件')
    if c['strategy']=='local_script' and not c['external_script']:raise TradeError('请选择本地策略Python脚本')
    try:c['position_floor']=floor.checked_policy(c['position_floor'],c['target_fraction'])
    except (ValueError,TypeError) as error:raise TradeError(str(error)) from None
    from pilot_one_lot import check_pilot_config
    check_pilot_config(c)
    return c


def days_for(c,day):
    days=read_json(c['calendar_path'])
    if not days or sorted(set(days))!=days:raise TradeError('交易日历无效')
    if day>c['calendar_valid_through']:raise TradeError('交易日历已到期，请在设置中导入更新的交易日历')
    if day not in days:raise TradeError('今天不是已确认的交易日')
    index=days.index(day)
    if index==0:raise TradeError('交易日历缺少前一交易日')
    return days,days[index-1]


def session_phase(now):
    t=now.astimezone(CN).time()
    if t<dt.time(9,30):return 'waiting_open'
    if t<dt.time(11,25) or dt.time(13,5)<=t<dt.time(14,50):return 'trading'
    if t<dt.time(13,5):return 'lunch'
    if t<dt.time(15):return 'closing'
    return 'closed'


def legacy_state(state):
    """Normalize legacy journals without discarding a single order or reservation."""
    result=copy.deepcopy(state)
    result.setdefault('prior',{})
    result.setdefault('historical',{})
    if 'baseline' in result:result.setdefault('carried_reserve',str(max(Decimal(0),dec(result['baseline']['paid']))))
    for a in result['attempts']:a['order'].setdefault('side',1)
    for o in result['historical'].values():o.setdefault('side',1)
    for o in result['prior'].values():o.setdefault('side',1)
    return result


def roll_day(state,day):
    state=legacy_state(state)
    expired_receipts(state)
    if day<state['day']:raise TradeError('禁止倒退交易日期')
    if day==state['day']:return state
    audit=state.get('last_audit')
    if not audit or audit['pending'] or dec(audit['frozen']):raise TradeError('前次运行尚未完成资金/订单核验')
    history=dict(state['historical'],**state['prior'])
    for a in state['attempts']:
        if not a.get('oid') or not a.get('terminal') or a['state'] not in TERMINAL:raise TradeError('前日有未确认委托，禁止跨日重置')
        if a['oid'] in history:raise TradeError('历史委托ID重复')
        history[a['oid']]=dict(a['order'],filled=a['filled'],state=a['state'])
    return dict(day=day,origin=copy.deepcopy(state.get('origin',{})),baseline=dict(positions=audit['positions'],cash=audit['cash'],paid=audit['paid']),
        historical=history,prior={},attempts=[],targets={},carried_reserve=str(max(Decimal(0),dec(audit['paid']))),
        baseline_security_reserve={},deferred={},quotes={},position_compliance=copy.deepcopy(state.get('position_compliance',{})),
        expired_unacknowledged=copy.deepcopy(state.get('expired_unacknowledged',[])),
        auth_recovery=dict(attempts=0,phase='ready',events=[]))


def intent_fingerprint(attempt):
    return digest(dict(order=attempt['order'],submitted_at=attempt['submitted_at'],reserve=attempt['reserve']))


def expired_receipts(state):
    """Local expiry evidence is not a fabricated server rejection or order ID."""
    entries=state.get('expired_unacknowledged',[]);seen=set()
    for entry in entries:
        try:
            intent=entry['intent'];proof=entry['proof'];fingerprint=intent_fingerprint(intent)
            times=[dt.datetime.fromisoformat(x['finished_at']) for x in proof['snapshots']]
            valid=(entry['classification']=='EXPIRED_UNOBSERVED' and not intent.get('oid')
                and intent['state']=='UNKNOWN' and intent.get('filled',0)==0
                and intent['order'].get('side',1)==1 and fingerprint==proof['intent_sha256']
                and fingerprint not in seen and len(times)==2 and all(t.tzinfo is not None for t in times)
                and all(t.astimezone(CN).date().isoformat()>entry['day'] for t in times)
                and 5<=(times[1]-times[0]).total_seconds()<=120
                and all(len(x['sha256'])==64 for x in proof['snapshots'])
                and len(proof['source_sha256'])==64 and isinstance(proof['seen_order_ids'],list)
                and dec(proof['released_reserve_cny'])==dec(intent['reserve'])
                and entry['day']<=state['day'])
            # Preserve and validate the original intraday quarantine proof too.
            isolated_attempts(dict(day=entry['day'],attempts=[intent]))
            valid=valid and bool(intent.get('isolation'))
        except (KeyError,TypeError,ValueError):valid=False
        if not valid:raise TradeError('跨日未确认委托归档证据无效')
        seen.add(fingerprint)
    return entries


def submission_count(state):
    return len(state.get('attempts',[]))+sum(e['day']==state['day'] for e in expired_receipts(state))


def isolated_attempts(state):
    result=[]
    for attempt in state['attempts']:
        if attempt.get('oid') or not attempt.get('isolation'):continue
        proof=attempt['isolation'];order=attempt['order']
        minimum=(dec(order['price'])*order['quantity']*Decimal('1.001')+10).quantize(Decimal('.01'),rounding='ROUND_CEILING')
        if (proof.get('day')!=state['day'] or proof.get('intent_sha256')!=intent_fingerprint(attempt)
                or proof.get('verified_snapshots')!=2 or not proof.get('first_snapshot_sha256') or not proof.get('second_snapshot_sha256')
                or attempt.get('state')!='UNKNOWN' or attempt.get('filled',0)!=0 or order.get('side',1)!=1
                or dec(attempt['reserve'])<minimum or dec(proof.get('reserve_cny',0))!=dec(attempt['reserve'])):
            raise TradeError('未确认委托的隔离证据或资金预约无效')
        result.append(attempt)
    if len(result)>1:raise TradeError('最多自动隔离一笔未确认委托')
    return result


def reconcile(raw,state,excluded='0.00',cash_tolerance_cny='.03',allow_quarantined=False):
    for entry in expired_receipts(state):
        known=set(entry['proof']['seen_order_ids'])
        for row in raw['orders']:
            if (str(row['orderDate']).replace('-','')==entry['day'].replace('-','')
                    and normalize_code(str(row['secCode']))==entry['intent']['order']['code']
                    and str(row['orderId']) not in known):
                raise TradeError('已归档未确认委托出现迟到回执，停止交易并重新核对')
    missing=[a for a in state['attempts'] if not a.get('oid')]
    isolated=isolated_attempts(state) if allow_quarantined else []
    if len(missing)!=len(isolated):raise TradeError('存在提交结果不明的委托；禁止猜测或自动重发，请核查日志')
    uncertain_reserve=sum((dec(a['reserve']) for a in isolated),Decimal(0))
    expected=dict(state.get('prior',{}))
    for a in state['attempts']:
        if not a.get('oid'):continue
        if a['oid'] in expected:raise TradeError('本地委托ID重复')
        expected[a['oid']]=a['order']
    historical=state.get('historical',{});orders=[];deals=[]
    for r in raw['orders']:
        date=str(r['orderDate']).replace('-','');oid=str(r['orderId'])
        if date==state['day'].replace('-',''):orders.append(r)
        elif date<state['day'].replace('-','') and oid in historical:
            o=historical[oid]
            if (normalize_code(str(r['secCode']))!=o['code'] or int(r['drt'])!=o.get('side',1)
                    or number(r['orderCount'],True)!=o['quantity'] or dec(r['orderPrice'])!=dec(o['price'])
                    or number(r['tradeCount'],True)!=o['filled'] or str(r['status'])!=o['state']):raise TradeError('历史委托发生变化')
        elif date<state['day'].replace('-','') and len(date)==8 and date.isdigit() and str(r['status'])=='8' and number(r['tradeCount'],True)==0:
            if any(str(d['orderId'])==oid for d in raw['deals']):raise TradeError('历史撤单出现异常成交')
        else:raise TradeError('存在未识别的历史或未来委托')
    for d in raw['deals']:
        oid=str(d['orderId'])
        if oid not in historical:deals.append(d);continue
        o=historical[oid];p=dec(d['price'])
        if normalize_code(str(d['secCode']))!=o['code'] or int(d['drt'])!=o.get('side',1) or p<=0:raise TradeError('历史成交身份变化')
        if (o.get('side',1)==1 and p>dec(o['price'])) or (o.get('side',1)==2 and p<dec(o['price'])):raise TradeError('历史成交超出限价')
    for oid,o in historical.items():
        linked=[d for d in raw['deals'] if str(d['orderId'])==oid]
        if linked and sum(number(d['tradeCount'],True) for d in linked)!=o['filled']:raise TradeError('历史成交数量变化')
    ids=[str(r['orderId']) for r in orders]
    if len(ids)!=len(set(ids)):raise TradeError('服务端委托ID重复')
    if set(ids)-set(expected):raise TradeError('发现外部委托，已暂停；请勿在同一账户手工交易')
    if set(expected)-set(ids):raise FillSnapshotPending('已确认委托暂未出现在委托查询')
    if any(str(d['orderId']) not in expected for d in deals):raise TradeError('发现未识别成交')
    rows={str(r['orderId']):r for r in orders};previous={a['oid']:a for a in state['attempts'] if a.get('oid')}
    positions=dict(state['baseline']['positions']);pending={};states={};net=Decimal(0);fees=Decimal(0)
    for oid,o in expected.items():
        r=rows[oid];side=o.get('side',1);filled=number(r['tradeCount'],True);status=str(r['status'])
        if normalize_code(str(r['secCode']))!=o['code'] or int(r['drt'])!=side or number(r['orderCount'],True)!=o['quantity'] or dec(r['orderPrice'])!=dec(o['price']):raise TradeError('委托身份、方向、数量或限价不一致')
        if status not in {str(i) for i in range(1,10)} or filled>o['quantity'] or (status=='4' and filled!=o['quantity']):raise TradeError('无效委托状态或成交数量')
        if oid in previous and filled<previous[oid].get('filled',0):raise TradeError('累计成交数量倒退')
        if oid in state.get('prior',{}) and (filled!=o['filled'] or status!=o['state']):raise TradeError('已核验委托发生变化')
        linked=[d for d in deals if str(d['orderId'])==oid]
        if sum(number(d['tradeCount'],True) for d in linked)!=filled:raise FillSnapshotPending('委托成交数量与逐笔成交数量暂不一致')
        for d in linked:
            p=dec(d['price']);q=number(d['tradeCount'],True)
            if normalize_code(str(d['secCode']))!=o['code'] or int(d['drt'])!=side or p<=0 or (side==1 and p>dec(o['price'])) or (side==2 and p<dec(o['price'])):raise TradeError('关联成交与委托不一致')
            fee=sum((dec(number(d[k])) for k in ('qsYj','ghCost','yhs')),Decimal(0));fees+=fee
            net+=p*q+fee if side==1 else -p*q+fee
        positions[o['code']]=positions.get(o['code'],0)+(filled if side==1 else -filled)
        if positions[o['code']]<0:raise TradeError('成交产生负持仓')
        if status not in TERMINAL:pending[oid]=dict(code=o['code'],side=side,quantity=o['quantity']-filled,price=o['price'])
        states[oid]=dict(state=status,filled=filled,terminal=status in TERMINAL)
    observed={};sellable={}
    for r in raw['holds']:
        code=normalize_code(str(r['secCode']));held=number(r['count'],True);available=number(r['availCount'],True)
        if code in observed or available>held:raise TradeError('持仓重复或可卖数量异常')
        observed[code]=held;sellable[code]=available
    observed={c:n for c,n in observed.items() if n}
    if observed!={c:n for c,n in positions.items() if n}:raise FillSnapshotPending('持仓数量与精确关联成交暂不一致')
    balance=raw['balance'];cash=dec(number(balance['availBalance']))-dec(excluded);frozen=dec(number(balance['frozenMoney']))
    if cash<0:raise TradeError('可用现金不足以保留隔离金额')
    if not pending and frozen>0:raise FillSnapshotPending('委托已结束，冻结资金尚未释放')
    book_cash=dec(state['baseline']['cash'])-frozen-net
    cash_drift=cash-book_cash;tolerance=dec(cash_tolerance_cny)
    if not Decimal('.03')<=tolerance<=MAX_CASH_DRIFT_CNY:raise TradeError('现金差异容忍额度无效')
    if abs(cash_drift)>tolerance:
        raise FillSnapshotPending(f'可用现金、冻结资金与成交净额暂不一致：现金差额{cash_drift:+.2f}元，允许±{tolerance:.2f}元')
    accepted=state.get('cash_variance',{})
    confirmed=dec(accepted.get('amount_cny',0)) if accepted.get('day')==state['day'] else Decimal(0)
    needs_confirmation=abs(cash_drift-confirmed)>Decimal('.03')
    if needs_confirmation and (pending or frozen):
        raise FillSnapshotPending(f'现金差额{cash_drift:+.2f}元发生变化，须待已知挂单和冻结资金结清后再次核验')
    actual_cash=cash
    # A surplus is recorded but never treated as extra spending power. A shortfall
    # always lowers available funds. The original baseline is never rebased.
    cash=min(actual_cash,book_cash)-uncertain_reserve
    if cash<0:raise TradeError('保守可用现金不足')
    market=number(balance['totalPosValue']);nav=number(balance['totalAssets'])
    return dict(positions=observed,sellable=sellable,position_mark_values={normalize_code(str(r['secCode'])):str(dec(r['value'])) for r in raw['holds']},states=states,pending=pending,cash=str(cash),account_cash_before_quarantine=str(actual_cash+dec(excluded)),
        actual_cash_after_quarantine=str(actual_cash),cash_drift_cny=str(cash_drift),cash_drift_tolerance_cny=str(tolerance),
        uncertain_buy_reserve=str(uncertain_reserve),isolated_order_count=len(isolated),isolated_codes=[a['order']['code'] for a in isolated],
        cash_drift_requires_confirmation=needs_confirmation,
        frozen=str(frozen),daily_paid=str(net),paid=str(dec(state['baseline']['paid'])+net),fees=str(fees),
        market_value=market,total_assets=nav,stock_fraction=market/nav if nav else None)


def parse_targets(obj,day,signal,nav,c,quotes,*,valuation_time=None):
    if obj.get('execution_date')!=day or obj.get('signal_date')!=signal:raise TradeError('策略信号日期或执行日期错误，禁止使用过期目标')
    mode=obj.get('mode');rows=obj.get('targets')
    if mode not in ('weights','shares') or not isinstance(rows,list) or not rows:raise TradeError('策略须提供weights或shares目标列表')
    if valuation_time is not None:
        if mode!='shares' or valuation_time.tzinfo is None or valuation_time.astimezone(CN).date().isoformat()!=day:
            raise TradeError('盘中股数目标估值时点或模式异常')
        from batch_engine import validate_quote
    targets={};weights={};valuation_prices={}
    for row in rows:
        code=normalize_code(str(row['code']))
        if code in targets or code in weights:raise TradeError('目标文件证券重复')
        if code not in quotes:raise TradeError('缺少目标证券参考行情')
        reference=float(dec(quotes[code]['previous_close']))
        if reference<=0:raise TradeError('参考收盘价无效')
        mark=reference
        if valuation_time is not None:
            q=quotes[code];validate_quote(q,dict(quote_max_age_seconds=30),valuation_time)
            if q['code']!=code or dt.datetime.fromisoformat(q['timestamp']).astimezone(CN).date().isoformat()!=day:
                raise TradeError('盘中估值行情身份或日期异常')
            mark=float(dec(q['last']))
            if mark<=0:raise TradeError('盘中估值价格无效')
        valuation_prices[code]=mark
        if mode=='weights':
            weight=dec(row['weight'])
            if not 0<=weight<=1:raise TradeError('权重须在0–1之间，表示占账户总资产比例')
            weights[code]=weight
            quantity=int(dec(nav)*weight/dec(reference)//100)*100
            if code.startswith('SH688') and quantity<200:quantity=0
        else:quantity=number(row['shares'],True)
        if quantity%100 or (code.startswith('SH688') and 0<quantity<200):raise TradeError('目标股数不满足保守整手规则')
        if quantity*mark>c['max_security_value']+.01:raise TradeError('策略目标超过单股上限：'+code)
        targets[code]=dict(code=code,quantity=quantity,reference_price=reference)
    if weights and sum(weights.values())>dec(c['target_fraction'])+Decimal('.00000001'):raise TradeError('策略总权重超过设置的目标仓位')
    value=sum(o['quantity']*valuation_prices[code] for code,o in targets.items())
    if value>min(c['capital_limit'],nav*c['target_fraction'])+.01:raise TradeError('策略目标总市值超过仓位或总投入上限')
    return targets


def pcf_targets(record,day,signal,nav,c,quotes,universe,holdings=None):
    from quanta_agents.factor_lab_a.pcf import validate_pcf
    validate_pcf(record)
    if record['date']!=signal:raise TradeError('PCF日期错误')
    money={}
    for row in record['stocklist']:
        if float(row['L_NUMBER'])<=0:continue
        code=normalize_code(str(row['C_STOCKCODE']));q=quotes[code]
        if float(row['L_NUMBER'])>0 and code in universe and not q['is_st'] and q['tradable']:
            money[code]=dec(row['L_NUMBER'])*dec(q['previous_close'])
    total=sum(money.values())
    if total<=0:raise TradeError('PCF可执行股票池为空')
    if floor.active(c,day):
        try:quantities,value=floor.allocate(money,{code:q['previous_close'] for code,q in quotes.items()},holdings or {},nav,c)
        except ValueError as error:raise TradeError(str(error)) from None
        return {code:dict(code=code,quantity=quantity,reference_price=quotes[code]['previous_close']) for code,quantity in quantities.items()}
    obj=dict(execution_date=day,signal_date=signal,mode='weights',targets=[dict(code=code,weight=str(value/total*dec(c['target_fraction']))) for code,value in money.items()])
    return parse_targets(obj,day,signal,nav,c,quotes)


def differences(targets,audit):
    return {code:o['quantity']-audit['positions'].get(code,0) for code,o in targets.items() if o['quantity']!=audit['positions'].get(code,0)}

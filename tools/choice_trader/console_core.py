"""Date-independent strategy and reconciliation rules for the local simulation console.

No credentials, network requests, subprocesses or order writes in this module.
"""
import copy
import datetime as dt
import math
from decimal import Decimal
from pathlib import Path
from local_access import ROOT
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending, number
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import read_json, digest, CN

BASE_DIR=Path(__file__).resolve().parent
HOME=BASE_DIR/'runtime'
TERMINAL={'4','7','8','9'}
DEFAULTS=dict(version=1,account_mode='competition_simulation_only',strategy='pcf_563030',
    target_fraction=.5,capital_limit=5500000,max_security_value=500000,max_order_value=500000,
    price_guard=.03,spacing_seconds=5,allow_sells=False,external_file='',external_script='',
    calendar_path=str(HOME/'calendar.json'),calendar_valid_through='1970-01-01',
    excluded_cash_cny='0',fill_timeout_seconds=60,max_attempts_per_code=3,
    membership_path=str(HOME/'membership.parquet'))


def dec(value):
    try:v=Decimal(str(value))
    except Exception:raise TradeError('数值格式不正确') from None
    if not v.is_finite():raise TradeError('参数不能包含NaN或无穷值')
    return v


def config_checked(value):
    c=dict(DEFAULTS,**value)
    if c['account_mode']!='competition_simulation_only':raise TradeError('仅支持已绑定的Choice比赛模拟账户')
    if c['strategy'] not in ('pcf_563030','target_file','local_script'):raise TradeError('未知策略类型')
    for k in ('target_fraction','capital_limit','max_security_value','max_order_value','price_guard','spacing_seconds'):
        c[k]=float(dec(c[k]))
    if not 0<c['target_fraction']<=1 or not 0<c['price_guard']<=.20:raise TradeError('仓位须在0–100%，价格保护须在0–20%')
    if not 0<c['max_order_value']<=c['max_security_value']<=c['capital_limit']:raise TradeError('须满足：单笔上限≤单股上限≤总投入上限')
    if not 5<=c['spacing_seconds']<=3600:raise TradeError('委托间隔至少5秒')
    if type(c['allow_sells']) is not bool:raise TradeError('卖出开关必须为布尔值')
    if dt.date.fromisoformat(c['calendar_valid_through']).isoformat()!=c['calendar_valid_through']:raise TradeError('日历截止日期格式错误')
    if dec(c['excluded_cash_cny'])!=0:raise TradeError('共享版不接受未解释的现金差额；请核查原始账本')
    if c['fill_timeout_seconds']!=60 or c['max_attempts_per_code']!=3:raise TradeError('挂单及重试保护不可在普通设置中更改')
    if c['strategy']=='target_file' and not c['external_file']:raise TradeError('请选择目标持仓JSON文件')
    if c['strategy']=='local_script' and not c['external_script']:raise TradeError('请选择本地策略Python脚本')
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
    if t<dt.time(9,35):return 'waiting_open'
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
    if day<state['day']:raise TradeError('禁止倒退交易日期')
    if day==state['day']:return state
    audit=state.get('last_audit')
    if not audit or audit['pending'] or dec(audit['frozen']):raise TradeError('前次运行尚未完成资金/订单核验')
    history=dict(state['historical'],**state['prior'])
    for a in state['attempts']:
        if not a.get('oid') or not a.get('terminal') or a['state'] not in TERMINAL:raise TradeError('前日有未确认委托，禁止跨日重置')
        if a['oid'] in history:raise TradeError('历史委托ID重复')
        history[a['oid']]=dict(a['order'],filled=a['filled'],state=a['state'])
    return dict(day=day,account_binding_sha256=state.get('account_binding_sha256'),baseline=dict(positions=audit['positions'],cash=audit['cash'],paid=audit['paid']),
        historical=history,prior={},attempts=[],targets={},carried_reserve=str(max(Decimal(0),dec(audit['paid']))),
        baseline_security_reserve={},deferred={},quotes={},auth_recovery=dict(attempts=0,phase='ready',events=[]))


def reconcile(raw,state,excluded='0'):
    if any(not a.get('oid') for a in state['attempts']):raise TradeError('存在提交结果不明的委托；禁止猜测或自动重发，请核查日志')
    expected=dict(state.get('prior',{}))
    for a in state['attempts']:
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
    rows={str(r['orderId']):r for r in orders};previous={a['oid']:a for a in state['attempts']}
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
    if abs(dec(state['baseline']['cash'])-cash-frozen-net)>Decimal('.03'):raise FillSnapshotPending('可用现金、冻结资金与成交净额暂不一致')
    if not pending and frozen>0:raise FillSnapshotPending('委托已结束，冻结资金尚未释放')
    market=number(balance['totalPosValue']);nav=number(balance['totalAssets'])
    return dict(positions=observed,sellable=sellable,states=states,pending=pending,cash=str(cash),account_cash_before_quarantine=str(cash+dec(excluded)),
        frozen=str(frozen),daily_paid=str(net),paid=str(dec(state['baseline']['paid'])+net),fees=str(fees),
        market_value=market,total_assets=nav,stock_fraction=market/nav if nav else None)


def parse_targets(obj,day,signal,nav,c,quotes):
    if obj.get('execution_date')!=day or obj.get('signal_date')!=signal:raise TradeError('策略信号日期或执行日期错误，禁止使用过期目标')
    mode=obj.get('mode');rows=obj.get('targets')
    if mode not in ('weights','shares') or not isinstance(rows,list) or not rows:raise TradeError('策略须提供weights或shares目标列表')
    targets={};weights={}
    for row in rows:
        code=normalize_code(str(row['code']))
        if code in targets or code in weights:raise TradeError('目标文件证券重复')
        if code not in quotes:raise TradeError('缺少目标证券参考行情')
        reference=float(dec(quotes[code]['previous_close']))
        if reference<=0:raise TradeError('参考收盘价无效')
        if mode=='weights':
            weight=dec(row['weight'])
            if not 0<=weight<=1:raise TradeError('权重须在0–1之间，表示占账户总资产比例')
            weights[code]=weight
            quantity=int(dec(nav)*weight/dec(reference)//100)*100
            if code.startswith('SH688') and quantity<200:quantity=0
        else:quantity=number(row['shares'],True)
        if quantity%100 or (code.startswith('SH688') and 0<quantity<200):raise TradeError('目标股数不满足保守整手规则')
        if quantity*reference>c['max_security_value']+.01:raise TradeError('策略目标超过单股上限：'+code)
        targets[code]=dict(code=code,quantity=quantity,reference_price=reference)
    if weights and sum(weights.values())>dec(c['target_fraction'])+Decimal('.00000001'):raise TradeError('策略总权重超过设置的目标仓位')
    value=sum(o['quantity']*o['reference_price'] for o in targets.values())
    if value>min(c['capital_limit'],nav*c['target_fraction'])+.01:raise TradeError('策略目标总市值超过仓位或总投入上限')
    return targets


def pcf_targets(record,day,signal,nav,c,quotes,universe):
    from quanta_agents.factor_lab_a.pcf import validate_pcf
    validate_pcf(record)
    if record['date']!=signal:raise TradeError('PCF日期错误')
    money={}
    for row in record['stocklist']:
        code=normalize_code(str(row['C_STOCKCODE']));q=quotes[code]
        if float(row['L_NUMBER'])>0 and code in universe and not q['is_st'] and q['tradable']:
            money[code]=dec(row['L_NUMBER'])*dec(q['previous_close'])
    total=sum(money.values())
    if total<=0:raise TradeError('PCF可执行股票池为空')
    obj=dict(execution_date=day,signal_date=signal,mode='weights',targets=[dict(code=code,weight=str(value/total*dec(c['target_fraction']))) for code,value in money.items()])
    return parse_targets(obj,day,signal,nav,c,quotes)


def differences(targets,audit):
    return {code:o['quantity']-audit['positions'].get(code,0) for code,o in targets.items() if o['quantity']!=audit['positions'].get(code,0)}

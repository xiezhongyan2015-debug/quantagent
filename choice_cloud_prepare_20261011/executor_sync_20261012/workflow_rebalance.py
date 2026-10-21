"""Sealed pilot -> target workflow. Pure checks; no order/network operations."""
import copy
import datetime as dt
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from batch_engine import validate_quote, stable_price_at_ask
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import CN, digest

DAY='2026-10-12'
SIGNAL='2026-10-09'
ORDER_CAP=Decimal('50000')
MAX_ORDERS=2000
MAX_SLICES=128
MAX_ZERO_CANCELS=3  # Original order plus at most two fresh-price retries per code/day.

def dec(v):
    if isinstance(v,bool):raise TradeError('布尔值不能充当金额')
    n=Decimal(str(v))
    if not n.is_finite():raise TradeError('金额必须有限')
    return n

def directional_price_allowed(side,price,reference,guard):
    p,r,g=dec(price),dec(reference),dec(guard)
    if side not in (1,2) or min(p,r)<=0 or not 0<=g<=Decimal('.03'):
        raise TradeError('价格保护方向、价格或幅度异常')
    return p<=r*(1+g) if side==1 else p>=r*(1-g)

def zero_cancelled(attempt):
    return (bool(attempt.get('oid')) and attempt.get('terminal') is True
        and attempt.get('state')=='8' and type(attempt.get('filled')) is int
        and attempt['filled']==0 and bool(attempt.get('cancel_requested_at')))

def prior_slices_allowed(prior):
    return (sum(zero_cancelled(x) for x in prior)<MAX_ZERO_CANCELS
        and all(x.get('oid') and x.get('terminal') is True and
            ((x.get('state')=='4' and x.get('filled')==x['order']['quantity']) or zero_cancelled(x)) for x in prior))

def make_zero_cancel_proof(attempt,audits,now):
    if not zero_cancelled(attempt) or len(audits)!=2:raise TradeError('只允许核验本执行器零成交已撤委托')
    times=[dt.datetime.fromisoformat(a['checked_at']).astimezone(CN) for a in audits]
    if (any(t.date().isoformat()!=DAY for t in times) or not 5<=(times[1]-times[0]).total_seconds()<=120
        or not 0<=(now-times[1]).total_seconds()<=30):raise TradeError('撤单复核时点或间隔异常')
    for a in audits:
        s=a.get('states',{}).get(attempt['oid'],{})
        if (a.get('pending') or dec(a['frozen']) or a.get('cash_drift_requires_confirmation') or a.get('isolated_order_count',0)
            or s!={'state':'8','filled':0,'terminal':True}):raise TradeError('撤单、成交、冻结或账户核验未通过')
    if any(audits[0][k]!=audits[1][k] for k in ('cash','positions','sellable')):raise FillSnapshotPending('零成交撤单两轮现金或持仓尚未一致')
    return dict(oid=attempt['oid'],order_sha256=digest(attempt['order']),audits=[dict(at=a['checked_at'],sha256=digest(a)) for a in audits],
        cash=audits[1]['cash'],positions=copy.deepcopy(audits[1]['positions']),sellable=copy.deepcopy(audits[1]['sellable']))

def zero_cancel_proofs_valid(state,prior,now):
    a=state.get('last_audit',{});proofs=state.get('zero_cancel_retry_proofs',{})
    for attempt in prior:
        if not zero_cancelled(attempt):continue
        p=proofs.get(attempt['oid'],{})
        try:
            times=[dt.datetime.fromisoformat(x['at']).astimezone(CN) for x in p['audits']]
            if (p.get('oid')!=attempt['oid'] or p.get('order_sha256')!=digest(attempt['order']) or len(times)!=2
                or any(t.date().isoformat()!=DAY for t in times) or not 5<=(times[1]-times[0]).total_seconds()<=120
                or not 0<=(now-times[1]).total_seconds()<=30
                or any(len(x['sha256'])!=64 for x in p['audits'])
                or any(p[k]!=a[k] for k in ('cash','positions','sellable'))
                or a.get('states',{}).get(attempt['oid'])!={'state':'8','filled':0,'terminal':True}
                or a.get('pending') or dec(a['frozen']) or a.get('isolated_order_count',0)
                or a.get('cash_drift_requires_confirmation')):return False
        except (KeyError,TypeError,ValueError):return False
    return True

def reserved(price,quantity):
    return (dec(price)*quantity*Decimal('1.001')+10).quantize(Decimal('.01'),rounding=ROUND_CEILING)

def checked(c):
    if c['strategy']!='pilot_then_targets':return None
    r=c.get('rebalance_after_pilot')
    if not isinstance(r,dict) or set(r)!={'target_path','target_sha256','membership_path','membership_sha256','target_fraction','minimum_stock_fraction','minimum_csi500_nav','max_orders','max_slices_per_code','max_order_value'}:
        raise TradeError('正式调仓必须提供完整且已封存的目标及成分证据')
    if (dec(r['target_fraction'])!=Decimal('.95') or dec(r['minimum_stock_fraction'])!=Decimal('.90')
        or dec(r['minimum_csi500_nav'])!=Decimal('.80') or dec(r['max_order_value'])!=ORDER_CAP or type(r['max_orders']) is not int or r['max_orders']!=MAX_ORDERS
        or type(r['max_slices_per_code']) is not int or r['max_slices_per_code']!=MAX_SLICES):
        raise TradeError('正式阶段仓位、合规或拆单边界不符')
    for key in ('target_sha256','membership_sha256'):
        v=r[key]
        if not isinstance(v,str) or len(v)!=64 or any(x not in '0123456789abcdef' for x in v):raise TradeError('缺少封存文件SHA256')
    for key in ('target_path','membership_path'):
        if not isinstance(r[key],str) or not r[key] or '{' in r[key]:raise TradeError('正式目标必须为固定日期的本地文件')
    return copy.deepcopy(r)

def read_sealed(path,sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise TradeError('正式目标或成分文件已变化；禁止未经复核切换')
    return json.loads(raw)

def inputs(c):
    r=checked(c)
    if not r:raise TradeError('当前预约没有正式调仓范围')
    obj=read_sealed(r['target_path'],r['target_sha256'])
    m=read_sealed(r['membership_path'],r['membership_sha256'])
    if (obj.get('execution_date')!=DAY or obj.get('signal_date')!=SIGNAL or obj.get('mode')!='shares'
        or obj.get('review_only') is not False or obj.get('execution_enabled') is not True
        or obj.get('generated_from',{}).get('shadow') or obj.get('research_only')
        or obj.get('strategy_layer') not in ('P0','V5','V5_tilt')):raise TradeError('正式目标日期、模式或发布标记不符')
    if m.get('as_of')!=SIGNAL or len(m.get('allowed',[]))!=1800 or len(set(m['allowed']))!=1800 or len(m.get('csi500',[]))!=500 or len(set(m['csi500']))!=500:
        raise TradeError('正式阶段需要完整且同期的300/500/1000并集')
    allowed={normalize_code(x) for x in m['allowed']};c500={normalize_code(x) for x in m['csi500']}
    if len(allowed)!=1800 or len(c500)!=500 or not c500<=allowed:raise TradeError('中证500名单不属于完整且唯一的并集')
    rows=obj.get('targets');seen=set()
    if not isinstance(rows,list) or not 80<=len(rows)<=1800:raise TradeError('正式目标数量异常')
    for row in rows:
        code=normalize_code(row['code']);n=row.get('shares')
        if code not in allowed or code in seen or type(n) is not int or n<=0 or n%100 or (code.startswith('SH688') and n<200):raise TradeError('正式目标证券、重复项或整手股数不符')
        seen.add(code)
    sources=m.get('sources',[])
    if {x.get('index') for x in sources}!={'csi300','csi500','csi1000'} or len(sources)!=3:raise TradeError('缺少三只指数官方来源证明')
    urls={name:'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/'+code+'cons.xls' for name,code in [('csi300','000300'),('csi500','000905'),('csi1000','000852')]}
    for x in sources:
        sha=x.get('source_file_sha256','')
        if x.get('snapshot_date')!=SIGNAL or x.get('source_url')!=urls[x['index']] or len(sha)!=64 or any(ch not in '0123456789abcdef' for ch in sha):
            raise TradeError('官方成分来源异常')
    return obj,m

def verify_current_sources(c,public):
    _,m=inputs(c)
    for source in m['sources']:
        response=public.get(source['source_url'],timeout=15,allow_redirects=False)
        if response.status_code!=200 or not 0<len(response.content)<=2_000_000 or hashlib.sha256(response.content).hexdigest()!=source['source_file_sha256']:
            raise TradeError('正式调仓所用官方成分证据已改变或无法核验，停止并保留原记录')

def acceptance(c,state,audits):
    if c['strategy']!='pilot_then_targets':raise TradeError('未设置试单后正式调仓')
    if state.get('rebalance_workflow',{}).get('phase')=='formal':raise TradeError('正式阶段已经进入，不重复接入')
    attempts=state['attempts']
    if len(attempts)!=1:raise TradeError('试单验收必须恰好一笔')
    p=attempts[0];o=p['order']
    if (not p.get('oid') or p.get('state')!='4' or p.get('filled')!=100 or not p.get('terminal')
        or o['code']!=c['pilot_symbol'] or o['side']!=1 or o['quantity']!=100):raise TradeError('试单未完整成交100股，禁止进入正式调仓')
    plan=state.get('pilot_plan',{})
    if plan.get('preview_only',True) or plan.get('held_before')!=state['baseline']['positions'].get(c['pilot_symbol'],0):raise TradeError('试单计划与初始持仓不一致')
    if len(audits)!=2:raise TradeError('试单后需要两轮完整账户核验')
    for a in audits:
        s=a.get('states',{}).get(p['oid'],{})
        if (a.get('pending') or dec(a['frozen']) or a.get('cash_drift_requires_confirmation') or a.get('isolated_order_count',0)
            or s.get('state')!='4' or s.get('filled')!=100 or not s.get('terminal')
            or a['positions'].get(c['pilot_symbol'],0)!=plan['target_shares']):raise TradeError('試单回报、持仓或冻结资金验收未通过')
    times=[dt.datetime.fromisoformat(a['checked_at']).astimezone(CN) for a in audits]
    if any(t.date().isoformat()!=DAY for t in times) or not 5<=(times[1]-times[0]).total_seconds()<=120:raise TradeError('验收快照时点或间隔异常')
    return dict(pilot_oid=p['oid'],pilot_filled=100,pilot_code=o['code'],accepted_at=times[1].isoformat(),
        audits=[dict(at=a['checked_at'],sha256=digest(a)) for a in audits],original_config_sha256=digest(c),
        target_sha256=checked(c)['target_sha256'],membership_sha256=checked(c)['membership_sha256'])

def formal_state(state):return state.get('rebalance_workflow',{}).get('phase')=='formal'

def effective(c,state):
    if not formal_state(state):return c
    r=checked(c);w=state['rebalance_workflow'];p=state['attempts'][0]
    if (not r or w.get('original_config_sha256')!=digest(c) or w.get('target_sha256')!=r['target_sha256']
        or w.get('membership_sha256')!=r['membership_sha256'] or w.get('pilot_oid')!=p.get('oid')
        or p.get('state')!='4' or p.get('filled')!=100 or not p.get('terminal') or p['order']['code']!=c['pilot_symbol'] or p['order']['quantity']!=100 or p['order']['side']!=1
        or w.get('pilot_filled')!=100 or w.get('pilot_code')!=c['pilot_symbol'] or len(w.get('audits',[]))!=2
        or not w.get('targets_sha256') or w['targets_sha256']!=digest(state['targets'])):raise TradeError('正式阶段验收、封存目标或原始范围不一致')
    times=[dt.datetime.fromisoformat(x['at']).astimezone(CN) for x in w['audits']]
    if any(t.date().isoformat()!=DAY for t in times) or not 5<=(times[1]-times[0]).total_seconds()<=120 or any(len(x.get('sha256',''))!=64 for x in w['audits']):raise TradeError('正式阶段验收证据时点异常')
    obj,_=inputs(c);expected={normalize_code(x['code']):x['shares'] for x in obj['targets']}
    if not set(expected)<=set(state['targets']) or any(x['quantity']!=expected.get(code,0) for code,x in state['targets'].items()):raise TradeError('正式目标股数与已封存文件不符')
    out=copy.deepcopy(c);out.update(strategy='target_file',allow_sells=True,target_fraction=.95,max_order_value=float(ORDER_CAP),
        max_attempts_per_code=MAX_SLICES,max_new_orders=MAX_ORDERS+1,external_file=r['target_path'])
    return out

def valuation_bounds(audit):
    """Separate asynchronous price marks from exact cash/share/fill accounting.

    A small market-value timing gap is charged against the available risk
    headroom. Buys use upper stock/lower NAV; sells use lower stock/upper NAV
    and deduct the entire gap from CSI500 value. No cash is fabricated.
    """
    marks=audit.get('position_mark_values',{})
    positions={code:n for code,n in audit.get('positions',{}).items() if n}
    if not isinstance(marks,dict) or not marks or not set(positions)<=set(marks):
        raise TradeError('缺少完整持仓市值字段')
    values={code:dec(value) for code,value in marks.items()}
    if any(value<0 or (code in positions and value<=0) or (code not in positions and value!=0) for code,value in values.items()):
        raise TradeError('持仓市值字段与持仓身份不符')
    account=dec(audit['market_value']);marked=sum(values.values());nav=dec(audit['total_assets'])
    if account<=0 or nav<=0:raise TradeError('账户市值或净资产字段异常')
    delta=abs(marked-account);cap=min(Decimal('10000'),nav*Decimal('.001'))
    if delta>cap:
        raise FillSnapshotPending(f'持仓市值快照差额{delta:.2f}元超过估值缓冲{cap:.2f}元；保持只读重新核账')
    # Cash + frozen equals the non-stock part of the reported NAV. Using that
    # same part avoids converting marks into spendable cash or touching fees.
    marked_nav=nav+(marked-account)
    return dict(lower_stock=min(marked,account),upper_stock=max(marked,account),
        lower_nav=min(nav,marked_nav),upper_nav=max(nav,marked_nav),uncertainty=delta,
        snapshot_gap_cap=cap,marks=marks)

def consistent_position_marks(audit):
    return valuation_bounds(audit)['marks']

def sell_room(c,audit,members,code,quantity,price):
    r=checked(c);bounds=valuation_bounds(audit);nav=bounds['upper_nav'];value=dec(price)*quantity
    marks=bounds['marks']
    c500=set(members['csi500']);member=sum(dec(v) for k,v in marks.items() if k in c500)
    return (bounds['lower_stock']-value>=nav*dec(r['minimum_stock_fraction'])
            and member-bounds['uncertainty']-(value if code in c500 else 0)>=nav*dec(r['minimum_csi500_nav']))

def guard_formal(c,state,order,now):
    """Second write boundary, including independently checked zero-fill retries."""
    if not formal_state(state):raise TradeError('未验收一手试单，正式写入禁止')
    effective(c,state);obj,m=inputs(c);r=checked(c);a=state.get('last_audit',{})
    if now.astimezone(CN).date().isoformat()!=DAY or state['day']!=DAY or not a:raise TradeError('正式调仓日期或核账缺失')
    stamp=dt.datetime.fromisoformat(a['checked_at']).astimezone(CN)
    if stamp.date().isoformat()!=DAY or not 0<=(now-stamp).total_seconds()<=30:raise TradeError('正式写入前核账已过期')
    attempts=state['attempts'];current=attempts[-1]
    if (len(attempts)>MAX_ORDERS+1 or current.get('phase')!='formal' or current['state']!='SENDING'
        or current.get('oid') or current['order']!=order or any(not x.get('terminal') for x in attempts[:-1])
        or a['pending'] or dec(a['frozen'])):raise TradeError('正式拆单序列或待处理原单异常')
    prior=[x for x in attempts[1:-1] if x['order']['code']==order['code']]
    if len(prior)>=MAX_SLICES or not prior_slices_allowed(prior):raise TradeError('原单部分成交、拒单、未知或达到零成交撤单重试上限')
    if not zero_cancel_proofs_valid(state,prior,now):raise TradeError('零成交撤单重试缺少两轮新鲜完整核账证明')
    target=state['targets'].get(order['code'],{}).get('quantity',0);held=a['positions'].get(order['code'],0)
    expected_side=1 if target>held else 2;delta=abs(target-held);quantity=order['quantity']
    if type(quantity) is not int or quantity<=0 or quantity>delta or order['side']!=expected_side or dec(order['reference_price'])!=dec(state['targets'][order['code']]['reference_price']):raise TradeError('正式下单方向、参考价格或股数超过目标缺口')
    if order['side']==1 and (order['code'] not in set(m['allowed']) or quantity%100 or (order['code'].startswith('SH688') and quantity<200)):
        raise TradeError('正式买入证券或买入单位不符')
    if order['side']==2 and (quantity>a['sellable'].get(order['code'],0) or quantity%100 and quantity!=a['sellable'].get(order['code'],0)):
        raise TradeError('正式卖出超出T+1可卖数量')
    if order['side']==2 and order['code'].startswith('SH688') and quantity<200 and not (target==0 and quantity==held):
        raise TradeError('科创板非清仓卖出须至少200股')
    if reserved(order['price'],quantity)>ORDER_CAP or dec(current['reserve'])!=reserved(order['price'],quantity):raise TradeError('正式单笔含费用超过5万元或预约不符')
    q=state['quotes'][order['code']];validate_quote(q,dict(quote_max_age_seconds=30),now)
    if q['code']!=order['code'] or dt.datetime.fromisoformat(q['timestamp']).astimezone(CN).date().isoformat()!=DAY:raise TradeError('正式行情日期或身份不符')
    price=dec(order['price']);expected=stable_price_at_ask(q['ask'] if order['side']==1 else q['bid'],0)
    if (expected is None or price!=dec(expected) or price<=0 or price!=price.quantize(Decimal('.01'))
        or not dec(q['lower_limit'])<=price<=dec(q['upper_limit'])
        or not directional_price_allowed(order['side'],price,order['reference_price'],c['price_guard'])):raise TradeError('正式写入限价或价格保护不符')
    if order['side']==1:
        bounds=valuation_bounds(a)
        if (reserved(price,quantity)>dec(a['cash']) or bounds['upper_stock']+reserved(price,quantity)>min(dec(c['capital_limit']),bounds['lower_nav']*dec(r['target_fraction']))
            or max(price,dec(q['last']))*(held+quantity)>dec(c['max_security_value'])):raise TradeError('正式买入超过现金、总仓位或单股限额')
    elif not sell_room(c,a,m,order['code'],quantity,max(price,dec(q['last']))):raise TradeError('正式卖出会突破90%股票或80%中证500底线')

def max_order_quantity(price):
    """The cap includes the same conservative fee reserve used by the writer."""
    return max(0,int((ORDER_CAP-10)/Decimal('1.001')/dec(price)//100)*100)

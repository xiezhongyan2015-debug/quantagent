"""One incremental lot; pure limits, genuine source evidence, no order transport."""
import datetime as dt
import hashlib
import json
import xml.etree.ElementTree as ET
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import CN, digest
from batch_engine import validate_quote, stable_price_at_ask

EXECUTION_DATE='2026-10-12'
SIGNAL_DATE='2026-10-09'
CAP=Decimal('10000')
MEMBERS_URL='https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000905cons.xls'

def assert_membership_current(c,public_session):
    """Current public-file hash must still match the reviewed parsed evidence.

    An overnight change stops the pilot until a read-only refresh; it never
    silently substitutes the September or Friday list.
    """
    record=json.loads(Path(c['membership_path']).read_text(encoding='utf-8'))
    response=public_session.get(MEMBERS_URL,timeout=15,allow_redirects=False)
    if response.status_code!=200 or not 0<len(response.content)<=1024*1024:
        raise TradeError('中证最新成分文件暂不可核验，禁止沿用旧股票池试单')
    if hashlib.sha256(response.content).hexdigest()!=record['source_file_sha256']:
        raise TradeError('中证官方成分文件已更新，需先刷新只读证据；不会使用旧表执行')
    return dict(source_url=MEMBERS_URL,source_file_sha256=record['source_file_sha256'])

def money(x):
    if isinstance(x,bool):raise TradeError('布尔值不能充当金额')
    n=Decimal(str(x))
    if not n.is_finite():raise TradeError('金额必须有限')
    return n

def fee_reserve(price,shares=100):
    return (money(price)*shares*Decimal('1.001')+10).quantize(Decimal('.01'),rounding=ROUND_CEILING)

def check_pilot_config(c):
    # The complete executor remains present; this installation's authority is
    # deliberately bounded to the one-lot pilot authorized by the user.
    if (c['strategy'] not in ('pilot_one_lot','pilot_then_targets') or c.get('execution_date')!=EXECUTION_DATE
        or type(c.get('max_new_orders')) is not int or c['max_new_orders']!=1
        or type(c.get('pilot_shares')) is not int or c['pilot_shares']!=100
        or money(c.get('pilot_total_cap_cny',0))!=CAP or money(c['max_order_value'])!=CAP
        or c['allow_sells'] or c['position_floor']['enabled'] or c['prefer_sealed_plan']
        or c['isolate_unknown_orders'] or c['max_attempts_per_code']!=1
        or money(c['excluded_cash_cny'])!=0 or money(c['cash_drift_tolerance_cny'])!=Decimal('.03')):
        raise TradeError('当前试单固定为10月12日、1只1笔100股、含费用1万元；禁止卖出、补仓、重发及旧计划')
    if c.get('pilot_symbol'):
        code=normalize_code(c['pilot_symbol'])
        if code.startswith('SH688'):raise TradeError('试单仅支持普通100股买入单位')
    if c['strategy']=='pilot_then_targets':
        from workflow_rebalance import checked
        checked(c)

def sources(c,base_dir):
    path=Path(base_dir)/'daily_public_pcf/563030_20261009_official.xml'
    raw=path.read_bytes();root=ET.fromstring(raw)
    if root.findtext('FundInstrumentID')!='563030' or root.findtext('TradingDay')!='20261009' or root.findtext('PreTradingDay')!='20261008':
        raise TradeError('PCF基金或交易日期不符')
    rows=root.findall('ComponentList/Component');pcf={}
    if len(rows)!=int(root.findtext('RecordNumber')):raise TradeError('PCF明细不完整')
    for r in rows:
        code=normalize_code(r.findtext('InstrumentID'))
        if code in pcf:raise TradeError('PCF证券重复')
        qty=money(r.findtext('Quantity'))
        if qty<0 or qty!=qty.to_integral_value():raise TradeError('PCF股数异常')
        pcf[code]=dict(name=r.findtext('InstrumentName'),shares=int(qty))
    mpath=Path(c['membership_path']);m=json.loads(mpath.read_text(encoding='utf-8'))
    if (m.get('index_code')!='000905' or not SIGNAL_DATE<=m.get('snapshot_date','')<=EXECUTION_DATE
        or m.get('source_url')!=MEMBERS_URL
        or len(m.get('source_file_sha256',''))!=64):raise TradeError('缺少有效的中证官方最新成分证据')
    members={normalize_code(x['code']):x['name'] for x in m['members']}
    if len(members)!=500 or len(m['members'])!=500:raise TradeError('中证500成分必须完整且无重复')
    evidence=dict(signal_date=SIGNAL_DATE,pcf_sha256=hashlib.sha256(raw).hexdigest(),membership_sha256=digest(m),
        membership_snapshot_date=m['snapshot_date'],membership_fetched_at=m['fetched_at'],membership_source=m['source_url'])
    return pcf,members,evidence

def candidates(c,base_dir,positions,marks):
    pcf,members,evidence=sources(c,base_dir)
    codes=[code for code in positions if code in members and code in pcf and pcf[code]['shares']>0
        and not code.startswith('SH688') and not any(x in (members[code]+pcf[code]['name']).upper() for x in ('ST','退'))
        and code in marks and 0<money(marks[code]) and fee_reserve(marks[code])<=CAP]
    codes.sort(key=lambda code:(money(marks[code]),code))
    if not codes:raise TradeError('现有持仓中没有满足小额试单条件的PCF与中证500共同证券')
    return codes,evidence

def make_plan(c,base_dir,audit,q,now,executable=True):
    check_pilot_config(c);pcf,members,evidence=sources(c,base_dir)
    code=normalize_code(c['pilot_symbol'])
    if code not in pcf or code not in members or pcf[code]['shares']<=0 or code.startswith('SH688'):
        raise TradeError('试单证券不在有效PCF和中证500的交集中')
    if q['code']!=code or q['previous_close']<=0:raise TradeError('参考行情身份或昨收异常')
    live_day=now.astimezone(CN).date().isoformat()==EXECUTION_DATE
    validate_quote(q,dict(quote_max_age_seconds=30 if executable or live_day else 4*86400),now)
    if (executable or live_day) and (now.astimezone(CN).date().isoformat()!=EXECUTION_DATE
        or dt.datetime.fromisoformat(q['timestamp']).astimezone(CN).date().isoformat()!=EXECUTION_DATE):
        raise TradeError('必须使用试单当天新鲜行情')
    price=stable_price_at_ask(q['ask'],0)
    if price is None or abs(money(price)/money(q['previous_close'])-1)>money(c['price_guard']):raise TradeError('试单价格精度或涨跌幅保护')
    needed=fee_reserve(price)
    if needed>CAP or needed>money(audit['cash']):raise TradeError('一手金额含费用预留超过预算或现金')
    held=audit['positions'].get(code,0)
    if max(money(q['last']),money(price))*(held+100)>money(c['max_security_value']):raise TradeError('试单后单股持仓超过限额')
    targets={symbol:dict(code=symbol,quantity=shares,reference_price=1.0) for symbol,shares in audit['positions'].items()}
    targets[code]=dict(code=code,quantity=held+100,reference_price=q['previous_close'])
    return dict(day=EXECUTION_DATE,signal_date=SIGNAL_DATE,strategy='pilot_one_lot',selected_symbol=code,
        selected_name=members[code],incremental_shares=100,held_before=held,target_shares=held+100,limit_price=price,
        reserved_total_cny=str(needed),maximum_total_cny=str(CAP),maximum_new_orders=1,requires_sells=False,
        all_existing_positions_preserved=True,targets=targets,source_evidence=evidence,quote=q,
        preview_only=not executable,created_at=now.isoformat())

def guard_submission(c,state,order,now):
    """Secondary boundary after durable SENDING, immediately before network."""
    check_pilot_config(c)
    attempts=state['attempts'];plan=state.get('pilot_plan',{})
    if (now.astimezone(CN).date().isoformat()!=EXECUTION_DATE or state['day']!=EXECUTION_DATE
        or len(attempts)!=1 or attempts[0]['state']!='SENDING' or attempts[0].get('oid')
        or attempts[0]['order']!=order or order['side']!=1 or type(order['quantity']) is not int or order['quantity']!=100
        or order['code']!=c['pilot_symbol'] or order['code']!=plan.get('selected_symbol') or plan.get('preview_only',True)
        or fee_reserve(order['price'])>CAP or money(attempts[0]['reserve'])!=fee_reserve(order['price'])):
        raise TradeError('写入层一手试单边界不符，禁止重复、减仓或超额提交')
    q=state['quotes'][order['code']];validate_quote(q,dict(quote_max_age_seconds=30),now)
    if q['code']!=order['code'] or dt.datetime.fromisoformat(q['timestamp']).astimezone(CN).date().isoformat()!=EXECUTION_DATE:
        raise TradeError('写入层行情日期或证券不符')
    price=money(order['price'])
    if (price<=0 or price!=price.quantize(Decimal('.01')) or price!=money(stable_price_at_ask(q['ask'],0))
        or not money(q['lower_limit'])<=price<=money(q['upper_limit']) or abs(price/money(order['reference_price'])-1)>money(c['price_guard'])):
        raise TradeError('写入层限价不符')

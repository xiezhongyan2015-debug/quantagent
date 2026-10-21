"""Sealed 2026-09-23 competition simulation rebalance; staged sell verification."""
import datetime as dt
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from run_intraday_watch import ACCOUNT, BASE, WatchSession, Adapter
from watch_engine import save, reserve, in_session, TERMINAL
from batch_engine import validate_quote, stable_price_at_ask
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending, SessionExpired, number, order_payload
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import now_cn

DAY='2026-09-23'
EXCLUDED=Decimal('395.66')


def D(x):return Decimal(str(x))


def reconcile(raw,state):
    positions={};available={};attempts=state['attempts'];baseline=state['baseline'];targets=state['targets']
    if any(not a.get('oid') for a in attempts):raise TradeError('Unknown write outcome; read-only investigation required')
    expected={a['oid']:a for a in attempts}
    if len(expected)!=len(attempts):raise TradeError('Duplicate exact order IDs')
    historical=state['historical'];orders={}
    for row in raw['orders']:
        oid=str(row['orderId']);day=str(row['orderDate']).replace('-','')
        if day==DAY.replace('-',''):
            if oid not in expected or oid in orders:raise TradeError('Foreign or duplicate current order')
            orders[oid]=row
        elif day<DAY.replace('-','') and oid in historical:
            h=historical[oid]
            if (normalize_code(str(row['secCode']))!=h['code'] or int(row['drt'])!=h['side']
                    or number(row['orderCount'],True)!=h['quantity'] or D(row['orderPrice'])!=D(h['price'])
                    or number(row['tradeCount'],True)!=h['filled'] or str(row['status'])!=h['state']):
                raise TradeError('Historical order changed')
        elif day<DAY.replace('-','') and str(row['status'])=='8' and number(row['tradeCount'],True)==0:
            if any(str(d.get('orderId'))==oid for d in raw['deals']):raise TradeError('Unrecognized old deal')
        else:raise TradeError('Unrecognized historical/future order')
    if set(expected)-set(orders):raise FillSnapshotPending('Acknowledged exact order not visible')
    newdeals=[]
    for row in raw['deals']:
        oid=str(row.get('orderId'))
        if oid in expected:newdeals.append(row)
        elif oid in historical:
            h=historical[oid]
            if normalize_code(str(row['secCode']))!=h['code'] or int(row['drt'])!=h['side']:raise TradeError('Historical deal identity changed')
        else:raise TradeError('Unrecognized deal')
    calculated=dict(baseline['positions']);states={};buypending={};sellpending={};buycash=D(0);sellcash=D(0);fees=D(0)
    for oid,a in expected.items():
        o=a['order'];r=orders[oid];filled=number(r['tradeCount'],True);status=str(r['status'])
        if (normalize_code(str(r['secCode']))!=o['code'] or int(r['drt'])!=o['side'] or number(r['orderCount'],True)!=o['quantity']
                or D(r['orderPrice'])!=D(o['price']) or filled>o['quantity'] or filled<a.get('filled',0)
                or status not in {str(i) for i in range(1,10)} or status=='4' and filled!=o['quantity']
                or a.get('terminal') and (status!=a['state'] or filled!=a['filled'])):raise TradeError('Exact order identity/state mismatch')
        linked=[d for d in newdeals if str(d['orderId'])==oid]
        if sum(number(d['tradeCount'],True) for d in linked)!=filled:raise FillSnapshotPending('Order/deal fills disagree')
        for d in linked:
            px=D(d['price']);qty=number(d['tradeCount'],True)
            if normalize_code(str(d['secCode']))!=o['code'] or int(d['drt'])!=o['side'] or px<=0:raise TradeError('Deal identity/price mismatch')
            if o['side']==1 and px>D(o['price']) or o['side']==2 and px<D(o['price']):raise TradeError('Deal outside accepted limit')
            fee=sum(D(number(d[k])) for k in ('qsYj','ghCost','yhs'));fees+=fee
            if o['side']==1:buycash+=px*qty+fee
            else:sellcash+=px*qty-fee
        c=o['code'];calculated[c]=calculated.get(c,0)+filled*(1 if o['side']==1 else -1)
        if status not in TERMINAL:
            pending=buypending if o['side']==1 else sellpending
            pending[c]=pending.get(c,0)+o['quantity']-filled
        states[oid]=dict(state=status,filled=filled,terminal=status in TERMINAL)
    if any(n<0 for n in calculated.values()):raise TradeError('Negative inventory')
    for row in raw['holds']:
        c=normalize_code(str(row['secCode']));n=number(row['count'],True);v=number(row['availCount'],True)
        if c in positions or v>n:raise TradeError('Invalid holding/sellable snapshot')
        if n:positions[c]=n;available[c]=v
    if positions!={c:n for c,n in calculated.items() if n}:raise FillSnapshotPending('Inventory and linked fills disagree')
    for c,pending in buypending.items():
        if positions.get(c,0)+pending>targets[c]['quantity']:raise TradeError('Pending buys exceed target')
    for c,pending in sellpending.items():
        if positions.get(c,0)-pending<targets[c]['quantity']:raise TradeError('Pending sells exceed reduction')
    b=raw['balance'];cash=D(number(b['availBalance']))-EXCLUDED;frozen=D(number(b['frozenMoney']))
    if cash<0:raise TradeError('Excluded cash cannot be spent')
    if abs(D(baseline['cash'])+sellcash-buycash-cash-frozen)>D('.03'):raise FillSnapshotPending('Cash/deal/freeze disagree')
    if not buypending and not sellpending and frozen:raise FillSnapshotPending('Frozen balance not released')
    occupied=D(baseline['paid'])+buycash-sellcash+frozen
    if occupied>D(5500000):raise TradeError('Occupied capital cap exceeded')
    market=number(b['totalPosValue']);nav=number(b['totalAssets'])
    return dict(positions=positions,sellable=available,buy_pending=buypending,sell_pending=sellpending,
        pending={**{c:n for c,n in buypending.items()},**{c:-n for c,n in sellpending.items()}},
        states=states,cash=str(cash),account_cash_cny=str(cash+EXCLUDED),frozen=str(frozen),paid=str(occupied-frozen),
        buy_cash=str(buycash),sell_net_cash=str(sellcash),fees=str(fees),market_value=market,total_assets=nav,stock_fraction=market/nav,
        missing={c:t['quantity']-positions.get(c,0) for c,t in targets.items() if t['quantity']!=positions.get(c,0)})


def valid_drop_review(path,code,reference,now,pcf_sha256):
    if not path.exists():return False
    obj=json.loads(path.read_text(encoding='utf-8')).get(code,{})
    try:
        start=dt.datetime.fromisoformat(obj['reviewed_at']);end=dt.datetime.fromisoformat(obj['expires_at'])
        return (obj['execution_date']==DAY and obj['code']==code and obj['pcf_sha256']==pcf_sha256 and D(obj['reference_price'])==D(reference)
            and obj['status']=='cleared' and obj['announcements_checked'] is True and obj['corporate_actions_checked'] is True
            and obj['quote_verified'] is True and len(obj['evidence_urls'])>0 and start.tzinfo is not None and end.tzinfo is not None
            and dt.timedelta(0)<end-start<=dt.timedelta(minutes=30) and start<=now<=end)
    except (KeyError,TypeError,ValueError):return False


class PlanSession(WatchSession):
    """Transport permits only one armed, reconciled, sealed-plan instruction."""
    def __init__(self,path):
        super().__init__(path,DAY,500000);self.watcher=None

    def request(self,method,url,**kw):
        if method.upper()=='GET':return super().request(method,url,**kw)
        arm=self.armed;self.armed=None;now=now_cn();p=kw.get('json') or {};endpoint=url[len(BASE):] if url.startswith(BASE) else ''
        if (method.upper()!='POST' or kw.get('allow_redirects') is not False or not arm or self.writes_suspended
                or now.date().isoformat()!=DAY or p.get('accId')!=ACCOUNT):raise TradeError('Unarmed or expired simulation write')
        kind,o=arm
        if p.get('stkCode')!=o['code'][2:] or p.get('mktCode')!=('1' if o['code'].startswith('SH') else '0'):raise TradeError('Armed identity mismatch')
        if kind=='submit':
            if endpoint!='AguTrade/SpoOrder' or not in_session(now,DAY):raise TradeError('Submission window invalid')
            if p.get('orderDrt')!=o['side'] or p.get('volume')!=o['quantity'] or D(p['price'])!=D(o['price']):raise TradeError('Armed order mismatch')
            self.watcher.validate_order(o,now,sending=True)
            self.counter+=1;self.sent.add(self.counter)
        elif kind=='cancel':
            oid=str(p.get('orderId'))
            if endpoint!='AguTrade/SpoCancel' or oid!=o['oid'] or oid in self.cancelled or now.time()>=dt.time(15):raise TradeError('Wrong/duplicate cancellation')
            self.cancelled.add(oid)
        else:raise TradeError('Unknown write kind')
        event=dict(at=now.isoformat(),method=method.upper(),endpoint=endpoint)
        try:
            # requests.Session, not the old buy-only transport. Its old scope is unchanged.
            import requests
            r=requests.Session.request(self,method,url,**kw);event['http_status']=r.status_code
            try:event['business_code']=r.json().get('Code')
            except ValueError:pass
            return r
        except Exception as error:event['error_type']=type(error).__name__;raise
        finally:
            with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n');f.flush()


class PlanBroker:
    """Read adapter plus narrow plan writes; sell support is staged, never falsely certified."""
    def __init__(self,reader,transport):self.reader=reader;self.session=transport
    def _rows(self,path):return self.reader._rows(path)
    def _request(self,*args,**kwargs):return self.reader._request(*args,**kwargs)
    def post(self,path,payload):
        payload.update(uToken=os.environ['CHOICE_UTOKEN'],cToken=os.environ['CHOICE_CTOKEN'],uid=os.environ['CHOICE_UID'])
        try:
            r=self.session.post(BASE+path,json=payload,timeout=15,allow_redirects=False)
            if r.status_code!=200:raise TradeError('HTTP write response uncertain')
            obj=r.json()
        except (ValueError, __import__('requests').RequestException):raise TradeError('Write response uncertain; no retry') from None
        if str(obj.get('Code'))=='207':raise SessionExpired(request_was_write=True)
        if str(obj.get('Code'))!='0':raise TradeError('Write business response requires exact audit')
        return obj
    def submit(self,order):
        result=self.post('AguTrade/SpoOrder',order_payload(ACCOUNT,order['code'],order['side'],order['quantity'],order['price']))
        oid=result.get('Data',{}).get('orderID')
        if isinstance(oid,bool) or not isinstance(oid,(int,str)) or not str(oid).strip():raise TradeError('No exact acknowledgment ID')
        return str(oid)
    def cancel(self,oid,order):
        status=self.reader.order_status(oid,order)
        if status['state'] in TERMINAL or status['state'] in ('5','6'):return
        if status['state']=='10':raise TradeError('Failed cancellation requires review')
        self.post('AguTrade/SpoCancel',dict(accId=ACCOUNT,mktCode='1' if order['code'].startswith('SH') else '0',stkCode=order['code'][2:],orderId=oid))


class Watcher:
    def __init__(self,path,state,broker,quote_provider,clock,sleeper):
        self.path=Path(path);self.state=state;self.broker=broker;self.quote=quote_provider;self.clock=clock;self.sleep=sleeper;self.last_submit=None
        self.flush()
    def flush(self):save(self.path,self.state)
    def audit(self):
        a=reconcile(self.broker.read(),self.state)
        for attempt in self.state['attempts']:
            attempt.update(a['states'][attempt['oid']])
            if attempt['terminal'] and not attempt.get('terminal_at'):attempt['terminal_at']=self.clock().isoformat()
        self.state['last_audit']=dict(a,checked_at=self.clock().isoformat());self.last_successful_audit_monotonic=time.monotonic();self.flush()
        return self.state['last_audit']
    def cancel_known(self,force=False):
        for a in self.state['attempts']:
            if not a.get('oid') or a.get('terminal'):continue
            if a.get('cancel_requested_at'):
                if (self.clock()-dt.datetime.fromisoformat(a['cancel_requested_at'])).total_seconds()>60:raise TradeError('Cancel not confirmed within 60s')
                continue
            if not force and (self.clock()-dt.datetime.fromisoformat(a['submitted_at'])).total_seconds()<60:continue
            a['cancel_requested_at']=self.clock().isoformat();self.flush()
            try:self.broker.cancel(a['oid'],a['order'])
            except SessionExpired as error:
                if not error.request_was_write:a['cancel_not_sent_auth_at']=a.pop('cancel_requested_at')
                self.flush();raise
    def validate_order(self,o,now,sending=False):
        s=self.state;a=s['last_audit'];c=o['code'];q=s['quotes'][c];target=s['targets'][c]['quantity'];reference=s['targets'][c]['reference_price']
        prior=s['attempts']
        if sending:
            if not prior or prior[-1].get('oid') or prior[-1]['state']!='SENDING' or prior[-1]['order']!=o:raise TradeError('No matching durable sending intent')
            prior=prior[:-1]
        if any(not x.get('oid') for x in prior):raise TradeError('Unresolved earlier submission')
        if (not in_session(now,DAY) or self.path.with_name('STOP').exists() or o['quantity']<=0
                or D(o['reference_price'])!=D(reference) or not 0<=(now-dt.datetime.fromisoformat(a['checked_at'])).total_seconds()<=30):raise TradeError('Order scope/audit expired')
        validate_quote(q,{'quote_max_age_seconds':30},now)
        if abs(D(q['previous_close'])-D(reference))>D('.011'):raise TradeError('Corporate-action reference review required')
        if a['pending'].get(c):raise TradeError('Existing order excludes new order')
        if D(o['price'])*o['quantity']>500000:raise TradeError('Single order cap')
        if o['side']==2:
            if o['quantity']>a['positions'].get(c,0)-target or o['quantity']>a['sellable'].get(c,0):raise TradeError('Sell exceeds target or T+1 availability')
            if c.startswith('SH688') and o['quantity']<200 and o['quantity']!=a['positions'].get(c,0):raise TradeError('STAR sell must be at least 200 shares unless liquidating residual holding')
            if o['price']!=q['bid']:raise TradeError('Sell must use verified bid')
            if not s.get('sell_contract_verified_at'):
                sells=[x for x in prior if x['order']['side']==2]
                if o['quantity']!=100 or c.startswith('SH688') or any(not x.get('terminal') or x.get('filled') for x in sells):raise TradeError('Only first plan-required 100-share main-board sell can verify contract')
        elif o['side']==1:
            if any(a['positions'].get(k,0)>t['quantity'] for k,t in s['targets'].items()):raise TradeError('Complete reductions before buying')
            if s['requires_sell_orders'] and not s.get('sell_contract_verified_at'):raise TradeError('Sell path not verified')
            if o['quantity']>target-a['positions'].get(c,0):raise TradeError('Buy exceeds target')
            if o['price']!=q['ask'] or D(o['price'])>D(reference)*D('1.03'):raise TradeError('Buy chase/quote cap')
            if (D(q['ask'])-D(q['bid']))/D(q['ask'])>D('.01'):raise TradeError('Abnormal spread; no automatic buy')
            if D(q['bid'])<=D(q['lower_limit'])+D('.01') or D(o['price'])<=D(q['lower_limit'])+D('.01'):raise TradeError('Near lower limit; no automatic buy')
            if D(o['price'])<D(reference)*D('.97') and not valid_drop_review(self.path.with_name('drop_reviews.json'),c,reference,now,s['pcf_sha256']):raise TradeError('Drop over 3% requires fresh evidenced review')
            need=reserve(D(o['price'])*o['quantity'])
            pending_value=sum(D(x['order']['price'])*(x['order']['quantity']-x.get('filled',0)) for x in prior if not x.get('terminal') and x['order']['side']==1)
            if (D(a['paid'])+D(a['frozen'])+need>5500000 or D(a['market_value'])+pending_value+need>5500000
                    or need>D(a['cash']) or D(max(q['last'],o['price']))*(a['positions'].get(c,0)+o['quantity'])>500000):raise TradeError('Capital or single-security guard')
        else:raise TradeError('Unsupported direction')
    def tick(self,stopping=False):
        now=self.clock()
        if now.date().isoformat()!=DAY:raise TradeError('Execution date expired')
        a=self.audit();stop=stopping or now.time()>=dt.time(14,50) or self.path.with_name('STOP').exists()
        self.cancel_known(force=stop or not in_session(now,DAY))
        if stop:return 'stopped'
        if not in_session(now,DAY):return 'session_paused'
        if not self.state.get('sell_contract_verified_at'):
            candidate=next((x for x in self.state['attempts'] if x['order']['side']==2 and x.get('terminal') and x.get('filled',0)>0),None)
            if candidate:
                self.sleep(5);a=self.audit()
                self.state['sell_contract_verified_at']=self.clock().isoformat()
                self.state['sell_validation']=dict(order_id_tail=candidate['oid'][-4:],code=candidate['order']['code'],filled=candidate['filled'],checks='Exact direction, order ID, fill price/quantity, inventory reduction, fees and net cash; two snapshots')
                self.flush()
        reductions=sorted(c for c,t in self.state['targets'].items() if a['positions'].get(c,0)>t['quantity'])
        side=2 if reductions else 1
        codes=reductions or sorted(c for c,t in self.state['targets'].items() if a['positions'].get(c,0)<t['quantity'])
        for c in codes:
            history=[x for x in self.state['attempts'] if x['order']['code']==c]
            if any(not x.get('terminal') for x in history):continue
            if len(history)>=3:self.state['deferred'][c]='attempt_limit';continue
            if history and history[-1]['state']=='9':raise TradeError('Rejected order requires review')
            if history and (self.clock()-dt.datetime.fromisoformat(history[-1]['terminal_at'])).total_seconds()<60:continue
            if self.last_submit:
                wait=5-(self.clock()-self.last_submit).total_seconds()
                if wait>0:self.sleep(wait)
            if not in_session(self.clock(),DAY) or self.path.with_name('STOP').exists():break
            a=self.audit();self.cancel_known()
            if a['pending'].get(c):continue
            target=self.state['targets'][c]['quantity'];held=a['positions'].get(c,0)
            quantity=(held-target) if side==2 else (target-held)
            if quantity<=0:continue
            if side==2:
                quantity=min(quantity,a['sellable'].get(c,0))
                if not self.state.get('sell_contract_verified_at'):quantity=min(quantity,100)
                if quantity<=0:self.state['deferred'][c]='sellable_unavailable';continue
            elif quantity%100 or quantity<(200 if c.startswith('SH688') else 100):self.state['deferred'][c]='untradeable_lot';continue
            try:
                q=self.quote(c)
                if q['code']!=c:raise TradeError('Quote code mismatch')
                validate_quote(q,{'quote_max_age_seconds':30},self.clock());self.state['quotes'][c]=q
                price=stable_price_at_ask(q['bid'] if side==2 else q['ask'],0)
                if price is None:raise TradeError('Price precision unsafe')
                order=dict(code=c,side=side,quantity=quantity,price=price,reference_price=self.state['targets'][c]['reference_price'])
                order_payload('simulation',c,side,quantity,price);self.validate_order(order,self.clock())
            except Exception as error:
                self.state['deferred'][c]=str(error) if isinstance(error,TradeError) else type(error).__name__
                self.state['quote_failures'][c]=dict(at=self.clock().isoformat(),reason=self.state['deferred'][c]);self.flush();continue
            attempt=dict(order=order,state='SENDING',filled=0,submitted_at=self.clock().isoformat())
            self.state['attempts'].append(attempt);self.state['deferred'].pop(c,None);self.flush()
            try:attempt.update(oid=self.broker.submit(order),state='ACK')
            except Exception as error:
                attempt.update(state='UNKNOWN',error_type=type(error).__name__);self.flush();raise
            self.last_submit=self.clock();self.flush()
            if side==2 and not self.state.get('sell_contract_verified_at'):return 'verifying_first_planned_sell'
        self.flush();return 'selling_first' if side==2 else 'buying_remaining'


def public_report(w,status,reason=None):
    s=w.state;a=s.get('last_audit',{})
    return dict(at=now_cn().isoformat(),execution_date=DAY,account_tail=ACCOUNT[-4:],status=status,reason=reason,
        submitted=len(s['attempts']),buy_orders=sum(x['order']['side']==1 for x in s['attempts']),sell_orders=sum(x['order']['side']==2 for x in s['attempts']),
        unknown_outcomes=sum(not x.get('oid') for x in s['attempts']),filled_orders=sum(x.get('state')=='4' for x in s['attempts']),
        cancelled_orders=sum(x.get('state') in ('7','8') for x in s['attempts']),remaining=a.get('missing'),pending=a.get('pending'),
        cash_cny=a.get('account_cash_cny'),frozen_cny=a.get('frozen'),stock_fraction=a.get('stock_fraction'),market_value_cny=a.get('market_value'),
        actual_fees_cny=a.get('fees'),last_verified_at=a.get('checked_at'),deferred=s['deferred'],
        sell_validation=s.get('sell_validation'),authentication_recovery=s.get('auth_recovery'),
        last_snapshot_mismatch=s.get('last_snapshot_mismatch'))

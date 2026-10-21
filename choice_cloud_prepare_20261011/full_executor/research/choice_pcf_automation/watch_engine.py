"""Durable, buy-only PCF gap watcher. No credentials or network setup here."""
import datetime as dt
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from batch_engine import stable_price_at_ask, validate_quote
from quanta_agents.factor_lab_a.choice_pcf import TradeError, SessionExpired, FillSnapshotPending, number, order_payload
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import CN

TERMINAL={'4','7','8','9'}


def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    # Windows readers (including the console UI) may briefly hold the destination
    # without delete sharing. Retry only atomic replacement, never broker writes.
    for attempt in range(10):
        try:os.replace(temporary,path);break
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32,33) or attempt==9:raise
            time.sleep(.05)


def in_session(now, day):
    now=now.astimezone(CN)
    return now.date().isoformat()==day and (dt.time(9,35)<=now.time()<dt.time(11,25) or dt.time(13,5)<=now.time()<dt.time(14,50))


def reserve(amount):
    # Risk reserve remains conservative; research transaction cost is 0.0002.
    return (Decimal(str(amount))*Decimal('1.001')+Decimal('10')).quantize(Decimal('.01'),rounding='ROUND_CEILING')


def reconcile(raw, prior, attempts, targets, day, baseline=None, historical=None, capital_limit=2000000):
    """Exact order/deal/position/cash reconciliation, including partial cancellations."""
    if any(not a.get('oid') for a in attempts):raise TradeError('Unknown submission outcome: read-only investigation required')
    expected=dict(prior)
    for a in attempts:
        if a['oid'] in expected:raise TradeError('Duplicate acknowledged order ID')
        expected[a['oid']]=a['order']
    baseline=baseline or dict(positions={},cash=10000000,paid=0)
    historical=historical or {}
    all_deals=raw['deals'];deals=[];holds=raw['holds'];balance=raw['balance'];orders=[]
    for d in all_deals:
        oid=str(d.get('orderId'))
        if oid not in historical:deals.append(d);continue
        o=historical[oid]
        if (normalize_code(str(d['secCode']))!=o['code'] or int(d['drt'])!=1 or
            not 0<Decimal(str(d['price']))<=Decimal(str(o['price']))):raise TradeError('Historical deal changed')
    for oid,o in historical.items():
        linked=[d for d in all_deals if str(d.get('orderId'))==oid]
        if linked and sum(number(d['tradeCount'],True) for d in linked)!=o['filled']:raise TradeError('Historical deal quantity changed')
    # Choice dataRange=1 includes prior-day diagnostic cancellations. They have
    # no holdings/cash effect only when zero-fill, cancelled, and without deals.
    for r in raw['orders']:
        date=str(r['orderDate']).replace('-','')
        if date==day.replace('-',''):orders.append(r)
        elif date<day.replace('-','') and str(r['orderId']) in historical:
            o=historical[str(r['orderId'])]
            if (normalize_code(str(r['secCode']))!=o['code'] or int(r['drt'])!=1 or
                number(r['orderCount'],True)!=o['quantity'] or Decimal(str(r['orderPrice']))!=Decimal(str(o['price'])) or
                number(r['tradeCount'],True)!=o['filled'] or str(r['status'])!=o['state'] or o['state'] not in TERMINAL):raise TradeError('Historical order changed or pending')
        elif date<day.replace('-','') and len(date)==8 and date.isdigit() and str(r['status'])=='8' and number(r['tradeCount'],True)==0 and not any(str(d.get('orderId'))==str(r['orderId']) for d in all_deals):
            continue
        else:raise TradeError('Unresolved historical or future order')
    ids=[str(r['orderId']) for r in orders]
    if len(ids)!=len(set(ids)):raise TradeError('Duplicate server order ID')
    if set(ids)-set(expected):raise TradeError('Unrecognized account order')
    if set(expected)-set(ids):raise FillSnapshotPending('Acknowledged exact order not visible yet')
    if any(str(x.get('orderId')) not in expected for x in deals):raise TradeError('Unrecognized account deal')
    by_id={str(x['orderId']):x for x in orders};positions={};fills={};pending={};states={};paid=Decimal(0);fees=Decimal(0)
    previous={a['oid']:a for a in attempts}
    for oid,o in expected.items():
        r=by_id[oid];q=number(r['tradeCount'],True);state=str(r['status'])
        if (normalize_code(str(r['secCode']))!=o['code'] or int(r['drt'])!=1 or
            number(r['orderCount'],True)!=o['quantity'] or Decimal(str(r['orderPrice']))!=Decimal(str(o['price']))):
            raise TradeError('Exact order identity/limit mismatch')
        if state not in {str(i) for i in range(1,11)} or state=='10':raise TradeError('Unknown state or failed cancellation')
        if q>o['quantity'] or (state=='4' and q!=o['quantity']):raise TradeError('Invalid cumulative fill')
        if oid in prior and (q!=o['filled'] or state!=o['state']):raise TradeError('Previously verified order changed')
        if oid in previous and q<previous[oid].get('filled',0):raise TradeError('Cumulative fills decreased')
        linked=[x for x in deals if str(x.get('orderId'))==oid]
        if any(normalize_code(str(x['secCode']))!=o['code'] or int(x['drt'])!=1 for x in linked):raise TradeError('Wrong linked deal identity')
        total=sum(number(x['tradeCount'],True) for x in linked)
        if total!=q:raise FillSnapshotPending('Order/deal quantity snapshots disagree')
        for x in linked:
            price=Decimal(str(x['price']));qty=number(x['tradeCount'],True)
            if not price.is_finite() or price<=0 or price>Decimal(str(o['price'])):raise TradeError('Buy deal outside accepted limit')
            fee=sum(Decimal(str(number(x[k]))) for k in ('qsYj','ghCost','yhs'))
            fees+=fee;paid+=price*qty+fee
        fills[o['code']]=fills.get(o['code'],0)+q
        if state not in TERMINAL:pending[o['code']]=pending.get(o['code'],0)+o['quantity']-q
        states[oid]=dict(state=state,filled=q,terminal=state in TERMINAL)
    for r in holds:
        c=normalize_code(str(r['secCode']));n=number(r['count'],True)
        if c in positions:raise TradeError('Duplicate inventory')
        if n:positions[c]=n
    expected_positions=dict(baseline['positions'])
    for c,q in fills.items():expected_positions[c]=expected_positions.get(c,0)+q
    if positions!={c:q for c,q in expected_positions.items() if q}:raise FillSnapshotPending('Inventory and exact linked fills disagree')
    if any(positions.get(c,0)+pending.get(c,0)>targets.get(c,{}).get('quantity',0) for c in set(positions)|set(pending)):raise TradeError('Held plus pending exceeds PCF target')
    cash=Decimal(str(number(balance['availBalance'])));frozen=Decimal(str(number(balance['frozenMoney'])))
    if abs(Decimal(str(baseline['cash']))-cash-frozen-paid)>Decimal('.03'):raise FillSnapshotPending('Cash/deal/freeze snapshots disagree')
    daily_paid=paid;paid+=Decimal(str(baseline['paid']))
    if paid+frozen>capital_limit:raise TradeError('Actual occupied capital exceeds approved cap')
    if not pending and frozen>0:raise FillSnapshotPending('Funds not released yet')
    market_value=number(balance['totalPosValue']) if 'totalPosValue' in balance else None
    assets=number(balance['totalAssets']) if 'totalAssets' in balance else None
    return dict(positions=positions,pending=pending,states=states,paid=str(paid),daily_paid=str(daily_paid),fees=str(fees),cash=str(cash),frozen=str(frozen),
                market_value=market_value,total_assets=assets,stock_fraction=market_value/assets if market_value is not None and assets and assets>0 else None,
                missing={c:o['quantity']-positions.get(c,0)-pending.get(c,0) for c,o in targets.items() if o['quantity']>positions.get(c,0)+pending.get(c,0)})


class Watcher:
    def __init__(self,path,state,broker,quote_provider,clock,sleeper):
        self.path=Path(path);self.state=state;self.broker=broker;self.quote=quote_provider;self.clock=clock;self.sleep=sleeper
        self.last_submit=None
        self.flush()

    def flush(self):save(self.path,self.state)

    def audit(self):
        result=reconcile(self.broker.read(),self.state['prior'],self.state['attempts'],self.state['targets'],self.state['day'],
                         self.state.get('baseline'),self.state.get('historical'),self.state.get('capital_limit',2000000))
        for a in self.state['attempts']:
            current=result['states'][a['oid']]
            a.update(current)
            if current['terminal'] and not a.get('terminal_at'):a['terminal_at']=self.clock().isoformat()
        self.state['last_audit']=dict(result,checked_at=self.clock().isoformat());self.flush()
        self.last_successful_audit_monotonic=time.monotonic()
        return result

    def cancel_known(self, force=False):
        """One cancel request per exact order. Never infer success from its receipt."""
        for a in self.state['attempts']:
            if not a.get('oid') or a.get('terminal') or a.get('cancel_requested_at'):continue
            age=(self.clock()-dt.datetime.fromisoformat(a['submitted_at'])).total_seconds()
            if not force and age<self.state['fill_timeout_seconds']:continue
            a['cancel_requested_at']=self.clock().isoformat();self.flush()
            try:self.broker.cancel(a['oid'],a['order'])
            except SessionExpired as error:
                # cancel() first queries exact order status. A failed GET cannot
                # have submitted the cancellation; preserve that distinction.
                if not error.request_was_write:
                    a['cancel_not_sent_auth_at']=a.pop('cancel_requested_at')
                a['cancel_error_type']=type(error).__name__;self.flush();raise
            except Exception as error:
                a['cancel_error_type']=type(error).__name__;self.flush();raise

    def tick(self, stopping=False):
        now=self.clock()
        if now.date().isoformat()!=self.state['day']:raise TradeError('Watcher expired')
        audit=self.audit()
        for a in self.state['attempts']:
            if not a.get('terminal') and a.get('cancel_requested_at') and (now-dt.datetime.fromisoformat(a['cancel_requested_at'])).total_seconds()>60:
                raise TradeError('Cancellation not confirmed within deadline; review required')
        stop=stopping or now.time()>=dt.time(14,50) or self.path.with_name('STOP').exists()
        self.cancel_known(force=stop or not in_session(now,self.state['day']))
        if stop:return 'stopped'
        if not in_session(now,self.state['day']):return 'session_paused'
        for code in sorted(audit['missing']):
            attempts=[a for a in self.state['attempts'] if a['order']['code']==code]
            # A live order excludes replacements even if there is another apparent gap.
            if any(not a.get('terminal') for a in attempts):continue
            if code in self.state['blocked_codes']:
                self.state['deferred'][code]='identity_verification_required';continue
            if len(attempts)>=self.state['max_attempts_per_code']:
                self.state['deferred'][code]='attempt_limit';continue
            if attempts and attempts[-1]['state']=='9':
                self.state['deferred'][code]='rejected_requires_review';continue
            if attempts and (self.clock()-dt.datetime.fromisoformat(attempts[-1]['terminal_at'])).total_seconds()<self.state['retry_cooldown_seconds']:continue
            if self.last_submit:
                wait=5-(self.clock()-self.last_submit).total_seconds()
                if wait>0:self.sleep(wait)
            if not in_session(self.clock(),self.state['day']) or self.path.with_name('STOP').exists():break
            # Fresh account and then fresh quote for EVERY new submission.
            current=self.audit();self.cancel_known();quantity=current['missing'].get(code,0)
            for a in self.state['attempts']:
                if not a.get('terminal') and a.get('cancel_requested_at') and (self.clock()-dt.datetime.fromisoformat(a['cancel_requested_at'])).total_seconds()>60:
                    raise TradeError('Cancellation not confirmed within deadline; review required')
            if current['pending'].get(code,0) or not quantity:continue
            minimum=200 if code.startswith('SH688') else 100
            if quantity<minimum or quantity%100:
                self.state['deferred'][code]='untradeable_remaining_lot';continue
            try:
                q=self.quote(code)
                if q.get('code')!=code:raise TradeError('Quote security mismatch')
                validate_quote(q,{'quote_max_age_seconds':30},self.clock())
            except Exception as error:
                self.state['deferred'][code]='quote_unusable:'+type(error).__name__;continue
            self.state['quotes'][code]=q
            reference=Decimal(str(self.state['targets'][code]['reference_price']))
            if abs(Decimal(str(q['ask']))/reference-1)>Decimal('.03'):
                self.state['deferred'][code]='outside_3pct';continue
            price=stable_price_at_ask(q['ask'],self.state.get('padding_ticks',{}).get(code,0))
            if price is None:
                self.state['deferred'][code]='price_precision';continue
            if abs(Decimal(str(price))/reference-1)>Decimal('.03'):
                self.state['deferred'][code]='outside_3pct';continue
            amount=Decimal(str(price))*quantity;need=reserve(amount)
            prior_reserve=Decimal(str(self.state['carried_reserve']))+sum(Decimal(a['reserve']) for a in self.state['attempts'])
            cap=Decimal(str(self.state.get('capital_limit',2000000)))
            security_cap=Decimal(str(self.state.get('max_security_value',2000000)))
            security_cost=Decimal(str(self.state.get('baseline_security_reserve',{}).get(code,0)))+sum(Decimal(a['reserve']) for a in attempts)+need
            security_mark=Decimal(str(max(q['last'],price)))*(current['positions'].get(code,0)+current['pending'].get(code,0)+quantity)
            if security_cost>security_cap or security_mark>security_cap:
                self.state['deferred'][code]='single_security_guard';continue
            if amount>self.state.get('max_order_value',100000) or prior_reserve+need>cap or Decimal(current['paid'])+Decimal(current['frozen'])+need>cap or need>Decimal(current['cash']):
                self.state['deferred'][code]='capital_guard';continue
            order=dict(code=code,side=1,quantity=quantity,price=price,reference_price=float(reference))
            order_payload('simulation',code,1,quantity,price)
            if not in_session(self.clock(),self.state['day']):break
            validate_quote(q,{'quote_max_age_seconds':30},self.clock())
            attempt=dict(order=order,reserve=str(need),state='SENDING',filled=0,submitted_at=self.clock().isoformat())
            self.state['attempts'].append(attempt);self.state['deferred'].pop(code,None);self.flush()
            try:
                oid=self.broker.submit(order)
                attempt.update(oid=str(oid),state='ACK')
            except Exception as error:
                attempt.update(state='UNKNOWN',error_type=type(error).__name__);self.flush()
                if isinstance(error,SessionExpired):raise
                raise TradeError('Submission outcome unknown; stop without retry') from None
            self.last_submit=self.clock();self.flush()
        self.flush()
        return 'watching'

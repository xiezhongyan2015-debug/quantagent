"""Submit acknowledged buys without waiting for fills, then reconcile together.

Only used by the separately authorized current-day simulated PCF completion.
No automatic resubmission. Durable aggregate reservations precede every POST.
"""
import copy
import datetime as dt
import time
from pathlib import Path
from decimal import Decimal

from quanta_agents.factor_lab_a.choice_pcf import TradeError, OrderNotVisible, FillSnapshotPending, number, order_payload
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import now_cn, validate_session, calendar, order_cash_reserve, write_json, order_spacing

PATHS=('AguTrade/SpoOrders','AguTrade/SpoHold','AguTrade/SpoDeal')


def stable_price_at_ask(ask,padding_ticks=0):
    """Avoid the observed float-times-100 truncation; never disguise quote values."""
    scaled=Decimal(str(ask))*100
    if scaled!=scaled.to_integral_value() or padding_ticks not in (0,1):
        raise TradeError('Invalid exact A-share tick/padding permission')
    for offset in range(padding_ticks+1):
        cents=int(scaled)+offset
        value=float(Decimal(cents)/100)
        if int(value*100)==cents:return value
    return None


def validate_quote(q,cfg,now):
    stamp=dt.datetime.fromisoformat(q['timestamp'])
    if stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=cfg['quote_max_age_seconds']:
        raise TradeError('Missing, future or stale batch quote')
    if q['tradable']!=1 or q['is_st']!=0:raise TradeError('Halted or ST batch security')
    for k in ('bid','ask','last','lower_limit','upper_limit'):
        if number(q[k])<=0:raise TradeError('Invalid batch quote price')
    if not q['lower_limit']<=q['bid']<=q['ask']<=q['upper_limit']:
        raise TradeError('Invalid batch quote band')


def read_batch(broker,items,initial_positions,prior_states,day):
    cache={p:broker._rows(p) for p in PATHS}
    balance=broker._request('AguTrade/SpoBalInfo',params={'accId':broker.account})['Data']
    # Full available-cash decrease includes filled cash and pending-order freeze.
    if 10000000-number(balance['availBalance'])>2000000:
        raise TradeError('Actual cash occupation exceeds the approved CNY2m cap')
    known=set(prior_states)|{oid for oid,o in items}
    for row in cache[PATHS[0]]:
        if str(row['orderDate']).replace('-','')!=day.replace('-',''):continue
        oid=str(row['orderId'])
        if oid not in known:raise TradeError('Unrecognized account order during batch')
        if oid in prior_states and (str(row['status']),number(row['tradeCount'],True))!=prior_states[oid]:
            raise TradeError('Previously reconciled order changed during batch')
    for row in cache[PATHS[2]]:
        if str(row.get('orderId')) not in known:raise TradeError('Unrecognized account deal during batch')
    maximum=dict(initial_positions)
    for oid,order in items:
        if order['code'] in maximum:raise TradeError('Repeated security in approved batch')
        maximum[order['code']]=order['quantity']
    positions={}
    for row in cache[PATHS[1]]:
        code=normalize_code(str(row['secCode']));held=number(row['count'],True)
        if code in positions or held>maximum.get(code,0):raise TradeError('Unexpected batch inventory')
        if code in initial_positions and held!=initial_positions[code]:raise TradeError('Prior verified inventory changed')
        if held:positions[code]=held
    if any(positions.get(c,0)!=n for c,n in initial_positions.items()):raise TradeError('Prior verified inventory missing')
    cached=copy.copy(broker)
    cached._rows=lambda path:cache[path]
    results=[]
    for oid,order in items:
        try:
            progress=cached.progress(oid,order)
            # Explicit order linkage is observable in the live simulation schema.
            linked=[r for r in cache[PATHS[2]] if str(r.get('orderId'))==oid]
            if any(normalize_code(str(r['secCode']))!=order['code'] or int(r['drt'])!=1 for r in linked):
                raise TradeError('Wrong security/direction in exact linked deal')
            linked_count=sum(number(r['tradeCount'],True) for r in linked)
            if linked_count>order['quantity']:raise TradeError('Linked deal quantity exceeds order')
            if linked_count!=progress['filled']:
                progress=dict(progress,done=False,deals_complete=False)
            results.append((oid,order,progress))
        except (OrderNotVisible,FillSnapshotPending):
            results.append((oid,order,None))
    return results,positions,balance


def execute_batch(cfg,plan,broker,journal,quote_provider,prior_states,clock=now_cn,sleeper=time.sleep):
    spacing=order_spacing(cfg,broker)
    day=plan['execution_date'];baseline=plan['initial_positions']
    if cfg['capital_limit']!=2000000 or cfg['max_order_value']!=100000 or cfg['max_price_move']!=.03:
        raise TradeError('Batch execution boundaries changed')
    if not journal.start(day,plan):raise TradeError('Batch already executed')
    known=[];deferred=[];precision_deferred=[];failure=None;submitted_reserve=0.;quotes={};intents={}
    initial=broker.snapshot()
    if {c:p['shares'] for c,p in initial['positions'].items() if p['shares']}!=baseline or initial['frozen']:
        raise TradeError('Account changed before batch dispatch')
    initial_cash=initial['cash']
    try:
        for order in plan['orders']:
            now=clock();validate_session(now,calendar(cfg))
            if now.date().isoformat()!=day or Path(cfg['state_dir'],'STOP').exists():raise TradeError('Wrong day or STOP')
            if order['side']!=1 or order['code'] in baseline:raise TradeError('Only missing buy targets allowed')
            q=quote_provider(order['code']);validate_quote(q,cfg,clock())
            quotes[order['code']]=q
            write_json(Path(cfg['state_dir']).parent/'live_quotes.json',quotes)
            if abs(q['ask']/order['reference_price']-1)>cfg['max_price_move']:
                journal.mark(day,order['code'],'DEFERRED_PRICE');deferred.append(order['code']);continue
            padding=cfg.get('authorized_padding_ticks_by_code',{}).get(order['code'],0)
            price=stable_price_at_ask(q['ask'],padding)
            if price is None:
                journal.mark(day,order['code'],'DEFERRED_PRECISION');precision_deferred.append(order['code']);continue
            if abs(price/order['reference_price']-1)>cfg['max_price_move']:
                journal.mark(day,order['code'],'DEFERRED_PRICE');deferred.append(order['code']);continue
            order['price']=price
            amount=price*order['quantity'];reserve=order_cash_reserve(cfg,amount)
            if amount>cfg['max_order_value'] or submitted_reserve+reserve>initial_cash:
                raise TradeError('Batch order value/cash guard failed')
            order_payload(broker.account,order['code'],1,order['quantity'],price)
            journal.reserve_capital(day,order['code'],reserve,cfg['capital_limit'])
            submitted_reserve+=reserve
            intents[order['code']]=dict(order)
            write_json(Path(cfg['state_dir']).parent/'order_intents.json',intents)
            journal.mark(day,order['code'],'SENDING')
            try:oid=broker.submit(order)
            except Exception as error:
                journal.record_error(day,order['code'],'batch_submission',error)
                journal.mark(day,order['code'],'UNKNOWN')
                raise TradeError('Batch submission outcome uncertain; no further submissions or retry') from None
            known.append((oid,dict(order)))
            journal.mark(day,order['code'],'ACK',oid)
            sleeper(spacing)
    except Exception as error:
        failure=str(error) if isinstance(error,TradeError) else type(error).__name__
        journal.record_error(day,'BATCH','dispatch_stopped',error)
    deadline=time.monotonic()+cfg['fill_timeout_seconds']
    cancelling=bool(failure);cancel_attempted=set();last_filled={};latest={};audit_error=None
    if cancelling:deadline=time.monotonic()
    cancel_deadline=None
    while known:
        try:
            results,positions,balance=read_batch(broker,known,baseline,prior_states,day)
            unresolved=[]
            for oid,order,p in results:
                if p is None:
                    unresolved.append((oid,order));continue
                filled=p['filled']
                if filled<last_filled.get(oid,0):raise TradeError('Batch cumulative fill decreased')
                last_filled[oid]=filled;latest[oid]=p
                terminal=p['done'] or (p.get('state') in ('7','8','9') and p.get('deals_complete'))
                state='FILLED' if p['done'] else ('REJECTED' if p.get('state')=='9' else 'CANCELLED') if terminal else 'PARTIAL'
                journal.mark(day,order['code'],state,oid,filled)
                if not terminal:unresolved.append((oid,order))
            if not unresolved and number(balance['frozenMoney'])==0:break
            if not unresolved:unresolved=list(known)
        except Exception as error:
            audit_error=str(error) if isinstance(error,TradeError) else type(error).__name__
            journal.record_error(day,'BATCH','batch_readback',error)
            cancelling=True
            unresolved=known
        stop=Path(cfg['state_dir'],'STOP').exists()
        if cancelling or stop or time.monotonic()>=deadline:
            cancelling=True
            was_cancelling=cancel_deadline is not None
            if cancel_deadline is None:cancel_deadline=time.monotonic()+cfg['cancel_timeout_seconds']
            for oid,order in unresolved:
                if oid in cancel_attempted:continue
                cancel_attempted.add(oid)
                journal.mark(day,order['code'],'CANCEL_SENDING',oid,last_filled.get(oid,0))
                try:broker.cancel(oid,order)
                except Exception as error:journal.record_error(day,order['code'],'batch_cancellation',error)
            if was_cancelling and time.monotonic()>=cancel_deadline:
                for oid,order in unresolved:journal.mark(day,order['code'],'REVIEW_REQUIRED',oid,last_filled.get(oid,0))
                break
        sleeper(cfg['poll_seconds'])
    rows=journal.db.execute('SELECT code,state,filled FROM orders WHERE day=?',(day,)).fetchall()
    unresolved_codes=[c for c,s,f in rows if s not in ('FILLED','CANCELLED','REJECTED','DEFERRED_PRICE','DEFERRED_PRECISION')]
    incomplete=[c for c,s,f in rows if s!='FILLED']
    all_visited=len(rows)==len(plan['orders'])
    if not unresolved_codes and all_visited and not failure and not audit_error:
        journal.complete(day,deferred=bool(incomplete))
    return {'status':'review_required' if unresolved_codes or failure or audit_error or not all_visited else 'complete_with_deferred' if incomplete else 'complete',
            'submitted':len(known),'filled_orders':sum(s=='FILLED' for c,s,f in rows),
            'cancelled_orders':sum(s=='CANCELLED' for c,s,f in rows),'price_deferred':deferred,
            'precision_deferred':precision_deferred,
            'unresolved_codes':unresolved_codes,'unvisited':len(plan['orders'])-len(rows),
            'dispatch_failure':failure,'audit_error':audit_error}

"""Durable finite-depth IOC simulation connected to the frozen raw-share ledger.

Controller-only account adapter. No live orders, market reads, model calls or
execution certification. Missing corporate-action coverage is never absence.
An order is one fee assessment even when it consumes several price levels.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
import hashlib
import json
from pathlib import Path
import sqlite3

from .kernel import FROZEN, module
from .l2_execution_evidence import integer, local_time, validate_quote
from .l2_corporate_actions import CorporateSchedule, source_pins as corporate_source_pins

RawShareLedger=module('raw_share_ledger').RawShareLedger
D=Decimal
KINDS=('corporate_actions','market_status','capacity','fee_policy','availability','membership')


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()


def money(value):
    return format(value.quantize(D('.01'),rounding=ROUND_HALF_UP),'.2f')


def fee(notional,side,policy):
    if not notional:return {'commission':'0.00','transfer':'0.00','stamp':'0.00','total':'0.00'}
    values={'commission':max(D(policy['minimum_commission']),notional*D(policy['commission_rate'])),
        'transfer':notional*D(policy['transfer_rate']),
        'stamp':notional*D(policy['sell_stamp_rate']) if side=='sell' else D(0)}
    values={k:money(v) for k,v in values.items()}
    return {**values,'total':money(sum(D(x) for x in values.values()))}


class L2CashAccount:
    """SQLite is authoritative; unresolved intents block new orders.

    Each successful order commits all price-level fills, depth consumption and
    its receipt in one transaction. A crash before that commit leaves a pending
    intent and the prior account. Recovery reads saved events, never rematches.
    """
    def __init__(self,path,plan=None):
        self.path=Path(path)
        creating=not self.path.exists()
        if creating and plan is None:raise ValueError('new account requires frozen plan')
        if creating:self._validate_plan(plan)
        self.db=sqlite3.connect(self.path,isolation_level=None,timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        if creating:
            self.db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                'CREATE TABLE events(sequence INTEGER PRIMARY KEY,payload TEXT NOT NULL);'
                'CREATE TABLE orders(id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,input TEXT NOT NULL,status TEXT NOT NULL,result TEXT);'
                'CREATE TABLE books(id TEXT PRIMARY KEY,quote_hash TEXT NOT NULL,used TEXT NOT NULL);'
                'CREATE TABLE decisions(id TEXT PRIMARY KEY,payload TEXT NOT NULL);'
                'CREATE TABLE reservations(id TEXT PRIMARY KEY,start TEXT NOT NULL,end TEXT NOT NULL,code TEXT NOT NULL,side TEXT NOT NULL,cash TEXT NOT NULL,quantity INTEGER NOT NULL);'
                'CREATE TABLE corporate_stages(id TEXT PRIMARY KEY,payload TEXT NOT NULL);'
                "INSERT INTO meta VALUES('stored_payload_bytes','0');"
                "CREATE TRIGGER event_bytes AFTER INSERT ON events BEGIN UPDATE meta SET value=CAST(value AS INTEGER)+length(CAST(NEW.payload AS BLOB)) WHERE key='stored_payload_bytes'; END;"
                "CREATE TRIGGER corporate_bytes AFTER INSERT ON corporate_stages BEGIN UPDATE meta SET value=CAST(value AS INTEGER)+length(CAST(NEW.payload AS BLOB)) WHERE key='stored_payload_bytes'; END;"
                "CREATE TRIGGER order_input_bytes AFTER INSERT ON orders BEGIN UPDATE meta SET value=CAST(value AS INTEGER)+length(CAST(NEW.input AS BLOB)) WHERE key='stored_payload_bytes'; END;"
                "CREATE TRIGGER order_result_bytes AFTER UPDATE OF result ON orders BEGIN UPDATE meta SET value=CAST(value AS INTEGER)+coalesce(length(CAST(NEW.result AS BLOB)),0)-coalesce(length(CAST(OLD.result AS BLOB)),0) WHERE key='stored_payload_bytes'; END;")
            plan=deepcopy(plan)
            plan['ledger_source_sha256']=hashlib.sha256((FROZEN/'raw_share_ledger.py').read_bytes()).hexdigest()
            plan['adapter_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            plan['corporate_source_pins']=corporate_source_pins()
            self.plan=plan;self.plan_hash=digest(plan)
            source=self._source('frozen-account-plan',self.plan_hash)
            ledger=RawShareLedger(plan['calendar'],calendar_source=source,calendar_available_at=plan['initial_time'])
            ledger.apply({'event_id':'initial-capital','kind':'cash_deposit','effective_at':plan['initial_time'],
                'available_at':plan['initial_time'],'source':source,'data':{'amount':plan['initial_cash']}},as_of=plan['initial_time'])
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.executemany('INSERT INTO meta VALUES(?,?)',[('plan',encoded(plan)),('plan_hash',self.plan_hash),
                    ('genesis',encoded(ledger.genesis)),('halted','false')])
                self.db.execute('INSERT INTO events VALUES(?,?)',(1,encoded(ledger.journal[0])))
                self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise
        saved=json.loads(self.db.execute("SELECT value FROM meta WHERE key='plan'").fetchone()[0])
        self.plan=saved;self.plan_hash=digest(saved)
        if plan is not None:
            candidate={**plan,'ledger_source_sha256':saved['ledger_source_sha256'],'adapter_source_sha256':saved['adapter_source_sha256'],
                'corporate_source_pins':saved['corporate_source_pins']}
            if digest(candidate)!=self.plan_hash:raise ValueError('account plan differs; no reset')
        if self.db.execute("SELECT value FROM meta WHERE key='plan_hash'").fetchone()[0]!=self.plan_hash:
            raise ValueError('saved plan hash differs')
        if hashlib.sha256((FROZEN/'raw_share_ledger.py').read_bytes()).hexdigest()!=saved['ledger_source_sha256']:
            raise ValueError('frozen ledger source changed')
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=saved['adapter_source_sha256']:
            raise ValueError('account adapter source changed; use original snapshot for replay')
        if corporate_source_pins()!=saved['corporate_source_pins']:
            raise ValueError('corporate scheduler source changed; use original snapshot for replay')
        self.corporate=CorporateSchedule(saved)
        self._reload()

    @staticmethod
    def _validate_plan(p):
        if len(encoded(p).encode())>2*1024**2:raise ValueError('account plan exceeds 2 MiB')
        if p['mode'] not in ('fixture','real_development'):raise ValueError('formal promotion is not implemented here')
        if not D(p['initial_cash']).is_finite() or D(p['initial_cash'])<=0:raise ValueError('invalid full initial cash')
        if not 1<=len(p['codes'])<=64 or len(set(p['codes']))!=len(p['codes']):raise ValueError('invalid code scope')
        if not 2<=len(p['calendar'])<=512:raise ValueError('calendar bound')
        if p['fees'] is not None:
            if set(p['fees'])!={'commission_rate','minimum_commission','transfer_rate','sell_stamp_rate'}:raise ValueError('explicit fee components required')
            for k,v in p['fees'].items():
                if type(v) is not str or not D(v).is_finite() or D(v)<0 or (k!='minimum_commission' and D(v)>D('.01')):
                    raise ValueError('fee outside declared supported range')
            if D(p['fees']['commission_rate'])==0 or D(p['fees']['minimum_commission'])==0:
                raise ValueError('cannot silently omit commission')
        if p['policy']['price_scale']!=10000 or p['policy']['tick_scaled']!=100 or p['policy']['unit_lot']!=100:
            raise ValueError('only explicit mainland share/tick convention supported')
        if not D(0)<D(p['policy']['per_level_displayed_depth_fraction'])<=D(1):raise ValueError('invalid depth fraction')
        expected={(c,d,k) for c in p['codes'] for d in p['calendar'] for k in KINDS}
        for obligation in p['obligations']:
            key=(obligation['code'],obligation['date'],obligation['kind'])
            if key not in expected:raise ValueError('duplicate or unexpected obligation')
            expected.remove(key)
            status=obligation['status']
            if status not in ('pending','unknown','unsupported','documented_scope','declared_simulation','fixture_complete'):
                raise ValueError('unknown obligation status')
            if status=='fixture_complete' and p['mode']!='fixture':raise ValueError('real data cannot claim fixture coverage')
            if status=='declared_simulation' and key[2]=='corporate_actions':raise ValueError('unknown actions cannot be assumed absent')
            if key[2]=='fee_policy' and p['fees'] is None and status not in ('pending','unknown','unsupported'):
                raise ValueError('missing numerical fees cannot have resolved fee obligations')
            if status not in ('pending','unknown','unsupported'):
                h=obligation.get('evidence_sha256','')
                if len(h)!=64 or any(x not in '0123456789abcdef' for x in h):raise ValueError('resolved obligation lacks source hash')
        if expected:raise ValueError('incomplete obligation grid')
        events=p['corporate_events']
        if type(events) is not list or len(events)>512:raise ValueError('explicit bounded corporate-event schedule required')
        allowed={'record_entitlement','activate_entitlement','cash_dividend_paid','tax_assessed','tax_paid','bonus_shares_listed'}
        if len({e['event_id'] for e in events})!=len(events):raise ValueError('duplicate planned corporate event')
        for e in events:
            if e['kind'] not in allowed or not e['event_id'].startswith('action:'):raise ValueError('invalid corporate-event type/ID')
            if local_time(e['effective_at']).date().isoformat() not in p['calendar']:raise ValueError('action outside covered calendar')
        CorporateSchedule(p)

    def _source(self,ref,sha):
        return {'ref':ref,'sha256':sha,'verified':True,'evidence_type':'simulated',
            'assumption_ref':'l2-cash-account-plan','assumption_sha256':self.plan_hash}

    def _reload(self):
        genesis=json.loads(self.db.execute("SELECT value FROM meta WHERE key='genesis'").fetchone()[0])
        journal=[json.loads(x[0]) for x in self.db.execute('SELECT payload FROM events ORDER BY sequence')]
        self.ledger=RawShareLedger.replay(genesis,journal)

    def _sync(self):
        # An append-only journal head check avoids replaying all old events for
        # every new order; a new process or changed writer reloads once.
        latest=self.db.execute('SELECT sequence,payload FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
        if latest[0]!=len(self.ledger._journal) or json.loads(latest[1])['entry_hash']!=self.ledger._previous_hash:
            self._reload()

    def close(self):self.db.close()

    def snapshot(self):
        self._sync()
        return {**self.ledger.snapshot(),'account_halted':self.db.execute("SELECT value FROM meta WHERE key='halted'").fetchone()[0]=='true',
            'pending_orders':self.db.execute("SELECT count(*) FROM orders WHERE status='pending'").fetchone()[0],
            'formal_target_success':False,'portfolio_pnl':None}

    def _missing(self,codes,day):
        return [o for o in self.plan['obligations'] if o['code'] in codes and o['date']==day and o['status'] in ('pending','unknown','unsupported')]

    def _held(self):
        result={}
        for lot in self.ledger._state['lots'].values():
            result[lot['symbol']]=result.get(lot['symbol'],0)+lot['quantity']
        return {k:v for k,v in result.items() if v}

    def _exposed(self,day=None):
        codes=set(self._held())
        for action in self.ledger._state['actions'].values():
            if (not action['active'] or not action['cash_paid'] or action['bonus_pending'] or
                not action['tax_final'] or action['tax_due'] is None or action['tax_due']>action['tax_paid']):
                codes.add(action['symbol'])
        # Settlement can close a right before this day's order check. It still
        # makes that stock-day an actually touched exposure. Do not erase its
        # source obligation merely because today's payment completed first.
        if day is not None:
            for entry in reversed(self.ledger._journal):
                applied=local_time(entry['applied_at']).date().isoformat()
                if applied<day:break
                if applied!=day:continue
                data=entry['event']['data']
                if 'symbol' in data:codes.add(data['symbol'])
                elif 'action_id' in data:
                    codes.add(self.ledger._state['actions'][data['action_id']]['symbol'])
        return codes

    def _tax_reserve_for(self,ledger):
        reserve=D(0)
        for aid,action in ledger._state['actions'].items():
            if action.get('active'):
                if action['tax_due'] is None or not action['tax_final']:
                    reserve+=self.corporate.cash_reserve(aid,action)
                else:reserve+=max(D(0),action['tax_due']-action['tax_paid'])
        return reserve

    def _decision_state(self,as_of):
        moment=local_time(as_of);key=moment.isoformat(timespec='microseconds')
        saved=self.db.execute('SELECT payload FROM decisions WHERE id=?',(key,)).fetchone()
        if saved:return json.loads(saved[0])
        # A later fill/receipt already persisted by another order must not fund
        # an order with an earlier decision time. Build this clock once.
        prefix=[e for e in self.ledger._journal if local_time(e['applied_at'])<=moment]
        historical=RawShareLedger.replay(self.ledger.genesis,prefix)
        try:reserve=money(self._tax_reserve_for(historical))
        except ValueError:reserve=None
        result={'clock':key,'cash':money(historical._state['cash']),'tax_reserve':reserve,
            'sellable':{c:historical.sellable_quantity(c,as_of=moment.isoformat()) for c in self.plan['codes']},
            'journal_head':historical._previous_hash}
        self.db.execute('INSERT INTO decisions VALUES(?,?)',(key,encoded(result)))
        return result

    def _advance(self,as_of,fault=None):
        clock=local_time(as_of)
        if clock.date().isoformat() not in self.plan['calendar']:raise ValueError('advance outside covered calendar')
        start=len(self.ledger._journal);steps=[]
        for n,event in enumerate(self.plan['corporate_events']):
            at=max(local_time(event['effective_at']),local_time(event['available_at']))
            if event['event_id'] not in self.ledger._events and at<=clock:
                steps.append((at,0,n,event))
        for n,stage in enumerate(self.corporate.due(self.db,clock)):
            steps.append((stage[0],1,n,stage))
        for at,kind,_,value in sorted(steps):
            if kind==0:
                self.ledger.apply(value,as_of=at.isoformat())
                entry=self.ledger._journal[-1]
                self.db.execute('INSERT INTO events VALUES(?,?)',(entry['sequence'],encoded(entry)))
            else:self.corporate.apply(self,value,encoded,fault)
            stored=int(self.db.execute("SELECT value FROM meta WHERE key='stored_payload_bytes'").fetchone()[0])
            if stored+512*1024>128*1024**2 or len(self.ledger._journal)>32750:
                raise ValueError('corporate event journal resource bound')
        return len(self.ledger._journal)-start

    def advance(self,as_of,*,fault=None):
        """Apply frozen known account events independently of order success."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute("SELECT count(*) FROM orders WHERE status='pending'").fetchone()[0]:raise ValueError('pending order requires saved-state recovery first')
            self._sync()
            if self.ledger._last_applied and local_time(as_of)<self.ledger._last_applied:raise ValueError('account clock cannot move backwards')
            count=self._advance(as_of,fault)
            if fault:fault('before_commit')
            self.db.execute('COMMIT')
            if fault:fault('after_commit')
            return {'events_applied':count,**self.snapshot()}
        except BaseException:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            self._reload();raise

    def submit(self,order,quote,*,fault=None):
        payload={'order':order,'quote':quote};body=encoded(payload)
        if len(body.encode())>128*1024:raise ValueError('order evidence exceeds 128 KiB')
        oid=order['id'];ih=digest(payload)
        if type(oid) is not str or not 1<=len(oid)<=120:raise ValueError('bounded order ID required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing=self.db.execute('SELECT input_hash,status,result FROM orders WHERE id=?',(oid,)).fetchone()
            if existing:
                if existing[0]!=ih:raise ValueError('order ID payload conflict')
                self.db.execute('COMMIT')
                self._sync()
                return {'status':'pending_recovery_required','order_id':oid} if existing[1]=='pending' else json.loads(existing[2])
            if self.db.execute("SELECT count(*) FROM orders WHERE status='pending'").fetchone()[0]:raise ValueError('unresolved prior intent; no automatic rematching')
            if self.db.execute('SELECT count(*) FROM orders').fetchone()[0]>=4096:raise ValueError('order count bound')
            if self.db.execute('SELECT count(*) FROM events').fetchone()[0]>=32750:raise ValueError('ledger event count bound')
            stored=int(self.db.execute("SELECT value FROM meta WHERE key='stored_payload_bytes'").fetchone()[0])
            if stored+len(body.encode())+512*1024>128*1024**2:raise ValueError('saved-data byte limit with receipt reserve')
            self.db.execute('INSERT INTO orders VALUES(?,?,?,?,NULL)',(oid,ih,body,'pending'));self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            raise
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._sync()
            # Known entitlements/payment events survive an ordinary order rejection.
            if local_time(quote['event_ts']).date().isoformat()==order['date'] and order['date'] in self.plan['calendar']:
                self._advance(order['decision_time'])
                self._decision_state(order['decision_time'])
                self._advance(quote['event_ts'])
            start=len(self.ledger._journal)
            result=self._execute(order,quote)
            if fault:fault('before_commit')
            for entry in self.ledger._journal[start:]:
                self.db.execute('INSERT INTO events VALUES(?,?)',(entry['sequence'],encoded(entry)))
            result.update(order_id=oid,input_hash=ih,plan_hash=self.plan_hash,execution_valid=False,formal_target_success=False,portfolio_pnl=None)
            self.db.execute('UPDATE orders SET status=?,result=? WHERE id=?',(result['status'],encoded(result),oid))
            self.db.execute('COMMIT')
            if fault:fault('after_commit')
            return result
        except BaseException:
            if self.db.in_transaction:self.db.execute('ROLLBACK')
            self._reload()
            raise

    def _execute(self,order,quote):
        before=self.ledger.snapshot()
        base={'requested_quantity':order.get('quantity'),'filled_quantity':0,'remaining_quantity':order.get('quantity'),
            'fees':fee(D(0),'buy',self.plan['fees']),'cash_before':before['cash'],'cash_after':before['cash'],'holdings_after':self._held()}
        def reject(reason,halt=False):
            if halt:self.db.execute("UPDATE meta SET value='true' WHERE key='halted'")
            return {**base,'status':'halted_account' if halt else 'rejected_order','reason':reason}
        if self.db.execute("SELECT value FROM meta WHERE key='halted'").fetchone()[0]=='true':return reject('account already halted; inventory retained',True)
        try:
            code,day,side=order['code'],order['date'],order['side']
            if code not in self.plan['codes'] or day not in self.plan['calendar'] or side not in ('buy','sell'):raise ValueError('order outside frozen scope')
            held=self._held();missing_held=self._missing(self._exposed(day),day)
            if missing_held:return reject('unresolved held-exposure obligations: '+encoded(missing_held),True)
            missing=self._missing({code},day)
            if missing:return reject('unresolved new-order obligations: '+encoded(missing))
            quantity=integer(order['quantity'],positive=True);limit=integer(order['limit_price_scaled'],positive=True)
            if limit%100:raise ValueError('non-tick limit')
            decision=local_time(order['decision_time']);arrival=local_time(order['arrival_time']);clock=local_time(quote['event_ts'])
            if any(t.date().isoformat()!=day for t in (decision,arrival,clock)) or arrival<decision or clock<arrival:raise ValueError('noncausal order/quote times')
            if decision.strftime('%H:%M:%S')!=self.plan['policy']['decision_local_time'] or decision.microsecond:
                raise ValueError('decision time differs from frozen policy')
            if (arrival-decision).total_seconds()*1000!=self.plan['policy']['assumed_order_latency_ms']:
                raise ValueError('order latency differs from frozen policy')
            if (clock-arrival).total_seconds()*1000>self.plan['policy']['max_execution_snapshot_delay_ms']:raise ValueError('execution snapshot stale')
            if quote['wind_code']!=code:raise ValueError('quote security differs')
            latest_stage=self.db.execute('SELECT payload FROM corporate_stages ORDER BY json_extract(payload,\'$.at\') DESC LIMIT 1').fetchone()
            if latest_stage and local_time(json.loads(latest_stage[0])['at'])>clock:
                raise ValueError('order predates completed corporate stage; no late registration changes')
            validate_quote(quote,self.plan['policy'])
            at_decision=self._decision_state(order['decision_time'])
            pending_reserves=self.db.execute('SELECT code,side,cash,quantity FROM reservations WHERE start<=? AND end>=?',
                (at_decision['clock'],at_decision['clock'])).fetchall()
            cash_reserved=sum((D(x[2]) for x in pending_reserves if x[1]=='buy'),D(0))
            shares_reserved=sum(x[3] for x in pending_reserves if x[0]==code and x[1]=='sell')
            unit=100
            if side=='buy' and quantity%100:raise ValueError('buy must be whole-lot requested quantity')
            if side=='sell':
                sellable=min(at_decision['sellable'][code]-shares_reserved,
                    self.ledger.sellable_quantity(code,as_of=clock.isoformat()))
                if quantity>sellable:raise ValueError('requested shares unavailable under T+1/inventory')
                if quantity%100:
                    if quantity!=held.get(code,0) or quantity!=sellable:raise ValueError('odd-lot sale must request entire sellable holding')
                    unit=1
            if side=='buy':
                if at_decision['tax_reserve'] is None:raise ValueError('unresolved tax at decision prevents spending cash')
                maximum_notional=D(quantity)*D(limit)/10000
                cash_needed=maximum_notional+D(fee(maximum_notional,side,self.plan['fees'])['total'])
                decision_cash=D(at_decision['cash'])-D(at_decision['tax_reserve'])-cash_reserved
                current_cash=D(before['cash'])-self._tax_reserve_for(self.ledger)
                if cash_needed>min(decision_cash,current_cash):raise ValueError('pre-dispatch full-order cash reservation insufficient')
            key=digest({'code':code,'date':str(quote['date']),'source_row_no':quote['source_row_no'],'side':side})
            quote_hash=digest(quote)
            prior=self.db.execute('SELECT quote_hash,used FROM books WHERE id=?',(key,)).fetchone()
            if prior and prior[0]!=quote_hash:raise ValueError('same source quote key has changed content')
            used=json.loads(prior[1]) if prior else {};levels=[];contra='ask' if side=='buy' else 'bid'
            fraction=D(self.plan['policy']['per_level_displayed_depth_fraction'])
            for level in range(1,11):
                price,shown=quote[f'{contra}_price_{level}_x1e4'],quote[f'{contra}_volume_{level}']
                if not price or (side=='buy' and price>limit) or (side=='sell' and price<limit):break
                allowed=int((D(shown)*fraction).to_integral_value(rounding=ROUND_FLOOR))-used.get(str(level),0)
                if allowed<0:raise ValueError('saved depth consumption exceeds budget')
                levels.append((level,price,allowed))
            filled=min(quantity,sum(x[2] for x in levels)//unit*unit)
            left=filled;allocations=[]
            for level,price,available in levels:
                q=min(left,available)
                if q:allocations.append({'level':level,'price_scaled':price,'quantity':q});left-=q
            notional=sum((D(x['price_scaled'])*x['quantity']/10000 for x in allocations),D(0))
            fees=fee(notional,side,self.plan['fees'])
            # All checks precede any ledger mutation; exceptions roll the whole order back.
            for ordinal,allocation in enumerate(allocations):
                data={'symbol':code,'quantity':allocation['quantity'],'raw_price':money(D(allocation['price_scaled'])/10000),
                    'fees':fees['total'] if ordinal==0 else '0.00'}
                if side=='sell':
                    remaining=allocation['quantity'];lots={}
                    for lid,lot in self.ledger._state['lots'].items():
                        if lot['symbol']==code and lot['sellable_at']<=clock and lot['quantity']:
                            q=min(remaining,lot['quantity']);lots[lid]=q;remaining-=q
                            if not remaining:break
                    if remaining:raise ValueError('sell lot allocation shortfall')
                    data['lot_allocations']=lots
                event={'event_id':order['id']+f':level:{allocation["level"]}','kind':side+'_fill',
                    'effective_at':clock.isoformat(),'available_at':clock.isoformat(),
                    'source':self._source('quote:'+key,quote_hash),'data':data}
                self.ledger.apply(event,as_of=clock.isoformat())
                used[str(allocation['level'])]=used.get(str(allocation['level']),0)+allocation['quantity']
            self.db.execute('INSERT OR REPLACE INTO books VALUES(?,?,?)',(key,quote_hash,encoded(used)))
            self.db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?,?)',(order['id'],at_decision['clock'],
                clock.isoformat(timespec='microseconds'),code,side,money(cash_needed) if side=='buy' else '0.00',quantity if side=='sell' else 0))
            return {**base,'status':'filled' if filled==quantity else 'partial_ioc' if filled else 'unmatched_ioc',
                'filled_quantity':filled,'remaining_quantity':quantity-filled,'unfilled_remainder_cancelled':True,
                'allocations':allocations,'notional':money(notional),'fees':fees,'cash_after':self.ledger.snapshot()['cash'],
                'holdings_after':self._held(),'quote_key':key,'quote_hash':quote_hash,
                'cash_at_decision':at_decision['cash'],'cash_reserved_before_order':money(cash_reserved)}
        except (ValueError,KeyError,TypeError) as exc:
            # A kernel rejection after a prior level must not persist a partial order.
            self._reload()
            return reject(str(exc))

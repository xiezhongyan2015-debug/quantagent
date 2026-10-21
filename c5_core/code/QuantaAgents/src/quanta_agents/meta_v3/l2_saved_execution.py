"""Controlled causal target program -> persistent L2 cash-account evidence.

Selected observations must already be bound by the controller to the source
selection audit. No hidden market scan, future-book search, source acquisition,
model call or live order occurs. Every day and the full capital remain visible.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import time

from . import real_program
from .kernel import module
from .ledger import need, digest
from .l2_cash_account import L2CashAccount, money
from .l2_execution_evidence import local_time, validate_quote
from .l2_corporate_actions import source_pins

MAX_COMPRESSED=8*1024**2
MAX_DECOMPRESSED=16*1024**2
RESOURCE_BUDGET={'wall_seconds':600,'maximum_case_bytes':8*1024**2,'orders':4096,
                 'calendar_sessions':512,'symbols':16,'formal_target_success':False}


def wind(code):
    return code[2:]+'.'+code[:2].upper()


def engine_sources():
    paths=[Path(__file__),Path(real_program.__file__),Path(__file__).with_name('l2_cash_account.py')]
    return {**{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},**source_pins()}


def validate_case(case):
    need(len(json.dumps(case,ensure_ascii=False,allow_nan=False).encode())<=MAX_COMPRESSED,'L2 case byte bound')
    table=case['decision_fixture'];real_program.matrices(table)
    need(table['kind']=='exposed_l2_decision_table','explicit L2 development source class required')
    bound=case['l2_execution'];p=bound['account_plan']
    need(p['mode']=='real_development','research account must preserve real-development class')
    need(p['calendar']==table['calendar'] and p['codes']==[wind(c) for c in table['codes']],'L2 calendar/security mismatch')
    need(p['initial_cash']==case['initial_cash'],'full initial capital mismatch')
    need(p['corporate_events']==[],'research actions must derive from admitted announcements and actual lots')
    L2CashAccount._validate_plan(p)
    need(case['raw_source_bindings']['obligations']==p['obligations'],'research/account obligation mismatch')
    need(len(bound['observations'])<=len(p['calendar'])*len(p['codes']),'selected observation bound')
    seen=set()
    for item in bound['observations']:
        key=(item['code'],item['date'])
        need(key not in seen and key[0] in p['codes'] and key[1] in p['calendar'],'duplicate/foreign selected observation')
        seen.add(key)
        for field in ('source_selection_sha256',):
            need(type(item[field]) is str and len(item[field])==64 and all(c in '0123456789abcdef' for c in item[field]),'source selection audit binding required')
    marks=bound['marks'];seen=set()
    need(len(marks)<=len(p['calendar'])*len(p['codes']),'mark bound')
    for row in marks:
        key=(row['code'],row['date'])
        need(key not in seen and key[0] in p['codes'] and key[1] in p['calendar'],'duplicate/foreign mark')
        seen.add(key)
    need(p['policy']['decision_local_time']=='09:35:00','research schedule currently fixes 09:35 dispatch')
    for key in ('assumed_feed_delay_ms','max_decision_quote_source_age_ms',
                'buy_limit_vs_decision_ask_fraction','sell_limit_vs_decision_bid_fraction'):
        need(key in p['policy'],'missing sizing/timing policy: '+key)
    return bound


def _reference(observation, code, day, policy):
    quote=observation['decision'];validate_quote(quote,policy)
    decision=local_time(day+'T'+policy['decision_local_time'])
    stamp=local_time(quote['event_ts'])
    need(quote['wind_code']==code and str(quote['date'])==day,'reference quote identity differs')
    age=(decision-stamp).total_seconds()*1000
    need(policy['assumed_feed_delay_ms']<=age<=policy['max_decision_quote_source_age_ms'],'unavailable/stale sizing quote')
    buy=int((Decimal(quote['ask_price_1_x1e4'])*(1+Decimal(policy['buy_limit_vs_decision_ask_fraction']))/100).to_integral_value(rounding=ROUND_CEILING))*100
    sell=int((Decimal(quote['bid_price_1_x1e4'])*(1-Decimal(policy['sell_limit_vs_decision_bid_fraction']))/100).to_integral_value(rounding=ROUND_FLOOR))*100
    return decision,buy,sell


def develop(folder,case,program,save):
    bound=validate_case(case);work=folder/'workbench';work.mkdir()
    plan=bound['account_plan'];policy=plan['policy'];started=time.monotonic()
    save(work/'intent.json',{'case_hash':digest(case),'program_hash':digest(program),
        'engine_sources':engine_sources(),'resource_budget':RESOURCE_BUDGET,'research_class':'real_saved_development',
        'execution_policy':'causal previous-session target; prior completed NAV; known pre-dispatch quote sizes limit IOC; all sides reserve at the same decision clock'})
    save(work/'case.json',case)
    phases=[];daily=[];rejections=[];compiled=None;account=None;failure=None;targets=None
    raw_result=None;orders=[];valuation_through=None
    try:
        compiled=real_program.validate_program(program,public_field_contract=case['decision_fixture']['fields'])
        save(work/'compiled.json',compiled);phases.append({'name':'compile','status':'completed'})
        targets=real_program.build_targets(compiled,decision_fixture=case['decision_fixture'],frozen_policy=real_program.POLICY)
        save(work/'targets.json',targets);phases.append({'name':'target_generation','status':'completed'})
        account=L2CashAccount(work/'account.sqlite',plan)
        save(work/'account_plan.json',account.plan)
        save(work/'corporate_manifests.json',account.corporate.manifests())
        by_day={day:[] for day in plan['calendar']}
        for target in targets['targets']:by_day[target['trade_date']].append(target)
        observations={(r['code'],r['date']):r for r in bound['observations']}
        marks={(r['code'],r['date']):r for r in bound['marks']}
        nav=Decimal(plan['initial_cash'])
        for day in plan['calendar']:
            need(time.monotonic()-started<RESOURCE_BUDGET['wall_seconds'],'L2 execution wall bound')
            clock=day+'T'+policy['decision_local_time']+'+08:00'
            account.advance(clock)
            need(not account._missing(account._exposed(day),day),'unknown held economic exposure; preserve account and stop numeric path')
            state=account.snapshot();held=account._held();planned=[]
            for t in by_day[day]:
                code=wind(t['symbol']);weight=Decimal(t['target_weight'])
                if weight==0 and not held.get(code,0):continue
                obs=observations.get((code,day))
                if obs is None:
                    rejections.append({'date':day,'symbol':code,'target':t,'reason':'missing frozen decision/execution observation'})
                    continue
                try:
                    decision,buy,sell=_reference(obs,code,day,policy)
                    need(local_time(t['available_at'])<decision,'target unavailable at dispatch')
                    desired=int((nav*weight*10000/(buy*100)).to_integral_value(rounding=ROUND_FLOOR))*100
                    delta=desired-held.get(code,0)
                    if not delta:continue
                    side='buy' if delta>0 else 'sell'
                    quantity=abs(delta) if desired==0 or delta>0 else abs(delta)//100*100
                    if not quantity:continue
                    order={'id':f'{day}:{code}:{side}','code':code,'date':day,'side':side,'quantity':quantity,
                        'limit_price_scaled':buy if side=='buy' else sell,'decision_time':decision.isoformat(),
                        'arrival_time':(decision+timedelta(milliseconds=policy['assumed_order_latency_ms'])).isoformat()}
                    planned.append((side,code,order,obs,t))
                except (ValueError,KeyError) as exc:
                    rejections.append({'date':day,'symbol':code,'target':t,'reason':str(exc)})
            # Freeze all quantities before examining any execution depth or
            # recording same-time proceeds. Never renormalize failed targets.
            save(work/f'{day}_orders.json',[{'order':o,'target':t,'selection_sha256':obs['source_selection_sha256']}
                for _,_,o,obs,t in planned])
            for _,_,order,obs,t in sorted(planned,key=lambda row:(row[0]!='sell',row[1])):
                receipt=account.submit(order,obs['execution'])
                orders.append({'date':day,'symbol':order['code'],'order':order,'target':t,'receipt':receipt})
                if receipt['status'] in ('rejected_order','halted_account','pending_recovery_required'):
                    rejections.append({'date':day,'symbol':order['code'],'order_id':order['id'],'reason':receipt.get('reason',receipt['status'])})
                need(receipt['status'] not in ('halted_account','pending_recovery_required'),'account halt/pending intent; no continuation')
            eod=day+'T15:05:00+08:00';account.advance(eod)
            need(not account._missing(account._exposed(day),day),'unknown held economic exposure at close')
            actual_marks={}
            for code in account._held():
                row=marks.get((code,day));need(row is not None,'missing held mark: '+code+' '+day)
                need(type(row['raw_price']) is str and Decimal(row['raw_price']).is_finite() and Decimal(row['raw_price'])>0,'invalid held raw mark')
                need(row['effective_at'][:10]==day and local_time(row['available_at'])<=local_time(eod),'mark unavailable/current day missing')
                need(len(row['source_sha256'])==64,'mark source identity missing')
                actual_marks[code]={'raw_price':row['raw_price'],'effective_at':row['effective_at'],'available_at':row['available_at'],
                    'source':account._source('frozen-selected-raw-mark',row['source_sha256'])}
            valuation=module('corporate_action_adapter').portfolio_tax_valuation(account.ledger,list(account.corporate.adapters.values()),actual_marks,as_of=eod)
            nav=Decimal(valuation['simulated_net_asset_value'])
            row={'date':day,'cash':account.snapshot()['cash'],'holdings':account._held(),
                'full_initial_cash':plan['initial_cash'],'valuation':valuation,
                'fees_paid_cumulative':account.snapshot()['fees_paid'],'execution_valid':False}
            daily.append(row);valuation_through=day;save(work/f'{day}_account.json',row)
        snapshot=account.snapshot()
        receipts=[json.loads(x[0]) for x in account.db.execute('SELECT result FROM orders WHERE result IS NOT NULL ORDER BY id')]
        fills=sum(r['filled_quantity']>0 for r in receipts)
        complete={'initial_cash':plan['initial_cash'],'final_snapshot':snapshot,'daily':daily,'trades':orders,
            'rejections':rejections,'filled_order_count':fills,'costs_applied':plan['fees'] is not None and fills>0,
            'all_requested_execution_inputs_admitted':not rejections,'execution_valid':False,'formal_target_success':False,
            'portfolio_pnl':money(nav-Decimal(plan['initial_cash'])) if fills and not rejections else None}
        # A fully rejected run is evidence for abstention, never a funded
        # strategy promoted by the fact that its initial deposit is intact.
        status='completed_mechanical' if fills and not rejections else 'completed_execution_evidence'
        raw_result={'status':status,'result':complete,'error':None,'valuation_complete_through':valuation_through}
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        failure={'type':type(exc).__name__,'message':str(exc)}
        raw_result={'status':'failed','result':None,'error':failure,'valuation_complete_through':valuation_through,
            'partial':{'final_snapshot':account.snapshot() if account else None,'daily':daily,'trades':orders,'rejections':rejections},
            'pending_event_intent':None if account is None else account.snapshot()['pending_orders']}
    finally:
        if account:
            save(work/'ledger_genesis.json',account.ledger.genesis)
            save(work/'ledger_journal.json',account.ledger.journal)
            save(work/'corporate_stages.json',[json.loads(x[0]) for x in account.db.execute('SELECT payload FROM corporate_stages ORDER BY rowid')])
            save(work/'saved_account_snapshot.json',account.snapshot());account.close()
    phases.append({'name':'raw_mechanical_execution','status':raw_result['status']})
    result={'status':'failed' if failure else 'completed','error':failure,'subattempts':phases,'execution_valid':False,'formal_target_success':False}
    save(work/'application.json',result);save(work/'raw_result.json',raw_result)
    return {'workbench':result,'raw':raw_result,'program':program,
            'audit':{'candidate_count':1,'subattempts':phases,'research_class':'real_saved_development','execution_backend':'v3_l2_cash_001'}}


def public_result(raw,case,program):
    complete=raw.get('result');partial=raw.get('partial') or {};snap=(complete or partial).get('final_snapshot')
    if snap:
        snap={**{k:v for k,v in snap.items() if k not in ('lots','actions')},
              'original_lot_count':len(snap['lots']),'original_action_count':len(snap['actions']),
              'original_account_sha256':digest(snap),'account_detail':'Original snapshot and all daily rights/tax evidence retained in saved artifact'}
    return {'program_hash':digest(program),'raw_status':raw['status'],'full_initial_cash':case['initial_cash'],
        'costs_applied':bool(complete and complete['costs_applied']),'execution_backend':'v3_l2_cash_001',
        'cost_source':'frozen fee components and finite selected book scenario; real account contract not inferred',
        'error':raw['error'],'final_snapshot':snap,'portfolio_pnl':complete['portfolio_pnl'] if complete else None,
        'valuation_complete_through':raw.get('valuation_complete_through'),
        'table_counts':{k:len((complete or partial).get(k,[])) for k in ('daily','trades','rejections')},
        'evidence_query':'inspect_execution tables daily/trades/rejections/failure; exact next_offset paging',
        'execution_valid':False,'formal_target_success':False}


def compact(page,table):
    if table=='daily_compact':
        columns=['date','cash','gross_assets','net_assets','realized_tax_unpaid','remaining_tax_reserve',
                 'cash_after_tax_reserve','fees_paid','holdings','full_initial_cash']
        rows=[]
        for row in page['rows']:
            v=row['valuation']
            rows.append([row['date'],row['cash'],v['gross_asset_value'],v['simulated_net_asset_value'],
                v['realized_tax_unpaid'],v['remaining_tax_reserve'],v['conservative_cash_after_tax_reserve'],
                row['fees_paid_cumulative'],row['holdings'],row['full_initial_cash']])
    else:
        columns=['date','symbol','order_id','side','requested_quantity','limit_price_scaled','decision_time',
                 'arrival_time','status','filled_quantity','remaining_quantity','notional','fees','cash_before',
                 'cash_after','allocations','reason']
        rows=[]
        for row in page['rows']:
            o=row['order'];r=row['receipt']
            rows.append([row['date'],row['symbol'],o['id'],o['side'],o['quantity'],o['limit_price_scaled'],
                o['decision_time'],o['arrival_time'],r['status'],r['filled_quantity'],r['remaining_quantity'],
                r.get('notional'),r['fees'],r['cash_before'],r['cash_after'],r.get('allocations'),r.get('reason')])
    return {**{k:page[k] for k in ('offset','next_offset','total_rows')},'columns':columns,'rows':rows,
            'source_page_sha256':digest(page),'projection':'Every row retained, money unrounded; repeated source and nested tax lineage remain in original daily/trades pages.'}

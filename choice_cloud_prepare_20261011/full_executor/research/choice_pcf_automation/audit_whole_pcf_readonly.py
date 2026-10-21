"""Independent, GET-only full account/order/deal audit after queued completion."""
import json
import os
import sqlite3
from decimal import Decimal
from run_queued_basket import DEST, OLD, RETEST, PREVIOUS, ACCOUNT, DAY
from verify_readonly import GetOnlySession,session_token
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker
from quanta_agents.factor_lab_a.pcf import COST_PER_SIDE
from quanta_agents.factor_lab_a.pcf_automation import read_json,write_json,now_cn

SUPPLEMENT=DEST.parent/'queued_supplement_20260921'


def main():
    if now_cn().date().isoformat()!=DAY:raise RuntimeError('Wrong day')
    # New intraday attempts can contain smaller replacement quantities, so use
    # their exact-ID-aware reconciler instead of the old one-order-per-target map.
    watch_state=DEST.parent/'intraday_watch_20260921_v2/private_state.json'
    if watch_state.exists():
        from audit_intraday_watch_readonly import main as audit_watch
        return audit_watch()
    known={};journal_rows=[];targets=None;reserved=0
    directories=[OLD,RETEST,PREVIOUS,DEST]
    if (SUPPLEMENT/'state/choice.sqlite').exists():directories.append(SUPPLEMENT)
    intent_by_run={directory.name:read_json(directory/'order_intents.json') for directory in directories if (directory/'order_intents.json').exists()}
    for directory in directories:
        with sqlite3.connect((directory/'state/choice.sqlite').as_uri()+'?mode=ro',uri=True) as db:
            plan=json.loads(db.execute('SELECT plan FROM batches WHERE day=?',(DAY,)).fetchone()[0])
            if targets is None:targets={x['code']:x for x in plan['orders']}
            for code,state,oid,filled in db.execute('SELECT code,state,oid,filled FROM orders WHERE day=?',(DAY,)):
                if oid:
                    if str(oid) in known:raise RuntimeError('Duplicate exact order ID across runs')
                    known[str(oid)]=(code,directory.name)
                journal_rows.append({'run':directory.name,'code':code,'state':state,'filled':filled})
            reserved=db.execute('SELECT COALESCE(SUM(reserved_cents),0)/100.0 FROM capital WHERE day=?',(DAY,)).fetchone()[0]
    session=GetOnlySession(ACCOUNT)
    old={k:os.environ.get(k) for k in ('CHOICE_JTOKEN','CHOICE_ACCOUNT_ID')}
    try:
        os.environ['CHOICE_JTOKEN']=session_token();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker({'initial_buy_only':True},False,session)
        orders=broker._rows('AguTrade/SpoOrders');holds=broker._rows('AguTrade/SpoHold');deals=broker._rows('AguTrade/SpoDeal')
        b=broker._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data']
        today=[r for r in orders if str(r['orderDate']).replace('-','')==DAY.replace('-','')]
        issues=[];verified=[];changes=[];filled_by_code={};pending=[]
        if {str(r['orderId']) for r in today}!=set(known):issues.append('Current-day order IDs differ from all saved acknowledgments')
        if any(str(r.get('orderId')) not in known for r in deals):issues.append('Unknown deal order ID')
        for oid,(code,run) in known.items():
            matches=[r for r in today if str(r['orderId'])==oid]
            if len(matches)!=1:issues.append(code+': exact order missing/ambiguous');continue
            r=matches[0];q=int(r['tradeCount']);wanted=targets[code]['quantity']
            linked=[x for x in deals if str(x.get('orderId'))==oid]
            linked_quantity=sum(int(x['tradeCount']) for x in linked)
            if (str(r['secCode'])!=code[2:] or int(r['drt'])!=1 or int(r['orderCount'])!=wanted or
                    not 0<=q<=wanted or q!=linked_quantity or
                    any(str(x['secCode'])!=code[2:] or int(x['drt'])!=1 for x in linked)):
                issues.append(code+': order/deal identity or quantity mismatch')
            accepted=Decimal(str(r['orderPrice']))
            if any(Decimal(str(x['price']))>accepted for x in linked):issues.append(code+': buy fill above accepted limit')
            intent=intent_by_run.get(run,{}).get(code)
            intended=Decimal(str(intent['price'])) if intent else None
            if intended is not None and accepted!=intended:
                changes.append({'code':code,'requested_limit':float(intended),'accepted_limit':float(accepted),
                                'difference':float(accepted-intended),'order_id_tail':oid[-4:]})
                if accepted>intended or intended-accepted!=Decimal('.01'):issues.append(code+': unexplained limit discrepancy')
            if str(r['status'])=='4' and q!=wanted:issues.append(code+': full state with incomplete quantity')
            filled_by_code[code]=filled_by_code.get(code,0)+q
            row={'code':code,'run':run,'order_id_tail':oid[-4:],'status':str(r['status']),'quantity':wanted,'filled':q,
                 'accepted_limit':float(accepted),'linked_deal_quantity':linked_quantity}
            verified.append(row)
            if str(r['status']) not in ('4','7','8','9'):pending.append(row)
        inventory={}
        for r in holds:
            code=('SH' if str(r['secCode']).startswith('6') else 'SZ')+str(r['secCode'])
            if code in inventory:issues.append(code+': duplicate inventory')
            inventory[code]=int(r['count'])
        filled_by_code={k:v for k,v in filled_by_code.items() if v}
        if inventory!=filled_by_code:issues.append('Inventory does not equal total exact-order fills')
        if any(q>targets.get(c,{}).get('quantity',0) for c,q in inventory.items()):issues.append('Inventory exceeds PCF target')
        principal=sum(Decimal(str(x['price']))*int(x['tradeCount']) for x in deals)
        fee_components={k:sum(Decimal(str(x[k])) for x in deals) for k in ('qsYj','ghCost','yhs')}
        fees=sum(fee_components.values());paid=principal+fees
        if abs(Decimal('10000000')-Decimal(str(b['availBalance']))-Decimal(str(b['frozenMoney']))-paid)>Decimal('.02'):
            issues.append('Cash principal/fee/freeze reconciliation differs')
        if paid+Decimal(str(b['frozenMoney']))>2000000:issues.append('Actual occupied capital above CNY2m')
        missing=[{'code':c,'target_shares':o['quantity'],'held_shares':inventory.get(c,0),'missing_shares':o['quantity']-inventory.get(c,0)}
                 for c,o in targets.items() if inventory.get(c,0)<o['quantity']]
        q_orders=[r for r in verified if r['run']==DEST.name]
        s_orders=[r for r in verified if r['run']==SUPPLEMENT.name]
        last_state={x['code']:x['state'] for x in journal_rows}
        for row in missing:row['latest_local_state']=last_state.get(row['code'],'NOT_SUBMITTED')
        out={'checked_at':now_cn().isoformat(),'account_tail':ACCOUNT[-4:],'new_orders':0,'cancellations':0,
             'target_securities':len(targets),'held_securities':len(inventory),'fully_held_targets':len(targets)-len(missing),
             'known_order_count_today':len(verified),'queued_orders':len(q_orders),
             'queued_state_counts':{s:sum(x['status']==s for x in q_orders) for s in sorted({x['status'] for x in q_orders})},
             'supplement_orders':len(s_orders),'supplement_state_counts':{s:sum(x['status']==s for x in s_orders) for s in sorted({x['status'] for x in s_orders})},
             'cumulative_reserved_cny':reserved,
             'pending_count':len(pending),'pending':pending,'principal_cny':str(principal),'actual_fees_cny':str(fees),
             'actual_fee_components':{k:str(v) for k,v in fee_components.items()},'actual_paid_cny':str(paid),
             'research_cost_per_side':COST_PER_SIDE,
             'research_buy_cost':str(principal*Decimal(str(COST_PER_SIDE))),
             'balance':{k:b[k] for k in ('totalAssets','availBalance','frozenMoney','totalPosValue')},
             'remaining_targets':missing,'accepted_price_differences':changes,'issues':issues,
             'verified_orders':verified,'inventory':inventory,'network_audit':session.audit}
        latest=DEST/'independent_whole_account_audit.json'
        if latest.exists():
            previous=read_json(latest)
            stamp=previous['checked_at'].replace(':','').replace('-','').replace('+','_')
            saved=DEST/('audit_snapshot_'+stamp+'.json')
            if not saved.exists():write_json(saved,previous)
        write_json(latest,out)
        if len(directories)>4:write_json(SUPPLEMENT/'independent_whole_account_audit.json',out)
        print(json.dumps({k:v for k,v in out.items() if k not in ('verified_orders','inventory','network_audit')},ensure_ascii=False,indent=2))
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Read-only whole-account audit failed:',type(error).__name__);raise SystemExit(1)

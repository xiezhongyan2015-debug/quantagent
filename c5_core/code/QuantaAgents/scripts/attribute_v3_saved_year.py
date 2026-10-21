"""One frozen, read-only attribution of the three existing 2019 accounts.

No market read, model, strategy execution, optimization or old-scope update.
"""
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from quanta_agents.meta_v3.kernel import ROOT
from quanta_agents.meta_v3.ledger import Ledger,digest,need
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3.research_iteration import diagnose,public_diagnosis


def main():
    parent=ROOT/'experiment_traces/meta_framework_v3/year_research_001'
    root=ROOT/'experiment_traces/meta_framework_v3/year_attribution_001'
    ledger=Ledger(parent);history=ledger.history('year_reversal_001')
    rows=[r for r in history if r['status']=='applied' and r['response']['action']=='develop_strategy']
    need(len(rows)==3,'exact original three account scope')
    sources=[]
    for row in rows:
        path=parent/'tools/year_reversal_001'/row['id']/'artifact.json'
        need(path.stat().st_size<=8*1024**2,'saved input byte bound')
        sources.append({'evidence_id':row['id'],'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'artifact_hash':row['result']['artifact_hash'],'bytes':path.stat().st_size})
    source_files=[Path(__file__).resolve(),ROOT/'src/quanta_agents/meta_v3/research_iteration.py']
    plan={'kind':'saved_year_attribution_v1','created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'All three already exposed full-capital 2019 development accounts; first is original reference.',
        'sources':sources,'max_accounts':3,'max_daily_rows_per_account':300,'max_trades_per_account':2000,
        'model_calls':0,'market_reads':0,'strategy_executions':0,'parameter_changes':0,
        'analysis':'Full initial cash, NAV, fees, recorded slippage, turnover, cash/exposure, monthly concentration and matched saved comparisons. No causal verdict or statistical selection.',
        'source_pins':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        'old_scope_plan_sha256':hashlib.sha256((parent/'plan.json').read_bytes()).hexdigest(),
        'old_history_hash':digest(history),'formal_target_success':False}
    root.mkdir(exist_ok=False);save_once(root/'plan.json',plan)
    inputs=[]
    for source in sources:
        raw=Path(source['path']).read_bytes()
        need(hashlib.sha256(raw).hexdigest()==source['sha256'],'saved source drift')
        artifact=json.loads(raw)
        need(digest(artifact)==source['artifact_hash'],'saved result differs from settled ledger')
        body=artifact['raw']['result']
        need(body is not None and len(body['daily'])<=300 and len(body['trades'])<=2000,'account scope bound')
        inputs.append((source['evidence_id'],artifact))
    result=diagnose(inputs);save_once(root/'result.json',result)
    checks=[]
    for (_,original),actual in zip(inputs,result['accounts']):
        body=original['raw']['result'];capital=Decimal(body['initial_cash']);last=capital;peak=capital;drawdown=Decimal(0)
        daily=body['daily'];monthly={};notional=Decimal(0);fees=Decimal(0)
        for row in daily:
            nav=Decimal(row['simulated_net_asset_value']);month=row['date'][:7]
            monthly[month]=monthly.get(month,Decimal(0))+nav-last
            peak=max(peak,nav);drawdown=max(drawdown,(peak-nav)/peak);last=nav
        for trade in body['trades']:
            notional+=Decimal(trade['raw_price'])*trade['quantity'];fees+=Decimal(trade['fees']['total'])
        check={'evidence_id':actual['evidence_id'],'full_capital_and_daily_denominator':
            capital==Decimal(actual['initial_cash']) and len(daily)==actual['saved_cash_days']==244,
            'final_cash_snapshot_matches':Decimal(body['final_snapshot']['cash'])==Decimal(daily[-1]['cash_available']),
            'final_fee_snapshot_matches':Decimal(body['final_snapshot']['fees_paid'])==fees,
            'pnl_telescopes':last-capital==Decimal(actual['net_pnl'])==sum(monthly.values()),
            'monthly_all_retained':monthly=={r['month']:Decimal(r['net_change']) for r in actual['monthly']},
            'fees_all_trades':fees==Decimal(actual['fees_on_recorded_trades']),
            'turnover_all_trades':notional/capital==Decimal(actual['turnover_multiple_of_initial_cash']),
            'drawdown':abs(drawdown-Decimal(actual['maximum_drawdown']))<Decimal('1e-24'),
            'source_account_not_rewritten':hashlib.sha256(Path(next(s['path'] for s in sources if s['evidence_id']==actual['evidence_id'])).read_bytes()).hexdigest()==next(s['sha256'] for s in sources if s['evidence_id']==actual['evidence_id'])}
        checks.append(check)
    checks.append({'old_plan_unchanged':hashlib.sha256((parent/'plan.json').read_bytes()).hexdigest()==plan['old_scope_plan_sha256'],
        'old_history_unchanged':digest(ledger.history('year_reversal_001'))==plan['old_history_hash']})
    audit={'checks':checks,'all_passed':all(v is True for c in checks for k,v in c.items() if k!='evidence_id'),
        'method':'Independent Decimal arithmetic from original saved cash-day and trade rows; no strategy rerun.',
        'formal_target_success':False}
    save_once(root/'independent_audit.json',audit)
    need(audit['all_passed'],'saved attribution audit failed; retained for inspection')
    summary=public_diagnosis(result);save_once(root/'summary.json',summary)
    files=[p for p in root.iterdir() if p.is_file()]
    save_once(root/'closed.json',{'closed_at':datetime.now(timezone.utc).isoformat(),'plan_hash':digest(plan),
        'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'new_model_calls':0,
        'new_strategy_executions':0,'formal_target_success':False})
    print(json.dumps({'root':str(root),'audit_passed':True,'summary':summary},ensure_ascii=True))


if __name__=='__main__':main()

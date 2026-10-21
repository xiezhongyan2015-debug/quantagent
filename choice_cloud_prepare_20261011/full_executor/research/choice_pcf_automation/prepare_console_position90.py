"""Prepare a read-only preview and next-click settings for the user's 90% rule.

This does not authenticate, launch workers, submit/cancel orders, delete STOP files,
restore the canceled dated session, or create a scheduler task.
"""
import copy
import datetime as dt
import json
from pathlib import Path
import pandas as pd
from console_core import BASE_DIR,HOME,config_checked,pcf_targets,roll_day,dec
from console_runtime import active_session,latest_session
from position_floor import POLICY_DEFAULTS,plan_fraction
from watch_engine import save,reserve
from quanta_agents.factor_lab_a.pcf import normalize_code,validate_pcf
from quanta_agents.factor_lab_a.pcf_automation import read_json,digest,now_cn

DAY='2026-09-23';SIGNAL='2026-09-22'
OUT=HOME/'plans'/DAY


def main():
    if now_cn().date().isoformat()!=SIGNAL:raise RuntimeError('This requested configuration change is bound to the signal evening')
    if active_session():raise RuntimeError('Existing worker must be stopped and reconciled before changing its next-run settings')
    previous=read_json(HOME/'settings.json')
    config=config_checked(dict(previous,target_fraction=.9,capital_limit=9500000,prefer_sealed_plan=False,
        position_floor=dict(POLICY_DEFAULTS,enabled=True,recovery_unit='trading_days')))
    source=latest_session();original=read_json(source/'private_state.json');seed=roll_day(original,DAY)
    arc=BASE_DIR/'daily_public_pcf/state/archive'/SIGNAL
    record=read_json(arc/'pcf.json');manifest=read_json(arc/'manifest.json');validate_pcf(record)
    if record['date']!=SIGNAL or manifest['execution_date']!=DAY or (arc/'REVISION_BLOCK.json').exists():raise RuntimeError('PCF date/revision mismatch')
    if digest({k:record[k] for k in ('date','baseinfo','stocklist')})!=manifest['pcf_sha256']:raise RuntimeError('PCF content mismatch')
    closes=read_json(BASE_DIR/'session_20260923/close_quotes.json')
    quotes=copy.deepcopy(closes)
    for code,q in quotes.items():
        stamp=dt.datetime.fromisoformat(q['timestamp'])
        if stamp.date().isoformat()!=SIGNAL or stamp.time()<dt.time(15):raise RuntimeError('Closing reference is not the signal-day close')
        # Tomorrow's previous-close field will be today's close, not yesterday's.
        q['previous_close']=q['last']
    holdings=seed['baseline']['positions'];cash=dec(seed['baseline']['cash'])
    nav=cash+dec(config['excluded_cash_cny'])+sum(dec(closes[c]['last'])*n for c,n in holdings.items())
    universe=set(pd.read_parquet(config['membership_path']).symbol.map(normalize_code))
    targets=pcf_targets(record,DAY,SIGNAL,float(nav),config,quotes,universe,holdings)
    for c in holdings:
        targets.setdefault(c,dict(code=c,quantity=0,reference_price=quotes[c]['previous_close']))
        if not config['allow_sells']:targets[c]['quantity']=max(holdings[c],targets[c]['quantity'])
    buys={c:t['quantity']-holdings.get(c,0) for c,t in targets.items() if t['quantity']>holdings.get(c,0)}
    sells={c:holdings[c]-targets[c]['quantity'] for c in holdings if holdings[c]>targets[c]['quantity']}
    if sells and not config['allow_sells']:raise RuntimeError('Preview requires sells but current settings do not allow them')
    value=sum(dec(t['reference_price'])*t['quantity'] for t in targets.values())
    principal=sum(dec(targets[c]['reference_price'])*n for c,n in buys.items())
    worst_buy=sum(reserve(dec(targets[c]['reference_price'])*(1+dec(config['price_guard']))*n) for c,n in buys.items())
    worst_total=dec(seed['carried_reserve'])+worst_buy
    if value<nav*plan_fraction(config) or worst_buy>cash or worst_total>dec(config['capital_limit']):raise RuntimeError('Preview cannot support the requested floor within existing order protections')
    OUT.mkdir(parents=True,exist_ok=True)
    plan=dict(prepared_at=now_cn().isoformat(),execution_date=DAY,signal_date=SIGNAL,preview_only=True,
        source_state_sha256=digest(original),pcf_sha256=manifest['pcf_sha256'],config_sha256=digest(config),
        minimum_stock_fraction=.9,planning_fraction=float(plan_fraction(config)),signal_close_nav=str(nav),
        estimated_stock_value=str(value),estimated_stock_fraction=float(value/nav),buy_principal=str(principal),
        buy_names=len(buys),sell_names=len(sells),worst_buy_reserve=str(worst_buy),worst_total_reserved=str(worst_total),
        maximum_security_value=str(max(dec(t['reference_price'])*t['quantity'] for t in targets.values())),
        capital_limit=config['capital_limit'],targets=targets,buys=buys,sells=sells,
        first_deadline=DAY,recovery_days=3,recovery_unit='trading_days',detection_day_counts_as_first=True,
        old_scheduled_session_remains_cancelled=True,orders_submitted=0,cancellations_submitted=0)
    save(OUT/'plan_preview.json',plan)
    if not (OUT/'settings_before.json').exists():save(OUT/'settings_before.json',previous)
    save(OUT/'settings_after.json',config)
    save(OUT/'user_requirement.json',dict(recorded_at=now_cn().isoformat(),
        instruction='明天收盘股票市值占总资产90%；之后低于90%须在三个交易日内调整回来。使用一键控制台，保留旧计划取消状态。',
        first_deadline=DAY,minimum_fraction=.9,recovery_days=3,recovery_unit='trading_days',
        startup='manual_console_click',old_stop_preserved=True))
    save(HOME/'settings.json',config)
    print(json.dumps({k:v for k,v in plan.items() if k not in ('targets','buys','sells')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

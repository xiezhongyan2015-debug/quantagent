"""Prepare a close-price plan only; public GETs, no broker/ledger/settings mutations."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import datetime as dt
import json
import time

import pandas as pd
import requests
from console_core import HOME, BASE_DIR, config_checked, days_for, pcf_targets, reconcile, roll_day, dec
from console_runtime import latest_session, active_session
from watch_engine import save, reserve
from position_floor import plan_fraction, recovery_deadline
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf import download_pcf, normalize_code, validate_pcf
from quanta_agents.factor_lab_a.pcf_automation import read_json, digest, now_cn

DAY, SIGNAL = '2026-09-24', '2026-09-23'
OUT = HOME/'plans'/DAY
DIAG = HOME/'diagnostics/20260924_001112'


def main():
    if now_cn().date().isoformat() != DAY or now_cn().time() >= dt.time(9, 0):
        raise RuntimeError('This close-price preview is bound to the requested premarket date')
    if active_session():
        raise RuntimeError('Do not prepare from a changing live ledger')
    OUT.mkdir(parents=True, exist_ok=True)
    c = config_checked(read_json(HOME/'settings.json'))
    calendar, signal = days_for(c, DAY)
    assert signal == SIGNAL
    source = latest_session(); state = read_json(source/'private_state.json')
    status=read_json(source/'status_redacted.json')
    diagnostic=Path(status['receipt_expiry_evidence']) if state.get('expired_unacknowledged') else DIAG
    snapshots=[read_json(diagnostic/f'snapshot_{i}.json') for i in range(2)]
    snapshots=[x.get('raw',x) for x in snapshots]
    audits = [reconcile(raw, state, c['excluded_cash_cny'], c['cash_drift_tolerance_cny'], allow_quarantined=True) for raw in snapshots]
    assert audits[0] == audits[1]
    a = audits[-1]
    save(OUT/'plan_audit.json',a)
    rollover_error = None
    try:
        roll_day(state, DAY)
    except TradeError as e:
        rollover_error = str(e)
    record_path = OUT/'pcf_20260923.json'
    record = read_json(record_path) if record_path.exists() else download_pcf('563030', SIGNAL, str(record_path))
    validate_pcf(record)
    assert record['date'] == SIGNAL
    codes = {normalize_code(str(x['C_STOCKCODE'])) for x in record['stocklist'] if float(x['L_NUMBER']) > 0} | set(a['positions'])
    path = OUT/'close_quotes_20260923.json'
    quotes = read_json(path) if path.exists() else {}
    def fetch(code):
        for attempt in range(3):
            try:
                q = quote(code)
                stamp = dt.datetime.fromisoformat(q['timestamp'])
                if stamp.date().isoformat() != SIGNAL or stamp.time() < dt.time(15) or q['last'] <= 0:
                    raise RuntimeError(f'Not a validated {SIGNAL} closing reference: {code}')
                return code, q
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(1+attempt)
    pending = sorted(codes-set(quotes))
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(fetch,code) for code in pending]
        for task in as_completed(tasks):
            code,q = task.result(); quotes[code] = q; save(path,quotes)
            if len(quotes)%25==0:
                print('Closing references',len(quotes),'/',len(codes),flush=True)
    reference = copy.deepcopy(quotes)
    for q in reference.values():
        # This field is only for a documented closing-price preview, not a live quote.
        q['previous_close'] = q['last']
    universe = set(pd.read_parquet(c['membership_path']).symbol.map(normalize_code))
    targets = pcf_targets(record, DAY, SIGNAL, a['total_assets'], c, reference, universe, a['positions'])
    for code, held in a['positions'].items():
        targets.setdefault(code,dict(code=code,quantity=0,reference_price=reference[code]['previous_close']))
        if not c['allow_sells']:
            targets[code]['quantity'] = max(held,targets[code]['quantity'])
    buys = {code:t['quantity']-a['positions'].get(code,0) for code,t in targets.items() if t['quantity']>a['positions'].get(code,0)}
    sells = {code:a['positions'][code]-targets[code]['quantity'] for code in a['positions'] if targets[code]['quantity']<a['positions'][code]}
    blocked = set(a['isolated_codes'])
    buy_value = sum(dec(reference[code]['last'])*qty for code,qty in buys.items())
    worst_reserve = sum(reserve(dec(reference[code]['last'])*(1+dec(c['price_guard']))*qty) for code,qty in buys.items())
    value = sum(dec(reference[code]['last'])*t['quantity'] for code,t in targets.items())
    held_value = sum(dec(reference[code]['last'])*qty for code,qty in a['positions'].items())
    assert abs(held_value-dec(a['market_value'])) <= dec('.05'), 'Closing-price holdings and account value differ'
    budget_ok = worst_reserve <= dec(a['cash']) and dec(a['paid'])+dec(a['uncertain_buy_reserve'])+worst_reserve <= dec(c['capital_limit'])
    preview = dict(prepared_at=now_cn().isoformat(),execution_date=DAY,signal_date=SIGNAL,preview_only=True,
        source_ledger_sha256=digest(state),config_sha256=digest(c),pcf_sha256=digest({k:record[k] for k in ['date','baseinfo','stocklist']}),
        total_assets=a['total_assets'],stock_value=a['market_value'],stock_fraction=a['stock_fraction'],
        planning_fraction=float(plan_fraction(c)),minimum_fraction=c['target_fraction'],
        account_cash_cny=a['account_cash_before_quarantine'],conservative_cash_cny=a['cash'],unknown_reserve_cny=a['uncertain_buy_reserve'],
        estimated_target_value_cny=str(value),estimated_target_fraction=float(value/dec(a['total_assets'])),
        buy_names=len(buys),sell_names=len(sells),buy_principal_cny=str(buy_value),worst_new_buy_reserve_cny=str(worst_reserve),
        budget_ok=budget_ok,rollover_ready=rollover_error is None,rollover_blocker=rollover_error,
        account_evidence=str(diagnostic),expired_receipt_count=len(state.get('expired_unacknowledged',[])),
        isolated_symbols=sorted(blocked),blocked_plan_buys={k:v for k,v in buys.items() if k in blocked},
        targets=targets,buys=buys,sells=sells,limits=c,
        recovery_deadline_if_below_floor_on_20260924=recovery_deadline(DAY,calendar,c['position_floor']),
        orders_submitted=0,cancellations_submitted=0,live_ledger_edits=0,settings_edits=0,
        assumptions='Closing-reference preview. Fresh intraday quotes, eligibility, account and order reconciliation are required at launch. No future PCF assumed.')
    save(OUT/'plan_preview.json',preview)
    # Archive exact official data for the already-configured prior-day PCF path.
    archive = BASE_DIR/'daily_public_pcf/state/archive'/SIGNAL
    if (archive/'REVISION_BLOCK.json').exists():
        raise RuntimeError('Existing PCF revision block must be reviewed')
    if (archive/'pcf.json').exists():
        old=read_json(archive/'pcf.json')
        assert digest({k:old[k] for k in ['date','baseinfo','stocklist']}) == preview['pcf_sha256']
    else:
        save(archive/'pcf.json',record)
        save(archive/'manifest.json',dict(pcf_date=SIGNAL,execution_date=DAY,pcf_sha256=preview['pcf_sha256'],captured_at=record['retrieved_at'],historical_publication_timestamp_verified=False))
    print(json.dumps({k:v for k,v in preview.items() if k not in ['targets','buys','sells','limits']},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()

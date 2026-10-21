"""GET-only account verification and durable local closure of an expired intent.

The CLI defaults to preview. The console calls this while holding launch.lock.
No worker is started and the account transport cannot submit or cancel orders.
"""
import argparse
import json
import time
import uuid
from types import SimpleNamespace
from console_core import config_checked, dec, roll_day
from console_runtime import (ACCOUNT, APP_DIR, Broker, Worker, active_session,
                             account_snapshot, latest_session, mutex)
from choice_auth import ChoiceAuthenticator, install_tokens
from receipt_recovery import expire_previous_day, unresolved
from cloud_account import DIRECTORIES
from verify_readonly import GetOnlySession
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import SessionExpired, TradeError
from quanta_agents.factor_lab_a.pcf_automation import now_cn, read_json, digest, lock as legacy_lock


def commit_source(source,original,proposed,destination):
    """Save evidence before the atomic ledger replacement; never rebase cash."""
    if digest(read_json(source/'private_state.json'))!=digest(original):
        raise TradeError('核验期间来源账本发生变化，取消归档')
    if active_session():raise TradeError('核验期间出现交易进程，取消归档')
    if original['baseline']!=proposed['baseline']:raise TradeError('跨日归档不得改写原始资金或持仓基线')
    save(destination/'proposed_ledger.json',proposed)
    save(source/'private_state.json',proposed)
    status=read_json(source/'status_redacted.json') if (source/'status_redacted.json').exists() else {}
    status.update(at=now_cn().isoformat(),status='previous_receipt_reconciled',reason=proposed['ack_recovery']['reason'],
        unresolved_intents=0,trading_writes_suspended=True,ack_recovery=proposed['ack_recovery'],
        receipt_expiry_evidence=str(destination),frozen_cny=proposed['last_audit']['frozen'])
    save(source/'status_redacted.json',status)


def resolve_source(source,config,apply=False):
    """Caller must own launch.lock; share both execution locks with all runners."""
    with mutex(APP_DIR/'account_execution.lock'),legacy_lock(DIRECTORIES[0]/'state/choice.lock'):
        if active_session():raise TradeError('交易进程仍在运行，不能跨日归档')
        original=read_json(source/'private_state.json');now=now_cn()
        if original['day']>=now.date().isoformat() or len(unresolved(original))!=1:
            raise TradeError('仅处理前一交易日的一笔未确认委托')
        destination=APP_DIR/'diagnostics'/('receipt_expiry_'+now.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
        destination.mkdir(parents=True)
        save(destination/'input_ledger.json',original)
        if (source/'status_redacted.json').exists():save(destination/'input_status.json',read_json(source/'status_redacted.json'))
        trace=Worker.receipt_trace(SimpleNamespace(run=source))
        auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);observations=[]
        try:
            tokens=auth.current_tokens()
            install_tokens(tokens,http,ACCOUNT);tokens.clear()
            broker=Broker({},allow_orders=False,session=http)
            for index in range(2):
                begin=now_cn()
                try:raw=account_snapshot(broker,original)
                except SessionExpired:
                    tokens=auth.login();install_tokens(tokens,http,ACCOUNT);tokens.clear()
                    begin=now_cn();raw=account_snapshot(broker,original)
                observation=dict(started_at=begin.isoformat(),finished_at=now_cn().isoformat(),raw=raw)
                observations.append(observation);save(destination/f'snapshot_{index}.json',observation)
                if index==0:time.sleep(5)
            proposed=expire_previous_day(original,config,observations,now_cn(),trace)
            rolled=roll_day(proposed,now_cn().date().isoformat())
            # Verify the new-day baseline against the same complete snapshot too.
            from console_core import reconcile
            next_audit=reconcile(observations[-1]['raw'],rolled,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'])
            save(destination/'next_day_preview.json',dict(state=rolled,audit=next_audit))
            save(destination/'proposed_ledger.json',proposed)
            if apply:commit_source(source,original,proposed,destination)
            last=proposed['last_audit'];entry=proposed['expired_unacknowledged'][-1]
            report=dict(at=now_cn().isoformat(),applied=apply,source=str(source),diagnostics=str(destination),
                code=entry['intent']['order']['code'],classification=entry['classification'],
                holding_quantity=last['positions'].get(entry['intent']['order']['code'],0),
                released_reserve_cny=entry['proof']['released_reserve_cny'],
                conservative_cash_cny=last['cash'],frozen_cny=last['frozen'],stock_fraction=last['stock_fraction'],
                unresolved_count=len(unresolved(proposed)),rollover_ready=True,order_writes=0,worker_started=False,
                baseline_unchanged=original['baseline']==proposed['baseline'])
            save(destination/'report.json',report)
            return proposed,report
        finally:
            save(destination/'http_trace_redacted.json',http.audit)
            http.headers.clear();http.close();auth.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    config=config_checked(read_json(APP_DIR/'settings.json'))
    with mutex(APP_DIR/'launch.lock'):
        _,report=resolve_source(latest_session(),config,args.apply)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__,reason=str(error) if isinstance(error,TradeError) else '跨日核验未完成'),ensure_ascii=False))
        raise SystemExit(1)

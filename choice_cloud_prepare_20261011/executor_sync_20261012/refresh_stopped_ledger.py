"""Reaudit a prior-day session that failed preflight without submitting orders."""
import copy
import time
import uuid
from console_core import reconcile,dec
from console_runtime import APP_DIR,ACCOUNT,mutex,active_session,account_snapshot,Broker
from choice_auth import ChoiceAuthenticator,install_tokens
from cloud_account import DIRECTORIES
from verify_readonly import GetOnlySession
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import TradeError,SessionExpired
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,digest,lock as legacy_lock


def verified_state(original,config,observations,now):
    if original['day']>=now.date().isoformat() or original['attempts'] or original.get('last_audit'):
        raise TradeError('仅自动复核前日未下单且首次核验失败的账本')
    if len(observations)!=2:raise TradeError('需要两轮完整账户核验')
    import datetime as dt
    audits=[];stable=[]
    times=[dt.datetime.fromisoformat(x['at']) for x in observations]
    if any(t.tzinfo is None for t in times) or not 5<=(times[1]-times[0]).total_seconds()<=120 or not times[1]<=now or not 0<=(now-times[0]).total_seconds()<=120:
        raise TradeError('复核快照时间无效或过期')
    for observation in observations:
        if not observation['raw'].get('history_complete'):raise TradeError('历史委托查询不完整')
        audit=reconcile(observation['raw'],original,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'])
        if audit['pending'] or dec(audit['frozen']):raise TradeError('挂单或冻结资金尚未结清')
        audits.append(audit)
        stable.append({k:audit[k] for k in ('positions','sellable','states','cash','paid','frozen','cash_drift_cny')})
    if stable[0]!=stable[1]:raise TradeError('两轮账户核验不一致，不能跨日恢复')
    proposed=copy.deepcopy(original);audit=audits[-1]
    if audit['cash_drift_requires_confirmation']:
        event=dict(at=now.isoformat(),day=original['day'],amount_cny=audit['cash_drift_cny'],
            tolerance_cny=audit['cash_drift_tolerance_cny'],baseline_cash=original['baseline']['cash'],
            source='two_consistent_snapshots_no_pending_or_frozen')
        proposed['cash_variance']=event;proposed.setdefault('cash_variance_events',[]).append(event.copy())
        audit['cash_drift_requires_confirmation']=False
    audit['checked_at']=now.isoformat();proposed['last_audit']=audit
    proposed['preflight_recovery']=dict(at=now.isoformat(),source_sha256=digest(original),
        snapshot_sha256=[digest(x) for x in observations],meaning='No orders in stopped session; full history and balances reconciled before rolling day.')
    return proposed


def refresh_source(source,config):
    # start_console already holds launch.lock. Both execution locks prevent a
    # console worker or dated legacy runner writing while the ledger is checked.
    with mutex(APP_DIR/'account_execution.lock'),legacy_lock(DIRECTORIES[0]/'state/choice.lock'):
        if active_session():raise TradeError('仍有交易进程，不能复核旧账本')
        original=read_json(source/'private_state.json')
        if original['attempts'] or original.get('last_audit'):raise TradeError('旧账本不属于首次核验失败且未下单的状态')
        dest=APP_DIR/'diagnostics'/('preflight_recovery_'+now_cn().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
        save(dest/'input_ledger.json',original)
        auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);observations=[]
        try:
            tokens=auth.current_tokens();install_tokens(tokens,http,ACCOUNT);tokens.clear()
            broker=Broker({},allow_orders=False,session=http)
            for index in range(2):
                try:raw=account_snapshot(broker,original)
                except SessionExpired:
                    tokens=auth.login();install_tokens(tokens,http,ACCOUNT);tokens.clear()
                    raw=account_snapshot(broker,original)
                observation=dict(at=now_cn().isoformat(),raw=raw);observations.append(observation)
                save(dest/f'snapshot_{index}.json',observation)
                if index==0:time.sleep(5)
            proposed=verified_state(original,config,observations,now_cn())
            if digest(read_json(source/'private_state.json'))!=digest(original) or active_session():
                raise TradeError('核验期间账本或进程发生变化，取消恢复')
            save(dest/'proposed_ledger.json',proposed)
            save(source/'private_state.json',proposed)
            return proposed
        finally:
            save(dest/'http_trace_redacted.json',http.audit)
            http.headers.clear();http.close();auth.close()

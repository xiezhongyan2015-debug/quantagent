"""Diagnose the latest simulation ledger using account GETs only; no ledger edits."""
import copy
import json
import time
from decimal import Decimal
from pathlib import Path
from choice_auth import ChoiceAuthenticator, install_tokens
from console_runtime import ACCOUNT, APP_DIR, Broker, account_snapshot, latest_session
from console_core import dec, reconcile
from verify_readonly import GetOnlySession
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, SessionExpired, TradeError
from quanta_agents.factor_lab_a.pcf_automation import now_cn, read_json


def main():
    source = latest_session()
    state = read_json(source / 'private_state.json')
    destination = APP_DIR / 'diagnostics' / now_cn().strftime('%Y%m%d_%H%M%S')
    destination.mkdir(parents=True)
    save(destination / 'input_ledger.json', state)
    auth = ChoiceAuthenticator()
    http = GetOnlySession(ACCOUNT)
    broker = None
    reports = []
    try:
        tokens = auth.current_tokens()
        install_tokens(tokens, http, ACCOUNT)
        tokens.clear()
        broker = Broker({}, allow_orders=False, session=http)
        for index in range(2):
            try:
                raw = account_snapshot(broker, state)
            except SessionExpired:
                tokens = auth.login()
                install_tokens(tokens, http, ACCOUNT)
                tokens.clear()
                raw = account_snapshot(broker, state)
            save(destination / f'snapshot_{index}.json', raw)
            result = dict(at=now_cn().isoformat(), source=source.name,
                          balance={k: raw['balance'].get(k) for k in ('availBalance', 'frozenMoney', 'totalAssets', 'totalPosValue')},
                          submitted=len(state['attempts']), holding_names=len(raw['holds']))
            try:
                result['audit'] = reconcile(raw, state)
                result['strict_passed'] = True
            except TradeError as error:
                result['strict_passed'] = False
                result['reason'] = str(error)
                if str(error).startswith('可用现金、冻结资金与成交净额暂不一致'):
                    expected = dict(state.get('prior', {}))
                    expected.update({a['oid']: a['order'] for a in state['attempts']})
                    net = Decimal(0)
                    for deal in raw['deals']:
                        order = expected.get(str(deal['orderId']))
                        if order is None:
                            continue
                        fee = sum((dec(deal[k]) for k in ('qsYj', 'ghCost', 'yhs')), Decimal(0))
                        principal = dec(deal['price']) * dec(deal['tradeCount'])
                        net += principal + fee if order.get('side', 1) == 1 else -principal + fee
                    current = dec(raw['balance']['availBalance']) - dec('395.66')
                    measured_baseline = current + dec(raw['balance']['frozenMoney']) + net
                    result['cash_delta_cny'] = str(measured_baseline - dec(state['baseline']['cash']))
                    diagnostic = copy.deepcopy(state)
                    diagnostic['baseline']['cash'] = str(measured_baseline)
                    try:
                        result['audit'] = reconcile(raw, diagnostic)
                        result['all_other_checks_passed'] = True
                    except TradeError as other:
                        result['all_other_checks_passed'] = False
                        result['other_reason'] = str(other)
            private = result.pop('audit', None)
            if private:
                save(destination / f'audit_{index}.json', private)
                result['pending_count'] = len(private['pending'])
                result['stock_fraction'] = private['stock_fraction']
            reports.append(result)
            save(destination / 'report.json', dict(reads=http.audit, snapshots=reports, order_writes=0, ledger_edits=0))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if index == 0:
                time.sleep(5)
        print('diagnostics=' + str(destination), flush=True)
    finally:
        http.headers.clear()
        http.close()
        auth.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, reason=str(error) if isinstance(error, TradeError) else 'Read-only diagnosis failed'), ensure_ascii=False))
        raise SystemExit(1)

"""One-time, read-only enrollment of a fresh Choice competition simulation account.

Never sends orders/cancellations. Refuses to invent a baseline for an account
with existing positions, fills, active orders or a previous local journal.
"""
import argparse
import os
import time
from pathlib import Path

from local_access import APP_DIR, GetOnlySession, session_tokens
from choice_auth import ChoiceAuthenticator, ENV_KEYS, install_tokens, uid_digest
from console_core import dec, reconcile
from console_runtime import historical_rows, mutex
from support import save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, TradeError, number
from quanta_agents.factor_lab_a.pcf_automation import digest, now_cn


def initial_state(raw, day):
    if raw['deals'] or any(number(r['count'], True) for r in raw['holds']):
        raise TradeError('首次初始化只支持无持仓、无历史成交的账户；已有账户须独立核验并迁移账本')
    for row in raw['orders']:
        if (str(row['status']) != '8' or number(row['tradeCount'], True) != 0
                or str(row['orderDate']).replace('-', '') >= day.replace('-', '')):
            raise TradeError('账户存在当日委托或未结束的历史委托，不能创建空账本')
    balance = raw['balance']
    cash = dec(number(balance['availBalance']))
    if (cash <= 0 or number(balance['frozenMoney']) != 0
            or number(balance['totalPosValue']) != 0
            or abs(dec(number(balance['totalAssets'])) - cash) > dec('.03')):
        raise TradeError('初始资产必须全部为可用现金，无持仓或冻结，且总资产一致')
    state = dict(day=day, baseline=dict(positions={}, cash=str(cash), paid='0'),
                 historical={}, prior={}, attempts=[], targets={}, carried_reserve='0',
                 baseline_security_reserve={}, deferred={}, quotes={})
    state['last_audit'] = reconcile(raw, state)
    return state


def snapshot(broker):
    orders = broker._rows('AguTrade/SpoOrders')
    deals = broker._rows('AguTrade/SpoDeal')
    # Always inspect historical endpoints at enrollment, even when today's list
    # contains rows. A cash-only account may have traded and liquidated before.
    seen = {str(row['orderId']): row for row in orders}
    for row in historical_rows(broker, 'AguTrade/SpoOrders'):
        oid = str(row['orderId'])
        if oid in seen and seen[oid] != row:
            raise TradeError('初始化期间历史与当前委托不一致')
        seen[oid] = row
    deals += historical_rows(broker, 'AguTrade/SpoDeal')
    return dict(orders=list(seen.values()), deals=deals,
                holds=broker._rows('AguTrade/SpoHold'),
                balance=broker._request('AguTrade/SpoBalInfo', params={'accId': broker.account})['Data'])


def enroll(account, profile):
    if not account or not account.strip():
        raise TradeError('模拟账户ID不能为空')
    profile = Path(profile).expanduser().resolve()
    with mutex(APP_DIR / 'launch.lock'), mutex(APP_DIR / 'account_execution.lock'):
        if (APP_DIR / 'account.json').exists() or list((APP_DIR / 'runs').glob('*/*')):
            raise TradeError('已有账户绑定或账本；不能重新初始化、清空或覆盖')
        tokens = session_tokens(tuple(ENV_KEYS), profile=profile)
        identity = dict(account_mode='competition_simulation_only', account_id=account,
                        uid_sha256=uid_digest(tokens['UID']), choice_profile=str(profile))
        auth = ChoiceAuthenticator(identity=identity,
                                   cookie_reader=lambda names: session_tokens(names, profile=profile))
        previous = {key: os.environ.get(key) for key in (*ENV_KEYS.values(), 'CHOICE_ACCOUNT_ID')}
        http = GetOnlySession(account)
        try:
            tokens = auth.sso(tokens)
            install_tokens(tokens, http, account)
            tokens.clear()
            broker = ChoiceBroker({}, allow_orders=False, session=http)
            day = now_cn().date().isoformat()
            first = initial_state(snapshot(broker), day)
            time.sleep(5)
            second = initial_state(snapshot(broker), day)
            if now_cn().date().isoformat() != day or digest(first) != digest(second):
                raise TradeError('两轮初始化快照不一致，请等待账户稳定后重试')
            second['last_audit']['checked_at'] = now_cn().isoformat()
            second['account_binding_sha256'] = digest(identity)
            destination = APP_DIR / 'runs' / day / 'bootstrap'
            save(destination / 'private_state.json', second)
            save(APP_DIR / 'account.json', identity)
            save(destination / 'status_redacted.json', dict(status='initialized',
                 reason='只读初始化完成，未提交任何委托；请配置策略和限额后启动'))
        finally:
            tokens.clear()
            http.headers.clear()
            http.close()
            auth.close()
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='你本人在 Choice 金融实验室的比赛模拟账户 ID')
    parser.add_argument('--choice-profile', default='C:/Eastmoney/Choice/EMWeb/Cache')
    parser.add_argument('--simulation-account', action='store_true', required=True,
                        help='确认该 ID 属于比赛模拟账户，授权读取该本机 Choice 登录会话')
    args = parser.parse_args()
    try:
        enroll(args.account, args.choice_profile)
    except Exception as error:
        print(str(error) if isinstance(error, TradeError) else '只读初始化失败：' + type(error).__name__)
        raise SystemExit(1) from None
    print('初始化完成，未下单。请重启控制台，配置日历、策略和限额。')


if __name__ == '__main__':
    main()

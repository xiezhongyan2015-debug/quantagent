"""Codex onboarding CLI: inspect, configure, and verify an account read-only.

inspect/configure never read session cookies or call the network. verify-account
uses the GET-only account transport and an optional normal SSO exchange. No command
in this module starts a trading worker, submits an order, or cancels an order.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / 'runtime'
DEPENDENCIES = ('requests', 'psutil', 'cryptography', 'numpy', 'pandas', 'pyarrow', 'yaml', 'tkinter')
PATH_KEYS = ('calendar_path', 'membership_path', 'external_file', 'external_script')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def candidate_profiles():
    # Check only the conventional installation directories, never scan cookies.
    roots = [Path('C:/Eastmoney/Choice')]
    for name in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        if os.environ.get(name): roots.append(Path(os.environ[name]) / 'Eastmoney/Choice')
    result = []
    for root in roots:
        profile = root / 'EMWeb/Cache'
        if (profile / 'LocalPrefs.json').is_file() and (profile / 'Network/Cookies').is_file():
            result.append(str(profile.resolve()))
    return sorted(set(result))


def normalized_config(value, relative_to, prior=None):
    from console_core import DEFAULTS, config_checked
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('配置必须为对象，且不能包含未知字段')
    result = {**DEFAULTS, **(prior or {}), **value}
    for key in PATH_KEYS:
        if result[key]:
            path = Path(result[key]).expanduser()
            result[key] = str((path if path.is_absolute() else relative_to / path).resolve())
    return config_checked(result)


def input_checks(config, day):
    from console_core import days_for
    from quanta_agents.factor_lab_a.pcf import normalize_code
    calendar = read(config['calendar_path'])
    if (not isinstance(calendar, list) or not calendar or sorted(set(calendar)) != calendar
            or any(not isinstance(x, str) or dt.date.fromisoformat(x).isoformat() != x for x in calendar)):
        raise ValueError('交易日历必须为真实、完整、升序且无重复的 ISO 日期列表')
    if day > config['calendar_valid_through'] or day > calendar[-1]:
        raise ValueError('交易日历已过期或未覆盖检查日期')
    signal = days_for(config, day)[1] if day in calendar else None
    # A weekend installation is allowed; the runner still rejects non-trading days.
    if signal is None and not any(x < day for x in calendar):
        raise ValueError('交易日历缺少检查日期之前的交易日')
    strategy = config['strategy']
    if strategy == 'pcf_563030':
        import pandas as pd
        table = pd.read_parquet(config['membership_path'])
        if 'symbol' not in table or table.empty or table.symbol.isna().any():
            raise ValueError('PCF 股票池缺少非空 symbol 列')
        symbols = [normalize_code(str(x)) for x in table.symbol]
        if len(set(symbols)) != len(symbols): raise ValueError('PCF 股票池存在重复证券')
    elif strategy == 'local_script':
        path = Path(config['external_script'])
        if not path.is_file() or path.suffix.lower() != '.py': raise ValueError('本地策略 Python 文件不存在')
    else:
        path = config['external_file'].replace('{date}', day).replace('{yyyymmdd}', day.replace('-', ''))
        target = read(path)
        if target.get('execution_date') != day or (signal and target.get('signal_date') != signal):
            raise ValueError('目标文件的执行日期或信号日期不正确')
        if target.get('mode') not in ('weights', 'shares') or not target.get('targets'):
            raise ValueError('目标文件必须包含非空 weights/shares 目标列表')
    return dict(strategy=strategy, execution_date=day, trading_day=signal is not None,
                signal_date=signal, calendar_valid_through=config['calendar_valid_through'])


def inspect(day):
    result = dict(status='needs_input', execution_date=day, checks=[],
                  choice_profile_candidates=candidate_profiles(), account_accessed=False, trading_started=False)
    def add(name, function):
        try:
            detail = function()
            result['checks'].append(dict(check=name, ok=True, detail=detail))
        except Exception as error:
            # Keep local paths, IDs, account balances and external exception bodies out of reports.
            result['checks'].append(dict(check=name, ok=False, error_type=type(error).__name__))
    missing = [name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]
    result['checks'].append(dict(check='environment', ok=sys.version_info >= (3, 10) and not missing,
                                 missing=missing))
    if missing: return result
    from local_access import binding
    from quanta_agents.factor_lab_a.pcf_automation import digest
    from console_core import roll_day
    from console_runtime import active_session, latest_session
    def identity():
        value = binding()
        return dict(account_mode=value['account_mode'], account_id='***' + value['account_id'][-4:])
    add('account_binding', identity)
    def journal():
        value = read(latest_session() / 'private_state.json')
        if value.get('account_binding_sha256') != digest(binding()): raise ValueError('binding mismatch')
        roll_day(value, day)
        if any(not a.get('oid') for a in value['attempts']): raise ValueError('unknown order outcome')
        return dict(day=value['day'], reviewed_locally=True)
    add('existing_journal', journal)
    def settings():
        value = normalized_config(read(RUNTIME / 'settings.json'), BASE)
        return input_checks(value, day)
    add('settings_and_inputs', settings)
    add('worker_status', lambda: dict(running=active_session() is not None))
    if all(x['ok'] for x in result['checks']): result['status'] = 'configured'
    result['next_step'] = 'verify-account, then open console' if result['status'] == 'configured' else 'resolve failed checks using CODEX_SETUP.md'
    if any(x['check'] == 'worker_status' and x['ok'] and x['detail']['running'] for x in result['checks']):
        result['next_step'] = 'show existing worker status; do not start another worker or verifier'
    return result


def configure(path, day):
    from console_runtime import active_session, mutex
    from support import save
    path = Path(path).resolve()
    with mutex(RUNTIME / 'launch.lock'):
        if active_session() is not None: raise ValueError('运行中不能应用配置，请先使用现有控制台停止并核验')
        previous = read(RUNTIME / 'settings.json') if (RUNTIME / 'settings.json').exists() else None
        value = normalized_config(read(path), path.parent, previous)
        detail = input_checks(value, day)
        if previous is not None and previous != value:
            from quanta_agents.factor_lab_a.pcf_automation import digest
            save(RUNTIME / 'config_history' / (digest(previous) + '.json'), previous)
        save(RUNTIME / 'settings.json', value)
    return dict(status='configured', inputs=detail, account_accessed=False, trading_started=False)


def verify_account():
    import time
    from local_access import binding, GetOnlySession
    from choice_auth import ChoiceAuthenticator, ENV_KEYS, install_tokens
    from console_runtime import active_session, latest_session, account_snapshot, mutex
    from console_core import roll_day, reconcile
    from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, FillSnapshotPending, SessionExpired
    from quanta_agents.factor_lab_a.pcf_automation import now_cn, digest
    identity = binding()
    with mutex(RUNTIME / 'launch.lock'), mutex(RUNTIME / 'account_execution.lock'):
        if active_session() is not None: raise ValueError('已有交易进程；查看已有状态，不启动独立核验')
        original = read(latest_session() / 'private_state.json')
        if original.get('account_binding_sha256') != digest(identity): raise ValueError('账本绑定不一致')
        day = now_cn().date().isoformat()
        state = roll_day(original, day)
        auth = ChoiceAuthenticator()
        http = GetOnlySession(identity['account_id'])
        previous = {k: os.environ.get(k) for k in (*ENV_KEYS.values(), 'CHOICE_ACCOUNT_ID')}
        tokens = {}
        try:
            tokens = auth.current_tokens()
            install_tokens(tokens, http, identity['account_id']); tokens.clear()
            broker = ChoiceBroker({}, allow_orders=False, session=http)
            deadline = time.monotonic() + 90
            consistent = 0
            sso_attempted = False
            while consistent < 2:
                if now_cn().date().isoformat() != day: raise ValueError('核验跨日，请重试')
                try:
                    audit = reconcile(account_snapshot(broker, state), state)
                    if audit['pending'] or float(audit['frozen']): raise ValueError('存在未结束委托或冻结资金')
                    consistent += 1
                except SessionExpired:
                    if sso_attempted: raise
                    sso_attempted = True; consistent = 0
                    tokens = auth.login(); install_tokens(tokens, http, identity['account_id']); tokens.clear()
                except FillSnapshotPending:
                    consistent = 0
                if time.monotonic() > deadline: raise ValueError('账户核验连续90秒未通过')
                if consistent < 2: time.sleep(5)
            return dict(status='account_verified', checked_at=now_cn().isoformat(),
                        consistent_snapshots=consistent, orders_submitted=0, cancellations_submitted=0,
                        pcf_checked=False, trading_started=False)
        finally:
            tokens.clear(); http.headers.clear(); http.close(); auth.close()
            for key, value in previous.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    for name in ('inspect', 'configure'):
        sub = commands.add_parser(name); sub.add_argument('--date', default=today)
        if name == 'configure': sub.add_argument('--from-json', required=True)
    commands.add_parser('verify-account')
    args = parser.parse_args()
    try:
        if args.command == 'inspect': result = inspect(args.date)
        elif args.command == 'configure': result = configure(args.from_json, args.date)
        else: result = verify_account()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('status') == 'needs_input' else 0
    except Exception as error:
        print(json.dumps(dict(status='blocked', command=args.command, error_type=type(error).__name__,
                             trading_started=False, next_step='See CODEX_SETUP.md; preserve existing journals'), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

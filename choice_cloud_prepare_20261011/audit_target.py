"""Offline target audit. Never reads credentials, contacts Choice, or places orders.

Account JSON: {nav, positions: {SH600000: shares}, prices_now: {SH600000: price}}.
Quote CSV: code,previous_close. Membership JSON: {as_of,csi500:[...],allowed:[...]}.
Missing account, configuration, quotes, or dated membership blocks readiness.
This is a review aid, not the executable console plan or an order authorization.
"""
import argparse
import csv
import datetime as dt
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a financial number')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite financial number')
    return result


def audit(payload, execution_date, signal_date, account=None, config=None,
          quotes=None, membership=None):
    failures, pending, metrics = [], [], {}
    try:
        execution = dt.date.fromisoformat(execution_date)
        signal = dt.date.fromisoformat(signal_date)
        if signal >= execution:
            failures.append('Signal must precede execution')
    except ValueError:
        failures.append('Invalid date')
    if payload.get('execution_date') != execution_date or payload.get('signal_date') != signal_date:
        failures.append('Target dates differ from explicitly expected session dates')
    if payload.get('mode') != 'weights':
        failures.append('Expected mode=weights')
    if payload.get('generated_from', {}).get('pcf_trading_day') != signal_date.replace('-', ''):
        # Official XML dates may also be ISO; require either exact spelling.
        if payload.get('generated_from', {}).get('pcf_trading_day') != signal_date:
            failures.append('PCF trading day differs from signal day or provenance is missing')
    weights = {}
    rows = payload.get('targets')
    if not isinstance(rows, list) or not rows:
        failures.append('Empty or invalid targets')
        rows = []
    for row in rows:
        try:
            code, weight = row['code'], number(row['weight'])
            if not isinstance(code, str) or not re.fullmatch(r'(SH|SZ)\d{6}', code):
                raise ValueError('Invalid code')
            if code in weights:
                failures.append('Duplicate target: ' + code)
            if not 0 < weight <= .05:
                failures.append('Nonpositive or over 5% target: ' + code)
            weights[code] = weight
        except (KeyError, TypeError, ValueError):
            failures.append('Invalid target row')
    total = sum(weights.values())
    metrics.update(names=len(weights), stock_weight=total, max_single=max(weights.values(), default=0))
    if len(weights) < 80:
        failures.append('Fewer than 80 names')
    if not .95 - 1e-6 <= total <= .98 + 1e-6:
        failures.append('Stock weight outside the handbook 95%-98% precheck range')
    if config is None:
        pending.append('Current console configuration is missing')
    else:
        try:
            if total > number(config['target_fraction']) + 1e-8:
                failures.append('Console parse_targets rejects sum(weights) above target_fraction')
        except (KeyError, TypeError, ValueError):
            failures.append('Invalid console target_fraction')
    if account is None:
        pending.append('Current account NAV, holdings, and prices are missing; turnover unverified')
    else:
        try:
            nav = number(account['nav'])
            if nav <= 0:
                raise ValueError('Nonpositive NAV')
            current = {}
            for code, shares in account['positions'].items():
                shares, price = number(shares), number(account['prices_now'][code])
                if shares < 0 or price <= 0:
                    raise ValueError('Invalid position or price')
                current[code] = shares * price / nav
            turnover = sum(abs(weights.get(c, 0) - current.get(c, 0)) for c in set(weights) | set(current))
            metrics['planned_two_way_turnover'] = turnover
            if turnover > .4 + 1e-8:
                failures.append('Turnover over 40%; requires a separately reviewed initial/recovery plan')
            if config and total * nav > number(config['capital_limit']) + .01:
                failures.append('Fractional target exceeds console capital_limit')
            if config and any(w * nav > number(config['max_security_value']) + .01 for w in weights.values()):
                failures.append('Target exceeds console max_security_value')
            if quotes is None:
                pending.append('Execution-session previous-close quotes are missing; lots unverified')
            else:
                values = {}
                for code, weight in weights.items():
                    price = number(quotes[code])
                    if price <= 0:
                        raise ValueError('Nonpositive reference quote')
                    shares = int(Decimal(str(nav)) * Decimal(str(weight)) / Decimal(str(price)) // 100) * 100
                    if code.startswith('SH688') and shares < 200:
                        shares = 0
                    values[code] = shares * price
                metrics['stock_weight_after_console_rounding'] = sum(values.values()) / nav
                if metrics['stock_weight_after_console_rounding'] < .9:
                    failures.append('Conservative console lot rounding leaves stock weight below 90%')
                if config and sum(values.values()) > min(number(config['capital_limit']), nav * number(config['target_fraction'])) + .01:
                    failures.append('Rounded target exceeds console budget')
                if membership:
                    ratio = sum(v for c, v in values.items() if c in membership['csi500']) / nav
                    metrics['csi500_nav_weight_after_rounding'] = ratio
                    if ratio < .8 - 1e-8:
                        failures.append('CSI500 weight after rounding below 80% of NAV')
        except (KeyError, TypeError, ValueError, OverflowError):
            failures.append('Invalid/incomplete account, quote, or limit inputs')
    if membership is None:
        pending.append('Dated CSI300/500/1000 membership missing; eligible universe and 80% rule unverified')
    else:
        try:
            if membership['as_of'] != signal_date:
                failures.append('Membership is not certified for the signal date')
            allowed, members500 = set(membership['allowed']), set(membership['csi500'])
            if not members500 or not members500 <= allowed:
                raise ValueError('Invalid membership sets')
            bad = sorted(set(weights) - allowed)
            if bad:
                failures.append('Targets outside permitted index union: ' + ','.join(bad))
            ratio = sum(w for c, w in weights.items() if c in members500)
            metrics['csi500_nav_weight'] = ratio
            if ratio < .8 - 1e-8:
                failures.append('CSI500 weight below conservative 80% of NAV rule')
        except (KeyError, TypeError, ValueError):
            failures.append('Invalid membership input')
    return dict(ready_for_console_dry_run=not failures and not pending,
                failures=failures, pending=pending, metrics=metrics,
                scope='Offline input audit only; identity, ledger, sellability, sessions and fills remain unverified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--exec-date', required=True)
    parser.add_argument('--signal-date', required=True)
    for name in ('account', 'config', 'membership', 'quotes', 'out'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig')) if p else None
    quotes = None
    if args.quotes:
        with args.quotes.open(encoding='utf-8-sig', newline='') as source:
            quote_rows = list(csv.DictReader(source))
        quotes = {row['code']: row['previous_close'] for row in quote_rows}
        if len(quotes) != len(quote_rows):
            raise SystemExit('Duplicate quote codes')
    report = audit(read(args.target), args.exec_date, args.signal_date,
                   read(args.account), read(args.config), quotes, read(args.membership))
    report['target_sha256'] = hashlib.sha256(args.target.read_bytes()).hexdigest()
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(content + '\n', encoding='utf-8')
    print(content)
    raise SystemExit(0 if report['ready_for_console_dry_run'] else 2)


if __name__ == '__main__':
    main()

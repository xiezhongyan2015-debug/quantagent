"""GET-only post-run audit of the one authorized 2026-09-21 simulation.

Never submits, cancels, resumes, or changes the execution journal. Credentials
and full account/order identifiers are excluded from the output.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from verify_readonly import ROOT, GetOnlySession, session_token
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, TradeError, number
from quanta_agents.factor_lab_a.pcf import normalize_code

ACCOUNT = __import__('os').environ['CHOICE_SIMULATION_ACCOUNT']
DAY = '2026-09-21'
DEST = ROOT / 'research/choice_pcf_automation/launch_20260921'
TERMINAL = {'4', '7', '8', '9'}


def main():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    if now.date().isoformat() != DAY:
        raise RuntimeError('Only the authorized current-day audit is supported')
    result_path = DEST / 'launch_result.json'
    if not result_path.exists():
        raise RuntimeError('Wait for the existing execution process to finish')
    launch = json.loads(result_path.read_text(encoding='utf-8'))
    path = DEST / 'state/choice.sqlite'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        binding = db.execute('SELECT account FROM binding WHERE id=1').fetchone()
        if binding != (hashlib.sha256(ACCOUNT.encode()).hexdigest(),):
            raise RuntimeError('Account binding mismatch')
        batch = db.execute('SELECT state,plan FROM batches WHERE day=?', (DAY,)).fetchone()
        journal = db.execute('SELECT code,state,oid,filled FROM orders WHERE day=?', (DAY,)).fetchall()
        reserve = db.execute('SELECT COALESCE(SUM(reserved_cents),0) FROM capital WHERE day=?', (DAY,)).fetchone()[0]
    planned = {o['code']: o for o in json.loads(batch[1])['orders']} if batch else {}
    old = {k: os.environ.get(k) for k in ('CHOICE_JTOKEN', 'CHOICE_ACCOUNT_ID')}
    session = GetOnlySession(ACCOUNT)
    try:
        os.environ['CHOICE_JTOKEN'] = session_token()
        os.environ['CHOICE_ACCOUNT_ID'] = ACCOUNT
        broker = ChoiceBroker({'initial_buy_only': True}, allow_orders=False, session=session)
        orders = broker._rows('AguTrade/SpoOrders')
        holds = broker._rows('AguTrade/SpoHold')
        deals = broker._rows('AguTrade/SpoDeal')
        balance = broker._request('AguTrade/SpoBalInfo', params={'accId': ACCOUNT})['Data']
        known = {str(r[2]) for r in journal if r[2]}
        matched, issues = [], []
        for code, state, oid, recorded_fill in journal:
            row = {'code': code, 'journal_state': state, 'journal_filled': recorded_fill,
                   'order_id_tail': str(oid)[-4:] if oid else None}
            actual = [r for r in orders if oid and str(r['orderId']) == str(oid)]
            if len(actual) != 1:
                row['verified'] = False
                row['reason'] = 'Exact order ID unavailable or ambiguous; no guessing or retry'
                issues.append(code + ': exact order identification unresolved')
            else:
                r = actual[0]
                expected = planned[code]
                same = [x for x in orders if normalize_code(str(x['secCode'])) == code]
                inventory = [x for x in holds if normalize_code(str(x['secCode'])) == code]
                trades = [x for x in deals if normalize_code(str(x['secCode'])) == code]
                filled = number(r['tradeCount'], True)
                held = sum(number(x['count'], True) for x in inventory)
                dealt = sum(number(x['tradeCount'], True) for x in trades)
                identity = (normalize_code(str(r['secCode'])) == code and
                            number(r['orderCount'], True) == expected['quantity'] and
                            number(r['drt'], True) == 1 and str(r['orderDate']).replace('-', '') == DAY.replace('-', ''))
                quantities = (len(same) == 1 and len(inventory) <= 1 and
                              all(number(x['drt'], True) == 1 for x in trades) and
                              held == dealt == filled and recorded_fill <= filled <= expected['quantity'])
                row.update(server_state=str(r['status']), quantity=number(r['orderCount'], True),
                           limit_price=number(r['orderPrice']), filled=filled, held=held, deal_quantity=dealt,
                           order_time=r.get('orderTime'), identity_verified=identity,
                           quantities_verified=quantities, verified=identity and quantities,
                           outstanding_quantity=0 if str(r['status']) in TERMINAL else number(r['orderCount'], True)-filled)
                if not row['verified']:
                    issues.append(code + ': identity/inventory/deal reconciliation failed')
                try:
                    row['current_adapter_progress'] = broker.progress(str(oid), dict(expected, price=row['limit_price']))
                except Exception as error:
                    row['current_adapter_error_type'] = type(error).__name__
                    if isinstance(error, TradeError):
                        row['current_adapter_error'] = str(error)
            matched.append(row)
        other = [r for r in orders if str(r['orderId']) not in known]
        pending = [r for r in orders if str(r['status']) not in TERMINAL]
        out = {
            'checked_at': now.isoformat(), 'account_tail': ACCOUNT[-4:], 'mode': 'competition_simulation_GET_only',
            'launch': launch, 'journal_batch_state': batch[0] if batch else None,
            'planned_orders': len(planned), 'journal_orders': len(journal),
            'known_submitted_orders': len(known), 'budget_reserved_cny': reserve / 100,
            'budget_limit_cny': 2000000, 'account_orders_returned': len(orders),
            'account_holdings_count': len(holds), 'account_deal_rows': len(deals),
            'balance': {k: balance.get(k) for k in ('totalAssets', 'availBalance', 'frozenMoney', 'totalPosValue')},
            'exact_orders': matched, 'unmatched_account_orders_count': len(other),
            'unmatched_orders': [{'code': normalize_code(str(r['secCode'])),
                                  'order_id_tail': str(r['orderId'])[-4:], 'state': str(r['status']),
                                  'order_date': r.get('orderDate'), 'order_time': r.get('orderTime')}
                                 for r in other],
            'account_pending_orders_count': len(pending),
            'pending_orders': [{'code': normalize_code(str(r['secCode'])), 'order_id_tail': str(r['orderId'])[-4:],
                                'state': str(r['status']), 'known_test_order': str(r['orderId']) in known}
                               for r in pending],
            'holdings': [{k: r.get(k) for k in ('secCode', 'count', 'costPrice', 'costAmount', 'marketValue')} for r in holds],
            'deals': [{k: r.get(k) for k in ('secCode', 'drt', 'tradeCount', 'tradePrice', 'tradeAmount',
                                            'tradeAmt', 'commission', 'stampTax', 'transferFee', 'fee', 'tradeTime')
                       if k in r} for r in deals],
            'holding_field_names': sorted(holds[0]) if holds else [],
            'deal_field_names': sorted(deals[0]) if deals else [],
            'reconciliation_issues': issues, 'network_audit': session.audit,
            'audit_new_orders': 0, 'audit_cancellations': 0,
        }
        audit_path = DEST / ('readonly_audit_' + now.strftime('%H%M%S') + '.json')
        audit_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print('Redacted audit:', str(audit_path))
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        session.headers.clear()
        session.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Read-only audit failed:', type(exc).__name__)
        raise SystemExit(1)

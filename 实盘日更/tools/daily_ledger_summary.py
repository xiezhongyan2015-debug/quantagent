"""Summarize dated executor order evidence across sessions without broker calls."""
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

CN = dt.timezone(dt.timedelta(hours=8))

def summarize(paths, day):
    orders, warnings = {}, []
    fees, pilot = None, None
    for path in sorted(map(Path, paths)):
        ledger = json.loads(path.read_text(encoding='utf8'))
        if ledger.get('day') != day:
            warnings.append('账本日期不符：'+path.parent.name)
            continue
        for attempt in ledger.get('attempts', []):
            stamp = dt.datetime.fromisoformat(attempt['submitted_at'])
            if stamp.tzinfo is None:
                raise ValueError('Order submission timestamp lacks timezone')
            if stamp.astimezone(CN).date().isoformat() != day:
                continue  # inherited history is not today's trade
            oid = attempt.get('oid')
            if not oid:
                warnings.append('存在未关联回执的委托，未计入已核验成交')
                continue
            quantity = attempt.get('filled', 0)
            if type(quantity) is not int or quantity < 0:
                raise ValueError('Invalid filled quantity')
            order = attempt['order']
            if order['side'] not in (1, 2):
                raise ValueError('Invalid order side')
            old = orders.get(str(oid))
            if old:
                old_order = old['order']
                if any(old_order[k] != order[k] for k in ('code', 'side', 'quantity', 'price')):
                    raise ValueError('Conflicting evidence for one order receipt')
                if quantity < old.get('filled', 0):
                    continue  # never replace a more complete inherited observation
                if quantity == old.get('filled', 0) and old.get('terminal') and not attempt.get('terminal'):
                    continue
            orders[str(oid)] = attempt
        if ledger.get('attempts'):
            audit = ledger.get('last_audit') or {}
            if audit.get('fees') is not None: fees = float(audit['fees'])
            if ledger.get('pilot_plan'): pilot = ledger['pilot_plan'].get('selected_symbol') or pilot
    fills, cancelled = [], 0
    for attempt in orders.values():
        order = attempt['order']; quantity = attempt.get('filled', 0)
        if quantity:
            price = Decimal(str(order['price']))
            if not price.is_finite() or price <= 0: raise ValueError('Invalid order limit price')
            fills.append(dict(code=order['code'], side='买' if order['side']==1 else '卖',
                qty=quantity, price=float(price), cny=float((price*quantity).quantize(Decimal('.01'))),
                phase=attempt.get('phase'), at=attempt.get('terminal_at',attempt['submitted_at'])[11:19],
                oid=str(attempt['oid'])))
        if str(attempt.get('state')) in ('7','8') and attempt.get('terminal'):
            cancelled += 1
    return dict(fills=fills,cancelled=cancelled,fees=fees,pilot=pilot,warnings=sorted(set(warnings)))

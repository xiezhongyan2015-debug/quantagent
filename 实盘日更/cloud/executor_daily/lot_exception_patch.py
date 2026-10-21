"""2026-10-20: one-lot exception to the 50,000 CNY per-order cap (formal rebalancing only).

Problem: max_order_quantity() floors (50,000 - fees) / price to 100 shares. When one legal minimum slice already costs more
than 50,000 CNY (main board price > ~499.5, STAR price > ~249.7), the result is below one lot, the planner records
'不足买入整手' and the name can never be bought (nor sold) - e.g. SZ300308 at 660.7 and SH688536 at 265 on 10-20.

Change (exact-string replacements, each asserted to occur once; nothing else in the executor changes):
  * workflow_rebalance: LOT_EXCEPTION_CAP = 200,000 CNY; lot_exception_slice(code, price, quantity) is True only when the
    minimum slice of that name alone exceeds ORDER_CAP and the order is exactly that slice (STAR: 200, or 300 so a gap
    never strands an illegal 100-share remainder) and costs <= LOT_EXCEPTION_CAP with fees. order_value_cap() returns
    LOT_EXCEPTION_CAP for such a slice, ORDER_CAP otherwise. max_order_quantity(price, code) returns that slice instead of 0.
    guard_formal's fee-inclusive cap uses order_value_cap().
  * console_runtime: the planner passes the code; the STAR remainder rule may grow a 200 slice to 300 under the
    exception; the write layer's cap uses order_value_cap() in the formal phase.
Unchanged: every other order stays <= 50,000; single-security 500,000, 95% stock cap, 90% / 80% floors, cash, T+1,
3% price guard, retries, pilot rules, position-floor top-ups, ORDER_CAP itself and the sealed settings check.
"""
from pathlib import Path

WORKFLOW = [
    ("ORDER_CAP=Decimal('50000')\n",
     "ORDER_CAP=Decimal('50000')\n"
     "LOT_EXCEPTION_CAP=Decimal('200000')  # 2026-10-20: one legal minimum slice of a high-priced name may exceed ORDER_CAP, never this\n"),
    ("    if reserved(order['price'],quantity)>ORDER_CAP or dec(current['reserve'])!=reserved(order['price'],quantity):",
     "    if reserved(order['price'],quantity)>order_value_cap(order['code'],order['price'],quantity) or dec(current['reserve'])!=reserved(order['price'],quantity):"),
    ("def max_order_quantity(price):\n    \"\"\"The cap includes the same conservative fee reserve used by the writer.\"\"\"\n    return max(0,int((ORDER_CAP-10)/Decimal('1.001')/dec(price)//100)*100)\n",
     "def min_slice(code):\n    return 200 if str(code).startswith('SH688') else 100\n\n"
     "def lot_exception_slice(code,price,quantity):\n"
     "    \"\"\"2026-10-20: True only for the smallest legal slice of a name whose minimum slice alone (with fees) exceeds ORDER_CAP.\n"
     "    STAR may also use 300 so a gap never leaves an illegal 100-share remainder. Hard ceiling LOT_EXCEPTION_CAP.\"\"\"\n"
     "    m=min_slice(code)\n"
     "    return reserved(price,m)>ORDER_CAP and quantity in ((m,m+100) if m==200 else (m,)) and reserved(price,quantity)<=LOT_EXCEPTION_CAP\n\n"
     "def order_value_cap(code,price,quantity):\n"
     "    return LOT_EXCEPTION_CAP if lot_exception_slice(code,price,quantity) else ORDER_CAP\n\n"
     "def max_order_quantity(price,code=None):\n    \"\"\"The cap includes the same conservative fee reserve used by the writer.\"\"\"\n"
     "    n=max(0,int((ORDER_CAP-10)/Decimal('1.001')/dec(price)//100)*100)\n"
     "    if code is not None and n<min_slice(code) and lot_exception_slice(code,price,min_slice(code)):return min_slice(code)\n"
     "    return n\n"),
]
RUNTIME = [
    ("            max_quantity=workflow.max_order_quantity(price) if formal else int(self.c['max_order_value']/price//100)*100\n",
     "            max_quantity=workflow.max_order_quantity(price,code) if formal else int(self.c['max_order_value']/price//100)*100\n"),
    ("                if remaining==100:quantity-=100\n",
     "                if remaining==100:quantity=quantity+100 if workflow.lot_exception_slice(code,price,quantity+100) else quantity-100\n"),
    ("                if o['quantity']*o['price']>self.c['max_order_value'] or not price_ok:raise TradeError('写入层限额或价格保护')\n",
     "                cap=workflow.order_value_cap(o['code'],o['price'],o['quantity']) if workflow.formal_state(ledger) else self.c['max_order_value']\n"
     "                if o['quantity']*o['price']>cap or not price_ok:raise TradeError('写入层限额或价格保护')\n"),
]


def apply(directory):
    d = Path(directory); out = {}
    for name, pairs in (('workflow_rebalance.py', WORKFLOW), ('console_runtime.py', RUNTIME)):
        p = d / name; s = p.read_bytes().decode('utf-8'); nl = '\r\n' if '\r\n' in s else '\n'   # keep the file's own line endings byte for byte
        if 'LOT_EXCEPTION_CAP' in s or 'lot_exception_slice' in s:
            raise RuntimeError(f'{name} already patched')
        for old, new in pairs:
            old, new = old.replace('\n', nl), new.replace('\n', nl)
            n = s.count(old)
            if n != 1:
                raise RuntimeError(f'{name}: expected one occurrence, found {n}: {old[:70]!r}')
            s = s.replace(old, new)
        out[name] = s
    for name, s in out.items():   # write only after every replacement of both files matched
        (d / name).write_bytes(s.encode('utf-8'))
    return sorted(out)

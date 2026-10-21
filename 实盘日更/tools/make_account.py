"""Convert the cloud machine's read-only captain snapshot (captain_account_baseline.json layout) into the live_targets account format.
Usage: make_account.py <baseline.json> <as_of YYYY-MM-DD> [--out DIR] -> DIR/snapshot_<as_of>.json"""
import sys, json
from pathlib import Path
src = Path(sys.argv[1]); as_of = sys.argv[2]; out = Path(sys.argv[sys.argv.index('--out') + 1]) if '--out' in sys.argv else Path(__file__).resolve().parents[1] / 'inputs/account'; out.mkdir(parents=True, exist_ok=True)
b = json.loads(src.read_text(encoding='utf-8-sig'))
def cc(code6): code6 = str(code6).zfill(6); return ('SH' if code6[0] in '569' else 'SZ') + code6
pos = {cc(p['code']): int(p['shares']) for p in b['positions']}
pn = {cc(p['code']): (float(p['implied_mark_price']) if p.get('implied_mark_price') not in (None, '') else float(p['market_value']) / float(p['shares'])) for p in b['positions'] if int(p['shares']) > 0}
pos = {c: n for c, n in pos.items() if n > 0}
bal = b.get('balance') or {}
def pick(*keys):
    for k in keys:
        if k in b and b[k] not in (None, ''): return float(b[k])
        if k in bal and bal[k] not in (None, ''): return float(bal[k])
    return None
stock = sum(pos[c] * pn[c] for c in pos); cash = pick('available_cash', 'cash', 'available', 'availableCash', 'enable_balance'); nav = pick('nav', 'total_assets', 'totalAssets', 'asset', 'total')
if nav is None: nav = stock + (cash or 0.0)
if cash is None: cash = nav - stock
assert abs(stock + cash - nav) < 1.0, f'positions + cash != nav ({stock + cash:.2f} vs {nav:.2f})'
snap = dict(as_of=as_of, captured_at=b.get('captured_at'), nav=nav, available_cash=cash, positions=pos, prices_now=pn, source=str(src), note='marks are the account valuation at as_of close, not executable quotes')
(out / f'snapshot_{as_of}.json').write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding='utf-8'); print(json.dumps(dict(ok=True, as_of=as_of, nav=nav, names=len(pos), stock_fraction=round(stock / nav, 4))))

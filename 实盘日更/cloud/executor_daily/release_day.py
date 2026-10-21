"""Daily sealed release for the cloud executor (pilot -> formal rebalance), generic in dates. No orders, no credentials.

Inputs : --signal T --exec T1 --target <实盘日更 target_T1.json (shares)> --account <readonly captain snapshot json> --pcf <563030 PCF xml of T>
Writes : BASE/daily_public_pcf/{563030_<T8>_official.xml, csi500_members.json, current_membership_union.json, active_rebalance_target.json}
         BASE/console/settings.json (pilot_then_targets for T1), C:/ChoiceBridge/live/active_day.json, ROOT/deployment_manifest.json (data entries)
         plus a release receipt. Mirrors choice_cloud_prepare_20261011/prepare_workflow_release.py (reviewed 2026-10-12) with the day as a parameter.
"""
import argparse, copy, datetime as dt, hashlib, itertools, json, re, struct as S, sys, time
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path
import requests
from share_units import odd_reference, reference_holding, target_allowed
ROOT = Path('C:/ChoiceBridge/executor'); BASE = ROOT / 'research/choice_pcf_automation'; DPP = BASE / 'daily_public_pcf'; LIVE = Path('C:/ChoiceBridge/live')
CS = {'000300': ('csi300', 300), '000905': ('csi500', 500), '000852': ('csi1000', 1000)}
URL = 'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{}cons.xls'


def sha(b): return hashlib.sha256(b if isinstance(b, bytes) else Path(b).read_bytes()).hexdigest()
def save(p, o): Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding='utf-8')
def digest(o): return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def parse_cons(blob, index_code, count):
    """Strict BIFF8 reader for the CSI constituent workbook (ported from choice_cloud_prepare_20261011/read_official_biff.py)."""
    assert blob[:8] == bytes.fromhex('d0cf11e0a1b11ae1'); sector = 1 << S.unpack_from('<H', blob, 30)[0]
    def block(i): return blob[(i + 1) * sector:(i + 2) * sector]
    fatids = list(S.unpack_from('<109I', blob, 76)); assert S.unpack_from('<I', blob, 72)[0] == 0, 'extended FAT unsupported'
    fat = []
    for i in fatids:
        if i < 0xfffffffa: fat.extend(S.unpack('<' + str(sector // 4) + 'I', block(i)))
    def chain(i):
        out = bytearray(); seen = set()
        while i < 0xfffffffa:
            assert i not in seen and i < len(fat); seen.add(i); out.extend(block(i)); i = fat[i]
        return bytes(out)
    dirb = chain(S.unpack_from('<I', blob, 48)[0]); work = None
    for i in range(0, len(dirb), 128):
        e = dirb[i:i + 128]; n = S.unpack_from('<H', e, 64)[0]; name = e[:max(0, n - 2)].decode('utf-16le')
        if name in ('Workbook', 'Book'):
            size = S.unpack_from('<Q', e, 120)[0]; assert size >= 4096; work = chain(S.unpack_from('<I', e, 116)[0])[:size]
    assert work; records = []; i = 0
    while i + 4 <= len(work):
        code, size = S.unpack_from('<HH', work, i); data = work[i + 4:i + 4 + size]; assert len(data) == size; records.append((code, data)); i += 4 + size
    ix = next(i for i, x in enumerate(records) if x[0] == 0xfc); segments = [records[ix][1]]
    for c, b in records[ix + 1:]:
        if c != 0x3c: break
        segments.append(b)
    class Reader:
        def __init__(self): self.i = 0; self.off = 0
        def read(self, n):
            out = b''
            while n:
                if self.off == len(segments[self.i]): self.i += 1; self.off = 0
                k = min(n, len(segments[self.i]) - self.off); out += segments[self.i][self.off:self.off + k]; self.off += k; n -= k
            return out
        def chars(self, n, wide):
            value = ''
            while n:
                if self.off == len(segments[self.i]): self.i += 1; self.off = 0; wide = bool(self.read(1)[0] & 1)
                k = min(n, (len(segments[self.i]) - self.off) // (2 if wide else 1)); assert k > 0
                value += self.read(k * (2 if wide else 1)).decode('utf-16le' if wide else 'latin1'); n -= k
            return value
    r = Reader(); total, unique = S.unpack('<II', r.read(8)); strings = []
    for _ in range(unique):
        cnt, flags = S.unpack('<HB', r.read(3)); runs = S.unpack('<H', r.read(2))[0] if flags & 8 else 0; ext = S.unpack('<I', r.read(4))[0] if flags & 4 else 0
        strings.append(r.chars(cnt, bool(flags & 1))); r.read(runs * 4 + ext)
    cells = {}
    for code, b in records:
        if code == 0xfd: row, col, xf, index = S.unpack('<HHHI', b); assert (row, col) not in cells; cells[row, col] = strings[index]
        elif code == 0x203: row, col, xf, val = S.unpack('<HHHd', b); cells[row, col] = str(int(val)) if val.is_integer() else str(val)
    headers = {re.sub(r'\s+', '', v): c for (row, c), v in cells.items() if row == 0}
    required = ['日期Date', '指数代码IndexCode', '成份券代码ConstituentCode', '成份券名称ConstituentName', '交易所Exchange']
    rows = []
    for n in sorted({row for row, col in cells if row > 0}):
        row = {k: cells[n, headers[k]] for k in required}; assert row['指数代码IndexCode'].zfill(6) == index_code
        exchange = {'上海证券交易所': 'SH', '深圳证券交易所': 'SZ'}[row['交易所Exchange']]; code = row['成份券代码ConstituentCode'].zfill(6); assert re.fullmatch(r'[036]\d{5}', code)
        rows.append({'code': exchange + code, 'name': row['成份券名称ConstituentName'], 'date': row['日期Date']})
    assert len(rows) == count and len({x['code'] for x in rows}) == count and len({x['date'] for x in rows}) == 1
    value = rows[0]['date']; day = dt.datetime.strptime(value, '%Y%m%d' if len(value) == 8 else '%Y-%m-%d').date().isoformat()
    return rows, day


def membership_evidence(signal):
    records = []
    with requests.Session() as s:
        s.trust_env = False
        for index_code, (name, count) in CS.items():
            url = URL.format(index_code)
            for attempt in range(4):   # 2026-10-14: one 30 s read timeout failed the whole release; retry the same official file
                try: r = s.get(url, timeout=60, allow_redirects=False); r.raise_for_status(); blob = r.content; break
                except requests.RequestException as e:
                    if attempt == 3: raise
                    print(f'constituent download {index_code} attempt {attempt + 1} failed: {type(e).__name__}; retrying', flush=True); time.sleep(15 * (attempt + 1))
            if not 4096 <= len(blob) <= 2_000_000: raise SystemExit('unexpected constituent file size ' + index_code)
            rows, day = parse_cons(blob, index_code, count)
            floor = (dt.date.fromisoformat(signal) - dt.timedelta(days=7)).isoformat()
            if not floor <= day <= signal: raise SystemExit(f'{name} constituent file dated {day}, outside [{floor}, {signal}]')
            (DPP / f'{index_code}cons_official_{signal}.xls').write_bytes(blob)
            records.append(dict(index=name, index_code=index_code, snapshot_date=day, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url, source_file_sha256=sha(blob), members=rows))
    csi500 = next(r for r in records if r['index'] == 'csi500')
    union = dict(as_of=signal, allowed=sorted({x['code'] for r in records for x in r['members']}), csi500=sorted(x['code'] for x in csi500['members']),
                 sources=[{k: r[k] for k in ('index', 'snapshot_date', 'source_url', 'source_file_sha256')} for r in records])
    if len(union['allowed']) != 1800 or len(union['csi500']) != 500: raise SystemExit('union must contain 1800 unique codes and 500 CSI500 codes')
    return csi500, union


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--signal', required=True); ap.add_argument('--exec', required=True); ap.add_argument('--target', required=True); ap.add_argument('--account', required=True); ap.add_argument('--pcf', required=True)
    ap.add_argument('--settings-template', default=str(BASE / 'console/settings.json')); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(); T, T1 = a.signal, a.exec
    raw = json.loads(Path(a.target).read_text(encoding='utf-8'))
    assert raw['mode'] == 'shares' and raw['signal_date'] == T and raw['execution_date'] == T1 and raw['generated_from']['mode'] in ('P0', 'V5', 'V5_tilt') and not raw['generated_from'].get('shadow'), 'target file is not a released shares target for these dates'
    acct = json.loads(Path(a.account).read_text(encoding='utf-8')); assert acct['account_tail'] == '3120' and acct['unfinished_order_count'] == 0 and D(acct['balance']['frozenMoney']) == 0
    nav = D(acct['balance']['totalAssets']); held = {}; prices = {}
    for x in acct['positions']:
        code = ('SH' if x['code'].startswith(('5', '6', '9')) else 'SZ') + x['code']
        if int(x['shares']) > 0: held[code] = int(x['shares']); prices[code] = D(str(x['implied_mark_price'])) if x.get('implied_mark_price') else D(str(x['market_value'])) / int(x['shares'])
    pcf_xml = Path(a.pcf).read_bytes(); import xml.etree.ElementTree as ET; root = ET.fromstring(pcf_xml)
    t8 = T.replace('-', ''); assert root.findtext('TradingDay') == t8, 'PCF is not for the signal day'; v8 = root.findtext('PreTradingDay')
    pcf_names = {}
    for r in root.findall('ComponentList/Component'):
        c6 = r.findtext('InstrumentID').zfill(6); pcf_names[('SH' if c6[0] in '569' else 'SZ') + c6] = dict(name=r.findtext('InstrumentName'), shares=int(D(r.findtext('Quantity'))))
    csi500, union = membership_evidence(T)
    c500 = set(union['csi500']); allowed = set(union['allowed'])
    assert all(type(x['shares']) is int for x in raw['targets']), 'target shares must be exact integers'
    t = {x['code']: x['shares'] for x in raw['targets']}
    assert len(t) == len(raw['targets']) and 80 <= len(t) <= 1800 and set(t) <= allowed
    assert all(n > 0 and target_allowed(c, n, held.get(c, 0)) for c, n in t.items()), 'target share units or observed odd balance invalid'
    assert all(not n % 100 or reference_holding(raw, c) == held.get(c, 0) for c, n in t.items()), 'odd target lacks matching dated holdings evidence'
    # reference prices for target names not held: previous closes from the target's own weight/shares (weight = shares*price/nav at generation)
    for x in raw['targets']:
        if x['code'] not in prices and x.get('weight') and x['shares']: prices[x['code']] = (D(str(x['weight'])) * D(str(raw.get('nav_used', nav))) / x['shares']).quantize(D('.0001'))
    missing = [c for c in set(t) | set(held) if c not in prices]; assert not missing, 'no reference price for ' + ','.join(missing[:5])
    want = sum(prices[c] * n for c, n in t.items()); sc = [c for c in t if c.startswith('SH688') and abs(t[c] - held.get(c, 0)) == 100 and t[c] != 0]
    fixed = sum(prices[c] * n for c, n in t.items() if c not in sc); best = None
    for bits in itertools.product((0, 1), repeat=len(sc)):
        proposal = dict(t); trade_value = D(0); trade_count = 0
        for c, b in zip(sc, bits):
            delta = t[c] - held.get(c, 0); proposal[c] = held.get(c, 0) + 2 * delta * b; trade_value += abs(2 * delta * b) * prices[c]; trade_count += b
        value = fixed + sum(prices[c] * proposal[c] for c in sc); member = sum(prices[c] * proposal[c] for c in proposal if c in c500)
        if not nav * D('.90') <= value <= nav * D('.95') or member < nav * D('.80'): continue
        if any(prices[c] * n > min(D(500000), nav * D('.05')) or (c.startswith('SH688') and n < 200) for c, n in proposal.items()): continue
        key = (abs(value - want), trade_value, trade_count, bits)
        if best is None or key < best[0]: best = (key, proposal, value, member)
    assert best, 'no admissible integer target (stock 90-95% of NAV, CSI500 >= 80%, single <= 5%/500k, STAR lots)'
    t2 = best[1]
    adjustments = [dict(code=c, held=held.get(c, 0), requested=t[c], execution=t2[c], reference_price=str(prices[c])) for c in sc if t2[c] != t[c]]
    obj = copy.deepcopy(raw); obj.update(strategy_layer=raw['generated_from']['mode'], review_only=False, execution_enabled=True, source_user_target_sha256=sha(Path(a.target)),
                                        targets=[dict(code=c, shares=n, weight=float(prices[c] * n / nav)) for c, n in sorted(t2.items())], legal_share_adjustments=adjustments, released_at=dt.datetime.now(dt.timezone.utc).isoformat())
    obj['odd_lot_reference'] = odd_reference(held, T, sha(Path(a.account)))
    # pilot symbol: cheapest held name in PCF ∩ CSI500, not STAR, no ST, one lot + fees <= 10000 CNY (same rule as pilot_one_lot.candidates)
    names500 = {x['code']: x['name'] for x in csi500['members']}
    cands = [c for c in held if c in names500 and c in pcf_names and pcf_names[c]['shares'] > 0 and not c.startswith('SH688') and not any(x in (names500[c] + pcf_names[c]['name']).upper() for x in ('ST', '退')) and (prices[c] * 100 * D('1.001') + 10) <= 10000]
    cands.sort(key=lambda c: (prices[c], c)); assert cands, 'no pilot candidate'; pilot = cands[0]
    cfg = json.loads(Path(a.settings_template).read_text(encoding='utf-8')); prefix = 'C:/ChoiceBridge/executor/research/choice_pcf_automation/daily_public_pcf/'
    cfg.update(strategy='pilot_then_targets', execution_date=T1, pilot_symbol=pilot, max_new_orders=1, pilot_shares=100, pilot_total_cap_cny=10000, max_order_value=10000.0, allow_sells=False, prefer_sealed_plan=False, isolate_unknown_orders=True, fill_timeout_seconds=60, max_attempts_per_code=1, excluded_cash_cny='0.00', cash_drift_tolerance_cny=0.03)
    cfg['position_floor']['enabled'] = False; cfg['membership_path'] = prefix.replace('/', '\\') + 'csi500_members.json'
    receipt = dict(signal_date=T, execution_date=T1, strategy_layer=obj['strategy_layer'], nav=str(nav), target_names=len(t2), stock_value_fraction=float(best[2] / nav), csi500_nav_fraction=float(best[3] / nav), adjustments=len(adjustments), pilot_symbol=pilot,
                   pilot_lot_cny=str(prices[pilot] * 100), constituent_dates={s['index']: s['snapshot_date'] for s in union['sources']}, account_checked_at=acct['checked_at'], dry_run=a.dry_run)
    if a.dry_run: print(json.dumps(receipt, ensure_ascii=False)); return
    DPP.mkdir(parents=True, exist_ok=True); LIVE.mkdir(parents=True, exist_ok=True)
    pcf_name = f'563030_{t8}_official.xml'; (DPP / pcf_name).write_bytes(pcf_xml)
    save(DPP / 'csi500_members.json', csi500); save(DPP / 'current_membership_union.json', union); save(DPP / 'active_rebalance_target.json', obj)
    cfg['rebalance_after_pilot'] = dict(target_path=prefix + 'active_rebalance_target.json', target_sha256=sha(DPP / 'active_rebalance_target.json'), membership_path=prefix + 'current_membership_union.json', membership_sha256=sha(DPP / 'current_membership_union.json'),
                                        target_fraction=0.95, minimum_stock_fraction=0.9, minimum_csi500_nav=0.8, max_orders=2000, max_slices_per_code=128, max_order_value=50000)
    save(BASE / 'console/settings.json', cfg)
    save(LIVE / 'active_day.json', dict(execution_date=T1, signal_date=T, pcf_file=pcf_name, pcf_trading_day=t8, pcf_valuation_day=v8, released_at=receipt['account_checked_at']))
    # deployment manifest: keep code entries, replace the daily data entries
    mp = ROOT / 'deployment_manifest.json'; m = json.loads(mp.read_text(encoding='utf-8')); keep = {k: v for k, v in m['files'].items() if not k.startswith('research/choice_pcf_automation/daily_public_pcf/')}
    for rel in ['calendar.json', 'csi500_members.json', 'current_membership_union.json', 'active_rebalance_target.json', pcf_name]: keep['research/choice_pcf_automation/daily_public_pcf/' + rel] = sha(DPP / rel)
    for code_file in ['pilot_one_lot.py', 'workflow_rebalance.py', 'console_runtime.py']: keep['research/choice_pcf_automation/' + code_file] = sha(BASE / code_file)
    m['files'] = keep; m['daily_release'] = receipt; mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    receipt.update(settings_sha256=sha(BASE / 'console/settings.json'), target_sha256=cfg['rebalance_after_pilot']['target_sha256'], manifest_sha256=sha(mp))
    save(LIVE / f'release_receipt_{T1}.json', receipt); print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Prepare a read-only full-strategy review. Never publishes executable targets.

Public membership data only; private account input is an observed GET snapshot.
No worker, authentication or order-writing modules are imported.
"""
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import requests

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
OUT = ROOT / 'full_switch_review_20261012'
CN = dt.timezone(dt.timedelta(hours=8))


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(path, 0o600)


def membership(index, count):
    url = f'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{index}cons.xls'
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(url, timeout=30, allow_redirects=False)
        response.raise_for_status()
    blob = response.content
    if not 4096 <= len(blob) <= 2_000_000:
        raise ValueError('Unexpected membership document size')
    source = OUT / f'{index}cons_official.xls'
    source.write_bytes(blob)
    # Reuse the already reviewed strict BIFF8 reader, without modifying the
    # deployed CSI500 evidence or the original parser. All substitutions below
    # are fixed constants, not input code or third-party document text.
    parser = (ROOT / 'read_official_biff.py').read_text()
    parser = parser.replace("p=Path(__file__).resolve().parent;blob=(p/'000905cons_official.xls').read_bytes()",
        f"p=Path({str(OUT)!r});blob=(p/'{index}cons_official.xls').read_bytes()")
    parser = parser.replace("=='000905'", f"=='{index}'")
    parser = parser.replace('len(rows)==500', f'len(rows)=={count}').replace("len({x['code'] for x in rows})==500", f"len({{x['code'] for x in rows}})=={count}")
    parser = parser[:parser.index("q=p/'full_executor/")] + "print(json.dumps(record,ensure_ascii=False))\n"
    record = json.loads(subprocess.check_output([sys.executable, '-c', parser], text=True))
    record.update(index={'000300': 'csi300', '000905': 'csi500', '000852': 'csi1000'}[index],
                  index_code=index, source_url=url)
    assert len(record['members']) == count
    save(OUT / f'{index}_members.json', record)
    return record


def main():
    OUT.mkdir(mode=0o700, exist_ok=True)
    latest = max((ROOT/'snapshots').glob('captain_snapshot_*.json'), key=lambda p:p.stat().st_mtime)
    snapshot = json.loads(latest.read_text())
    captured = dt.datetime.fromisoformat(snapshot['checked_at']).astimezone(CN)
    assert captured.date() == dt.datetime.now(CN).date()
    assert snapshot['account_tail'] == '3120' and snapshot['new_orders_submitted'] == 0
    assert snapshot['unfinished_order_count'] == 0 and snapshot['balance']['frozenMoney'] == '0.00'
    assert not snapshot['automatic_trading_started']
    account = dict(as_of=captured.isoformat(), captured_at=captured.isoformat(),
                   price_reference_day='2026-10-09', provenance='current_preexecution_GET_observation',
                   nav=float(snapshot['balance']['totalAssets']), positions={}, prices_now={})
    for row in snapshot['positions']:
        code = ('SH' if row['code'].startswith('6') else 'SZ')+row['code']
        account['positions'][code] = row['shares']
        account['prices_now'][code] = float(row['implied_mark_price'])
    # Friday closes price the proposed final shares for names absent from the
    # account. Thursday prices remain solely for PCF signal-weight valuation.
    import pandas as pd
    friday = pd.read_csv(PROJECT/'pcf_overlay_20261012/live/prices/close_20261009.csv', dtype={'code':str})
    for row in friday.itertuples():
        code = row.code[-2:]+row.code[:6] if '.' in row.code else row.code
        account['prices_now'].setdefault(code, float(row.close))
    save(OUT/'account_observed_20261011.json', account)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(lambda pair:membership(*pair), [('000300',300),('000905',500),('000852',1000)]))
    days = {r['snapshot_date'] for r in records}
    assert days == {'2026-10-09'}, 'Index observations do not share the intended signal date'
    members = dict(as_of='2026-10-09', allowed=sorted({x['code'] for r in records for x in r['members']}),
                   csi500=sorted(x['code'] for r in records if r['index']=='csi500' for x in r['members']),
                   sources=[{k:r[k] for k in ('index','snapshot_date','source_url','source_file_sha256')} for r in records])
    assert len(members['allowed'])==1800
    save(OUT/'current_membership_union.json', members)
    # Review clone only: record the real Sunday account capture time, and
    # explicitly prevent this output being treated as a release/target file.
    code = (PROJECT/'pcf_overlay_20261012/live_targets.py').read_text()
    old = "if str(acct.get('as_of', ''))[:10] != a.signal_date: fail.append(f\"account snapshot as_of {acct.get('as_of')} != signal date\")"
    assert old in code
    new = "if not (a.signal_date <= str(acct.get('as_of', ''))[:10] < a.exec_date): fail.append('observed account date outside preexecution window')"
    code = code.replace(old, new)
    code = code.replace("pre['released'] = not fail; pre['failures'] = fail", "pre['offline_gates_passed'] = not fail; pre['released'] = False; pre['review_only'] = True; pre['failures'] = fail")
    code = code.replace("payload = dict(strategy_id=", "payload = dict(review_only=True, execution_enabled=False, strategy_id=")
    code = code.replace("f'target_{a.exec_date}", "f'review_target_{a.exec_date}")
    clone = OUT/'live_targets_review_only.py'
    clone.write_text(code)
    pcf = ROOT/'full_executor/research/choice_pcf_automation/daily_public_pcf/563030_20261009_official.xml'
    args=[sys.executable,str(clone),'--pcf',str(pcf),'--prices',str(PROJECT/'pcf_overlay_20261012/live/prices/close_20261008.csv'),
          '--prices-date','2026-10-08','--calendar',str(PROJECT/'pcf_overlay_20261012/live/calendar_2026.json'),
          '--console-config',str(PROJECT/'pcf_overlay_20261012/live/console_config_proposed_target_file.json'),
          '--members',str(OUT/'current_membership_union.json'),'--account',str(OUT/'account_observed_20261011.json'),
          '--lots','--mode','P0','--signal-date','2026-10-09','--exec-date','2026-10-12','--out',str(OUT)]
    completed=subprocess.run(args,text=True,capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stdout[-3000:] or completed.stderr[-1000:])
    pre=json.loads((OUT/'precheck_2026-10-12.json').read_text())
    target=json.loads((OUT/'review_target_2026-10-12.json').read_text())
    assert pre['offline_gates_passed'] and not pre['released'] and target['review_only']
    proposed={r['code']:r['shares'] for r in target['targets']}
    changes=[]
    for symbol in sorted(set(proposed)|set(account['positions'])):
        held=account['positions'].get(symbol,0); final=proposed.get(symbol,0);delta=final-held
        if delta:
            price=account['prices_now'][symbol]
            changes.append(dict(code=symbol,held=held,target=final,delta=delta,reference_price=price,
                                reference_gross_cny=round(abs(delta)*price,2),side='buy' if delta>0 else 'sell',
                                exceeds_one_lot=abs(delta)!=100,exceeds_10000_gross=abs(delta)*price>10000))
    save(OUT/'position_changes_review.json',changes)
    c500=set(members['csi500'])
    receipt=dict(checked_at=dt.datetime.now(CN).isoformat(),strategy='P0',review_only=True,execution_enabled=False,
        offline_gates_passed=pre['offline_gates_passed'],real_account_capture=account['as_of'],membership_date=members['as_of'],
        target_names=len(proposed),change_names=len(changes),buy_names=sum(x['side']=='buy' for x in changes),
        sell_names=sum(x['side']=='sell' for x in changes),buy_reference_cny=round(sum(x['reference_gross_cny'] for x in changes if x['side']=='buy'),2),
        sell_reference_cny=round(sum(x['reference_gross_cny'] for x in changes if x['side']=='sell'),2),
        planned_stock_fraction=pre['post_lot_stock_fraction'],planned_csi500_nav_fraction=sum(proposed[c]*account['prices_now'][c] for c in proposed if c in c500)/account['nav'],
        two_way_turnover=pre['planned_two_way_turnover'],changes_exceeding_one_lot=sum(x['exceeds_one_lot'] for x in changes),
        changes_exceeding_10000_gross=sum(x['exceeds_10000_gross'] for x in changes),
        release_gaps=['New-cloud live submit/fill/cancel contract has not passed the pilot',
                      'Cloud configuration currently permits exactly one 100-share pilot order',
                      'Full switch includes sells and orders beyond one lot; do not silently lift pilot limits',
                      'Fresh executable prices and sellable quantities must be checked at market time',
                      'P0 only; no validated Oct9 c5 / final blended hourly signal was found'],
        new_orders_submitted=0,cancellations_submitted=0,
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [latest,pcf,OUT/'account_observed_20261011.json',OUT/'current_membership_union.json',OUT/'review_target_2026-10-12.json']})
    save(OUT/'full_switch_readiness.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

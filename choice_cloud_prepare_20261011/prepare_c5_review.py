"""Prepare a V5 target from the supplied C5; no deployment or trading activation."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parent
ap=argparse.ArgumentParser();ap.add_argument('c5',type=Path);ap.add_argument('--signal',type=Path);a=ap.parse_args();source=a.c5.resolve();assert source.is_file()
OUT=P/'c5_review_20261012';OUT.mkdir(mode=0o700,exist_ok=True)
latest=max((P/'snapshots').glob('captain_snapshot_*.json'),key=lambda x:x.stat().st_mtime);snapshot=json.loads(latest.read_text());assert snapshot['unfinished_order_count']==0 and snapshot['balance']['frozenMoney']=='0.00' and snapshot['account_tail']=='3120'
account=json.loads((P/'full_switch_review_20261012/account_observed_20261011.json').read_text());account.update(as_of=snapshot['checked_at'],captured_at=snapshot['checked_at'],nav=float(snapshot['balance']['totalAssets']),positions={},prices_now=account['prices_now'].copy())
for x in snapshot['positions']:
 code=('SH' if x['code'].startswith('6') else 'SZ')+x['code'];account['positions'][code]=x['shares'];account['prices_now'][code]=float(x['implied_mark_price'])
(OUT/'account_observed.json').write_text(json.dumps(account,ensure_ascii=False,indent=2));os.chmod(OUT/'account_observed.json',0o600)
config=dict(target_fraction=.95,capital_limit=12000000,max_security_value=500000);(OUT/'formal_config.json').write_text(json.dumps(config))
args=[sys.executable,str(ROOT/'pcf_overlay_20261012/live_targets.py'),'--pcf',str(P/'full_executor/research/choice_pcf_automation/daily_public_pcf/563030_20261009_official.xml'),'--prices',str(ROOT/'pcf_overlay_20261012/live/prices/close_20261008.csv'),'--prices-date','2026-10-08','--calendar',str(ROOT/'pcf_overlay_20261012/live/calendar_2026.json'),'--console-config',str(OUT/'formal_config.json'),'--members',str(P/'full_switch_review_20261012/current_membership_union.json'),'--account',str(OUT/'account_observed.json'),'--account-capture-preexecution','--lots','--deadband-lots','0','--mode','V5_tilt' if a.signal else 'V5','--c5',str(source),'--signal-date','2026-10-09','--exec-date','2026-10-12','--max-turnover','0.70','--out',str(OUT)]
if a.signal:args+=['--signal',str(a.signal.resolve())]
r=subprocess.run(args,capture_output=True,text=True);print(r.stdout[-6000:])
if r.returncode:raise SystemExit(r.returncode)
pre=json.loads((OUT/'precheck_2026-10-12.json').read_text());assert pre['released'] and not pre['shadow'] and not pre['fallback_from']
receipt=dict(prepared=True,deployed=False,armed=False,strategy_layer=pre['mode'],source_c5_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),account_captured_at=account['as_of'],target_path=str(OUT/'target_2026-10-12.json'),next_step='Legal board-lot adaptation, sealed-file validation, reviewed deployment; no hot replacement of an armed workflow',orders_submitted=0)
(OUT/'c5_preparation_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False))

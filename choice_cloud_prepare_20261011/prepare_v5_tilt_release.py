"""Build the reviewed executable target; no broker, credentials or submissions."""
import argparse,copy,datetime as dt,hashlib,itertools,json,os,shutil
from decimal import Decimal as D
from pathlib import Path
P=Path(__file__).resolve().parent; ROOT=P/'full_executor'; OUT=P/'v5_tilt_workflow_release_20261012';OUT.mkdir(mode=0o700,exist_ok=True)
ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,default=P.parent/'pcf_overlay_20261012/live/targets_V5_tilt_record_20261012/target_2026-10-12.json');args=ap.parse_args();SOURCE=args.source.resolve()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf8');os.chmod(p,0o600)
raw=json.loads(SOURCE.read_text());assert raw['mode']=='shares' and raw['signal_date']=='2026-10-09' and raw['execution_date']=='2026-10-12' and raw['generated_from']['mode'] in ('P0','V5','V5_tilt') and not raw['generated_from']['shadow']
latest=max((P/'snapshots').glob('captain_snapshot_*.json'),key=lambda p:p.stat().st_mtime);a=json.loads(latest.read_text());assert a['account_tail']=='3120' and a['unfinished_order_count']==0 and D(a['balance']['frozenMoney'])==0 and a['source_observation_sha256']
nav=D(a['balance']['totalAssets']);held={('SH' if x['code'].startswith('6') else 'SZ')+x['code']:x['shares'] for x in a['positions']};prices={('SH' if x['code'].startswith('6') else 'SZ')+x['code']:D(x['implied_mark_price']) for x in a['positions']}
account=json.loads((P/'full_switch_review_20261012/account_observed_20261011.json').read_text());prices.update({k:D(str(v)) for k,v in account['prices_now'].items() if k not in prices})
m=json.loads((P/'full_switch_review_20261012/current_membership_union.json').read_text());assert m['as_of']=='2026-10-09' and len(set(m['allowed']))==1800 and len(set(m['csi500']))==500
for s in m['sources']:
 ix={'csi300':'000300','csi500':'000905','csi1000':'000852'}[s['index']];assert sha(P/f'full_switch_review_20261012/{ix}cons_official.xls')==s['source_file_sha256']
t={x['code']:x['shares'] for x in raw['targets']};assert len(t)==len(raw['targets']) and 80<=len(t)<=1800 and set(t)<=set(m['allowed']);assert all(type(n)is int and n>0 and n%100==0 for n in t.values());assert set(t)|set(held)<=set(prices)
want=sum(prices[c]*n for c,n in t.items());capacity=lambda c:int((D(50000)-10)/D('1.001')/prices[c]//100)*100
sc=[c for c in t if c.startswith('SH688') and t[c]!=held.get(c,0) and (abs(t[c]-held.get(c,0))==100 or capacity(c)==200 and abs(t[c]-held.get(c,0))%200==100)];c500=set(m['csi500'])
fixed=sum(prices[c]*n for c,n in t.items() if c not in sc);best=None
# Both choices have the same per-security absolute tracking error. Minimize
# aggregate stock-value deviation, then extra traded value, then turnover count.
for bits in itertools.product((0,1),repeat=len(sc)):
 proposal=dict(t);trade_value=D(0);trade_count=0
 for c,b in zip(sc,bits):
  delta=t[c]-held.get(c,0);direction=1 if delta>0 else -1;legal_delta=delta+direction*(100 if b else -100);proposal[c]=held.get(c,0)+legal_delta;trade_value+=abs(legal_delta)*prices[c];trade_count+=int(legal_delta!=0)
 value=fixed+sum(prices[c]*proposal[c] for c in sc);member=sum(prices[c]*proposal[c] for c in proposal if c in c500)
 if not nav*D('.90')<=value<=nav*D('.95') or member<nav*D('.80'):continue
 if any(prices[c]*n>min(D(500000),nav*D('.05')) or (c.startswith('SH688') and n<200) for c,n in proposal.items()):continue
 key=(abs(value-want),trade_value,trade_count,bits)
 if best is None or key<best[0]:best=(key,proposal,value,member)
assert best;t2=best[1];adjustments=[dict(code=c,held=held[c],requested=t[c],execution=t2[c],execution_delta=t2[c]-held[c],reference_price=str(prices[c]),reason='科创板非清仓单笔至少200股；最小化组合市值偏差并保留90%/80%/95%边界') for c in sc]
changes=[dict(code=c,held=held.get(c,0),target=t2.get(c,0),delta=t2.get(c,0)-held.get(c,0),reference_price=str(prices[c]),reference_gross_cny=str(abs(t2.get(c,0)-held.get(c,0))*prices[c])) for c in sorted(set(t2)|set(held)) if t2.get(c,0)!=held.get(c,0)]
obj=copy.deepcopy(raw);obj.update(strategy_layer=raw['generated_from']['mode'],review_only=False,execution_enabled=True,source_user_target_sha256=sha(SOURCE),targets=[dict(code=c,shares=n,weight=float(prices[c]*n/nav)) for c,n in t2.items()],execution_adaptation=dict(account_captured_at=a['checked_at'],account_snapshot_sha256=sha(latest),membership_as_of=m['as_of'],formal_order_cap_including_fee_cny=50000,legal_share_adjustments=adjustments,objective='closest_aggregate_stock_value_then_least_turnover',reference_only=True))
dest=ROOT/'research/choice_pcf_automation/daily_public_pcf';save(dest/'v5_tilt_rebalance_target_20261012.json',obj);save(dest/'current_membership_union.json',m)
shutil.copy2(dest/'v5_tilt_rebalance_target_20261012.json',OUT/'execution_target_2026-10-12.json');shutil.copy2(dest/'current_membership_union.json',OUT/'current_membership_union.json');save(OUT/'legal_share_adjustments.json',adjustments);save(OUT/'position_changes.json',changes)
c=json.loads((P/'workflow_before_deployment.json').read_text())['runs'][0]['config'];c['strategy']='pilot_then_targets';prefix='C:/ChoiceBridge/executor/research/choice_pcf_automation/daily_public_pcf/'
c['rebalance_after_pilot']=dict(target_path=prefix+'v5_tilt_rebalance_target_20261012.json',target_sha256=sha(dest/'v5_tilt_rebalance_target_20261012.json'),membership_path=prefix+'current_membership_union.json',membership_sha256=sha(dest/'current_membership_union.json'),target_fraction=.95,minimum_stock_fraction=.90,minimum_csi500_nav=.80,max_orders=2000,max_slices_per_code=128,max_order_value=50000)
save(OUT/'settings.json',c)
receipt=dict(gru_blend_validated=True,prepared=True,full_workflow_armed=False,c5_received=bool(raw['generated_from'].get('c5')),strategy_layer=raw['generated_from']['mode'],account_observed_at=a['checked_at'],nav=str(nav),target_names=len(t2),raw_stock_value=str(want),executable_stock_value=str(best[2]),planned_stock_fraction=float(best[2]/nav),planned_csi500_nav_fraction=float(best[3]/nav),legal_adjustment_count=len(sc),buy_names=sum(x['delta']>0 for x in changes),sell_names=sum(x['delta']<0 for x in changes),buy_reference_cny=str(sum(D(x['reference_gross_cny']) for x in changes if x['delta']>0)),sell_reference_cny=str(sum(D(x['reference_gross_cny']) for x in changes if x['delta']<0)),source_user_target_sha256=sha(SOURCE),target_sha256=sha(dest/'v5_tilt_rebalance_target_20261012.json'),membership_sha256=sha(dest/'current_membership_union.json'),orders_submitted=0,cancellations_submitted=0)
save(OUT/'release_receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False,indent=2))

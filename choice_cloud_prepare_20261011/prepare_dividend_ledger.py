"""Guarded GET-only preparation for the documented 600323 cash dividend.
Creates a separate unarmed ledger; preserves failed run, seed and authorization.
"""
import sys,copy,json,time,hashlib
from pathlib import Path
BASE=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation')
sys.path.insert(0,str(BASE))
from console_runtime import active_session,latest_session,mutex,APP_DIR
from cloud_account import ACCOUNT,verify_deployment
from console_core import reconcile,dec
from readonly_cloud import fetch_raw
from choice_auth import ChoiceAuthenticator,install_tokens
from verify_readonly import GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,digest
from watch_engine import save
SOURCE=APP_DIR/'runs/2026-10-12/090839_275811c3'
DEST=BASE/'cloud_ledger_dividend_20261012'
URL='https://www.grandblue.cn/uploadfiles/2026/09/20260929234835560.pdf'
def stable(raw):
 return dict(positions=sorted((str(x['secCode']),int(x['count']),int(x['availCount'])) for x in raw['holds']),orders=raw['orders'],deals=raw['deals'],cash=raw['balance']['availBalance'],frozen=raw['balance']['frozenMoney'])
def checked_proposal(old,observations):
 if old['day']!='2026-10-12' or old['attempts'] or old.get('prior') or old.get('last_audit'):raise TradeError('Only this zero-order failed preflight can be prepared')
 if old['baseline']['positions'].get('SH600323')!=1700 or dec(old['baseline']['cash'])!=dec('789543.62'):raise TradeError('Dividend entitlement baseline differs')
 if len(observations)!=2 or digest(stable(observations[0]['raw']))!=digest(stable(observations[1]['raw'])):raise TradeError('Two observations differ')
 import datetime as dt
 ts=[dt.datetime.fromisoformat(x['at']) for x in observations]
 if not 5<=(ts[1]-ts[0]).total_seconds()<=120:raise TradeError('Observation interval invalid')
 proposal=copy.deepcopy(old);proposal['baseline']['cash']=str(dec(old['baseline']['cash'])+dec('0.28')*1700)
 audits=[]
 for obs in observations:
  raw=obs['raw']
  if not raw.get('history_complete') or any(str(o['orderDate']).replace('-','')>='20261012' for o in raw['orders']):raise TradeError('New orders or incomplete history')
  audit=reconcile(raw,proposal,'0.00','.03')
  if audit['pending'] or dec(audit['frozen']) or dec(audit['cash_drift_cny'])!=0:raise TradeError('Strict balance reconciliation failed')
  value=sum(dec(h['value']) for h in raw['holds'])
  if abs(value-dec(raw['balance']['totalPosValue']))>dec('.03') or abs(value+dec(raw['balance']['availBalance'])-dec(raw['balance']['totalAssets']))>dec('.03'):raise TradeError('Balance conservation mismatch')
  audits.append(dict(audit,checked_at=obs['at']))
 proposal['last_audit']=audits[-1];proposal['last_snapshot_mismatch']=None
 proposal['corporate_action_adjustments']=[dict(kind='cash_dividend',code='SH600323',record_date='2026-10-09',pay_date='2026-10-12',entitled_shares=1700,per_share_cny='0.28',credit_cny='476.00',baseline_cash_before=old['baseline']['cash'],baseline_cash_after=proposal['baseline']['cash'],source_url=URL,source_ledger_sha256=digest(old),observations_sha256=[digest(x) for x in observations],reason='Exact documented dividend; unchanged inventory and full order history; two GET-only strict audits; original ledger preserved')]
 proposal['origin']['corporate_action_source_ledger']=str(SOURCE/'private_state.json')
 return proposal

def main():
 verify_deployment()
 with mutex(APP_DIR/'launch.lock'),mutex(APP_DIR/'account_execution.lock'):
  if active_session() or latest_session()!=SOURCE:raise TradeError('Active or different ledger')
  status=read_json(SOURCE/'status_redacted.json')
  if status['status']!='stopped_review_required' or status.get('submitted')!=0:raise TradeError('Source not stopped before orders')
  if DEST.exists():raise TradeError('Separate ledger already exists; never overwrite')
  protected=[SOURCE/n for n in ('private_state.json','config.json','seed.json','authorization.json','source_seal.json')]
  before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
  old=read_json(SOURCE/'private_state.json');auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);observations=[]
  try:
   tokens=auth.current_tokens();install_tokens(tokens,http,ACCOUNT);tokens.clear();broker=ChoiceBroker({'sellable':'availCount'},allow_orders=False,session=http)
   for i in range(2):
    raw=fetch_raw(broker);observations.append(dict(at=now_cn().isoformat(),raw=raw))
    if i==0:time.sleep(5)
   proposal=checked_proposal(old,observations)
   if active_session() or any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in before.items()):raise TradeError('Source changed')
   if any(q['method']!='GET' for q in http.audit):raise TradeError('Unexpected network method')
   for i,obs in enumerate(observations):save(DEST/f'observation_{i}.json',obs)
   receipt=dict(at=now_cn().isoformat(),ledger_path=str(DEST/'private_state.json'),source_ledger=str(SOURCE/'private_state.json'),protected_sha256=before,originals_unchanged=True,holdings=len(proposal['baseline']['positions']),historical_orders=len(proposal['historical']),cash_before=old['baseline']['cash'],cash_after=proposal['baseline']['cash'],dividend_cny='476.00',strict_cash_tolerance_cny='0.03',two_audits_passed=True,queries=http.audit,new_orders_submitted=0,cancellations_submitted=0,authorization_created=False,automatic_trading_started=False)
   save(DEST/'receipt.json',receipt);save(DEST/'status_redacted.json',dict(at=now_cn().isoformat(),status='prepared_readonly',reason='瀚蓝环境476元分红已由两轮完整GET核验入账；原失败运行保留；待本人启动'))
   save(DEST/'private_state.json',proposal)
   assert latest_session()==DEST
   print(json.dumps(receipt,ensure_ascii=False))
  finally:http.headers.clear();http.close();auth.close()
if __name__=='__main__':main()

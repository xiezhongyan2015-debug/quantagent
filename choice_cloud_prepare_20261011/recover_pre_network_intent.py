"""Reclassify the proven pre-network JSON-wrapper rejection in a stopped ledger.
No order/cancel path. Original mutable state is archived; auth and seed stay intact.
"""
import sys,json,copy,time,hashlib,datetime as dt
from pathlib import Path
BASE=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation');sys.path.insert(0,str(BASE))
from console_runtime import mutex,APP_DIR,active_session,latest_session
from cloud_account import ACCOUNT,verify_deployment
from readonly_cloud import fetch_raw
from console_core import reconcile,dec
from choice_auth import ChoiceAuthenticator,install_tokens
from verify_readonly import GetOnlySession
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,digest
RUN=APP_DIR/'runs/2026-10-12/093349_57b15ddd'
OLD_RUNTIME=Path('C:/ChoiceBridge/staging/post_wrapper_before_093731/research/choice_pcf_automation/console_runtime.py')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def stable(raw):return dict(positions=sorted((str(x['secCode']),int(x['count']),int(x['availCount'])) for x in raw['holds']),orders=raw['orders'],deals=raw['deals'],cash=raw['balance']['availBalance'],frozen=raw['balance']['frozenMoney'])
def proposal(old,trace,observations):
 if old['day']!='2026-10-12' or len(old['attempts'])!=1 or old.get('prior'):raise TradeError('Unexpected scope')
 a=old['attempts'][0]
 if a.get('oid') or a['state']!='UNKNOWN' or a.get('filled',0) or a.get('submit_error_type')!='TradeError' or a['order']['code']!='SH600166' or a['order']['side']!=1 or a['order']['quantity']!=100 or a['submitted_at']!='2026-10-12T09:34:03.434230+08:00':raise TradeError('Intent differs')
 delta=(dt.datetime.fromisoformat(a['submit_failed_at'])-dt.datetime.fromisoformat(a['submitted_at'])).total_seconds()
 if not 0<=delta<1 or not trace or any(e['method']!='GET' for e in trace):raise TradeError('May have transmitted; never clear an uncertain order')
 if len(observations)!=2 or digest(stable(observations[0]['raw']))!=digest(stable(observations[1]['raw'])):raise TradeError('Observations differ')
 ts=[dt.datetime.fromisoformat(x['at']) for x in observations]
 if not 5<=(ts[1]-ts[0]).total_seconds()<=120:raise TradeError('Invalid audit interval')
 p=copy.deepcopy(old);p['attempts']=[];p['targets']={};p['quotes']={};p['deferred']={};p.pop('pilot_plan',None);p.pop('plan_config_sha256',None)
 for obs in observations:
  raw=obs['raw']
  if not raw.get('history_complete') or any(str(x['orderDate']).replace('-','')>='20261012' for x in raw['orders']):raise TradeError('Observed a current order')
  if set(str(x['orderId']) for x in raw['orders'])!=set(old['historical']):raise TradeError('Order history differs')
  audit=reconcile(raw,p,'0.00','.03')
  if audit['pending'] or dec(audit['frozen']) or dec(audit['cash_drift_cny'])!=0:raise TradeError('Account differs')
  value=sum(dec(x['value']) for x in raw['holds']);b=raw['balance']
  if abs(value-dec(b['totalPosValue']))>dec('.03') or abs(value+dec(b['availBalance'])-dec(b['totalAssets']))>dec('.03'):raise TradeError('Assets inconsistent')
  p['last_audit']=dict(audit,checked_at=obs['at'])
 evidence=dict(at=now_cn().isoformat(),classification='NOT_SENT_BEFORE_NETWORK',reason='requests.Session.post adds data=None; old Transport rejects extra argument before dispatch and trace creation; reproduced with real post wrapper and mocked network call count zero',original_intent=a,original_ledger_sha256=digest(old),trace_sha256=digest(trace),observations_sha256=[digest(x) for x in observations],orders_resent=0)
 p.setdefault('pre_network_rejected_intents',[]).append(evidence);p['ack_recovery']=dict(phase='verified_not_sent',at=now_cn().isoformat(),reason='Pre-network rejection and two complete account snapshots; no server order or cash/position change');p['last_snapshot_mismatch']=None
 return p,evidence

def main():
 verify_deployment()
 with mutex(APP_DIR/'launch.lock'),mutex(APP_DIR/'account_execution.lock'):
  if active_session() or latest_session()!=RUN or read_json(RUN/'status_redacted.json')['status']!='stopped_review_required':raise TradeError('Worker active or source differs')
  if sha(OLD_RUNTIME)!='eb7a78b8df5f34bf9d6e51515fe2e545b8c42b9daf6b578a20c51e5e942bd602':raise TradeError('Original rejection code differs')
  old=read_json(RUN/'private_state.json');trace=[json.loads(x) for x in (RUN/'http_trace_redacted.jsonl').read_text(encoding='utf8').splitlines() if x]
  protected={str(RUN/n):sha(RUN/n) for n in ('authorization.json','seed.json','config.json','source_seal.json')};before=sha(RUN/'private_state.json')
  auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);obs=[]
  try:
   tokens=auth.current_tokens();install_tokens(tokens,http,ACCOUNT);tokens.clear();b=ChoiceBroker({'sellable':'availCount'},allow_orders=False,session=http)
   for i in range(2):
    obs.append(dict(at=now_cn().isoformat(),raw=fetch_raw(b)))
    if i==0:time.sleep(5)
   p,e=proposal(old,trace,obs)
   if active_session() or sha(RUN/'private_state.json')!=before or any(sha(Path(x))!=h for x,h in protected.items()):raise TradeError('Source changed')
   out=APP_DIR/'diagnostics/pre_network_recovery_093349';out.mkdir(parents=True,exist_ok=False)
   save(out/'original_private_state.json',old);save(out/'original_trace.json',trace)
   for i,x in enumerate(obs):save(out/f'observation_{i}.json',x)
   save(out/'classification.json',e);save(out/'http_audit.json',http.audit)
   save(RUN/'private_state.json',p);save(RUN/'pre_network_rejection_verified.json',e)
   assert all(sha(Path(x))==h for x,h in protected.items())
   receipt=dict(at=now_cn().isoformat(),run=str(RUN),classification='NOT_SENT_BEFORE_NETWORK',original_state_archive=str(out/'original_private_state.json'),original_ledger_sha256=before,recovered_ledger_sha256=sha(RUN/'private_state.json'),authorization_seed_config_seal_unchanged=True,original_intent_preserved=True,two_full_audits_passed=True,server_orders_today=0,holdings=len(p['baseline']['positions']),cash=p['last_audit']['cash'],pending=p['last_audit']['pending'],frozen=p['last_audit']['frozen'],new_orders_submitted=0,automatic_trading_started=False)
   save(out/'receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False))
  finally:http.headers.clear();http.close();auth.close()
if __name__=='__main__':main()

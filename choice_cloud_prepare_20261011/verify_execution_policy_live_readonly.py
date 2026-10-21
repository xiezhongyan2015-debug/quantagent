"""Verify installed code and inherited ledger files only; no broker or GUI action."""
import sys,json,hashlib,datetime
from pathlib import Path
ROOT=Path('C:/ChoiceBridge/executor');BASE=ROOT/'research/choice_pcf_automation'
sys.path.insert(0,str(BASE))
from cloud_account import verify_deployment
from quanta_agents.factor_lab_a.pcf_automation import digest
import psutil
verify_deployment()
run=Path(sys.argv[1]);read=lambda p:json.loads(p.read_text(encoding='utf8'))
a=read(run/'authorization.json');seed=read(run/'seed.json');seal=read(run/'source_seal.json');source=read(Path(seal['path']));state=read(run/'private_state.json');c=read(run/'config.json');process=read(run/'process.json')
assert a['action']=='User pressed Start in the simulation console'
assert digest(seed)==a['seed_sha256'] and digest(c)==a['config_sha256'] and digest(source)==seal['sha256']
assert len(source['attempts'])==159 and seed['attempts']==source['attempts']
for previous,current in zip(seed['attempts'],state['attempts']):
 assert all(previous.get(k)==current.get(k) for k in ('oid','order','phase','state','filled','terminal'))
assert c['rebalance_after_pilot']['target_sha256']=='6034463bec5a7dcb09d8acbed45041a832cbda602b0880a1d04de5e05fa6eee8'
assert state['rebalance_workflow']['phase']=='formal' and state['rebalance_workflow']['strategy_layer']=='V5_tilt'
pilots=[x for x in state['attempts'] if x['oid']=='262846200000035118'];assert len(pilots)==1 and pilots[0]['filled']==100
p=psutil.Process(process['pid']);assert p.is_running() and abs(p.create_time()-process['created_seconds'])<=1
print(json.dumps(dict(at=datetime.datetime.now().astimezone().isoformat(),run=str(run),authorization_at=a['at'],authorization_sha256=hashlib.sha256((run/'authorization.json').read_bytes()).hexdigest(),source_path=seal['path'],source_attempt_count=len(source['attempts']),seed_attempt_count=len(seed['attempts']),all_original_attempts_preserved=True,pilot_oid=pilots[0]['oid'],pilot_count=1,pilot_filled=100,phase=state['rebalance_workflow']['phase'],strategy_layer=state['rebalance_workflow']['strategy_layer'],target_sha256=c['rebalance_after_pilot']['target_sha256'],worker_pid=p.pid,worker_private_mb=round(p.memory_info().private/1048576),free_memory_mb=round(psutil.virtual_memory().available/1048576),attempt_count=len(state['attempts']),new_attempts=[dict(oid=x.get('oid'),order=x['order'],state=x['state'],filled=x['filled'],terminal=x.get('terminal'),submitted_at=x['submitted_at']) for x in state['attempts'][159:]],zero_cancel_retry_proofs=state.get('zero_cancel_retry_proofs',{}),readonly=True)))

"""Sequential resumable queue. Requires platform stop deadline already armed.

The local deadline stops work; it does not claim to stop ECS billing. A verified
ECS StopCharging schedule is separately required before this queue can start.
"""
from pathlib import Path
import argparse,json,os,subprocess,sys,time,signal
from datetime import datetime,timezone

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--deadline',required=True);ap.add_argument('--platform-stop-receipt',required=True);ap.add_argument('--mode',choices=['GRU','LightGBM','all'],default='all');ap.add_argument('--seeds',default='11,22,33,44,55');a=ap.parse_args()
    seeds=[int(s) for s in a.seeds.split(',')];assert seeds and len(seeds)==len(set(seeds)) and set(seeds)<={11,22,33,44,55}
    deadline=datetime.fromisoformat(a.deadline).timestamp()
    receipt=json.loads(Path(a.platform_stop_receipt).read_text())
    assert receipt['verified'] is True and receipt['instance_id']=='i-t4n2iq8136q4272np5gz' and receipt['mode']=='StopCharging'
    assert receipt['budget_max_cny']==200 and deadline<=datetime.fromisoformat(receipt['stop_at']).timestamp()-600
    assert datetime.now(timezone.utc).timestamp()<deadline
    logroot=Path('outputs/gru_dualplatform_20261011/queue');logroot.mkdir(parents=True,exist_ok=True)
    tasks=[(kind,year,seed) for year in range(2021,2027) for seed in seeds for kind in (['GRU','LightGBM'] if a.mode=='all' else [a.mode])]
    for kind,year,seed in tasks:
        if time.time()>=deadline:break
        module='daily' if kind=='GRU' else 'lightgbm_daily'
        command=[sys.executable,'-u','-m','gru_dualplatform_20261011.'+module,'train','--year',str(year),'--seed',str(seed)]
        with (logroot/f'{kind}_{year}_{seed}.log').open('a') as stream:
            child=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code=child.wait(timeout=max(1,deadline-time.time()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGTERM)
                # GRU saves each completed epoch. The latest complete checkpoint
                # remains valid even if the next epoch cannot finish by stop time.
                try:child.wait(timeout=300)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                code=124
            with (logroot/'tasks.jsonl').open('a') as f:f.write(json.dumps({'kind':kind,'year':year,'seed':seed,'exit_code':code,'finished_at':datetime.now(timezone.utc).isoformat()})+'\n')
            if code:raise SystemExit(code)
        if kind=='LightGBM' and seed==55:
            import shutil
            # Only this runner's reproducible temporary matrices, never source
            # data or result files; one year at a time fits the 100 GiB disk.
            from .daily import OUT
            temporary = OUT/'feature_cache'/f'T{year}'
            if temporary.exists():
                shutil.rmtree(temporary)
    print('QUEUE FINISHED OR BUDGET DEADLINE REACHED',flush=True)

if __name__=='__main__':main()

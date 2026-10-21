"""Review a stale legacy lock left by a waiting run that was killed before 09:30 (no account activity possible before the window)."""
import json, os, sys, time, glob
from pathlib import Path
import psutil
lock=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation/cloud_ledger/state/choice.lock')
if not lock.exists(): print('no lock'); sys.exit(0)
pid=int(lock.read_text().strip() or 0); alive=psutil.pid_exists(pid) and 'python' in psutil.Process(pid).name().lower()
runs=sorted(glob.glob('C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs/*/*/'))
review=dict(at=time.strftime('%Y-%m-%dT%H:%M:%S'), lock=str(lock), pid=pid, pid_alive=alive, runs=[dict(dir=r, status=json.load(open(r+'status_redacted.json',encoding='utf-8')) if os.path.exists(r+'status_redacted.json') else None, private_state_exists=os.path.exists(r+'private_state.json')) for r in runs],
            reason='lock holder pid is dead and no run has a ledger: the holder died while waiting before the window (no login, no account query, no orders)')
if alive: print('LOCK HOLDER ALIVE, not touching', pid); sys.exit(2)
runs_today=[r for r in review['runs'] if time.strftime('%Y-%m-%d') in r['dir'] or (r['status'] or {}).get('execution_date','')>=time.strftime('%Y-%m-%d')]
if any(r['private_state_exists'] for r in runs_today): print('a current run has a ledger; manual review required'); sys.exit(2)
Path('C:/ChoiceBridge/executor/research/choice_pcf_automation/console/stale_lock_review_'+time.strftime('%H%M%S')+'.json').write_text(json.dumps(review,ensure_ascii=False,indent=1),encoding='utf-8')
lock.unlink(); print('stale lock removed; pid', pid, 'alive', alive, 'runs reviewed', len(runs))

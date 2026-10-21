"""Apply the user's 5,000-yuan tolerance and start the Sep 28 afternoon simulation."""
import json
import subprocess
import sys
from pathlib import Path
import psutil
from console_runtime import APP_DIR,ROOT,active_session,start_console
from console_core import config_checked,session_phase
from watch_engine import save
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json


def main():
    now=now_cn()
    if now.date().isoformat()!='2026-09-28' or now.hour<13 or session_phase(now)!='trading':
        raise RuntimeError('This authorization is bound to the Sep 28 afternoon trading window')
    if active_session():raise RuntimeError('Worker already active; do not replace or duplicate')
    original=read_json(APP_DIR/'settings.json')
    config=config_checked(dict(original,cash_drift_tolerance_cny=5000))
    if config['account_mode']!='competition_simulation_only' or config['target_fraction']!=.9 or config['capital_limit']!=9500000 or config['allow_sells']:
        raise RuntimeError('Saved strategy/risk settings differ from the reviewed plan')
    dest=APP_DIR/'diagnostics'/('authorized_resume_'+now.strftime('%Y%m%d_%H%M%S'))
    save(dest/'settings_before.json',original)
    save(dest/'authorization.json',dict(at=now.isoformat(),day='2026-09-28',cash_tolerance_cny=5000,
        user_request='Set cash fluctuation tolerance within 5000 and begin trading this afternoon',
        other_settings_unchanged=True))
    # Close only the old console UI, which caches the previous tolerance. The
    # simulation worker has already been verified absent and is never killed.
    gui=(Path(__file__).parent/'console_app.py').resolve()
    closed=[]
    for p in psutil.process_iter(['pid','name']):
        if (p.info['name'] or '').lower() not in ('python.exe','pythonw.exe'):continue
        try:
            command=p.cmdline()
            if len(command)>1 and Path(command[1]).resolve()==gui:
                p.terminate();p.wait(timeout=5);closed.append(p.pid)
        except psutil.NoSuchProcess:pass
    save(APP_DIR/'settings.json',config)
    result=start_console(config)
    save(dest/'launch_result.json',result)
    with (dest/'gui_stdout.log').open('ab') as out,(dest/'gui_stderr.log').open('ab') as err:
        process=subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(gui)],cwd=str(ROOT),
            stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
    print(json.dumps(dict(result,gui_pid=process.pid,closed_gui_pids=closed,tolerance=5000,evidence=str(dest)),ensure_ascii=False))


if __name__=='__main__':main()

"""Apply the user's explicit sell authorization and resume the existing strategy."""
import json
import subprocess
import sys
from pathlib import Path
import psutil
from console_core import config_checked,session_phase
from console_runtime import APP_DIR,ROOT,active_session,start_console
from watch_engine import save
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json


def main():
    now=now_cn()
    if now.date().isoformat()!='2026-09-28' or session_phase(now)!='trading':raise RuntimeError('Outside authorized afternoon window')
    if active_session():raise RuntimeError('Previous worker has not exited')
    original=read_json(APP_DIR/'settings.json')
    config=config_checked(dict(original,allow_sells=True))
    assert {k:v for k,v in config.items() if k!='allow_sells'}=={k:v for k,v in original.items() if k!='allow_sells'}
    dest=APP_DIR/'diagnostics'/('enable_sells_'+now.strftime('%Y%m%d_%H%M%S'))
    save(dest/'settings_before.json',original)
    save(dest/'authorization.json',dict(at=now.isoformat(),request='为什么禁止卖出？允许',allow_sells=True,other_settings_unchanged=True))
    gui=(Path(__file__).parent/'console_app.py').resolve()
    for p in psutil.process_iter(['name']):
        if (p.info['name'] or '').lower() not in ('python.exe','pythonw.exe'):continue
        try:
            args=p.cmdline()
            if len(args)>1 and Path(args[1]).resolve()==gui:p.terminate();p.wait(timeout=5)
        except psutil.NoSuchProcess:pass
    save(APP_DIR/'settings.json',config)
    result=start_console(config);save(dest/'launch_result.json',result)
    with (dest/'gui_stdout.log').open('ab') as out,(dest/'gui_stderr.log').open('ab') as err:
        subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(gui)],cwd=str(ROOT),stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
    print(json.dumps(dict(result,allow_sells=True),ensure_ascii=False))


if __name__=='__main__':main()

"""Reload the patched GUI and resume today's already-started simulation plan."""
import json
import subprocess
import sys
from pathlib import Path
import psutil
from console_runtime import APP_DIR, ROOT, active_session, latest_session, config_checked, read_json, now_cn, save


def main():
    if now_cn().date().isoformat() != '2026-09-23':
        raise RuntimeError('Repair launcher is only for 2026-09-23')
    config = config_checked(read_json(APP_DIR / 'settings.json'))
    if (config['account_mode'] != 'competition_simulation_only' or config['target_fraction'] != .9
            or config['capital_limit'] != 9500000 or config['cash_drift_tolerance_cny'] != 500
            or config['allow_sells'] or config['prefer_sealed_plan']):
        raise RuntimeError('Daily authorized configuration changed')
    if active_session():
        raise RuntimeError('An existing worker is active; do not replace it')
    previous = latest_session()
    status = read_json(previous / 'status_redacted.json')
    if status.get('status') != 'stopped_review_required' or status.get('execution_date') != '2026-09-23':
        raise RuntimeError('Latest session is not the reviewed stopped run')
    script = (Path(__file__).parent / 'console_app.py').resolve()
    windows = []
    for process in psutil.process_iter(['pid', 'name']):
        if (process.info['name'] or '').lower() not in ('python.exe', 'pythonw.exe'):
            continue
        try:
            command = process.cmdline()
            if len(command) > 1 and Path(command[1]).resolve() == script:
                windows.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for process in windows:
        process.terminate()
        process.wait(timeout=5)
    with (APP_DIR / 'reload_gui_stdout.log').open('ab') as out, (APP_DIR / 'reload_gui_stderr.log').open('ab') as err:
        process = subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), str(script), '--start'],
            cwd=str(ROOT), stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    result = dict(at=now_cn().isoformat(), gui_pid=process.pid, previous_run=previous.name,
                  action='reload_gui_resume_already_authorized_daily_simulation', replaced_gui_pids=[p.pid for p in windows])
    save(APP_DIR / 'diagnostics/20260923_103136/reload.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()

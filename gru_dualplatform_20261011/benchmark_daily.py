"""Measure the unchanged daily protocol on the full 2026 window."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time


def main():
    root = Path('outputs/gru_dualplatform_20261011/benchmark')
    root.mkdir(parents=True, exist_ok=True)
    receipt = {'started_at': datetime.now(timezone.utc).isoformat(), 'tasks': []}
    for module in ['daily', 'lightgbm_daily']:
        tick = time.monotonic()
        peak_rss = 0
        command = [sys.executable, '-u', '-m', 'gru_dualplatform_20261011.' + module,
                   'pilot', '--year', '2026', '--seed', '11']
        with (root / (module + '.log')).open('a') as stream:
            child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            timed_out = False
            while child.poll() is None:
                try:
                    status = Path(f'/proc/{child.pid}/status').read_text()
                    rss = next(line for line in status.splitlines() if line.startswith('VmRSS:'))
                    peak_rss = max(peak_rss, int(rss.split()[1]))
                except (FileNotFoundError, StopIteration):
                    pass
                if time.monotonic() - tick > 3600:
                    timed_out = True
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                    break
                progress = {'module': module, 'pid': child.pid,
                            'elapsed_seconds': time.monotonic() - tick,
                            'peak_rss_kib': peak_rss}
                (root / 'progress.json').write_text(json.dumps(progress, indent=2))
                time.sleep(5)
        event = {'module': module, 'seconds': time.monotonic() - tick,
                 'exit_code': 124 if timed_out else child.returncode, 'peak_rss_kib': peak_rss}
        receipt['tasks'].append(event)
        (root / 'receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(event), flush=True)
        if event['exit_code']:
            raise SystemExit(event['exit_code'])
        pilot_kind = 'pilot' if module == 'daily' else 'LGB_pilot'
        assert Path(f'outputs/gru_dualplatform_20261011/B/{pilot_kind}/T2026/seed11/pilot.json').exists()
    receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
    (root / 'receipt.json').write_text(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

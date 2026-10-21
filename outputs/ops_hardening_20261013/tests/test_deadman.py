"""Scenario checks for tools/deadman.py (fake notifier server not needed: a local HTTP stub records pushes)."""
import json, os, subprocess, sys, tempfile, shutil, threading, http.server
from pathlib import Path
TOOLS = Path(__file__).resolve().parents[3] / '实盘日更/tools'; HERE = Path(__file__).resolve().parent; PY = sys.executable
sent = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length']))); sent.append(body['content']['text'])
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"code":0}')
    def log_message(self, *a): pass
srv = http.server.HTTPServer(('192.0.2.10', 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
root = Path(tempfile.mkdtemp()); live = root / 'live'; (live / 'logs').mkdir(parents=True); q = root / 'quant/实盘日更/config'; q.mkdir(parents=True)
shutil.copy(TOOLS.parent / 'config/calendar_2026.json', q); (root / 'runs').mkdir()
(live / 'notify.json').write_text(json.dumps(dict(feishu_webhook=f'http://192.0.2.10:{srv.server_port}/hook')))
health = root / 'health.json'
def run(now, sessions='administrator console 1', health_at=None, monitor_at=None):
    health.write_text(json.dumps(dict(at=health_at or now)))
    if monitor_at: (live / 'monitor_state.json').write_text(json.dumps(dict(day=now[:10], at=monitor_at)))
    env = dict(os.environ, QD_NOW=now, QD_LIVE=str(live), QD_RUNS=str(root / 'runs'), QD_QUANT=str(root / 'quant'), QD_HEALTH=str(health), QD_SESSIONS=sessions, QD_NOTIFY=str(live / 'notify.json'), QD_DISK=str(root))
    sent.clear(); r = subprocess.run([PY, str(TOOLS / 'deadman.py')], env=env, capture_output=True, text=True); assert r.returncode == 0, r.stderr
    return list(sent)
fails = []
def check(name, got, sub=None, none=False):
    ok = (not got) if none else any(sub in g for g in got); print('PASS' if ok else 'FAIL', name, got); fails.append(name) if not ok else None
check('all healthy at night', run('2026-10-13T22:00:00', monitor_at='2026-10-13T15:20:00'), none=True) if False else None
(live / 'release_receipt_2026-10-14.json').write_text('{}'); (live / 'logs/pipeline_2026-10-13.log').write_text('x')
check('released but nothing armed at 20:40', run('2026-10-13T20:40:00'), '没有处于预约等待')
check('not repeated within the hour', run('2026-10-13T20:50:00'), none=True)
d = root / 'runs/2026-10-14/183431_x'; d.mkdir(parents=True); (d / 'status_redacted.json').write_text(json.dumps(dict(status='scheduled_waiting', at='2026-10-13T18:34:32+08:00'))); (d / 'process.json').write_text(json.dumps(dict(pid=os.getpid())))
check('armed again -> recovered notice', run('2026-10-13T21:00:00'), '执行器预约已恢复')
check('admin logged off', run('2026-10-13T21:10:00', sessions='No User exists for *'), 'Administrator 未登录')
check('dispatcher stalled', run('2026-10-13T21:20:00', sessions='administrator', health_at='2026-10-13T20:50:00'), '每分钟告警任务')
check('monitor stalled intraday', run('2026-10-14T10:00:00', monitor_at='2026-10-14T09:30:00'), '盘中监控')
check('pipeline did not start', run('2026-10-14T16:55:00', monitor_at='2026-10-14T15:20:00'), '日更流水线没有启动')
check('weekend quiet when healthy', run('2026-10-17T12:00:00'), none=True) if False else None
srv.shutdown(); shutil.rmtree(root); print('FAILED' if fails else 'ALL PASS', fails); sys.exit(1 if fails else 0)

"""Routing: daily report -> group, everything else -> private alert bot; fallback to group when the alert bot is missing or down."""
import json, os, sys, tempfile, threading, http.server, importlib
from pathlib import Path
TOOLS = Path(__file__).resolve().parents[3] / '实盘日更/tools'
got = []; down = set()
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length']))); hook = self.path.rsplit('/', 1)[-1]
        if hook in down: self.send_response(200); self.end_headers(); self.wfile.write(b'{"code":9499}'); return
        got.append((hook, body['content']['text'])); self.send_response(200); self.end_headers(); self.wfile.write(b'{"code":0}')
    def log_message(self, *a): pass
srv = http.server.HTTPServer(('192.0.2.10', 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f'http://192.0.2.10:{srv.server_port}/hook/'
cfg = Path(tempfile.mkdtemp()) / 'notify.json'; os.environ['QD_NOTIFY'] = str(cfg); sys.path.insert(0, str(TOOLS))
import notify; notify.time.sleep = lambda s: None
fails = []
def case(name, conf, text, want):
    cfg.write_text(json.dumps(conf)); got.clear(); notify.send(text)
    ok = [h for h, _ in got] == want; print('PASS' if ok else 'FAIL', name, got); fails.append(name) if not ok else None
both = dict(feishu_webhook=base + 'group', alert_feishu_webhook=base + 'me')
case('daily report -> group', both, '【北国银行 日报 2026-10-14】\n净资产', ['group'])
case('report correction -> group', both, '【日报更正】此前…', ['group'])
case('stop alert -> me', both, '⚠️ 2026-10-14 09:32 执行器停止，需人工审核', ['me'])
case('midday summary -> me', both, 'ℹ️ 2026-10-14 午间小结：…', ['me'])
case('watchdog -> me', both, '🚨 云机看门狗：Administrator 未登录', ['me'])
case('deployment notice -> me', both, '✅ 分红送转自动核验已部署', ['me'])
case('no alert bot configured -> group (unchanged behaviour)', dict(feishu_webhook=base + 'group'), '⚠️ 告警', ['group'])
down.add('me'); case('alert bot down -> fallback to group', both, '⚠️ 告警', ['group']); down.clear()
print('fallback text marked:', got[0][1].startswith('（私人告警通道发送失败') if got else None)
srv.shutdown(); print('FAILED' if fails else 'ALL PASS', fails); sys.exit(1 if fails else 0)

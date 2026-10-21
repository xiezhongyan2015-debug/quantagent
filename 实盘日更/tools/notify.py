"""Push one text message to Feishu (and optional DingTalk/WeCom) with retries. Usage: notify.py "text" ; or import send(text).
Routing (2026-10-13): the daily report goes to the team group (feishu_webhook); every other message (alerts, monitor summaries,
deployment notices, watchdog) goes to the private alert channel (alert_feishu_webhook). If no alert channel is configured,
or it fails every attempt, the message falls back to the group so nothing is lost.
send_or_queue(): if the push fails, the message goes to the durable outbox that QuantAlertDispatch retries every minute."""
import sys, os, json, time, hmac, hashlib, base64, datetime as dt
from pathlib import Path
import requests
CFG = Path(os.environ.get('QD_NOTIFY', 'C:/ChoiceBridge/live/notify.json'))
OUTBOX = Path(os.environ.get('QD_OUTBOX', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/corporate_actions/outbox'))
REPORT_PREFIXES = ('【北国银行 日报', '【日报更正】')
def channel_of(text): return 'report' if text.lstrip().startswith(REPORT_PREFIXES) else 'alert'
def _feishu(url, secret, text):
    body = {'msg_type': 'text', 'content': {'text': text}}
    if secret:
        ts = str(int(time.time())); body.update(timestamp=ts, sign=base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), b'', digestmod=hashlib.sha256).digest()).decode())
    r = requests.post(url, json=body, timeout=15); return r.status_code == 200 and r.json().get('code') == 0
def _push(n, text, url, secret, attempts):
    for attempt in range(attempts):
        try:
            ok_any = _feishu(url, secret, text) if url else False
            if n.get('wecom_webhook'): r = requests.post(n['wecom_webhook'], json={'msgtype': 'text', 'text': {'content': text}}, timeout=15); ok_any = ok_any or r.status_code == 200
            if n.get('dingtalk_webhook'): r = requests.post(n['dingtalk_webhook'], json={'msgtype': 'text', 'text': {'content': text}}, timeout=15); ok_any = ok_any or r.status_code == 200
            if ok_any: return True
        except Exception as e: print('notify error', e)
        if attempt + 1 < attempts: time.sleep(10)
    return False
def send(text, attempts=3, channel=None):
    if not CFG.exists(): print('no notify.json'); return False
    n = json.loads(CFG.read_text(encoding='utf-8')); channel = channel or channel_of(text)
    if channel == 'alert' and n.get('alert_feishu_webhook'):
        if _push({}, text, n['alert_feishu_webhook'], n.get('alert_feishu_secret'), attempts): return True
        print('alert channel failed, falling back to group'); text = '（私人告警通道发送失败，转发到群）\n' + text
    return _push(n, text, n.get('feishu_webhook'), n.get('feishu_secret'), attempts)
def enqueue(kind, text, key):
    """Same schema and file naming as corporate_action_runtime.queue_notice; atomic, never overwrites an existing entry."""
    OUTBOX.mkdir(parents=True, exist_ok=True); path = OUTBOX / (hashlib.sha256(key.encode()).hexdigest() + '.json')
    if path.exists(): return path
    entry = dict(schema=1, at=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(), kind=kind, text=text, delivered=False, attempts=0)
    tmp = OUTBOX / (path.name + '.tmp'); tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8'); os.replace(tmp, path)
    return path
def send_or_queue(text, kind, key):
    if send(text): return 'sent'
    try: enqueue(kind, text, key); return 'queued'
    except Exception as e: print('outbox error', e); return 'failed'
if __name__ == '__main__': print('sent' if send(sys.argv[1]) else 'FAILED')

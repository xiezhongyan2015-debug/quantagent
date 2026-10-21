"""Read-only probe of the Tushare reseller for stk_mins (run ON the cloud machine).

- Reads credentials from C:/ChoiceBridge/live/secrets/tushare.json (keys url, token); never prints them.
- Hard cap MAX_CALLS HTTP calls. Outputs a sanitized JSON summary on stdout (token redacted everywhere).
Usage (cloud): gru-venv\\Scripts\\python.exe -X utf8 probe_stk_mins_cloud.py > probe_result.json
"""
import json, sys, time
from urllib.parse import urlparse
import requests

SECRET = 'C:/ChoiceBridge/live/secrets/tushare.json'
MAX_CALLS = 15
cfg = json.load(open(SECRET, encoding='utf-8'))
URL, TOK = cfg['url'], cfg['token']
pu = urlparse(URL)
ROOT = f'{pu.scheme}://{pu.netloc}'
calls = 0
out = {'url_host': pu.netloc, 'url_path': pu.path if TOK not in pu.path else '<redacted>', 'results': []}


def red(s):
    s = str(s)
    return s.replace(TOK, '<token>') if TOK else s


def budget():
    global calls
    if calls >= MAX_CALLS:
        raise SystemExit('call budget exhausted')
    calls += 1


def get(path):
    budget(); t0 = time.time()
    try:
        r = requests.get(ROOT + path, headers={'x-api-key': TOK}, timeout=60)
        body = r.text
        try:
            body = json.loads(body)
        except Exception:
            pass
        return {'kind': 'GET', 'path': path, 'status': r.status_code, 'secs': round(time.time() - t0, 2),
                'body': red(json.dumps(body, ensure_ascii=False))[:1500]}
    except Exception as e:
        return {'kind': 'GET', 'path': path, 'error': red(f'{type(e).__name__}: {e}')[:300], 'secs': round(time.time() - t0, 2)}


def stk_mins(label, ts_code, day, freq='1min'):
    budget(); t0 = time.time()
    params = dict(ts_code=ts_code, freq=freq, start_date=f'{day} 09:00:00', end_date=f'{day} 15:00:00')
    rec = {'kind': 'stk_mins', 'label': label, 'params': params}
    try:
        r = requests.post(URL, json=dict(api_name='stk_mins', token=TOK, params=params, fields=''), timeout=120)
        rec['http_status'] = r.status_code
        rec['secs'] = round(time.time() - t0, 2)
        try:
            j = r.json()
        except Exception:
            rec['error'] = red(r.text)[:300]; return rec
        rec['code'] = j.get('code'); rec['msg'] = red(j.get('msg'))[:300]
        d = j.get('data') or {}
        fields, items = d.get('fields') or [], d.get('items') or []
        rec['fields'] = fields; rec['rows'] = len(items)
        if items:
            rows = [dict(zip(fields, it)) for it in items]
            tcol = 'trade_time' if 'trade_time' in fields else fields[1]
            rows.sort(key=lambda x: str(x.get(tcol)))
            times = [str(x.get(tcol)) for x in rows]
            rec['first_times'] = times[:3]; rec['last_times'] = times[-5:]
            rec['has_0930'] = any(t.endswith('09:30:00') for t in times)
            rec['has_1300'] = any(t.endswith('13:00:00') for t in times)
            rec['has_1130'] = any(t.endswith('11:30:00') for t in times)
            rec['sample_first2'] = rows[:2]; rec['sample_last2'] = rows[-2:]
            if 'vol' in fields: rec['sum_vol'] = float(sum(float(x['vol'] or 0) for x in rows))
            if 'amount' in fields: rec['sum_amount'] = float(sum(float(x['amount'] or 0) for x in rows))
    except Exception as e:
        rec['error'] = red(f'{type(e).__name__}: {e}')[:300]; rec['secs'] = round(time.time() - t0, 2)
    return rec


R = out['results']
R.append(get('/tushare/capabilities/stk_mins'))
R.append(get('/tushare/upstreams/probe/stk_mins'))
a = stk_mins('live_2020', '000001.SZ', '2020-06-01'); R.append(a)
R.append(stk_mins('delisted_2020', '600634.SH', '2020-06-01'))
if a.get('rows'):
    for y, day in (('2009', '2009-06-01'), ('2015', '2015-06-01'), ('2018', '2018-06-01')):
        R.append(stk_mins(f'live_{y}', '000001.SZ', day))
else:
    R.append(stk_mins('live_2020_5min', '000001.SZ', '2020-06-01', freq='5min'))
    R.append(stk_mins('live_2020_60min', '000001.SZ', '2020-06-01', freq='60min'))
out['calls'] = calls
print(red(json.dumps(out, ensure_ascii=False, indent=1, default=str)))

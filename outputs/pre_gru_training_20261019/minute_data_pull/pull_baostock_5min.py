"""Resumable, single-connection baostock 5-minute pull for the all-A universe (data/universe.csv).

Rules (baostock: <=50k requests/day, no concurrency, over-limit => blacklist):
  * one process, one connection; an exclusive lock file refuses a second instance;
  * every socket request (login, each 2000-row page, adjust-factor query) goes through a wrapper that
    enforces >= MIN_GAP seconds between requests and counts it toward a per-calendar-day cap (MAX_PER_DAY,
    default 40000, persisted in state/request_counts.json so restarts on the same day keep the count);
    when the cap is reached the script logs out and sleeps until 00:10 the next day, then continues;
  * per stock: unadjusted 5-minute bars (adjustflag='3') -> data/5min/<code>.parquet,
    adjust factors (query_adjust_factor, 2000-01-01..END) -> data/adj_factor/<code>.parquet;
    a stock is skipped when both files exist (resume); files are written atomically (tmp + rename);
  * every query is appended to logs/calls.csv (ts, code, kind, start, end, rows, requests, secs, status, error);
  * errors -> exponential backoff (5s * 2^k, max 600s) with re-login; > MAX_CONSEC_ERR consecutive errors,
    or any blacklist-looking message, => stop and write logs/STOPPED_REASON.txt.
Usage: python pull_baostock_5min.py [--max-per-day 40000] [--min-gap 0.25] [--limit N] [--codes sh.600000,...]
Stop: kill <PID> (SIGTERM: finishes nothing half-written; the current stock is simply redone next run).
"""
import argparse, csv, datetime as dt, fcntl, json, math, os, signal, socket, sys, time, zlib
import pandas as pd
import baostock as bs
import baostock.common.contants as cons
import baostock.common.context as context
import baostock.util.socketutil as sockutil

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, LOGS, STATE = (os.path.join(HERE, d) for d in ('data', 'logs', 'state'))
OUT5, OUTADJ = os.path.join(DATA, '5min'), os.path.join(DATA, 'adj_factor')
for d in (OUT5, OUTADJ, LOGS, STATE):
    os.makedirs(d, exist_ok=True)
FIELDS = 'date,time,code,open,high,low,close,volume,amount,adjustflag'
ap = argparse.ArgumentParser()
ap.add_argument('--max-per-day', type=int, default=40000)
ap.add_argument('--min-gap', type=float, default=0.25)
ap.add_argument('--max-consec-err', type=int, default=20)
ap.add_argument('--limit', type=int, default=0)
ap.add_argument('--codes', default='')
ap.add_argument('--socket-timeout', type=float, default=120)
args = ap.parse_args()


def now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log(msg):
    with open(os.path.join(LOGS, 'pull.log'), 'a', encoding='utf-8') as f:
        f.write(f'{now()} {msg}\n')


def stop(reason, code=2):
    with open(os.path.join(LOGS, 'STOPPED_REASON.txt'), 'w', encoding='utf-8') as f:
        f.write(f'{now()} {reason}\n')
    log(f'STOP: {reason}')
    try:
        bs.logout()
    except Exception:
        pass
    sys.exit(code)


# ---------- single instance ----------
lockf = open(os.path.join(STATE, 'pull.lock'), 'w')
try:
    fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print('another pull instance is running; refusing to start (baostock forbids concurrency)')
    sys.exit(3)
with open(os.path.join(LOGS, 'pull.pid'), 'w') as f:
    f.write(str(os.getpid()))
if os.path.exists(os.path.join(LOGS, 'STOPPED_REASON.txt')):
    os.replace(os.path.join(LOGS, 'STOPPED_REASON.txt'), os.path.join(LOGS, f'STOPPED_REASON_prev_{int(time.time())}.txt'))

# ---------- request accounting + rate limit (wraps every socket message) ----------
CNT_FILE = os.path.join(STATE, 'request_counts.json')
counts = json.load(open(CNT_FILE)) if os.path.exists(CNT_FILE) else {}
last_req = [0.0]
page_fail = [False]


def save_counts():
    tmp = CNT_FILE + '.tmp'
    json.dump(counts, open(tmp, 'w'), indent=0)
    os.replace(tmp, CNT_FILE)


def today():
    return dt.date.today().isoformat()


def wait_for_quota():
    if counts.get(today(), 0) < args.max_per_day:
        return
    log(f'daily cap {args.max_per_day} reached ({counts[today()]}); logging out and sleeping until tomorrow 00:10')
    try:
        bs.logout()
    except Exception:
        pass
    tgt = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(0, 10))
    while dt.datetime.now() < tgt:
        time.sleep(60)
    ensure_login(force=True)


def send_msg(msg):
    """Replica of baostock.util.socketutil.send_msg with: rate limit, request counting, recv-EOF guard."""
    gap = last_req[0] + args.min_gap - time.time()
    if gap > 0:
        time.sleep(gap)
    last_req[0] = time.time()
    d = today(); counts[d] = counts.get(d, 0) + 1
    if counts[d] % 50 == 0:
        save_counts()
    try:
        s = getattr(context, 'default_socket', None)
        if s is None:
            page_fail[0] = True; return None
        s.send(bytes(msg + '\n', encoding='utf-8'))
        receive = b''
        while True:
            r = s.recv(65536)
            if not r:
                raise ConnectionError('server closed connection')
            receive += r
            if receive[-13:] == b'<![CDATA[]]>\n':
                break
        head_str = bytes.decode(receive[0:cons.MESSAGE_HEADER_LENGTH])
        head_arr = head_str.split(cons.MESSAGE_SPLIT)
        if head_arr[1] in cons.COMPRESSED_MESSAGE_TYPE_TUPLE:
            n = int(head_arr[2])
            return head_str + bytes.decode(zlib.decompress(receive[cons.MESSAGE_HEADER_LENGTH:cons.MESSAGE_HEADER_LENGTH + n]))
        return bytes.decode(receive)
    except Exception as e:
        log(f'socket error: {type(e).__name__}: {e}')
        page_fail[0] = True
        return None


sockutil.send_msg = send_msg   # resultset/history/loginout all call sockutil.send_msg via the module
socket.setdefaulttimeout(args.socket_timeout)
logged_in = [False]


def ensure_login(force=False):
    if logged_in[0] and not force:
        return
    try:
        bs.logout()
    except Exception:
        pass
    lg = bs.login()
    logged_in[0] = lg.error_code == '0'
    log(f'login: {lg.error_code} {lg.error_msg}')
    if not logged_in[0]:
        raise RuntimeError(f'login failed: {lg.error_code} {lg.error_msg}')


def blacklisted(msg):
    m = str(msg)
    return any(k in m for k in ('黑名单', 'blacklist', '限制', '禁止'))


# ---------- logging of calls ----------
CALLS = os.path.join(LOGS, 'calls.csv')
if not os.path.exists(CALLS):
    with open(CALLS, 'w', newline='') as f:
        csv.writer(f).writerow(['ts', 'code', 'kind', 'start', 'end', 'rows', 'requests', 'secs', 'status', 'error'])


def log_call(*row):
    with open(CALLS, 'a', newline='') as f:
        csv.writer(f).writerow([now(), *row])


def fetch(kind, code, start, end):
    """Returns DataFrame; raises on any error (including a failed page fetch => no silent truncation)."""
    wait_for_quota(); ensure_login()
    page_fail[0] = False
    c0, t0 = counts.get(today(), 0), time.time()
    if kind == '5min':
        rs = bs.query_history_k_data_plus(code, FIELDS, start_date=start, end_date=end, frequency='5', adjustflag='3')
    else:
        rs = bs.query_adjust_factor(code=code, start_date=start, end_date=end)
    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())
    secs, nreq = round(time.time() - t0, 2), counts.get(today(), 0) - c0
    err = '' if rs.error_code == '0' else f'{rs.error_code} {rs.error_msg}'
    if page_fail[0] and not err:
        err = 'page fetch failed (socket)'
    log_call(code, kind, start, end, len(rows), nreq, secs, 'ok' if not err else 'error', err)
    if err:
        if blacklisted(err):
            stop(f'blacklist-like message from baostock on {code} {kind}: {err}')
        raise RuntimeError(err)
    return pd.DataFrame(rows, columns=rs.fields if rows else FIELDS.split(',') if kind == '5min' else
                        ['code', 'dividOperateDate', 'foreAdjustFactor', 'backAdjustFactor', 'adjustFactor'])


def to_5min_frame(df):
    out = pd.DataFrame({
        'time': pd.to_datetime(df['time'], format='%Y%m%d%H%M%S%f') if len(df) else pd.Series([], dtype='datetime64[ns]'),
        'code': df['code'].astype(str),
    })
    for c in ('open', 'high', 'low', 'close', 'amount'):
        out[c] = pd.to_numeric(df[c], errors='coerce').astype('float64')
    out['volume'] = pd.to_numeric(df['volume'], errors='coerce').astype('float64')  # shares
    return out


def write_atomic(df, path):
    tmp = path + '.tmp'
    df.to_parquet(tmp, compression='zstd', index=False)
    os.replace(tmp, path)


# ---------- main loop ----------
stopping = [False]
signal.signal(signal.SIGTERM, lambda *a: stopping.__setitem__(0, True))
u = pd.read_csv(os.path.join(DATA, 'universe.csv'), dtype=str)
if args.codes:
    u = u[u.code.isin(args.codes.split(','))]
if args.limit:
    u = u.head(args.limit)
total = len(u)
log(f'start pid={os.getpid()} universe={total} max_per_day={args.max_per_day} min_gap={args.min_gap}')
consec_err, done, t_start, done_now = 0, 0, time.time(), 0
ADJ_START = '2000-01-01'
for i, r in enumerate(u.itertuples(index=False)):
    if stopping[0]:
        break
    p5, pa = os.path.join(OUT5, f'{r.code}.parquet'), os.path.join(OUTADJ, f'{r.code}.parquet')
    if os.path.exists(p5) and os.path.exists(pa):
        done += 1
        continue
    attempt = 0
    while not stopping[0]:
        try:
            if not os.path.exists(p5):
                write_atomic(to_5min_frame(fetch('5min', r.code, r.start, r.end)), p5)
            if not os.path.exists(pa):
                write_atomic(fetch('adj', r.code, ADJ_START, r.end), pa)
            consec_err = 0
            break
        except SystemExit:
            raise
        except Exception as e:
            consec_err += 1; attempt += 1
            log(f'error {r.code} (consecutive {consec_err}, attempt {attempt}): {type(e).__name__}: {e}')
            if consec_err > args.max_consec_err:
                save_counts()
                stop(f'more than {args.max_consec_err} consecutive errors; last on {r.code}: {type(e).__name__}: {e}')
            time.sleep(min(600, 5 * 2 ** min(attempt, 7)))
            try:
                ensure_login(force=True)
            except Exception as e2:
                log(f'relogin failed: {e2}')
    else:
        break
    done += 1; done_now += 1
    if done_now % 10 == 0 or done == total:
        el = time.time() - t_start
        rate = el / max(done_now, 1)
        prog = dict(ts=now(), done=done, total=total, done_this_run=done_now, secs_per_stock_this_run=round(rate, 1),
                    requests_today=counts.get(today(), 0), eta_hours_at_current_rate=round(rate * (total - done) / 3600, 1))
        json.dump(prog, open(os.path.join(LOGS, 'progress.json'), 'w'), indent=1)
        save_counts()
        log(f'progress {done}/{total} req_today={counts.get(today(), 0)} {rate:.1f}s/stock')
save_counts()
try:
    bs.logout()
except Exception:
    pass
log(f'exit: done={done}/{total} stopping={stopping[0]}')
if done == total and not stopping[0]:
    open(os.path.join(LOGS, 'COMPLETED.txt'), 'w').write(f'{now()} all {total} stocks done\n')

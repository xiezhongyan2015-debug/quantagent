"""Choice session check (task QuantSessionCheck, Administrator interactive: 08:30 on trading days, 21:00 every day). 2026-10-17.
Runs the existing read-only account script (four GET endpoints, no orders) into a temp file, deletes it, and keeps only ok/failed.
Why: the executor, the 16:05 snapshot and every account read reuse the Choice client's session on this machine; logging in to the captain
account elsewhere can invalidate it. A failure here gives hours of warning instead of finding out at 09:30.
Alerts on the private channel (deduplicated per 2 h, recovery notice). State: live/session_check.json"""
import sys, os, json, subprocess, tempfile, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); STAGING = Path('C:/ChoiceBridge/staging')
EXE = os.environ.get('QD_EXE', 'C:/ChoiceBridge/runtime/choice-venv/Scripts/python.exe'); ACCOUNT = os.environ.get('QD_ACCOUNT', '000000000000000000')
STATE = LIVE / 'session_check.json'


def main():
    now = dt.datetime.now(); hm = now.strftime('%m-%d %H:%M')
    st = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    out = Path(tempfile.gettempdir()) / f'session_check_{now:%Y%m%d%H%M%S}.json'
    try:
        r = subprocess.run([EXE, '-X', 'utf8', 'captain_account_readonly.py', '--account', ACCOUNT, '--out', str(out)], cwd=STAGING, capture_output=True, timeout=180)
        ok = r.returncode == 0 and out.exists()
        tail = '' if ok else (r.stderr or r.stdout).decode('utf-8', errors='replace').strip().splitlines()[-1:]
        if ok:
            j = json.loads(out.read_text(encoding='utf-8')); detail = f"持仓 {len(j.get('positions', []))} 只"
    except Exception as e:
        ok, tail = False, [f'{type(e).__name__}: {e}']
    finally:
        out.unlink(missing_ok=True)
    rec = dict(at=now.isoformat(timespec='seconds'), ok=ok, error=(tail[0][-200:] if tail else ''))
    if not ok:
        last = st.get('alerted_at')
        if not last or (now - dt.datetime.fromisoformat(last)).total_seconds() > 7200:
            msg = (f"🚨 {hm} 云机 Choice 会话失效（只读账户查询失败）：{rec['error'] or '无输出'}\n"
                   "可能原因：在别处登录了队长账号把云机会话顶掉。处理：远程桌面登录云机 → 打开 Choice 客户端 → 用队长账号登录 → 看到账户后可关闭客户端（不要注销 Windows）。"
                   "登录后告诉我，我再跑一次检查；执行器 09:30 需要这个会话。")
            if send(msg): rec['alerted_at'] = now.isoformat()
        else: rec['alerted_at'] = last
    elif st.get('ok') is False:
        send(f"✅ {hm} 云机 Choice 会话已恢复（只读查询成功，{detail}）")
    STATE.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(rec, ensure_ascii=False))


if __name__ == '__main__':
    main()

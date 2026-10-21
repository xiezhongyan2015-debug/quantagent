"""Local iFinD login; credentials remain only in process memory."""
import json
import os
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent.parent / 'data_expansion' / 'ifind_1min_hfq_control'
ROOT.mkdir(parents=True, exist_ok=True)
JOBS = ROOT / 'jobs'
RESULTS = ROOT / 'results'
JOBS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding='utf-8')
    for attempt in range(100):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.1)


def state(status, **kwargs):
    save(ROOT / 'worker_status.json', dict(status=status, pid=os.getpid(), time=time.strftime('%F %T'), **kwargs))


def main(title='中证500历史1分钟下载 · iFinD 本机登录', concurrency=4):
    import iFinDPy as api
    state('awaiting_login')
    win = tk.Tk()
    win.title(title)
    win.geometry('530x270')
    win.attributes('-topmost', True)
    tk.Label(win, text='请输入同花顺 iFinD 数据接口账号和密码', font=('Microsoft YaHei', 12)).pack(pady=12)
    tk.Label(win, text='密码仅用于本机接口登录，不写入脚本、日志或聊天。').pack()
    form = tk.Frame(win)
    form.pack(pady=15)
    tk.Label(form, text='账号').grid(row=0, column=0)
    username = tk.Entry(form, width=32)
    username.grid(row=0, column=1, padx=8, pady=5)
    tk.Label(form, text='密码').grid(row=1, column=0)
    password = tk.Entry(form, width=32, show='*')
    password.grid(row=1, column=1, padx=8, pady=5)
    logged_in = False

    def login():
        nonlocal logged_in
        if not username.get().strip() or not password.get():
            return
        button.config(state='disabled', text='正在登录…')
        win.update_idletasks()
        code = api.THS_iFinDLogin(username.get().strip(), password.get())
        password.delete(0, tk.END)
        if code in (0, -201):
            logged_in = True
            state('ready', login_code=code)
            win.destroy()
        else:
            state('login_failed', errorcode=code)
            messagebox.showerror('登录失败', f'iFinD 返回错误码：{code}。请检查账号与接口权限。', parent=win)
            button.config(state='normal', text='登录并继续下载准备')

    button = tk.Button(win, text='登录并继续下载准备', command=login)
    button.pack()
    username.focus_set()
    win.bind('<Return>', lambda event: login())
    win.mainloop()
    if not logged_in:
        state('login_cancelled')
        return
    from ifind_parallel_dispatch import Dispatcher
    Dispatcher(api, ROOT, save, state, concurrency=concurrency, spacing=5, cooldown=5).run()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        state('crashed', error=traceback.format_exc())
        raise

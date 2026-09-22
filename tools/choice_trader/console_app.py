"""Local Tk control panel. Closing the window does not terminate the trading worker."""
import argparse
import json
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from console_core import DEFAULTS, HOME as APP_DIR, config_checked
from console_runtime import start_console, status_view, active_session, latest_session, alive
from support import save
from quanta_agents.factor_lab_a.pcf_automation import read_json, now_cn

STRATEGIES={'易方达563030 PCF跟随':'pcf_563030','队友目标持仓文件（JSON）':'target_file','本地策略脚本（Python）':'local_script'}
LABELS={'starting':'正在启动','preparing':'正在准备今日计划','preflight_passed':'核验通过','watching':'自动交易中',
    'session_paused':'午间暂停 · 13:05自动继续','waiting_open':'等待09:35自动开始','trading':'自动交易中',
    'waiting_login':'等待恢复登录','login_required':'需要在Choice登录','reconciling':'核对账户数据中',
    'completed_targets':'全部目标已完成','closed':'今日已收盘','closing':'收盘前清理委托','stopped':'已按要求停止',
    'stopped_review_required':'已停止 · 需要核查','waiting_for_consistent_snapshot':'等待接口数据一致'}


class App:
    def __init__(self,root,auto_start=False):
        self.root=root;self.directory=None;self.busy=False;self.closed=False
        root.title('兴证杯 · 自动交易控制台（比赛模拟账户）');root.geometry('1040x760');root.minsize(920,700)
        root.configure(bg='#eef2f6');root.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style();style.theme_use('clam');style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('TFrame',background='#eef2f6');style.configure('TLabel',background='#eef2f6',foreground='#23314a')
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',22,'bold'))
        style.configure('Status.TLabel',font=('Microsoft YaHei UI',17,'bold'),foreground='#146b59')
        style.configure('Primary.TButton',font=('Microsoft YaHei UI',12,'bold'),padding=(18,12))
        style.configure('TButton',padding=(10,7))
        outer=ttk.Frame(root,padding=24);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='自动交易控制台',style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer,text='本机绑定的 Choice 比赛模拟账户  ·  策略与执行分离  ·  每次点击只授权当天运行').pack(anchor='w',pady=(3,15))
        self.tabs=ttk.Notebook(outer);self.tabs.pack(fill='both',expand=True)
        run=ttk.Frame(self.tabs,padding=18);settings_page=ttk.Frame(self.tabs);integration=ttk.Frame(self.tabs,padding=18)
        self.tabs.add(run,text='  运行状态  ');self.tabs.add(settings_page,text='  策略和限额  ');self.tabs.add(integration,text='  队友策略接入  ')
        canvas=tk.Canvas(settings_page,bg='#eef2f6',highlightthickness=0)
        scrollbar=ttk.Scrollbar(settings_page,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set);scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        settings=ttk.Frame(canvas,padding=18);window=canvas.create_window((0,0),window=settings,anchor='nw')
        settings.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
        root.bind('<MouseWheel>',lambda event:canvas.yview_scroll(int(-event.delta/120),'units') if self.tabs.select()==str(settings_page) else None)
        self.status=tk.StringVar(value='正在读取运行状态…');ttk.Label(run,textvariable=self.status,style='Status.TLabel').pack(anchor='w',pady=(0,12))
        self.summary=tk.StringVar(value='');ttk.Label(run,textvariable=self.summary,font=('Microsoft YaHei UI',12),justify='left').pack(anchor='w',pady=8)
        self.detail=tk.StringVar(value='');ttk.Label(run,textvariable=self.detail,wraplength=900,justify='left').pack(anchor='w',pady=(5,10))
        row=ttk.Frame(run);row.pack(fill='x',pady=12)
        self.start_button=ttk.Button(row,text='▶ 启动 / 查看已有运行',style='Primary.TButton',command=self.start);self.start_button.pack(side='left',padx=(0,10))
        ttk.Button(row,text='■ 停止交易',command=self.stop).pack(side='left',padx=4)
        ttk.Button(row,text='打开日志目录',command=self.open_logs).pack(side='left',padx=4)
        row2=ttk.Frame(run);row2.pack(fill='x',pady=5)
        ttk.Button(row2,text='打开 Choice',command=self.open_choice).pack(side='left',padx=(0,10))
        ttk.Button(row2,text='已登录，重试',command=self.login_ready).pack(side='left')
        ttk.Label(run,text='关闭本窗口不会停止后台交易。需要结束时，请先点击“停止交易”。\n午间暂停及14:50停止新增委托由程序自动处理。验证码需要你本人完成。',wraplength=850).pack(anchor='w',pady=16)
        self.events=tk.Text(run,height=7,bg='#ffffff',fg='#334155',relief='flat',font=('Microsoft YaHei UI',9),wrap='word')
        self.events.pack(fill='both',expand=True);self.events.configure(state='disabled')
        self.vars={};cfg=read_json(APP_DIR/'settings.json') if (APP_DIR/'settings.json').exists() else DEFAULTS.copy()
        strategy_name=next((name for name,key in STRATEGIES.items() if key==cfg['strategy']),list(STRATEGIES)[0])
        self.vars['strategy']=tk.StringVar(value=strategy_name)
        ttk.Label(settings,text='选择策略').grid(row=0,column=0,sticky='w',pady=8)
        ttk.Combobox(settings,textvariable=self.vars['strategy'],values=list(STRATEGIES),state='readonly',width=40).grid(row=0,column=1,columnspan=2,sticky='ew')
        fields=[('target_fraction','目标股票仓位（%）',100),('capital_limit','总投入上限（元）',1),('max_security_value','单股上限（元）',1),
            ('max_order_value','单笔上限（元）',1),('price_guard','相对昨收价格保护（%）',100),('spacing_seconds','委托最小间隔（秒）',1)]
        self.fields=fields
        for row,(key,label,scale) in enumerate(fields,1):
            self.vars[key]=tk.StringVar(value=f'{cfg[key]*scale:g}')
            ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',pady=6)
            ttk.Entry(settings,textvariable=self.vars[key],width=28).grid(row=row,column=1,sticky='ew')
        self.vars['allow_sells']=tk.BooleanVar(value=cfg['allow_sells'])
        options=ttk.Frame(settings);options.grid(row=7,column=0,columnspan=3,sticky='w',pady=8)
        ttk.Checkbutton(options,text='允许卖出调仓（新功能，尚未做真实模拟账户卖出成交联调）',variable=self.vars['allow_sells']).pack(anchor='w')
        for row,key,label in [(8,'external_file','队友目标文件'),(9,'external_script','本地策略脚本'),(10,'calendar_path','交易日历JSON'),(12,'membership_path','PCF股票池快照')]:
            self.vars[key]=tk.StringVar(value=cfg.get(key,''));ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',pady=4)
            ttk.Entry(settings,textvariable=self.vars[key]).grid(row=row,column=1,sticky='ew')
            ttk.Button(settings,text='浏览',command=lambda k=key:self.browse(k)).grid(row=row,column=2,padx=6)
        self.vars['calendar_valid_through']=tk.StringVar(value=cfg['calendar_valid_through'])
        ttk.Label(settings,text='日历有效至（YYYY-MM-DD）').grid(row=11,column=0,sticky='w',pady=4)
        ttk.Entry(settings,textvariable=self.vars['calendar_valid_through']).grid(row=11,column=1,sticky='ew')
        settings.columnconfigure(1,weight=1)
        ttk.Button(settings,text='保存下次启动配置',command=self.save_settings).grid(row=13,column=0,columnspan=2,sticky='w',pady=12)
        ttk.Label(settings,text='运行中的计划和参数不会被更改。需要应用新配置时，先停止，待核验退出后再启动。\n总投入检查同时计入持仓、在途单和费用预留。',wraplength=880).grid(row=14,column=0,columnspan=3,sticky='w')
        help_text='''策略接入只负责输出目标，账户登录、下单、风控和核账由控制台处理。

方式一：队友每天生成JSON文件。在设置里选择“队友目标持仓文件”，指定路径。
路径可使用 {date}（2026-09-23）或 {yyyymmdd}（20260923）。

方式二：选择“本地策略脚本”。程序自动调用你指定的Python脚本：
  --execution-date 当天 --signal-date 前一交易日
  --account-snapshot 输入JSON路径 --output 输出JSON路径
输入提供现金、持仓、净资产及限额；不向策略脚本传递Choice登录令牌。

输出示例（日期必须对应本次执行，权重表示占整个账户总资产的比例）：
{
  "execution_date": "2026-09-23", "signal_date": "2026-09-22",
  "mode": "weights",
  "targets": [{"code": "SH600000", "weight": 0.03}]
}

也可用 "mode": "shares"，每行填写 "shares": 1000，表示目标总股数而非新增股数。
未列出的已有持仓目标为0；需要卖出时，必须由你开启卖出调仓。
总权重不得超过设置的目标仓位。买入向下取整，不为凑仓位超限。
策略首次接入请先运行示例脚本并检查输出文件，确认目标文件后再启动。

默认PCF策略读取前一交易日563030的公开PCF；首次生成当天计划时自动下载并核对日期。
当天首次生成的计划会保存；相同配置续跑继承原计划及所有委托，不重复下单。
电脑需保持开机联网。首次登录、验证码、外部交易或账本异常仍需你处理。'''
        text=tk.Text(integration,wrap='word',font=('Microsoft YaHei UI',10),bg='white',relief='flat');text.pack(fill='both',expand=True)
        text.insert('1.0',help_text);text.configure(state='disabled')
        self.log('控制台已打开。当前工作模式：比赛模拟交易。')
        root.after(100,self.refresh)
        if auto_start:root.after(600,self.start)
    def log(self,message):
        self.events.configure(state='normal');self.events.insert('end',now_cn().strftime('%H:%M:%S')+'  '+message+'\n');self.events.see('end');self.events.configure(state='disabled')
    def config(self):
        c=DEFAULTS.copy();c['strategy']=STRATEGIES[self.vars['strategy'].get()]
        for key,label,scale in self.fields:c[key]=float(self.vars[key].get())/scale
        for key in ('external_file','external_script','calendar_path','calendar_valid_through','membership_path'):c[key]=self.vars[key].get().strip()
        c['allow_sells']=self.vars['allow_sells'].get();return config_checked(c)
    def save_settings(self):
        try:save(APP_DIR/'settings.json',self.config());self.log('已保存配置，下次启动时使用。')
        except Exception as e:messagebox.showerror('设置未保存',str(e))
    def browse(self,key):
        path=filedialog.askopenfilename(title='选择本地文件')
        if path:self.vars[key].set(path)
    def start(self):
        if self.busy:return
        try:c=self.config()
        except Exception as e:messagebox.showerror('配置错误',str(e));return
        save(APP_DIR/'settings.json',c);self.busy=True;self.start_button.configure(state='disabled');self.log('检查已有进程和账户账本…')
        def work():
            try:result=start_console(c);error=None
            except Exception as e:result=None;error=str(e)
            if not self.closed:self.root.after(0,lambda:self.started(result,error))
        threading.Thread(target=work,daemon=True).start()
    def started(self,result,error):
        self.busy=False;self.start_button.configure(state='normal')
        if error:self.log(error);messagebox.showerror('未启动',error)
        else:
            self.directory=Path(result['directory']);self.tabs.select(0)
            self.log('已有交易进程，已显示状态，没有重复启动。' if result['action']=='attached' else '后台程序已启动，正在自动核验账户。')
    def stop(self):
        try:
            directory=active_session()
            if not directory:self.log('当前没有正在运行的交易进程。');return
            (directory/'STOP').write_text(now_cn().isoformat(),encoding='utf-8');self.log('已请求停止；程序将清理自己的已知挂单并核验。')
        except Exception as e:messagebox.showerror('停止失败',str(e))
    def login_ready(self):
        try:
            directory=active_session()
            if not directory:messagebox.showinfo('当前未运行','先在Choice正常登录，然后点击启动。');return
            (directory/'LOGIN_READY').write_text(now_cn().isoformat(),encoding='utf-8');self.log('已通知后台重新读取登录会话并核验账户。')
        except Exception as e:messagebox.showerror('登录通知失败',str(e))
    def open_choice(self):
        path=Path('C:/Eastmoney/Choice/Choice.exe')
        if path.exists():os.startfile(str(path))
        else:messagebox.showinfo('打开Choice','请从电脑的Choice快捷方式正常登录。')
    def open_logs(self):
        try:os.startfile(str(self.directory or latest_session()))
        except Exception as e:messagebox.showerror('日志目录',str(e))
    def refresh(self):
        if self.closed:return
        try:
            active=active_session();self.directory=active or self.directory or latest_session();s=status_view(self.directory)
            status=LABELS.get(s.get('status'),s.get('status','尚未启动'))
            if not s['running'] and s.get('status') not in ('completed_targets','closed','stopped','stopped_review_required'):status='进程已退出，请点击启动进行核验和恢复'
            self.status.set(status)
            ratio='—' if s.get('stock_fraction') is None else f"{s['stock_fraction']:.2%}"
            self.summary.set(f"股票仓位  {ratio}       委托  {s.get('submitted',0)} 笔       成交  {s.get('filled_shares',0):,} 股\n冻结资金  {s.get('frozen','—')} 元       剩余目标  {s.get('remaining_count','—')} 只")
            self.detail.set((s.get('reason') or '账户数据更新时间：'+str(s.get('verified_at','等待首次核验')))+'\n运行记录：'+str(self.directory.name))
        except Exception as e:self.detail.set(str(e))
        self.root.after(2000,self.refresh)
    def close(self):self.closed=True;self.root.destroy()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',action='store_true');ap.add_argument('--smoke-test',action='store_true');args=ap.parse_args()
    root=tk.Tk();app=App(root,auto_start=args.start and not args.smoke_test)
    if args.smoke_test:root.after(1200,app.close)
    root.mainloop()


if __name__=='__main__':main()

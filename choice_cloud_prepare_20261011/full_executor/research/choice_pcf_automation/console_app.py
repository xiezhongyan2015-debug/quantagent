"""Local Tk control panel. Closing the window does not terminate the trading worker."""
import argparse
import copy
import json
import os
import threading
import hashlib
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from console_core import DEFAULTS, HOME as APP_DIR, config_checked
from console_runtime import start_console, status_view, active_session, latest_session, alive
from watch_engine import save
from quanta_agents.factor_lab_a.pcf_automation import read_json, now_cn

STRATEGIES={'10月12日 · 一手验收后正式调仓':'pilot_then_targets','10月12日 · 仅单只一手试单':'pilot_one_lot'}
LABELS={'pilot_accepted_preparing_targets':'试单验收通过 · 正在核验正式目标','formal_rebalancing':'试单已验收 · 正式调仓中','scheduled_waiting':'已预约 · 10月12日09:30自动开始','completed_pilot':'一手试单已核验结束 · 不再下单','prepared_readonly':'账户核验通过 · 尚未启动交易','starting':'正在启动','preparing':'正在准备今日计划','preflight_passed':'核验通过','watching':'自动交易中',
    'session_paused':'午间暂停 · 13:05自动继续','waiting_open':'等待09:30自动开始','trading':'自动交易中',
    'waiting_login':'等待恢复登录','login_required':'需要在Choice登录','reconciling':'核对账户数据中',
    'recovering_submission':'正在自动追查委托回执 · 不重复下单',
    'recovering_readonly':'短暂故障 · 后台自动核验恢复',
    'closed_with_isolated_order':'今日已结束 · 有隔离委托待确认',
    'previous_receipt_reconciled':'前日回执已核验 · 可启动今日计划',
    'completed_targets':'全部目标已完成','closed':'今日已收盘','closing':'收盘前清理委托','stopped':'已按要求停止',
    'maintaining_position_floor':'仓位已达标 · 持续核验至收盘','position_topup_planned':'仓位不足 · 已安排计划内补足',
    'position_floor_unmet':'仓位未达标 · 检查价格与限额阻碍','closed_below_position_floor':'已停止 · 仓位仍未达标',
    'position_floor_deadline_missed':'仓位恢复期限已到 · 尚未达标',
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
        ttk.Label(outer,text='固定 Choice 比赛账户 · 先一手验收、通过后正式调仓 · 试单≤1万元、正式每笔≤5万元').pack(anchor='w',pady=(3,15))
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
        self.start_button=ttk.Button(row,text='▶ 预约 / 升级试单→正式调仓',style='Primary.TButton',command=self.start);self.start_button.pack(side='left',padx=(0,10))
        ttk.Button(row,text='■ 停止交易',command=self.stop).pack(side='left',padx=4)
        ttk.Button(row,text='打开日志目录',command=self.open_logs).pack(side='left',padx=4)
        row2=ttk.Frame(run);row2.pack(fill='x',pady=5)
        ttk.Button(row2,text='打开 Choice',command=self.open_choice).pack(side='left',padx=(0,10))
        ttk.Button(row2,text='已登录，重试',command=self.login_ready).pack(side='left')
        ttk.Button(row2,text='只读核验与试单预览',command=self.preview).pack(side='left',padx=10)
        ttk.Label(run,text='关闭本窗口不会停止后台交易。需要结束时，请先点击“停止交易”。\n午间暂停及14:50停止新增委托由程序自动处理。验证码需要你本人完成。',wraplength=850).pack(anchor='w',pady=16)
        self.events=tk.Text(run,height=7,bg='#ffffff',fg='#334155',relief='flat',font=('Microsoft YaHei UI',9),wrap='word')
        self.events.pack(fill='both',expand=True);self.events.configure(state='disabled')
        self.vars={};cfg=dict(DEFAULTS,**read_json(APP_DIR/'settings.json')) if (APP_DIR/'settings.json').exists() else copy.deepcopy(DEFAULTS)
        self.pilot_symbol=cfg.get('pilot_symbol','')
        self.workflow_settings=copy.deepcopy(cfg.get('rebalance_after_pilot'))
        self.floor_policy=copy.deepcopy(cfg['position_floor'])
        self.cash_tolerance=cfg['cash_drift_tolerance_cny']
        self.isolate_unknown_orders=cfg['isolate_unknown_orders']
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
        ttk.Checkbutton(options,text='允许卖出调仓（新功能，尚未做真实模拟账户卖出成交联调）',state='disabled',variable=self.vars['allow_sells']).pack(anchor='w')
        self.vars['floor_enabled']=tk.BooleanVar(value=self.floor_policy['enabled'])
        ttk.Checkbutton(options,text='将目标仓位作为最低要求：预留0.5个百分点、持续核验、仅在策略已有股票内补足',state='disabled',variable=self.vars['floor_enabled']).pack(anchor='w')
        self.vars['prefer_sealed_plan']=tk.BooleanVar(value=cfg.get('prefer_sealed_plan',True))
        ttk.Checkbutton(options,text='PCF优先使用已封存日计划：其专用规则优先于以上普通参数',state='disabled',variable=self.vars['prefer_sealed_plan']).pack(anchor='w')
        for row,key,label in [(8,'external_file','队友目标文件'),(9,'external_script','本地策略脚本'),(10,'calendar_path','交易日历JSON'),(12,'membership_path','PCF股票池快照')]:
            self.vars[key]=tk.StringVar(value=cfg.get(key,''));ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',pady=4)
            ttk.Entry(settings,textvariable=self.vars[key]).grid(row=row,column=1,sticky='ew')
            ttk.Button(settings,text='浏览',command=lambda k=key:self.browse(k)).grid(row=row,column=2,padx=6)
        self.vars['calendar_valid_through']=tk.StringVar(value=cfg['calendar_valid_through'])
        ttk.Label(settings,text='日历有效至（YYYY-MM-DD）').grid(row=11,column=0,sticky='w',pady=4)
        ttk.Entry(settings,textvariable=self.vars['calendar_valid_through']).grid(row=11,column=1,sticky='ew')
        settings.columnconfigure(1,weight=1)
        ttk.Button(settings,text='保存下次启动配置',command=self.save_settings).grid(row=13,column=0,columnspan=2,sticky='w',pady=12)
        ttk.Label(settings,text='试单仍只买100股；完整成交并通过两轮核账后才进入正式调仓。正式阶段按封存目标股数买入或卖出，每笔含费用不超5万元，大单拆分。\n原预约范围为“仅一手”时，点击升级按钮会保留原记录、取消其等待并建立新的完整流程预约；未验收不进入正式阶段。',wraplength=880).grid(row=14,column=0,columnspan=3,sticky='w')
        help_text='''一手验收后正式调仓：初始试单只新增100股，含费用不超过1万元。试单必须完整成交100股、取得确切委托号，并通过两轮完整账户核验；失败、部分成交、撤单或回执不明就停止，不能进入正式阶段。

正式阶段读取已封存且经日期、股数、当前成分及仓位核验的P0或c5目标文件。目标股数表示最终总持仓，未列出的旧持仓目标为0。按实际持仓差额买入或卖出，正式每笔含费用预留不超过5万元。拆单只有前一笔完整成交核验后才能继续；拒单、部分成交或撤单不自动重发剩余股数。
正式阶段保留T+1可卖数量、30秒实时报价、3%价格保护、单股50万元、总股票95%上限。减仓还须保留90%股票和80%中证500资产比例。目标文件冻结后不因覆盖文件而盘中热切换；c5必须在预约前通过数据闸门并封存。
预约和升级必须由本人点击。打开控制台、后台预检、部署新文件都不会创建新交易授权。运行中记录保持原样。

正式目标表示最终持仓股数，已经包括试单买入的100股。科创板目标差额已按至少200股申报单位适配，原始目标和每项调整单独保留。每次重新启动继承同一账本、原委托号及已完成切换，不再试第二笔、不重新调入未封存的目标。

云机需保持运行及Choice登录。关闭本窗口不会终止已由本人预约的进程；停止按钮能取消预约或结束执行。登录失效、外部交易、现金/持仓不一致、成分更新或信号文件变化会停止并保留记录。
'''
        text=tk.Text(integration,wrap='word',font=('Microsoft YaHei UI',10),bg='white',relief='flat');text.pack(fill='both',expand=True)
        text.insert('1.0',help_text);text.configure(state='disabled')
        self.log('控制台已打开。当前工作模式：比赛模拟交易。')
        if cfg['strategy']=='pilot_then_targets':
            from workflow_rebalance import inputs
            released,_=inputs(cfg)
            self.log(f"已封存 {released['strategy_layer']} 正式目标：{len(released['targets'])}只；单笔含费用≤5万元；须先通过100股试单验收。")
        if (APP_DIR/'preflight_preview.json').exists():
            plan=read_json(APP_DIR/'preflight_preview.json')
            self.log(f"已准备 {plan['selected_name']} {plan['selected_symbol']}：现有 {plan['held_before']} 股，新增100股后为 {plan['target_shares']} 股。参考限价 {plan['limit_price']:.2f} 元，含费用预留 {plan['reserved_total_cny']} 元。")
            self.log('已准备“试单验收→正式调仓”。点击预约/升级后，云机10月12日09:30先试100股，完整成交并通过两轮核账后自动接入封存目标；开盘无需再点击。正式阶段包含卖出和多笔拆单，试单≤1万元、正式每笔≤5万元。')
            existing=active_session()
            if existing and (existing/'config.json').exists() and read_json(existing/'config.json').get('strategy')=='pilot_then_targets':
                old=read_json(existing/'config.json')
                if old.get('rebalance_after_pilot')!=cfg.get('rebalance_after_pilot'):self.log('当前等待进程使用旧目标，本次C5尚未预约；本人点击升级后切换到新的封存版本。')
            if existing and (existing/'config.json').exists() and read_json(existing/'config.json').get('strategy')=='pilot_one_lot':
                self.log('当前运行仍是原来“仅一手”的预约；新流程已准备，需本人点击升级按钮才替换预约范围。原授权不会被改写。')
        root.after(100,self.refresh)
        if Path('C:/ChoiceBridge/staging/efficiency_upgrade_pending.json').exists():
            self.start_button.configure(text='应用优化并继续')
            self.log('已准备效率与自动恢复升级：本人点击一次“应用优化并继续”，后台会核验并保留所有委托，安装后从剩余目标继续。后续短暂故障在原进程内恢复。')
        # Opening the application never authorizes orders.
    def preview(self):
        if self.busy:return
        self.busy=True;self.log('正在读取账户与试单预览；不会提交或撤销委托。')
        def work():
            try:
                from readonly_cloud import prepare_cloud
                result=prepare_cloud(preview=True);error=None
            except Exception as e:result=None;error=str(e)
            if not self.closed:self.root.after(0,lambda:self.previewed(result,error))
        threading.Thread(target=work,daemon=True).start()
    def previewed(self,result,error):
        self.busy=False
        if error:self.log(error);return
        self.pilot_symbol=result['selected_symbol']
        self.log(f"{result['name']} {self.pilot_symbol}：现有 {result['held_before']} 股，新增 100 股，目标 {result['target_shares']} 股；参考限价 {result['indicative_limit_price']:.2f} 元，含费用预留 {result['reserved_total_cny']} 元。行情时点 {result['quote_timestamp']}。点击启动后仍会重新核验当天价格。")
    def log(self,message):
        self.events.configure(state='normal');self.events.insert('end',now_cn().strftime('%H:%M:%S')+'  '+message+'\n');self.events.see('end');self.events.configure(state='disabled')
    def config(self):
        c=DEFAULTS.copy();c['pilot_symbol']=self.pilot_symbol;c['strategy']=STRATEGIES[self.vars['strategy'].get()]
        if c['strategy']=='pilot_then_targets':c['rebalance_after_pilot']=copy.deepcopy(self.workflow_settings)
        for key,label,scale in self.fields:c[key]=float(self.vars[key].get())/scale
        for key in ('external_file','external_script','calendar_path','calendar_valid_through','membership_path'):c[key]=self.vars[key].get().strip()
        c['allow_sells']=False;c['prefer_sealed_plan']=False
        c['position_floor']=copy.deepcopy(self.floor_policy);c['position_floor']['enabled']=False
        c['cash_drift_tolerance_cny']=self.cash_tolerance
        c['isolate_unknown_orders']=self.isolate_unknown_orders
        return config_checked(c)
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
            try:result=self.apply_pending_upgrade(c);error=None
            except Exception as e:result=None;error=str(e)
            if not self.closed:self.root.after(0,lambda:self.started(result,error))
        threading.Thread(target=work,daemon=True).start()
    def apply_pending_upgrade(self,c):
        pending=Path('C:/ChoiceBridge/staging/efficiency_upgrade_pending.json')
        if not pending.exists():return start_console(c)
        info=read_json(pending)
        from quanta_agents.factor_lab_a.pcf_automation import digest
        active=active_session()
        if active:
            if digest(config_checked(read_json(active/'config.json')))!=digest(c):
                raise ValueError('优化升级仅允许保留当前完整配置，请勿同时更改策略或限额')
            save(active/'operator_upgrade_request.json',dict(at=now_cn().isoformat(),action='User pressed Apply optimization and continue',package_sha256=info['package_sha256']))
            (active/'STOP').write_text(now_cn().isoformat(),encoding='utf8')
            for _ in range(120):
                if not alive(active):break
                time.sleep(1)
            else:raise ValueError('原进程仍在确认委托；保留记录，尚未应用优化')
        if active_session():raise ValueError('已有其他进程，禁止重复启动')
        source=latest_session();state=read_json(source/'private_state.json')
        if (any(not a.get('oid') or not a.get('terminal') for a in state['attempts'])
            or state.get('last_audit',{}).get('pending') or float(state.get('last_audit',{}).get('frozen',0))):
            raise ValueError('仍有未完整确认的委托或冻结资金；优化未重启交易，原记录保留')
        script=Path('C:/ChoiceBridge/staging/apply_efficiency_upgrade.py')
        if hashlib.sha256(script.read_bytes()).hexdigest()!=info['applier_sha256']:
            raise ValueError('优化安装程序校验不符')
        result=subprocess.run([str(Path(sys.executable).with_name('python.exe')),'-X','utf8',str(script),info['package_sha256']],capture_output=True,text=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('优化安装未通过，旧版本及账本已保留；查看安装诊断记录')
        receipt=json.loads(result.stdout)
        if receipt['offline_tests_passed']!=119 or not receipt['all_run_files_unchanged']:
            raise ValueError('优化验证回执不符，尚未启动交易')
        pending.rename(pending.with_name('efficiency_upgrade_applied.json'))
        # Only this user button reaches Start. The monitoring agent never does.
        return start_console(c)

    def started(self,result,error):
        self.busy=False;self.start_button.configure(state='normal')
        if error:self.log(error)
        else:
            self.directory=Path(result['directory']);self.tabs.select(0)
            self.log('已有交易进程，已显示状态，没有重复启动。' if result['action']=='attached' else '完整流程已预约：今天09:30先试一手，验收通过后自动正式调仓；原一手预约记录已保留。开盘不需再点。' if result['action']=='pipeline_scheduled' else '已预约：云机等待10月12日09:30自动核验和尝试唯一一手；开盘不用再点，保持服务器和Choice登录。' if result['action']=='scheduled' else '将按已封存专用计划的时间和规则启动。' if result['action']=='approved_plan_waiting' else '后台程序已启动，正在自动核验账户。')
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
            state=read_json(directory/'status_redacted.json') if (directory/'status_redacted.json').exists() else {}
            if directory.parent.parent==APP_DIR/'runs':
                (directory/'LOGIN_READY').write_text(now_cn().isoformat(),encoding='utf-8');self.log('已通知后台重新读取登录会话并核验账户。')
            else:
                request=directory/'native_login_request.json'
                if request.exists():
                    import datetime as dt
                    r=read_json(request)
                    if r.get('status')=='pending' and now_cn()<dt.datetime.fromisoformat(r['deadline']):
                        save(directory/'native_login_reply.json',dict(request_id=r['request_id'],status='completed',at=now_cn().isoformat(),reason='User clicked already logged in; Python must verify SSO and account'))
                        self.log('已通知专用脚本；仍须SSO和两轮账户核验通过。');return
                self.log('当前没有有效的界面登录请求；脚本会按原退避规则自行恢复SSO。')
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
            if s.get('status')=='scheduled_waiting' and '09:35' in (s.get('reason') or ''):status='旧预约 · 09:35仅执行原封存范围'
            if not s['running'] and s.get('status') not in ('completed_pilot','prepared_readonly','completed_targets','closed','stopped','stopped_review_required','closed_below_position_floor','position_floor_deadline_missed','closed_with_isolated_order','previous_receipt_reconciled'):status='进程已退出，请点击启动进行核验和恢复'
            if self.status.get()!=status:self.status.set(status)
            ratio='—' if s.get('stock_fraction') is None else f"{s['stock_fraction']:.2%}"
            summary=f"股票仓位  {ratio}       委托  {s.get('submitted',0)} 笔       成交  {s.get('filled_shares',0):,} 股\n冻结资金  {s.get('frozen','—')} 元       剩余目标  {s.get('remaining_count','—')} 只"
            if self.summary.get()!=summary:self.summary.set(summary)
            detail=(s.get('reason') or '账户数据更新时间：'+str(s.get('verified_at','等待首次核验')))+'\n运行记录：'+str(self.directory.name)
            cash=s.get('cash_variance') or {}
            if cash:
                detail+=f"\n小额现金差异 {float(cash['amount_cny']):+.2f} 元已连续核验，保守计算可用资金"
            recovery=s.get('ack_recovery') or {}
            if recovery.get('phase')=='isolated':detail+='\n'+recovery['reason']
            floor=s.get('position_compliance') or {}
            if floor.get('enabled'):
                text=f"最低仓位 {floor['minimum_fraction']:.2%} · 当前 {'已达标' if floor['met'] else '未达标'}"
                if not floor['met']:text+=f" · 缺口 {float(floor['gap_cny']):,.2f} 元 · 最迟 {floor['deadline']} 收盘恢复"
                detail=text+'\n'+detail
            if self.detail.get()!=detail:self.detail.set(detail)
        except Exception as e:self.detail.set(str(e))
        self.root.after(5000,self.refresh)
    def close(self):self.closed=True;self.root.destroy()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',action='store_true');ap.add_argument('--smoke-test',action='store_true');args=ap.parse_args()
    root=tk.Tk();app=App(root,auto_start=False)
    if args.smoke_test:root.after(1200,app.close)
    root.mainloop()


if __name__=='__main__':main()

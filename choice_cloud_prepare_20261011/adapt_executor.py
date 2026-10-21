from pathlib import Path
import ast,json,shutil
root=Path(__file__).resolve().parent/'full_executor'; app=root/'research/choice_pcf_automation'; lib=root/'references/quantagent-pr/src/quanta_agents/factor_lab_a'
s=(lib/'competition.py').read_text(); node=next(n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name=='precheck')
(lib/'competition_checks.py').write_text('"""Pure competition arithmetic, detached from research-only dependencies."""\n'+ast.get_source_segment(s,node)+'\n')
p=lib/'pcf.py';p.write_text(p.read_text().replace('from .competition import precheck','from .competition_checks import precheck'))
# Reuse the reviewed DPAPI reader and strict four-GET session.
s=(Path(__file__).resolve().parent/'captain_account_readonly.py').read_text(); tree=ast.parse(s)
functions=[ast.get_source_segment(s,n) for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in ('session_tokens',)) or (isinstance(n,ast.ClassDef) and n.name=='GetOnlySession')]
(app/'verify_readonly.py').write_text('''"""Authorized current-user Choice session and fixed read-only query guard."""
import base64,ctypes,hashlib,json,sqlite3,sys,time
from ctypes import wintypes
from pathlib import Path
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'references/quantagent-pr/src'))
from quanta_agents.factor_lab_a.choice_pcf import BASE,ChoiceBroker
'''+ '\n\n'.join(functions).replace('self.account = account','self.trust_env = False\n        self.account = account')+'\n\ndef session_token():return session_tokens()["JToken"]\n')
(app/'cloud_account.py').write_text('''"""Confirmed captain identity, without passwords or session tokens."""
from pathlib import Path
import hashlib
from verify_readonly import ROOT
ACCOUNT='000000000000000000'
ACCOUNT_SHA256='94f8a6ec1fe5ecb6a5c74defa4934d14c3fb69a640bdcf1c693fe7c023a023d8'
UID_SHA256='9f0be8d959e8037c89f1e113b1b3604c0b1efc6eaf24f8d827378325b6ae1a70'
DIRECTORIES=[ROOT/'research/choice_pcf_automation/cloud_ledger']
def assert_binding(identity):
    if hashlib.sha256(ACCOUNT.encode()).hexdigest()!=ACCOUNT_SHA256 or identity.get('account_sha256')!=ACCOUNT_SHA256 or identity.get('uid_sha256')!=UID_SHA256:
        raise RuntimeError('Captain identity binding differs')
''')
for name in ('console_runtime.py','refresh_stopped_ledger.py','reconcile_previous_receipt.py'):
 p=app/name;p.write_text(p.read_text().replace('from run_intraday_watch import ACCOUNT','from cloud_account import ACCOUNT').replace('from run_intraday_watch import DIRECTORIES','from cloud_account import DIRECTORIES'))
# Cloud profile's initial ledger is independently observed; old 395.66 reserve is not transferred.
p=app/'console_core.py';s=p.read_text();s=s.replace("strategy='pcf_563030'","strategy='pilot_one_lot'").replace('target_fraction=.5,capital_limit=5500000,max_security_value=500000,max_order_value=500000','target_fraction=.92,capital_limit=12000000,max_security_value=500000,max_order_value=10000').replace('prefer_sealed_plan=True','prefer_sealed_plan=False').replace("excluded_cash_cny='395.66',cash_drift_tolerance_cny=500","excluded_cash_cny='0.00',cash_drift_tolerance_cny=.03").replace('max_attempts_per_code=3,','max_attempts_per_code=1,execution_date=\'2026-10-12\',pilot_symbol=\'\',max_new_orders=1,pilot_shares=100,pilot_total_cap_cny=10000,').replace("membership_path=str(ROOT/'data_expansion/normalized/current_index_membership.parquet')","membership_path=str(BASE_DIR/'daily_public_pcf/csi500_members.json')")
s=s.replace("('pcf_563030','target_file','local_script')","('pilot_one_lot','pcf_563030','target_file','local_script')")
s=s.replace("if dec(c['excluded_cash_cny'])!=Decimal('395.66'):raise TradeError('395.66元历史隔离金额不可通过普通参数修改')","if dec(c['excluded_cash_cny'])!=0:raise TradeError('新云机使用已核验的新账本，隔离金额固定为0')")
s=s.replace("if c['fill_timeout_seconds']!=60 or c['max_attempts_per_code']!=3:raise TradeError('挂单及重试保护不可在普通设置中更改')","if c['fill_timeout_seconds']!=60 or c['max_attempts_per_code']!=1:raise TradeError('试单固定60秒挂单保护、最多一次提交')")
s=s.replace('    return c\n\n\ndef days_for',"    from pilot_one_lot import check_pilot_config\n    check_pilot_config(c)\n    return c\n\n\ndef days_for",1)
s=s.replace("def reconcile(raw,state,excluded='395.66'","def reconcile(raw,state,excluded='0.00'")
p.write_text(s)
# Identity is a hash-only binding, proven from current session and user-confirmed captain.
a=app/'auth_local';a.mkdir(exist_ok=True);shutil.copy(Path(__file__).resolve().parent/'uid_binding_receipt.json',a/'identity.json')
# Guard native/SSO identity and disable legacy password vault use for this new cloud installation.
p=app/'choice_auth.py';s=p.read_text().replace("self.cookie_reader=cookie_reader;self.http_factory=http_factory;self.current=None","from cloud_account import assert_binding\n        assert_binding(self.identity)\n        self.cookie_reader=cookie_reader;self.http_factory=http_factory;self.current=None")
s=s.replace("        credentials=credential_reader()","        raise HumanLoginRequired('本机请使用Choice正常登录；不读取或保存密码')\n        credentials=credential_reader()")
p.write_text(s)
# Calendar proven from uploaded actual source; copied unchanged.
source=Path('outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/auto_trading_full_source_20261011')
d=app/'daily_public_pcf';d.mkdir(exist_ok=True);shutil.copy(source/'research/choice_pcf_automation/daily_public_pcf/calendar.json',d/'calendar.json')
shutil.copy(Path('outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011/pcf/official_xml_recent/563030_20261009_official.xml'),d/'563030_20261009_official.xml')
print('portable adapters prepared')

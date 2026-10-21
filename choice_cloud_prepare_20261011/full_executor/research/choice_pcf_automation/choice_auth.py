"""Official Choice lab SSO; fixed HTTPS endpoint, pinned identity, no trading."""
import hashlib
import json
import os
from pathlib import Path
import requests
from verify_readonly import ROOT,session_tokens
from quanta_agents.factor_lab_a.choice_pcf import TradeError

AUTH_ROOT=ROOT/'research/choice_pcf_automation/auth_local'
AUTH_BASE='https://choicelabapi.eastmoney.com/emlab-api'
ENV_KEYS={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}


class LoginUnavailable(TradeError):pass
class HumanLoginRequired(TradeError):pass
class IdentityMismatch(TradeError):pass


def uid_digest(uid):return hashlib.sha256(str(uid).encode()).hexdigest()


class ChoiceAuthenticator:
    def __init__(self,identity=None,cookie_reader=session_tokens,http_factory=requests.Session):
        self.identity=identity or json.loads((AUTH_ROOT/'identity.json').read_text(encoding='utf-8'))
        from cloud_account import assert_binding
        assert_binding(self.identity)
        self.cookie_reader=cookie_reader;self.http_factory=http_factory;self.current=None

    def check_uid(self,uid):
        if uid_digest(uid)!=self.identity['uid_sha256']:raise IdentityMismatch('Choice login identity changed; do not access another account')

    def current_tokens(self):
        try:tokens=self.cookie_reader(tuple(ENV_KEYS))
        except Exception:raise HumanLoginRequired('Native Choice session unavailable; normal login required') from None
        self.check_uid(tokens['UID'])
        if any(not isinstance(tokens.get(k),str) or not tokens[k] for k in ENV_KEYS):raise LoginUnavailable('Incomplete native login session')
        return tokens

    def _post(self,path,payload):
        allowed={'/Crm/GetUserAfterLogin':{'ct','ut','userAgent'},'/Login/PasswordLogin':{'UserName','PassWord','CaptContext','CaptValidate'}}
        if path not in allowed or set(payload)!=allowed[path]:raise TradeError('Authentication endpoint or schema not allowed')
        with self.http_factory() as session:
            try:
                r=session.post(AUTH_BASE+path,json=payload,timeout=20,allow_redirects=False)
                if r.status_code!=200:raise LoginUnavailable('Official login HTTP failure; no redirect followed')
                obj=r.json()
            except (requests.RequestException,ValueError):raise LoginUnavailable('Official login transport/JSON failure') from None
        if not isinstance(obj,dict):raise LoginUnavailable('Official login response shape invalid')
        if str(obj.get('Code'))!='0':
            # Never attempt to bypass a verification requirement, replay an old
            # captcha, or log the server response (it can contain identity data).
            raise HumanLoginRequired('Official login rejected existing session; password/verification in Choice required')
        data=obj.get('Data')
        if not isinstance(data,dict):raise LoginUnavailable('Official login data missing')
        return data

    def sso(self,source):
        self.check_uid(source['UID'])
        data=self._post('/Crm/GetUserAfterLogin',{'ct':source['ctoken'],'ut':source['utoken'],'userAgent':3})
        self.check_uid(data.get('UID',''))
        result={'JToken':data.get('JToken'),'utoken':data.get('UToken'),'ctoken':data.get('CToken'),'UID':str(data.get('UID',''))}
        if any(not isinstance(v,str) or not v for v in result.values()):raise LoginUnavailable('Refreshed login response incomplete')
        self.current=result
        return dict(result)

    def login(self):
        # Prefer the latest terminal session, but retain a successfully exchanged
        # session in memory if the native cookie store becomes temporarily locked.
        try:source=self.current_tokens()
        except HumanLoginRequired:
            if self.current is None:raise
            source=self.current
        return self.sso(source)

    def password_login(self,credential_reader,captcha):
        """Normal official password flow AFTER an actual completed verification.

        The public login page requires CaptContext/CaptValidate. No blank proof,
        challenge solving, or expired-proof replay is performed by this client.
        Proof and password must only be supplied in memory by the normal login UI.
        """
        if not isinstance(captcha,dict) or set(captcha)!={'context','validate'} or not all(isinstance(v,str) and v for v in captcha.values()):
            raise HumanLoginRequired('Complete the official login verification before password submission')
        raise HumanLoginRequired('本机请使用Choice正常登录；不读取或保存密码')
        credentials=credential_reader()
        if credentials['username'].lower()!=self.identity['username'].lower():raise IdentityMismatch('Credential account mismatch')
        password_hash=hashlib.md5(credentials.pop('password').encode('utf-8')).hexdigest()
        data=self._post('/Login/PasswordLogin',{'UserName':credentials['username'].lower(),'PassWord':password_hash,
                         'CaptContext':captcha['context'],'CaptValidate':captcha['validate']})
        # UID is checked on the subsequent SSO response; no orders or cookies are
        # installed before that identity binding succeeds.
        data=self._post('/Crm/GetUserAfterLogin',{'ct':data['CToken'],'ut':data['UToken'],'userAgent':1})
        self.check_uid(data.get('UID',''))
        result={'JToken':data['JToken'],'utoken':data['UToken'],'ctoken':data['CToken'],'UID':str(data['UID'])}
        self.current=result;return dict(result)

    def close(self):
        if self.current:self.current.clear()


def install_tokens(tokens,transport,account):
    for key,env in ENV_KEYS.items():os.environ[env]=tokens[key]
    os.environ['CHOICE_ACCOUNT_ID']=account
    transport.headers['token']=tokens['JToken']


def protected_credentials():
    """Windows current-user DPAPI vault; never return it to logs/tool output."""
    import win32crypt
    blob=(AUTH_ROOT/'credentials.dpapi').read_bytes()
    _,clear=win32crypt.CryptUnprotectData(blob,None,None,None,0)
    return json.loads(clear.decode('utf-8'))

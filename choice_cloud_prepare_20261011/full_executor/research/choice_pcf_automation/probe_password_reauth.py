"""One authorized password-only compatibility probe; never trades or logs secrets.

This diagnostic does not change production authentication guards. It makes at
most one password request without a completed challenge to record whether the
official server requires it. It never solves, fabricates or replays a challenge.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests
from choice_auth import ChoiceAuthenticator, AUTH_BASE, protected_credentials
from run_session_20260922 import initial_state, DAY, ACCOUNT, Adapter
from verify_readonly import GetOnlySession
from watch_engine import reconcile, save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, TradeError
from quanta_agents.factor_lab_a.pcf_automation import now_cn

DEST=Path(__file__).resolve().parent/'password_reauth_probe_20260921'
PUBLIC='https://choicelab.eastmoney.com'


def public_evidence(report):
    with requests.Session() as http:
        page=http.get(PUBLIC+'/login',timeout=20,allow_redirects=False)
        page.raise_for_status()
        names=re.findall(r'(?:src=["\'])(/js/main-[A-Za-z0-9]+\.js)',page.text)
        if len(names)!=1:raise TradeError('Unexpected public login script layout')
        main=http.get(PUBLIC+names[0],timeout=20,allow_redirects=False);main.raise_for_status()
        chunk=re.search(r'420:["\']([a-f0-9]+)["\']',main.text)
        if not chunk:raise TradeError('Public login chunk not identified')
        url=PUBLIC+'/js/420-'+chunk.group(1)+'.js'
        source=http.get(url,timeout=20,allow_redirects=False);source.raise_for_status()
        (DEST/'public_login_source.js').write_text(source.text,encoding='utf-8')
        report['public_login']={'url':PUBLIC+'/login','script_url':url,
            'sha256':hashlib.sha256(source.content).hexdigest(),
            'password_endpoint_present':'/Login/PasswordLogin' in source.text,
            'verification_fields_present':all(s in source.text for s in ('CaptContext','CaptValidate','getValidate().validate','EMCaptcha'))}
        if not all((report['public_login']['password_endpoint_present'],report['public_login']['verification_fields_present'])):
            raise TradeError('Login protocol changed; no password attempt')


def audit_tokens(tokens):
    state=initial_state()
    old={k:os.environ.get(k) for k in ('CHOICE_JTOKEN','CHOICE_ACCOUNT_ID')}
    transport=GetOnlySession(ACCOUNT)
    try:
        os.environ['CHOICE_JTOKEN']=tokens['JToken'];os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker({'initial_buy_only':True},False,transport)
        for i in range(2):
            if i:time.sleep(5)
            raw=Adapter(broker,transport).read()
            checked=reconcile(raw,state['prior'],state['attempts'],state['targets'],DAY,state['baseline'],state['historical'],5500000)
        return dict(passes=2,held_securities=len(checked['positions']),cash=str(checked['cash']),
                    frozen=str(checked['frozen']),pending_count=len(checked['pending']),
                    new_orders=0,cancellations=0,identity_uid_matched=True)
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        transport.headers.clear();transport.close()


def main(credential_reader=protected_credentials):
    if now_cn().date().isoformat()!='2026-09-21':raise TradeError('Diagnostic date expired')
    DEST.mkdir(exist_ok=True)
    if (DEST/'password_submission.json').exists():raise TradeError('Password request already attempted; no replay')
    previous=DEST/'result_redacted.json'
    if previous.exists():
        prior=json.loads(previous.read_text(encoding='utf-8'))
        if prior.get('password_requests')!=0:raise TradeError('Earlier password outcome cannot be repeated')
        with (DEST/('pre_submit_failure_'+now_cn().strftime('%H%M%S')+'.json')).open('x',encoding='utf-8') as f:
            json.dump(prior,f,ensure_ascii=False,indent=2)
    report=dict(started_at=now_cn().isoformat(),status='started',password_requests=0,new_orders=0,cancellations=0)
    auth=ChoiceAuthenticator();credentials=None;tokens=None
    try:
        credentials=credential_reader()
        if credentials['username'].lower()!=auth.identity['username'].lower():raise TradeError('Credential account mismatch')
        public_evidence(report)
        # Identity binding is checked before using the explicitly supplied account.
        current=auth.current_tokens();current.clear()
        with requests.Session() as http:
            # Fresh HTTP session: no saved cookies or existing SSO token.
            response=http.get(AUTH_BASE+'/Login/GetCaptchaContext',timeout=20,allow_redirects=False)
            if response.status_code!=200:raise TradeError('Challenge context HTTP failure')
            obj=response.json();context=obj.get('Data')
            report['challenge_context']={'http_status':response.status_code,'business_code':str(obj.get('Code')),
                'nonempty_context_returned':isinstance(context,str) and bool(context)}
            if str(obj.get('Code'))!='0' or not isinstance(context,str) or not context:
                raise TradeError('Official challenge context unavailable')
            with (DEST/'password_submission.json').open('x',encoding='utf-8') as f:
                json.dump({'at':now_cn().isoformat(),'max_attempts':1},f);f.flush();os.fsync(f.fileno())
            payload={'UserName':credentials['username'].lower(),
                'PassWord':hashlib.md5(credentials.pop('password').encode()).hexdigest(),
                'CaptContext':context,'CaptValidate':''}
            report['password_requests']=1
            try:
                response=http.post(AUTH_BASE+'/Login/PasswordLogin',json=payload,timeout=20,allow_redirects=False)
            finally:payload.clear();credentials.clear()
            if response.status_code!=200:raise TradeError('Password probe HTTP failure; no retry')
            obj=response.json()
            # Output only an allowlisted diagnostic message, never arbitrary body.
            msg=str(obj.get('Message',''))
            safe_message=msg if msg in {'验证码错误','验证码无效','验证码验证失败','请先完成验证','人机识别验证失败','验证码验证不通过','成功','操作成功'} else None
            report['password_response']={'http_status':response.status_code,'business_code':str(obj.get('Code')),
                'message':safe_message,'message_mentions_verification':any(x in msg for x in ('验证码','人机','验证失败','滑块','captcha')),
                'message_mentions_password':any(x in msg for x in ('密码','password')),
                'message_length':len(msg),'token_pair_returned':isinstance(obj.get('Data'),dict) and all(obj['Data'].get(k) for k in ('CToken','UToken'))}
            if str(obj.get('Code'))=='0' and report['password_response']['token_pair_returned']:
                data=auth._post('/Crm/GetUserAfterLogin',{'ct':obj['Data']['CToken'],'ut':obj['Data']['UToken'],'userAgent':1})
                auth.check_uid(data.get('UID',''))
                tokens={'JToken':data['JToken']}
                report['password_account_audit']=audit_tokens(tokens)
                report['status']='password_only_login_and_readonly_audit_succeeded'
            else:
                report['status']='password_only_login_rejected_no_retry'
                # A separate SSO check is explicitly labelled, not counted as
                # successful password reauthentication.
                tokens=auth.login()
                report['existing_session_sso_audit']=audit_tokens(tokens)
    except Exception as exc:
        report['error_type']=type(exc).__name__;report['status']='stopped_review_required'
    finally:
        if credentials:credentials.clear()
        if tokens:tokens.clear()
        auth.close();report['finished_at']=now_cn().isoformat()
        save(DEST/'result_redacted.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

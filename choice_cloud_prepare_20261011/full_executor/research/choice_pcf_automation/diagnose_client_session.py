"""Inspect the existing native webpage token without logging out or trading."""
import json
from pathlib import Path
from choice_auth import ChoiceAuthenticator
from verify_readonly import GetOnlySession
from run_intraday_watch import ACCOUNT
from quanta_agents.factor_lab_a.choice_pcf import BASE
from quanta_agents.factor_lab_a.pcf_automation import now_cn
from watch_engine import save

def main():
    auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);tokens=None
    report=dict(checked_at=now_cn().isoformat(),new_orders=0,cancellations=0,logout_performed=False)
    try:
        tokens=auth.current_tokens();http.headers['token']=tokens['JToken']
        response=http.get(BASE+'AguTrade/SpoBalInfo',params={'accId':ACCOUNT},timeout=20,allow_redirects=False)
        report.update(http_status=response.status_code,business_code=str(response.json().get('Code')),uid_matched=True)
    except Exception as e:report['error_type']=type(e).__name__
    finally:
        http.headers.clear();http.close();auth.close()
        if tokens:tokens.clear()
    save(Path(__file__).resolve().parent/'session_20260922/native_cookie_diagnostic_redacted.json',report)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()

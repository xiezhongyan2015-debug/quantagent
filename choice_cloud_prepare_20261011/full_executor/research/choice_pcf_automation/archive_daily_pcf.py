"""Official public PCF archive only. Does not authenticate or trade."""
import datetime as dt
import json
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.pcf_automation import close_job,now_cn,write_json

DEST=ROOT/'research/choice_pcf_automation/daily_public_pcf'

def main():
    now=now_cn()
    if not dt.date(2026,9,21)<=now.date()<=dt.date(2026,12,30):raise RuntimeError('Official calendar coverage expired; update calendar before continuing')
    # SSE 2026 holiday notice: Sep25-27 and Oct1-7; weekends excluded.
    start=dt.date(2026,9,17);end=dt.date(2026,12,31)
    days=[];d=start
    while d<=end:
        if d.weekday()<5 and not dt.date(2026,9,25)<=d<=dt.date(2026,9,27) and not dt.date(2026,10,1)<=d<=dt.date(2026,10,7):days.append(d.isoformat())
        d+=dt.timedelta(days=1)
    write_json(DEST/'calendar.json',days)
    cfg=dict(state_dir=str(DEST/'state'),calendar=str(DEST/'calendar.json'),enable_choice_orders=False,close_command=[])
    result=close_job(cfg,now)
    write_json(DEST/'latest_status.json',dict(checked_at=now.isoformat(),orders_submitted=0,manifest=result,
        source='https://api.efunds.com.cn/xcowch/front/etffund/',calendar_source='https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml',calendar_valid_through='2026-12-30'))
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()

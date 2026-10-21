"""Alternating monthly CSI300/CSI1000 downloads with a shared logged-in worker."""
import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from download_ifind_csi500_1min import atomic_json

WORK=Path(__file__).resolve().parent.parent
CONTROL=WORK/'data_expansion/ifind_indices_daily_control'
API=WORK/'data_expansion/ifind_fundamental_control'
CALENDAR=Path('E:/中证500_历年成分股_日频_2010-2026_iFinD_后复权/audit/trading_calendar.json')
INDICES=[('沪深300','000300.SH',300,'2010-01-01'),('中证1000','000852.SH',1000,'2014-10-17')]


def output(name):return Path(f'E:/{name}_历年成分股_日频_2010-2026_iFinD_后复权')


def state(status,**extra):
    atomic_json(CONTROL/'progress.json',dict(status=status,pid=os.getpid(),updated_at=datetime.now().isoformat(),
        api_control=str(API),outputs=[str(output(x[0])) for x in INDICES],**extra))


def prepare():
    CONTROL.mkdir(parents=True,exist_ok=True)
    for name,code,count,start in INDICES:
        dest=output(name)
        for folder in ['audit','memberships','scripts_backup','errors','bars']:(dest/folder).mkdir(parents=True,exist_ok=True)
        if not (dest/'audit/trading_calendar.json').exists():shutil.copy2(CALENDAR,dest/'audit/trading_calendar.json')
        for script in ['download_ifind_index_daily.py','download_ifind_csi500_1min.py','run_ifind_index_daily_pair.py','audit_ifind_index_daily.py']:
            shutil.copy2(WORK/'scripts'/script,dest/'scripts_backup'/script)
        sample='csi300_members_sample' if count==300 else 'csi1000_members_sample'
        samples=[(sample,'2026-09-24')]+([('csi1000_members_launch_sample','2014-10-17')] if count==1000 else [])
        for stem,date in samples:
            p=dest/'memberships'/(date+'.json.gz')
            if not p.exists():
                r=json.loads((API/'results'/(stem+'.json')).read_text(encoding='utf-8'))
                assert r['errorcode']==0
                with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False)
        text=f'''# {name}历年成分股日频后复权

指数代码 {code}，目标时间2010-01-01至2026-09-24；可用成分区间从{start}开始。
中证1000于2014-10-17发布，发布前不构造真实成分池，2010-01-04接口无数据的证据保留在控制目录。官方资料：https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000852factsheet.pdf
每天按iFinD历史成分名单，只请求股票实际在池日期。名单异常先复查并保留原始响应，复查一致不等于官方完整性已确认。
字段为 open/high/low/close/avgPrice/volume/amount/turnoverRatio；sellVolume、buyVolume明确留空，不填零。
THS_HQ参数 Interval:D,CPS:3,baseDate:1900-01-01,Fill:Omit，后复权（分红再投）；行情停牌或原始空值保留，不造数据。
bars按年月保存Parquet，memberships保存每日原始成分，manifest.sqlite保存批次、SHA256和缺失项。
两个指数按月份交替从近到远下载，同一API登录会话12线程、全局至少0.15秒、每路结束至少0.5秒。
逐日成分消耗基础额度，行情另计行情额度；每个月开始前查询真实余量，各保留至少15万，不足时等待额度恢复并断点续传，不切换账号绕过限制。
本目录progress.json为该指数状态；总体状态在工作目录data_expansion/ifind_indices_daily_control/progress.json。不重下已记录原始空值。
'''
        (dest/'使用说明.md').write_text(text,encoding='utf-8')


def main(max_jobs=None):
    prepare()
    import msvcrt
    lock=open(CONTROL/'pipeline.lock','a+b');lock.seek(0)
    if not lock.read(1):lock.write(b'0');lock.flush()
    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    checkpoint=CONTROL/'completed_months.json'
    done=json.loads(checkpoint.read_text()) if checkpoint.exists() else []
    calendar=json.loads(CALENDAR.read_text(encoding='utf-8'))['data']['time']
    tasks=[(month,spec) for month in sorted({d[:7] for d in calendar if '2010-01-01'<=d<='2026-09-24'},reverse=True)
           for spec in INDICES if month>=spec[3][:7]]
    executed=0
    try:
        for month,(name,code,count,start) in tasks:
            key=code+'_'+month
            if key in done:continue
            if (CONTROL/'STOP').exists():state('paused',completed_month_tasks=len(done),planned_month_tasks=len(tasks));return
            state('running',current_index=name,current_month=month,completed_month_tasks=len(done),planned_month_tasks=len(tasks))
            args=[sys.executable,str(WORK/'scripts/download_ifind_index_daily.py'),'--output',str(output(name)),
                '--index-code',code,'--expected-count',str(count),'--membership-start',start,'--only-month',month,'--allow-missing-inout']
            result=subprocess.run(args,cwd=WORK)
            p=json.loads((output(name)/'progress.json').read_text(encoding='utf-8'))
            if result.returncode or p['state']!='selected_month_complete':
                state('waiting_quota' if p['state']=='waiting_quota' else 'blocked',current_index=name,current_month=month,
                    completed_month_tasks=len(done),planned_month_tasks=len(tasks),detail=p)
                return
            done.append(key);atomic_json(checkpoint,done);executed+=1
            if max_jobs and executed>=max_jobs:state('sample_complete',completed_month_tasks=len(done),planned_month_tasks=len(tasks));return
        for name,_,_,_ in INDICES:
            subprocess.run([sys.executable,str(WORK/'scripts/audit_ifind_index_daily.py'),'--output',str(output(name))],cwd=WORK,check=True)
        state('complete_with_quality_reports',completed_month_tasks=len(done),planned_month_tasks=len(tasks))
    except Exception as e:
        state('blocked',error=repr(e),completed_month_tasks=len(done));raise
    finally:lock.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--max-jobs',type=int);a=p.parse_args();main(a.max_jobs)

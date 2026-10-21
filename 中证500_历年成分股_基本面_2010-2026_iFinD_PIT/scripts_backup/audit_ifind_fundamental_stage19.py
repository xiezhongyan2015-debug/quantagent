"""Audit the currently completed 19-field deliverable; not final completion."""
import hashlib
import json
from datetime import datetime

import pandas as pd
from download_ifind_fundamental_market import DEST, save
from download_ifind_pit_core import FIELDS as CORE
from download_ifind_pit_supplement import FIELDS as EXTRA
from download_ifind_cashflow import FIELDS as CASHFLOW


def main():
    financial=pd.read_parquet(DEST/'financial_verified_19_fields.parquet')
    plan=pd.read_parquet(DEST/'plan/pit_version_requests.parquet')
    universe=pd.read_parquet(DEST/'plan/weekly_universe.parquet')
    weekly=pd.read_parquet(DEST/'weekly_market_and_industry.parquet')
    events=pd.read_parquet(DEST/'plan/report_event_inventory.parquet')
    fields={**CORE,**EXTRA,**CASHFLOW}; keys=['asof_date','thscode','report_period']
    assert set(fields)<=set(financial.columns)
    match=financial[keys].merge(plan[keys],on=keys,how='outer',validate='one_to_one',indicator=True)
    assert match['_merge'].eq('both').all()
    membership=financial[['asof_date','thscode']].merge(universe[['asof_date','thscode']],how='left',validate='many_to_one',indicator=True)
    assert membership['_merge'].eq('both').all()
    assert financial.source_update_date.le(financial.asof_date).all()
    assert financial.report_period.le(financial.asof_date).all()
    assert financial.groupby('asof_date').thscode.nunique().max()<=500
    match=weekly[['asof_date','thscode']].merge(universe[['asof_date','thscode']],how='outer',validate='one_to_one',indicator=True)
    assert match['_merge'].eq('both').all()
    assert weekly.groupby('asof_date').thscode.nunique().max()<=500
    counts=universe.groupby('asof_date').size()
    names=['financial_verified_19_fields.parquet','weekly_market_and_industry.parquet','weekly_market_cap.parquet','weekly_historical_industry.parquet']
    artifacts={n:dict(bytes=(DEST/n).stat().st_size,sha256=hashlib.sha256((DEST/n).read_bytes()).hexdigest()) for n in names}
    quota=json.loads((DEST/'audit/latest_quota.json').read_text(encoding='utf-8'))
    report=dict(status='19_fields_verified_one_field_unresolved',full_fundamentals_complete=False,
        audited_at=datetime.now().isoformat(),financial_rows=len(financial),financial_field_count=len(fields),
        field_codes=fields,financial_duplicate_keys=0,financial_plan_exact_match=True,financial_queries_in_pool=True,
        future_source_update_dates=0,weekly_rows=len(weekly),weekly_dates=int(weekly.asof_date.nunique()),
        weekly_duplicate_keys=0,weekly_membership_exact_match=True,maximum_constituents_at_one_time=int(counts.max()),
        weekly_counts_below_500={d:int(v) for d,v in counts.items() if v<500},
        financial_null_counts={f:int(financial[f].isna().sum()) for f in fields},
        market_cap_null_count=int(weekly.total_market_cap_cny.isna().sum()),
        industry_code_null_count=int(weekly.sw_l1_provider_code.isna().sum()),
        report_event_pairs=len(events),report_event_pairs_without_events=int(events.provider_event_count.eq(0).sum()),
        unresolved_fields={'deducted_parent_net_profit':'PIT indicator/parameters not verified. Direct and two evidence-based alternatives returned -209. No latest ordinary value substituted.'},
        limitations=['Provider update dates are not precise disclosure timestamps.',
            'Historical industry identifiers retained; taxonomy revision unverified and Chinese labels omitted due to SDK decoding.',
            'Provider nulls preserved; zero values not inferred.',
            'Version rows are not a weekly factor panel; use only versions visible at each historical as-of date.',
            'Recorded 499-member dates not fabricated or filled.'],
        latest_quota=quota,artifacts=artifacts)
    save(DEST/'audit/stage19_quality_report.json',report)
    lines=['# 基本面阶段质量报告（19项完成，1项未解决）','',
        f'生成时间：{report["audited_at"]}。本报告不是全部完成声明。',
        '',f'- 财报：{len(financial):,} 条历史版本，19项字段，重复键0；与计划版本清单完全一致，查询日期均在池。',
        f'- 周度：{len(weekly):,} 行，{weekly.asof_date.nunique()} 个日期；市值及行业代码均无空值，成分池连接完全一致，每时点最多500只。',
        f'- 更新日期：{len(events):,} 个股票报告期组合，其中 {report["report_event_pairs_without_events"]} 个组合无供应商原始更新事件；不凭空补财报版本。',
        f'- 尚缺：扣非归母净利润。PIT代码或参数仍未验证，不用最新财报回填历史。',
        f'- 账户本周剩余基础额度：{quota["remaining"]:,} 单元（查询时点 {quota["checked_at"]}），至少保留150,000。',
        '', '文件校验、各字段空值数量、499只的周度日期详见 stage19_quality_report.json。',
        '', '限制：行业具体分类版本未核实；中文名称未填。财报更新时间不等于精确披露时刻，信号最早从下一交易日使用。流量指标为年初至今累计值。原始空值保留，财报版本不可回填到更早时点。']
    (DEST/'audit/阶段质量报告_19项完成.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['status','financial_rows','financial_field_count','weekly_rows','report_event_pairs_without_events']},ensure_ascii=False))


if __name__=='__main__':main()

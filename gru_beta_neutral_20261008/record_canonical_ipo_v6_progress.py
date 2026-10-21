"""Promote only the independently audited research candidate and preserve reports."""
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'outputs/gru_beta_neutral_20261008'
NAME='backfilled_panel_candidate_canonical_ipo_v6'
PREVIOUS='backfilled_panel_candidate_ipo_prefix_v5'
OUT=BASE/NAME


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.paths.extend(v for k,v in attrs if k=='href' and not v.startswith(('http:','https:','#')))


def main():
    output=OUT/'progress_recording_receipt.json'
    assert not output.exists()
    ap=OUT/'independent_completion_audit.json'; a=json.loads(ap.read_text())
    assert a['passed'] and a['added_raw_minute_quote_hours_verified']==8 and not a['production_ready']
    assert sha(ROOT/'gru_beta_neutral_20261008/audit_canonical_ipo_v6.py')==a['source_code_sha256']
    assert sha(OUT/'dataset/meta.json')==a['candidate_meta_sha256']
    meta=json.loads((OUT/'dataset/meta.json').read_text())
    hour=json.loads((OUT/'hour_panel_receipt.json').read_text())
    gap=json.loads((OUT/'gap_audit/receipt.json').read_text())
    old_meta=json.loads((BASE/PREVIOUS/'dataset/meta.json').read_text())
    assert meta['data_fingerprint']==a['candidate_fingerprint'] and hour['new_IPO_seed_prefix_hours']==32
    assert gap['minimum_intervals']['missing_primary_trading_hours']==3042
    assert gap['full_union_august_september']['missing_nonmember_primary_trading_hours']==84860
    now=datetime.now(timezone.utc)
    files=[BASE/'execution_manifest.json',BASE/'执行进度_2026-10-08.csv',BASE/'数据补齐核验与实验进度_2026-10-09.html']
    manifest=json.loads(files[0].read_text());data=manifest['data_backfill']
    assert data['latest_candidate_version']==PREVIOUS and data['new_X_fingerprint']==old_meta['data_fingerprint']
    snapshot=BASE/('progress_snapshots/canonical_ipo_v6_'+now.strftime('%Y%m%dT%H%M%SZ'));snapshot.mkdir(parents=True)
    for p in files:shutil.copyfile(p,snapshot/p.name)
    data.update({'latest_candidate_version':NAME,'new_X_fingerprint':meta['data_fingerprint'],
                 'new_X_shape':hour['shape']+[122],'candidate_panel_receipt':NAME+'/dataset/dataset_audit.json',
                 'independent_array_sha_checks':NAME+'/independent_completion_audit.json',
                 'gap_audit_receipt':NAME+'/gap_audit/receipt.json','coverage_by_year':hour['coverage_by_year'],
                 'new_donor_hours':hour['new_donor_hours'],'new_IPO_seed_prefix_hours':32,
                 'retained_directly_observed_IPO_seed_hours':24,'new_calculated_canonical_IPO_prefix_hours':8,
                 'IPO_factor_prefix_provenance':hour['IPO_factor_prefix_provenance'],
                 'IPO_prefix_candidate_independent_receipt':NAME+'/independent_completion_audit.json',
                 'previous_candidate_preserved':PREVIOUS+'/dataset/meta.json','previous_candidate_fingerprint':old_meta['data_fingerprint'],
                 'minimum_requested_missing_primary_trading_hours':3042,'full_union_tail_missing_nonmember_primary_trading_hours':84860,
                 'V6_model_training_or_backtest_rerun':False,'new_source_findings_hours_admitted':8,
                 'production_ready':False})
    method=data['IPO_factor_method_rounding_review']
    method.update({'qualified_unapplied_hours':0,'candidate_hours_admitted':8,
                   'applied_candidate_version':NAME,'application_independent_receipt':NAME+'/independent_completion_audit.json',
                   'complete_company_action_history_certified':False})
    manifest['required_work_remaining'][1]='Resolve remaining minimum-interval source conflicts and absent observed minute stockdays (3042 primary-daily trading hours still missing). The two canonical-basis IPO prefixes have been applied to independently verified V6 (8 native observed hours, outside the minimum interval); full source/action-history and live quality certification remain incomplete.'
    manifest['checked_utc']=manifest['latest_local_updated_utc']=now.isoformat()
    with files[1].open(newline='') as stream:rows=list(csv.reader(stream))
    for row in rows:
        if row[0]=='并集行情补齐':
            row[1:]=['第六版X与标签组装并独立核验完成；全范围仍有缺口','两股上市日新增8真实分钟聚合小时；累计IPO32小时，旧版小时全部保留',NAME+'/independent_completion_audit.json','最低仍缺3042、尾段仍缺84860；年度成分窗口覆盖率未变']
        elif row[0]=='慢因子与PIT':
            row[1:]=['第六版完整重算64因子与98慢特征；独立复核通过','19列财务PIT及来源字节一致；其余1176股原始慢特征相同',NAME+'/independent_completion_audit.json',f'两股慢值变动1403单元，其中历史成员167单元；{a["normalization_sample_full_crosssections"]}完整截面独立复算']
        elif row[0]=='代码沿革与旧因子':
            row[1:]=['旧代码1132小时与累计IPO32小时保留','两股计算前缀方法、官方现金表与下一日参考价核验通过',NAME+'/dataset/meta.json','未覆盖上市前、未替换后来既有因子；001872历史价量冲突仍保留']
        elif row[0]=='两股IPO复权方法差异':
            row[1:]=['第六版研究应用与独立复核完成','8小时已应用；两个值按既有口径计算，未声称IPO当日发布值',NAME+'/independent_completion_audit.json','全公司行动与实盘准入未认证；最低及尾段缺口不变']
    page=files[2].read_text()
    page=re.sub(r'更新时间：2026-10-09 \d\d:\d\d','更新时间：'+now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'),page)
    lead='<p class="lead">1178 股的第六版研究候选面板已组装并独立核验。芯动联科、成都华微两个上市日新增 8 个真实分钟聚合小时，使用经过独立复算的现有口径计算因子；前版 24 个 IPO 小时、59,412 个历史收盘统计补回小时及 1,132 个旧代码小时全部保留。最低补齐区间仍缺 3,042 正常交易小时；完整并集 2026 年 8—9 月非成分尾段仍缺 84,860 小时。2019 年窗口覆盖率为 98.89%；2020—2022 年仍未达到原 99.5% 目标。</p>'
    page,count=re.subn(r'<p class="lead">1178 股.*?</p>',lambda _:lead,page,count=1,flags=re.S);assert count==1
    page=page.replace('另外两只的复权方法差异已独立复算出数值解释，8 小时计算前缀尚未应用。','另外两只的复权方法差异已独立复算出数值解释，8 小时计算前缀已在第六版应用。')
    page=page.replace('8 小时计算前缀仍未应用，详见下方新增核验。','8 小时计算前缀已在第六版应用，详见下方新增核验。')
    page=page.replace('<td>未应用</td>','<td>第六版已应用</td>')
    page=page.replace('8 个小时均在最低区间之外，目前仍未改变面板。','8 个小时均在最低区间之外，已在第六版纳入并独立重建面板。')
    page=page.replace('其中三只种子与原有因子一致，另两只方法冲突待解。','其中三只种子与原有因子一致，另两只方法差异已解释并在第六版应用计算前缀。')
    block='<h2>第六版：两股上市日已补入并完成面板核验</h2><p>新增芯动联科 2023-06-30、成都华微 2024-02-07 的全部 8 个小时。下一交易日的原始前收盘分别与 IPO 原始收盘 46.85、19.69 精确相等，既有及来源事件表没有显示间隔内除权；只在实际 IPO 日期加入计算事件。两个因子值为 0.985996、0.992700，明确属于既有回顾性复权口径下的计算值，非当日直接观察发布值。原生分钟报价未替换，既有后来因子全部按原值保留。</p>'
    block+=f'<p>独立核验确认：两日 480 根标准分钟、两根正成交量开盘边界、8 个小时及全部受影响标签与执行价格通过；原始面板、前五版数组和 3,528 份沿用股票文件指纹保持。新增 8 个完整 80 小时窗口，均在历史成员掩码之外，因此年度覆盖率与最低补数缺口均不变。</p><p>64 个选定公式与 19 列财务时点字段保持；完整重算 98 慢特征，两股合计 1,403 个原始慢值变化，其中 167 个落在历史成员时点，其余 1,176 股原始小时及慢值一致。全部发生原始输入变化的 681 个交易日都覆盖到独立标准化复算，连同边界与抽样共检查 {a["normalization_sample_full_crosssections"]:,} 个完整截面，最大误差 {a["independent_normalization_max_abs_error"]:.2g}。本轮没有重新训练模型或重跑记分卡；已记录实验结论仍对应其冻结输入。该候选仍为研究版本，实盘来源准入尚未认证。</p>'
    block+=f'<p><a href="{NAME}/independent_completion_audit.json">第六版独立面板核验</a>；<a href="{NAME}/factor_prefix_asof_validation.json">事件日期边界核验</a>；<a href="public_source_probes/ipo_factor_rounding_method_20261009/isolated_research_prefix_admission.json">两日限定研究准入依据</a>；<a href="{NAME}/dataset/meta.json">第六版面板指纹</a></p>'
    assert page.count('<h2>可复核文件</h2>')==1
    before,latest=page.split('<h2>可复核文件</h2>')
    for sub in ['dataset/','gap_audit/','slow_features/']:latest=latest.replace(PREVIOUS+'/'+sub,NAME+'/'+sub)
    page=before+block+'<h2>可复核文件</h2>'+latest
    page=page.replace(old_meta['data_fingerprint'],meta['data_fingerprint'])
    page=re.sub(r'公开分钟来源累计新增 [\d,]+ 个完整小时',f'公开分钟来源累计新增 {hour["new_donor_hours"]:,} 个完整小时',page)
    parser=Links();parser.feed(page)
    assert all((BASE/p).exists() for p in parser.paths)
    assert '佳驰科技（688708）' in page and '正元地信' not in page
    files[0].write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    with files[1].open('w',newline='') as stream:csv.writer(stream).writerows(rows)
    files[2].write_text(page)
    result={'checked_utc':now.isoformat(),'candidate_version':NAME,'candidate_fingerprint':meta['data_fingerprint'],
            'source_code_sha256':sha(__file__),'independent_audit_sha256':sha(ap),
            'files_updated':[{'path':str(p),'sha256':sha(p)} for p in files],
            'prior_reports_snapshot':str(snapshot),'local_report_links_checked':len(parser.paths),
            'new_observed_minute_hours_admitted':8,'minimum_missing_hours':3042,'nonmember_tail_missing_hours':84860,
            'goal_complete':False,'production_ready':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()

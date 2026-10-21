"""Publish evidence only after independent V5 completion, retaining V4 reports."""
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'outputs/gru_beta_neutral_20261008'
NAME = 'backfilled_panel_candidate_ipo_prefix_v5'
PREVIOUS = 'backfilled_panel_candidate_closing_rule_v4'
OUT = BASE/NAME


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths = []

    def handle_starttag(self,tag,attrs):
        if tag == 'a':
            self.paths.extend(v for k,v in attrs if k == 'href' and not v.startswith(('http:','https:','#')))


def main():
    audit = json.loads((OUT/'independent_completion_audit.json').read_text())
    assert audit['passed'] and not audit['production_ready']
    assert sha(ROOT/'gru_beta_neutral_20261008/audit_ipo_prefix_v5.py') == audit['source_code_sha256']
    assert sha(OUT/'dataset/meta.json') == audit['candidate_meta_sha256']
    meta = json.loads((OUT/'dataset/meta.json').read_text())
    hour = json.loads((OUT/'hour_panel_receipt.json').read_text())
    gap = json.loads((OUT/'gap_audit/receipt.json').read_text())
    old_meta = json.loads((BASE/PREVIOUS/'dataset/meta.json').read_text())
    now = datetime.now(timezone.utc)
    missing = gap['minimum_intervals']['missing_primary_trading_hours']
    tail = gap['full_union_august_september']['missing_nonmember_primary_trading_hours']
    assert audit['added_raw_minute_quote_hours_verified'] == hour['new_IPO_seed_prefix_hours'] == 24
    files = [BASE/'execution_manifest.json', BASE/'执行进度_2026-10-08.csv', BASE/'数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    assert manifest['data_backfill']['latest_candidate_version'] == PREVIOUS
    snapshot = BASE/('progress_snapshots/ipo_prefix_v5_'+now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files: shutil.copyfile(path,snapshot/path.name)
    ceiling = json.loads((BASE/PREVIOUS/'gap_audit/strict_window_suspension_coverage_ceiling.json').read_text())
    ceiling.update({'checked_at_utc':now.isoformat(),'gap_receipt_sha256':sha(OUT/'gap_audit/receipt.json'),
                    'cause_table_sha256':sha(OUT/'gap_audit/member_window_causes_by_year.csv')})
    (OUT/'gap_audit/strict_window_suspension_coverage_ceiling.json').write_text(json.dumps(ceiling,indent=2))
    data = manifest['data_backfill']
    data.update({'latest_candidate_version':NAME,'new_X_fingerprint':meta['data_fingerprint'],
                 'new_X_shape':hour['shape']+[122],'candidate_panel_receipt':NAME+'/dataset/dataset_audit.json',
                 'independent_array_sha_checks':NAME+'/independent_completion_audit.json',
                 'gap_audit_receipt':NAME+'/gap_audit/receipt.json','coverage_by_year':hour['coverage_by_year'],
                 'new_donor_hours':hour['new_donor_hours'],'new_IPO_seed_prefix_hours':24,
                 'IPO_factor_prefix_provenance':hour['IPO_factor_prefix_provenance'],
                 'IPO_prefix_candidate_independent_receipt':NAME+'/independent_completion_audit.json',
                 'minimum_requested_missing_primary_trading_hours':missing,
                 'full_union_tail_missing_nonmember_primary_trading_hours':tail,
                 'strict_window_suspension_coverage_ceiling_receipt':NAME+'/gap_audit/strict_window_suspension_coverage_ceiling.json',
                 'previous_candidate_preserved':PREVIOUS+'/dataset/meta.json','previous_candidate_fingerprint':old_meta['data_fingerprint'],
                 'recent_sina_sample_quality_receipt':'public_source_probes/sina_latest_minutes_20261009/independent_sample_quality_audit.json',
                 'FTShare_anonymous_probe_receipt':'public_source_probes/ftshare_historical_minutes_20261009/anonymous_probe_receipt.json',
                 'all_later_year_coverage_gates_passed':all(r['target_passed'] for r in gap['coverage_by_year'] if r['year']>=2020),
                 'production_ready':False})
    manifest['required_work_remaining'] = [
        f'Recover or independently resolve full-union minute tail ({tail} nonmember primary-daily trading hours still absent); latest public mirror is byte-identical, anonymous FTShare returns API-key-required, recent Sina sample has missing minutes and volume disagreement and is not admitted',
        f'Resolve remaining minimum-interval source conflicts and absent observed minute stockdays ({missing} primary-daily trading hours still missing); 688582.SH and 688709.SH IPO factor prefixes remain pending because cross-provider factor methods disagree',
        'Independent source extreme/identity/live quality admission before production certification; resolve 001872.SZ historical daily-price disagreement; clock-minute concordance does not certify exact last-tick trailing minute',
        'Independently verify suspension statuses and address unchanged 2020-2022 coverage gate failures; preserve original denominators and never fabricate suspension trades',
    ]
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='') as handle: rows = list(csv.reader(handle))
    for row in rows:
        if row[0] == '并集行情补齐':
            row[1:] = ['第五版X与标签组装并独立核验完成；全范围仍有缺口','三只IPO最初6日新增24真实小时；第四版59412小时继续保留',NAME+'/independent_completion_audit.json',f'最低区间仍缺{missing}，尾段仍缺{tail}；年度覆盖率未变']
        elif row[0] == '慢因子与PIT':
            row[1:] = ['第五版完整重算64因子与98慢特征；独立复核通过','19列财务PIT及来源字节不变；1175只股票原始慢特征相同',NAME+'/independent_completion_audit.json',f'IPO新增观测引起三股因子变化；{audit["normalization_sample_full_crosssections"]}完整截面独立复算，包含所有原始输入变化日']
        elif row[0] == '代码沿革与旧因子':
            row[1:] = ['旧代码1132小时与IPO前缀24小时保留','688220/688234/688331观察到的IPO初始因子与既有种子一致',NAME+'/dataset/meta.json','688582/688709因子方法冲突待解；001872历史价量仍有冲突']
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d','更新时间：'+now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'),html)
    lead = f'<p class="lead">1178 股的第五版研究候选面板已组装并独立核验。三个新股上市初期的观察因子经来源核对后，额外纳入 24 个真实分钟聚合小时；此前历史沪市收盘统计补回的 59,412 小时及旧代码交付的 1,132 小时全部保留。最低补齐区间仍缺 {missing:,} 正常交易小时；完整并集 2026 年 8—9 月非成分股尾段仍缺 {tail:,} 小时。2019 年窗口覆盖率为 98.89%；2020—2022 年仍未达到原定 99.5% 目标。</p>'
    html,count = re.subn(r'<p class="lead">1178 股.*?</p>',lambda _:lead,html,count=1,flags=re.S); assert count == 1
    new = f'<h2>IPO 初期因子：第五版新增核验</h2><p>688220.SH 的 2022-01-14、01-17、01-18，688234.SH 的 2022-01-12，以及 688331.SH 的 2022-03-31、04-01，共 6 个股票日、24 个小时通过完整观察分钟网格、独立日线开收盘与成交量、价格范围核对。新浪实际 IPO 日期的种子因子为 1，与 Baostock 既有种子值一致；上市日期原文与来源文件均已留存。不将 1900 日期占位符当成真实事件，不向上市前扩展，不改变既有因子。<a href="public_source_probes/sina_ipo_factor_prefix_20261009/prefix_validation_receipt.json">逐日来源核验</a>；<a href="{NAME}/independent_completion_audit.json">第五版独立复核</a></p><p>新增小时都在最低补齐区间与当时成分掩码之外，年度覆盖率未变，完整并集新增 {audit["added_complete80_windows"]} 个 80 小时窗口。三股新增历史输入导致慢特征变化，已有 64 个公式保持固定；19 列财务 PIT 和其来源文件完全一致，另外 1175 股的原始小时及慢特征保持一致。复核包含所有原始输入变化日，共 {audit["normalization_sample_full_crosssections"]:,} 个完整截面。688582.SH、688709.SH 因两来源复权方法存在冲突，未应用其 IPO 前缀。</p>'
    html = html.replace('<h2>历史沪市收盘统计：第四版新增核验</h2>',new+'<h2>历史沪市收盘统计：第四版已完成核验</h2>')
    html = html.replace('五只新股上市最初数日的因子状态在最低区间之外另行核验，尚未应用。','三只新股上市初期已在第五版补回 24 小时；另外两只的复权方法冲突仍待解决。这些前缀均在最低区间之外。')
    html = html.replace('新浪取得五只 IPO 股票的因子日期记录，仍需处理口径对齐，未替换现有因子。','新浪五只 IPO 记录已核验，其中三只种子与原有因子一致，另两只方法冲突待解。原有因子均未替换。')
    note = '<h2>最新分钟数据源：实际访问结果</h2><p>已通过 Chrome 读取 FTShare 历史分钟接口说明，匿名实际查询返回 HTTP 401，要求 API Key。新浪公开一分钟接口可取得 600004.SH 的近期数据，但 9 月 22、23 日各缺 2、4 个标准分钟，成交量较独立日线分别少 5,100、7,100 股；扩大到 12,000 条的查询返回空数据。因此两种尝试都没有计入已补齐尾段。<a href="public_source_probes/ftshare_historical_minutes_20261009/anonymous_probe_receipt.json">FTShare 实际返回</a>；<a href="public_source_probes/sina_latest_minutes_20261009/independent_sample_quality_audit.json">新浪样本独立核验</a></p>'
    html = html.replace('<h2>可复核文件</h2>',note+'<h2>可复核文件</h2>')
    for sub in ['dataset/','gap_audit/','slow_features/']:
        html = html.replace(PREVIOUS+'/'+sub,NAME+'/'+sub)
    html = html.replace(old_meta['data_fingerprint'],meta['data_fingerprint'])
    html = re.sub(r'公开分钟来源累计新增 [\d,]+ 个完整小时',f'公开分钟来源累计新增 {hour["new_donor_hours"]:,} 个完整小时',html)
    parser = Links(); parser.feed(html)
    missing_links = [p for p in parser.paths if not (BASE/p).exists()]
    assert not missing_links, missing_links
    files[0].write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    with files[1].open('w',newline='') as handle: csv.writer(handle).writerows(rows)
    files[2].write_text(html)
    result = {'checked_utc':now.isoformat(),'candidate_version':NAME,'candidate_fingerprint':meta['data_fingerprint'],
              'files_updated':[{'path':str(p),'sha256':sha(p)} for p in files],
              'prior_reports_snapshot':str(snapshot),'local_report_links_checked':len(parser.paths),
              'source_code_sha256':sha(Path(__file__)),'goal_complete':False,'production_ready':False}
    (OUT/'progress_recording_receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

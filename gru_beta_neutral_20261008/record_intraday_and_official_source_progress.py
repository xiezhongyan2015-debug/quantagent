"""Record verified source findings without changing or admitting panel data."""
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
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
PROBES = BASE / 'public_source_probes'
NAME = 'backfilled_panel_candidate_ipo_prefix_v5'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths.extend(v for k, v in attrs if k == 'href' and not v.startswith(('http:', 'https:', '#')))


def main():
    now = datetime.now(timezone.utc)
    intraday_path = PROBES / 'original_intraday_concordance_20261009/concordance_audit_receipt.json'
    verify_path = intraday_path.parent / 'independent_findings_verification.json'
    intraday = json.loads(intraday_path.read_text())
    verified = json.loads(verify_path.read_text())
    assert verified['passed'] and verified['audited_receipt_sha256'] == sha(intraday_path)
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_original_intraday_findings.py') == verified['source_code_sha256']
    assert not intraday['candidate_changed'] and intraday['candidate_hours_admitted'] == 0
    sz = next(r for r in verified['checks'] if r['symbol'] == '002831.SZ')
    sh = next(r for r in verified['checks'] if r['symbol'] == '600167.SH')
    assert (sz['minute_close_matches'], sz['minute_volume_matches']) == (167, 54)
    assert (sh['minute_close_matches'], sh['minute_volume_matches']) == (240, 240)
    tdx_path = PROBES / 'tdx_protocol_research_20261009/public_probe_receipt.json'
    tdx = json.loads(tdx_path.read_text())
    assert sha(tdx['source_code_path']) == tdx['source_code_sha256']
    assert len(tdx['results']) == 4 and all(r['status'] == 'public_probe_failed' for r in tdx['results'])
    for peer in tdx['results']:
        for request in peer['requests']:
            assert sha(request['response_path']) == request['response_sha256']
    official_path = PROBES / 'official_sse_suspension_chrome_20261009/conservative_window_audit/conservative_official_suspension_window_receipt.json'
    official = json.loads(official_path.read_text())
    assert official['independent_convolution_all_columns_passed'] and not official['candidate_changed']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_official_sse_suspension_windows.py') == official['source_code_sha256']
    classification_path = official_path.parent / 'primary_daily_discrepancy_classification.json'
    classification = json.loads(classification_path.read_text())
    assert classification['audited_receipt_sha256'] == sha(official_path)
    assert classification['classification_counts']['primary_present_status_or_nonzero_volume_conflicts'] == 0
    assert official['stocks_with_official_evidence'] == 99 and official['officially_evidenced_stockdays'] == 800
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    assert data['latest_candidate_version'] == NAME
    meta_path = BASE / NAME / 'dataset/meta.json'
    assert sha(meta_path) == official['candidate_meta_sha256']
    assert json.loads(meta_path.read_text())['data_fingerprint'] == data['new_X_fingerprint']
    snapshot = BASE / ('progress_snapshots/intraday_official_sources_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for file in files:
        shutil.copyfile(file, snapshot / file.name)
    data.update({'TDX_public_probe_receipt': str(tdx_path.relative_to(BASE)),
                 'original_intraday_concordance_receipt': str(intraday_path.relative_to(BASE)),
                 'independent_intraday_findings_receipt': str(verify_path.relative_to(BASE)),
                 'hour_local_coarse_sample_receipt': 'public_source_probes/hour_local_coarse_concordance_20261009/sample_receipt.json',
                 'official_SSE_conservative_suspension_subset_receipt': str(official_path.relative_to(BASE)),
                 'official_SSE_verified_suspension_stockdays': 800,
                 'official_SSE_verified_suspension_stocks': 99,
                 'official_SSE_all_market_suspension_audit_complete': False,
                 'new_source_findings_hours_admitted': 0,
                 'new_intraday_provider_disagreement_requires_broader_admission_audit': True,
                 'production_ready': False})
    manifest['required_work_remaining'][2] = 'Independent minute-level source concordance, extreme/identity/live quality admission before production certification; 002831.SZ 2022-06-06 passes whole-day tests but external minute close/volume differ from original raw minutes and independent 5m data; resolve 001872.SZ historical daily-price disagreement; clock-minute concordance does not certify exact last-tick trailing minute'
    manifest['required_work_remaining'][3] = 'Continue official suspension verification beyond conservative SSE subset (99 stocks, 800 stockdays verified); unchanged 2020-2022 coverage failures and vendor-derived ceilings remain unresolved for the full market; this official subset alone does not prove 99.5% target unattainable; preserve original denominators and never fabricate suspension trades'
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='') as f:
        rows = list(csv.reader(f))
    for row in rows:
        if row[0] == '新分钟来源网页试查':
            row[1:] = ['多个来源实查；本批未纳入新小时', 'Chrome已连接；匿名Meoz/FTShare要求授权，新浪样本分钟与成交量不足，通达信4节点没有可解析记录', str(tdx_path.relative_to(BASE)), '尾段仍缺84860小时；已保存原始返回与核验记录']
    rows.append(['分钟来源交叉核验', '两股原始一分钟样本及独立复算完成', '600167收盘/成交量240根一致；002831收盘167根、成交量54根一致，全天核验通过不能替代逐分钟核验', str(verify_path.relative_to(BASE)), '仅诊断；未改价格或面板，0新小时纳入'])
    rows.append(['官方停牌核验', '沪市保守子集已完成；全国核验未完', '99股800交易日；供应商停牌状态一致，677天成交量明确为0、123天未报告', str(official_path.relative_to(BASE)), '连续区间边界及不明确事件未推断；原分母和99.5%目标保持'])
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    assert '<h2>分钟来源交叉核验：新增原始样本证据</h2>' not in html
    new = '<h2>分钟来源交叉核验：新增原始样本证据</h2><p>从原始分卷交付中提取 2022-06-06 的 002831.SZ、600167.SH 原始一分钟文件，五卷 SHA256 及压缩尾校验均通过。002831 的原始与外部来源虽然全天开收盘、范围、成交量均通过日线核验，逐分钟收盘仅 167/240 根一致、成交量仅 54/240 根完全一致，单分钟最大成交量差 26,600 股；简单错移一分钟不能解决。原始分钟聚合后与独立五分钟源的 48 根收盘和成交量全部一致，而外部分钟聚合的收盘 34/48、成交量 6/48 完全一致。600167 样本逐分钟收盘与成交量均为 240/240 一致，开盘与高低仍存在口径差异。两股核验不能推广为全股票源质量结论，逐分钟及执行窗口口径仍需进一步审查；本批没有新增小时，也没有改写价格。<a href="public_source_probes/original_intraday_concordance_20261009/concordance_audit_receipt.json">原始逐分钟比较</a>；<a href="public_source_probes/original_intraday_concordance_20261009/independent_findings_verification.json">独立分组复算</a></p><p>通达信四个已公开节点实际返回条数标记，但没有分钟记录内容，未计入补齐。固定 IP 返回只作未经认证的数据线索。<a href="public_source_probes/tdx_protocol_research_20261009/public_probe_receipt.json">实际访问与报文证据</a></p>'
    new += '<h2>官方停牌：沪市保守子集新增核验</h2><p>经 Chrome 观察实际公开查询，再保留交易所完整分页原始返回，已取得 2019 年四季度与 2020—2022 年记录。去重后 1,570 条记录中，仅固定股票并集内、明确全天或闭合连续停牌区间内部日期纳入：188 条事件、99 股、800 个交易日。800 天的供应商停牌状态均一致；677 天成交量报告为 0，123 天为空值，空值保留为未报告。边界日、部分日及不明确事件不作推断，没有生成交易价格。</p>'
    new += '<table><thead><tr><th>年份</th><th>官方子集证实的不可完整成分窗口</th><th>仅据此子集的乐观覆盖率上限</th></tr></thead><tbody>'
    for r in official['rows']:
        new += f'<tr><td>{r["year"]}</td><td>{r["officially_evidenced_unavoidable_incomplete_member_windows"]:,}</td><td>{r["conservative_optimistic_coverage_ceiling"]:.4%}</td></tr>'
    new += '</tbody></table><p>该子集尚不足以证明 99.5% 目标不可达；此前供应商状态推算的更低上限仍属条件结论。全国停牌审查尚未完成，原覆盖率分母与目标保持不变。<a href="public_source_probes/official_sse_suspension_chrome_20261009/conservative_window_audit/conservative_official_suspension_window_receipt.json">官方区间与窗口核验</a>；<a href="public_source_probes/official_sse_suspension_chrome_20261009/conservative_window_audit/primary_daily_discrepancy_classification.json">未报告成交量分类</a></p>'
    html = html.replace('<h2>可复核文件</h2>', new + '<h2>可复核文件</h2>')
    parser = Links()
    parser.feed(html)
    assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='') as f:
        csv.writer(f).writerows(rows)
    files[2].write_text(html)
    receipt = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(Path(__file__)),
               'files_updated': [{'path': str(p), 'sha256': sha(p)} for p in files],
               'prior_reports_snapshot': str(snapshot), 'report_local_links_checked': len(parser.paths),
               'candidate_version_unchanged': NAME, 'candidate_fingerprint_unchanged': data['new_X_fingerprint'],
               'candidate_values_changed': False, 'new_hours_admitted': 0,
               'minimum_trading_gap_unchanged': 3042, 'nonmember_tail_gap_unchanged': 84860,
               'goal_complete': False, 'production_ready': False}
    (intraday_path.parent / 'progress_recording_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

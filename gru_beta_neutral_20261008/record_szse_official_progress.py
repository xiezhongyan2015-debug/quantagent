"""Publish independently checked official status evidence, without panel edits."""
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from html import escape
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
            self.paths.extend(value for key, value in attrs if key == 'href' and not value.startswith(('http:', 'https:', '#')))


def main():
    now = datetime.now(timezone.utc)
    path = PROBES / 'official_szse_suspension_chrome_20261009/conservative_window_audit/combined_official_suspension_window_receipt.json'
    verify_path = path.parent / 'independent_official_findings_verification.json'
    audit = json.loads(path.read_text())
    verification = json.loads(verify_path.read_text())
    assert verification['passed'] and verification['audited_receipt_sha256'] == sha(path)
    assert verification['rows'] == audit['rows']
    assert not audit['candidate_changed'] and audit['original_denominators_preserved'] and audit['original_targets_preserved']
    sensitivity_path = path.parent / 'conflicted_day_exclusion_sensitivity_receipt.json'
    sensitivity = json.loads(sensitivity_path.read_text())
    assert sensitivity['passed'] and sensitivity['audited_receipt_sha256'] == sha(path)
    assert sensitivity['independent_verification_receipt_sha256'] == sha(verify_path)
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_official_suspension_conflict_sensitivity.py') == sensitivity['source_code_sha256']
    assert len(sensitivity['excluded_conflicted_stockdays']) == audit['SZSE_primary_classification_counts']['primary_status_or_nonzero_volume_conflicts']
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_official_szse_findings.py') == verification['source_code_sha256']
    modern_path = PROBES / 'tdx_documented_modern_peers_20261009/probe_receipt.json'
    control_path = PROBES / 'tdx_legacy_daily_control_20261009/receipt.json'
    modern, control = json.loads(modern_path.read_text()), json.loads(control_path.read_text())
    assert modern['serializer_matches_author_published_packet'] and modern['candidate_hours_admitted'] == 0
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_documented_tdx_modern_peers.py') == modern['source_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_tdx_legacy_daily_control.py') == control['source_code_sha256']
    queries = [record for record in control['records'] if 'category' in record]
    assert len(queries) == 3 and all(not record['has_record_payload'] for record in queries)
    for record in control['records']:
        assert sha(record['response_path']) == record['response_sha256']
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    assert data['latest_candidate_version'] == NAME
    meta = BASE / NAME / 'dataset/meta.json'
    assert sha(meta) == audit['candidate_meta_sha256']
    assert json.loads(meta.read_text())['data_fingerprint'] == data['new_X_fingerprint']
    assert data['minimum_requested_missing_primary_trading_hours'] == 3042
    assert data['full_union_tail_missing_nonmember_primary_trading_hours'] == 84860
    proved = [row['year'] for row in sensitivity['rows'] if row['original_target_proven_unattainable_by_this_subset']]
    snapshot = BASE / ('progress_snapshots/szse_official_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for file in files:
        shutil.copyfile(file, snapshot / file.name)
    data.update({'official_SZSE_conservative_suspension_subset_receipt': str(path.relative_to(BASE)),
        'official_two_exchange_independent_verification_receipt': str(verify_path.relative_to(BASE)),
        'official_two_exchange_conflict_exclusion_receipt': str(sensitivity_path.relative_to(BASE)),
        'official_two_exchange_stockdays_after_conflict_exclusion': sensitivity['combined_official_stockdays_after_conflict_exclusion'],
        'official_two_exchange_verified_suspension_stockdays': audit['combined_officially_evidenced_stockdays'],
        'official_two_exchange_verified_suspension_stocks': audit['combined_stocks_with_official_evidence'],
        'official_subset_coverage_target_proven_unattainable_years': proved,
        'official_all_market_suspension_audit_complete': False,
        'TDX_documented_modern_probe_receipt': str(modern_path.relative_to(BASE)),
        'TDX_daily_control_probe_receipt': str(control_path.relative_to(BASE)),
        'new_source_findings_hours_admitted': 0, 'production_ready': False})
    proof_text = ('The conservative official SSE/SZSE subset now proves the original 99.5% target unattainable in ' + ', '.join(map(str, proved)) + ' under unchanged strict 80-hour eligibility' if proved else 'This conservative official SSE/SZSE subset alone still does not prove the original 99.5% target unattainable')
    manifest['required_work_remaining'][3] = proof_text + '; retain the failed original gates and denominators, continue distinguishing source omissions and genuine halts, and do not fabricate executable suspension bars or reinterpret the original target as passed'
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='') as file:
        rows = list(csv.reader(file))
    for row in rows:
        if row[0] == '新分钟来源网页试查':
            row[1:] = ['多个来源实查；本批0新小时', 'Chrome历史停复牌查询已取数；通达信3个新节点连接失败，旧节点日线及分钟对照均无记录内容', str(control_path.relative_to(BASE)), '最低缺3042小时、尾段缺84860小时保持']
        if row[0] == '官方停牌核验':
            row[1:] = ['沪深保守子集及独立复算完成', f'{audit["combined_stocks_with_official_evidence"]}股、{audit["combined_officially_evidenced_stockdays"]}股票交易日；深市只纳入明确开市停牌至开市复牌的闭合区间', str(path.relative_to(BASE)), ('原99.5%目标已证实不可达年份：' + ','.join(map(str, proved))) if proved else '本子集仍不足以证明99.5%不可达']
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    block = '<h2>沪深官方停复牌：合并核验</h2>'
    block += f'<p>深交所公开历史查询已取得 2019 年四季度、2020—2022 年完整分页记录，原始返回及 SHA256 全部保留。深市纳入 {audit["SZSE_closed_intervals_admitted"]} 个明确的开市停牌至开市复牌闭合区间，{audit["SZSE_stocks_with_official_evidence"]} 股、{audit["SZSE_officially_evidenced_stockdays"]} 个股票交易日，复牌日期不算停牌。部分日、未闭合或不明确的区间保留待核验。与沪市保守证据合并后，共 {audit["combined_stocks_with_official_evidence"]} 股、{audit["combined_officially_evidenced_stockdays"]} 个股票交易日。原分母、完整80小时规则及99.5%目标均保持。</p>'
    block += '<table><thead><tr><th>年份</th><th>官方证实的不可完整成分窗口</th><th>乐观覆盖率上限</th><th>是否证明原99.5%不可达</th></tr></thead><tbody>'
    for row in sensitivity['rows']:
        block += f'<tr><td>{row["year"]}</td><td>{row["officially_evidenced_unavoidable_incomplete_member_windows"]:,}</td><td>{row["conservative_optimistic_coverage_ceiling"]:.4%}</td><td>{"是" if row["original_target_proven_unattainable_by_this_subset"] else "否"}</td></tr>'
    block += '</tbody></table><p>上表额外剔除了山西证券 2020-06-17 的一条供应商成交量冲突：官方为开市停牌，供应商状态也为停牌，但成交量为正、成交金额为零。原因仍待核验，原始记录保持；剔除后仍有 225 股、1,777 个股票交易日支持上述保守上限，2020—2022 年结论均保持。这是回顾性停牌原因核验，并非全国完整的历史状态数据，也不作为当时可用信号。独立程序从原始分页重建停复牌配对，再用另一种滚动计算核对全部窗口。原覆盖率未达标仍按未达标报告；此次没有补入新价格、分钟或小时，最低缺口 3,042 小时、尾段缺口 84,860 小时保持。</p>'
    block += f'<p><a href="{escape(str(path.relative_to(BASE)))}">沪深区间与窗口核验</a>；<a href="{escape(str(verify_path.relative_to(BASE)))}">独立原始分页复算</a>；<a href="{escape(str(sensitivity_path.relative_to(BASE)))}">剔除冲突日后重算</a></p>'
    block += f'<p>通达信新增三个公开节点连接失败；旧节点的两组日线对照及一分钟请求均只返回标记而无记录内容，未取得可用行情。<a href="{escape(str(modern_path.relative_to(BASE)))}">新增节点访问证据</a>；<a href="{escape(str(control_path.relative_to(BASE)))}">日线与分钟对照证据</a></p>'
    if '<h2>沪深官方停复牌：合并核验</h2>' in html:
        html = re.sub(r'<h2>沪深官方停复牌：合并核验</h2>.*?(?=<h2>可复核文件</h2>)', lambda match: block, html)
    else:
        html = html.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    ceiling_text = '、'.join(f'{row["conservative_optimistic_coverage_ceiling"]:.4%}' for row in sensitivity['rows'])
    lead = '<p class="lead"><strong>官方停复牌证据已确认严格窗口目标的限制。</strong>保留原历史成分股分母、要求 80 根连续完整小时且不补造停牌成交价时，即使其他缺口全部解决，仅根据已核验的沪深官方停复牌保守子集，2020、2021、2022 年覆盖率的乐观上限分别为 ' + ceiling_text + '，均低于 99.5%。上限还额外剔除了一条供应商成交量冲突；原目标继续按未通过报告。全国事件未全部审计，但该子集已足够证明这三个年份的目标不可达。<a href="' + escape(str(sensitivity_path.relative_to(BASE))) + '">官方证据与剔除冲突后的上限</a>；<a href="backfilled_panel_candidate_ipo_prefix_v5/gap_audit/strict_window_suspension_coverage_ceiling.json">此前供应商口径作为对照</a></p>'
    html = re.sub(r'<p class="lead"><strong>覆盖率目标存在可计算的限制。</strong>.*?</p>', lambda match: lead, html)
    html = re.sub(r'<p class="lead"><strong>官方停复牌证据已确认严格窗口目标的限制。</strong>.*?</p>', lambda match: lead, html)
    html = html.replace('覆盖率上限仍依赖供应商停牌分类，尚未取得交易所级停牌认证。', '此前供应商口径作为对照保留；下节新增沪深交易所停复牌证据与独立窗口复算，已确认 2020—2022 年原目标不可达。')
    html = html.replace('该子集尚不足以证明 99.5% 目标不可达；此前供应商状态推算的更低上限仍属条件结论。全国停牌审查尚未完成，原覆盖率分母与目标保持不变。', '仅沪市这一子集不足以证明 99.5% 目标不可达；下节合并新增深市官方证据后，已得到低于目标的保守上限。全国事件未全部审计，原覆盖率分母与目标保持不变。')
    links = Links()
    links.feed(html)
    assert all((BASE / path).exists() for path in links.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='') as file:
        csv.writer(file).writerows(rows)
    files[2].write_text(html)
    result = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(Path(__file__)),
        'files_updated': [{'path': str(file), 'sha256': sha(file)} for file in files],
        'prior_reports_snapshot': str(snapshot), 'report_local_links_checked': len(links.paths),
        'official_proven_unattainable_years': proved, 'candidate_fingerprint_unchanged': data['new_X_fingerprint'],
        'candidate_changed': False, 'new_hours_admitted': 0, 'minimum_trading_gap_unchanged': 3042,
        'nonmember_tail_gap_unchanged': 84860, 'goal_complete': False, 'production_ready': False}
    (path.parent / 'progress_recording_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

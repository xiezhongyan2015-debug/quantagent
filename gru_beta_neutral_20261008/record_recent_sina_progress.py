"""Record independently verified recent controls and a bounded Chrome probe."""
import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from record_szse_official_progress import sha, Links

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
PROBES = BASE / 'public_source_probes'
OUT = PROBES / 'original_recent_sina_control_20261009'


def main():
    now = datetime.now(timezone.utc)
    audit_path = OUT / 'recent_concordance_receipt.json'
    verify_path = OUT / 'independent_recent_findings_verification.json'
    audit, verify = [json.loads(path.read_text()) for path in [audit_path, verify_path]]
    assert verify['passed'] and verify['audited_receipt_sha256'] == sha(audit_path)
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_recent_sina_controls.py') == verify['source_code_sha256']
    assert verify['positive_control_members_and_completeness_verified'] and verify['strict_native_hours_passed'] == 0
    assert not audit['candidate_changed'] and audit['candidate_hours_admitted'] == 0
    assert len(audit['results']) == 2
    for r in audit['results']:
        m = r['original_vs_public_5m']
        assert m['exact_price_intervals_by_field']['close'] == m['volume_exact_intervals'] == 48
        assert r['original_volume_at_native_missing_labels_shares'] == 0
    browser_path = PROBES / 'eastmoney_chrome_recent_probe_20261009/observation_receipt.json'
    browser_path.parent.mkdir(exist_ok=True)
    assert not browser_path.exists()
    browser = {'recorded_utc': now.isoformat(),
        'method': 'Read-only Chrome extension UI/CDP observations; this receipt summarizes displayed tool results, not a complete raw network archive',
        'browser_control_confirmed': True,
        'public_pages': ['https://quote.eastmoney.com/sh600004.html', 'https://quote.eastmoney.com/unify/cr/1.600004'],
        'classic_page_observed_quotes': {'open': '7.70', 'high': '7.76', 'volume_display_initial': '10.42万',
            'scope': 'Current displayed quote only, not either target historical stockday'},
        'classic_five_day_selection': {'observed_active_link_after_click': '一天', 'five_day_active': False,
            'native_minute_rows_retrieved': 0},
        'new_page_five_day_control': {'observed_DOM': '<li class="dayslist"><span>5日</span><span class="daydown"></span><div class="downlist"><ul><li>分时</li><li>2日</li><li>3日</li><li>4日</li><li>5日</li></ul></div></li>',
            'native_minute_rows_retrieved': 0},
        'observed_current_trade_detail_failures': {'endpoint_path': '/api/qt/stock/details/sse',
            'error': 'net::ERR_EMPTY_RESPONSE', 'scope': 'Current trade-detail request, not evidence of a historical-minute HTTP response'},
        'single_direct_documented_minute_request': {
            'source_basis_path': str(ROOT / 'gru_research/trading_data/future_minute_probe/eastmoney_trends2_600004_20261002T022334Z/probe_one_600004.py'),
            'source_basis_sha256': sha(ROOT / 'gru_research/trading_data/future_minute_probe/eastmoney_trends2_600004_20261002T022334Z/probe_one_600004.py'),
            'url': 'https://push2his.eastmoney.com/api/qt/stock/trends2/get?fields1=f1%2Cf2%2Cf3%2Cf4%2Cf5%2Cf6%2Cf7%2Cf8%2Cf9%2Cf10%2Cf11%2Cf12%2Cf13&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57%2Cf58&ut=7eea3edcaed734bea9cbfc24409ed989&ndays=5&iscr=0&secid=1.600004',
            'public_client_constant_is_account_credential': False,
            'browser_navigation_result': 'net::ERR_BLOCKED_BY_CLIENT', 'http_status_observed': None,
            'response_body_acquired': False, 'native_minute_rows_retrieved': 0,
            'retries_after_client_block': 0},
        'CDP_response_lookup_truncated': True,
        'unobserved_response_status_not_inferred': True,
        'account_credentials_read_or_transmitted': False,
        'browser_security_or_network_settings_changed': False, 'Network_observation_disabled_at_end': True,
        'accounts_created': False, 'candidate_changed': False, 'candidate_hours_admitted': 0}
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    meta = BASE / data['latest_candidate_version'] / 'dataset/meta.json'
    assert sha(meta) == verify['candidate_meta_sha256']
    assert json.loads(meta.read_text())['data_fingerprint'] == data['new_X_fingerprint']
    assert data['minimum_requested_missing_primary_trading_hours'] == 3042
    assert data['full_union_tail_missing_nonmember_primary_trading_hours'] == 84860
    snapshot = BASE / ('progress_snapshots/recent_native_controls_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for file in files:
        shutil.copyfile(file, snapshot / file.name)
    browser_path.write_text(json.dumps(browser, ensure_ascii=False, indent=2))
    data.update({'recent_native_vs_original_positive_controls_receipt': str(audit_path.relative_to(BASE)),
        'recent_native_vs_original_independent_verification_receipt': str(verify_path.relative_to(BASE)),
        'recent_native_hour_local_receipt': str(Path(audit['sina_hour_local_receipt_path']).relative_to(BASE)),
        'Eastmoney_Chrome_recent_readonly_probe_receipt': str(browser_path.relative_to(BASE)),
        'new_source_findings_hours_admitted': 0, 'production_ready': False})
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='') as stream:
        rows = list(csv.reader(stream))
    rows.append(['近期分钟原档案对照', '600004两日原始240分钟提取及独立复算完成', '原档案96根五分钟收盘及成交量完全符合公开参考；新浪少量分钟及分钟分配冲突仍未通过；Chrome分钟接口被客户端阻止', str(verify_path.relative_to(BASE)), '既有完整股票日仅作对照；0新小时；最低缺口3042、尾段84860保持'])
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    assert '<h2>近期分钟：原档案对照与 Chrome 入口核验</h2>' not in html
    block = '<h2>近期分钟：原档案对照与 Chrome 入口核验</h2><p>完整复核原始五卷压缩包 SHA256 及 gzip 尾校验，提取白云机场 2026-09-22、09-23 两天的原始 240 分钟。两日都已在 V5 中完整且属于成员股，仅作来源质量对照，不是新增补齐。</p><p>原档案聚合后的两日共 96 根五分钟收盘价与成交量全部符合另一公开五分钟来源；全天开收盘及成交量也符合现有日线。新浪分别缺 2、4 根分钟；这些缺失标签在原档案中的成交量均为零，但新浪全天仍少 5,100、7,100 股。共同时间点的收盘精确一致为 210/238、222/236 根，成交量精确一致为 131/238、162/236 根。六个完整的新浪原生小时也没有通过严格五分钟交叉核验，所以仅填缺失分钟不能解决问题。本结论仅限该股两日，不能由此认证任一来源的逐笔交易或全市场质量。</p>'
    block += f'<p><a href="{audit_path.relative_to(BASE)}">原始分钟、原生新浪与五分钟来源对照</a>；<a href="{verify_path.relative_to(BASE)}">独立时间分组与价量复算</a>；<a href="{Path(audit["sina_hour_local_receipt_path"]).relative_to(BASE)}">原生小时核验</a></p>'
    block += f'<p>Chrome 扩展已正常操控，东方财富旧版页面能够显示当前行情；旧版及新版五日图未取得原生分钟数据。单次直接访问既有文档中的公开五日分钟接口时，浏览器返回 net::ERR_BLOCKED_BY_CLIENT，没有观察到 HTTP 状态或响应正文。没有更改浏览器安全设置，也不将此推断为网站账号或历史数据权限结论。<a href="{browser_path.relative_to(BASE)}">Chrome 只读访问观察记录</a>。本批 0 新小时纳入；最低范围仍缺 3,042 小时，并集尾段仍缺 84,860 小时。</p>'
    html = html.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    links = Links()
    links.feed(html)
    assert all((BASE / value).exists() for value in links.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(html)
    receipt = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
        'files_updated': [{'path': str(file), 'sha256': sha(file)} for file in files],
        'browser_observation_receipt_path': str(browser_path), 'browser_observation_receipt_sha256': sha(browser_path),
        'prior_reports_snapshot': str(snapshot), 'report_local_links_checked': len(links.paths),
        'candidate_fingerprint_unchanged': data['new_X_fingerprint'], 'candidate_changed': False,
        'new_hours_admitted': 0, 'minimum_trading_gap_unchanged': 3042,
        'nonmember_tail_gap_unchanged': 84860, 'goal_complete': False}
    (OUT / 'progress_recording_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

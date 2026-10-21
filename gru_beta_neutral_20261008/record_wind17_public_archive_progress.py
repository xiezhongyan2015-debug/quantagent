"""Publish independently verified archive findings without changing candidate X."""
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
SOURCE = BASE / 'public_source_probes/additional_public_archives_20261009'
WIND = SOURCE / 'wind17'


def main():
    now = datetime.now(timezone.utc)
    output = WIND / 'progress_recording_receipt.json'
    assert not output.exists()
    bp = WIND / 'independent_all_2023_gap_findings/independent_all_2023_findings_verification.json'
    lp = WIND / 'remaining_late_years/independent_late_year_findings_verification.json'
    op = WIND / 'original_300114_controls/independent_original_control_verification.json'
    broad, late, original = [json.loads(p.read_text()) for p in [bp, lp, op]]
    for report, name in [(broad, 'verify_wind17_all_2023_gap_findings.py'),
                         (late, 'verify_wind17_late_year_findings.py'),
                         (original, 'verify_wind17_original_controls.py')]:
        assert report['passed'] and sha(ROOT / 'gru_beta_neutral_20261008' / name) == report['source_code_sha256']
    assert sha(broad['audited_daily_receipt_path']) == broad['audited_daily_receipt_sha256']
    assert sha(broad['audited_hour_receipt_path']) == broad['audited_hour_receipt_sha256']
    assert sha(WIND / 'remaining_late_years/acquisition_and_daily_diagnostics_receipt.json') == late['acquisition_daily_receipt_sha256']
    assert sha(WIND / 'remaining_late_years/late_year_hour_diagnostics_receipt.json') == late['hour_receipt_sha256']
    assert sha(WIND / 'original_300114_controls/original_control_receipt.json') == original['audited_receipt_sha256']
    assert broad['missing_hours_recomputed'] == 872 and broad['daily_gate_pass_hours'] == 852
    assert broad['strict_hour_diagnostic_count'] == 3 and broad['candidate_hours_admitted'] == 0
    assert late['target_missing_hours'] == 124 and late['daily_gate_pass_hours'] == 108
    assert late['strict_diagnostic_hours'] == 0 and late['candidate_hours_admitted'] == 0
    assert original['results'][0]['cumulative_native_lots_equal_original_cumulative_lots_rounded_half_up'] == 240
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    meta = BASE / data['latest_candidate_version'] / 'dataset/meta.json'
    assert sha(meta) == broad['candidate_meta_sha256'] == late['candidate_meta_sha256']
    assert json.loads(meta.read_text())['data_fingerprint'] == data['new_X_fingerprint'] == broad['candidate_fingerprint'] == late['candidate_fingerprint']
    assert data['minimum_requested_missing_primary_trading_hours'] == 3042
    assert data['full_union_tail_missing_nonmember_primary_trading_hours'] == 84860
    snapshot = BASE / ('progress_snapshots/wind17_native_archive_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for file in files:
        shutil.copyfile(file, snapshot / file.name)
    browser_path = SOURCE / 'baidu_public_share_observation_receipt.json'
    assert not browser_path.exists()
    browser = {'recorded_utc': now.isoformat(),
               'method': 'Summary of displayed Chrome-extension browser tool result; not a raw browser or HTTP response archive',
               'source_readme_url': 'https://github.com/aitech17/A_history',
               'public_share_url': 'https://pan.baidu.com/s/1W2TMPTHLWblKy1gBwMCIEQ',
               'observed_redirect_url': 'https://pan.baidu.com/share/init',
               'observed_tool_result': 'Browser Use rejected the operation because the browser security policy blocks the site',
               'stated_policy_reason': 'site-safety policy blocks action',
               'automatic_approval_review_attempted': False, 'permission_prompt_shown': False,
               'browser_use_not_permitted_for_this_site': True, 'operations_stopped_after_rejection': True,
               'alternate_browser_or_indirect_access_attempted': False,
               'share_files_retrieved': 0, 'provider_archive_claim_verified': False,
               'independent_next_source': 'https://huggingface.co/datasets/wind17/china-a-share-zip',
               'candidate_hours_admitted': 0}
    browser_path.write_text(json.dumps(browser, ensure_ascii=False, indent=2))
    archive_path = WIND / 'verified_whole_2023_archive/archive_acquisition_receipt.json'
    archive = json.loads(archive_path.read_text())
    inspection_path = SOURCE / 'inspection_receipt.json'
    metadata = json.loads((WIND / 'dataset_info_blobs.json').read_text())
    assert metadata['sha'] == 'ca4c51770f1fafb40317cc35bfd92e7c2be17827'
    assert archive['whole_LFS_SHA256_verified'] and archive['expected_whole_LFS_SHA256'] == broad['whole_archive_fresh_sha256']
    references = {'metadata_inspection': inspection_path,
                  'whole_2023_archive': archive_path,
                  'all_2023_daily_checks': Path(broad['audited_daily_receipt_path']),
                  'all_2023_hour_checks': Path(broad['audited_hour_receipt_path']),
                  'all_2023_independent_verification': bp,
                  'late_year_independent_verification': lp,
                  'original_quantity_controls': WIND / 'original_300114_controls/original_control_receipt.json',
                  'original_quantity_controls_independent_verification': op,
                  'baidu_browser_observation': browser_path}
    data['wind17_new_public_archive_review'] = {
        'source': 'https://huggingface.co/datasets/wind17/china-a-share-zip',
        'pinned_revision': metadata['sha'], 'anonymous_ungated_source': True,
        'whole_2023_archive_bytes': 3644873876, 'whole_2023_archive_SHA256': broad['whole_archive_fresh_sha256'],
        'whole_2023_archive_SHA256_verified': True,
        'all_2023_target_stocks': 29, 'all_2023_target_stockdays': 218,
        'all_2023_target_missing_hours': 872, 'all_2023_daily_gate_pass_hours': 852,
        'all_2023_native_amount_pass_hours_on_daily_pass_days': 58,
        'all_2023_strict_diagnostic_only_hours': 3,
        'late_year_target_stockyears': 16, 'late_year_target_stockdays': 31,
        'late_year_target_missing_hours': 124, 'late_year_daily_gate_pass_hours': 108,
        'late_year_strict_diagnostic_hours': 0,
        'other_year_whole_archives_SHA256_verified': False,
        '2026_observed_two_sample_stockfiles_last_date': '2026-02-27',
        '2026_whole_archive_end_date_certified': False,
        'source_minute_quantity_precision_certified': False,
        'original_control_dates': ['2023-12-11', '2023-12-12'],
        'control_20231211_cumulative_lot_rounding_match_points': 240,
        'new_hours_admitted': 0, 'candidate_changed': False,
        'production_ready': False,
        'receipts': {k: {'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for k, p in references.items()}}
    data['new_source_findings_hours_admitted'] = 0
    data['production_ready'] = False
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    assert isinstance(manifest['required_work_remaining'], list)
    manifest['required_work_remaining'].append('新增 wind17 原生档案核验未减少缺口：2023 年 872 小时及 2024—2026 年 124 小时已独立复算，仅 3 小时保留为严格诊断；分钟股数精度与来源语义仍未完成准入。两只 2026 样本文件只到 2 月 27 日，不能据此填 8—9 月尾段。')
    with files[1].open(newline='') as stream:
        rows = list(csv.reader(stream))
    rows.append(['新增公开年度原生档案', '2023整卷与29只缺口股独立核验完成', '3.64GB整卷SHA256通过；872小时中852日线通过，3严格诊断；0新增纳入', str(bp.relative_to(BASE)), '最低缺口3042、尾段84860保持'])
    rows.append(['2024—2026缺口源核验', '16股票年份、31股票日独立复算完成', '124小时中108日线通过，0严格诊断；2026两只样本仅到2月27日', str(lp.relative_to(BASE)), '逐分钟与五分钟冲突未消除；0新增纳入'])
    rows.append(['新档案分钟量精度对照', '航天智造两日原档案独立核对完成', '2023-12-11累计量240点符合按手取整；不能由全天一致推断真实逐分钟股数', str(op.relative_to(BASE)), '保留原始数据，未生成缺失股数'])
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    assert '<h2>新增公开年度档案：原生分钟仍须通过质量核验</h2>' not in html
    block = '<h2>新增公开年度档案：原生分钟仍须通过质量核验</h2><p>找到无需登录的 wind17/china-a-share-zip 原生年度 ZIP 档案，固定版本 ca4c51770f1fafb40317cc35bfd92e7c2be17827。2023.zip 完整下载 3,644,873,876 字节，整卷 SHA256 与发布元数据相符，选取的 29 只缺口股票 CSV 均重新解压并验证 CRC。其他年份只获取选定文件的压缩字节范围并校验 CRC，没有将局部检查表述为整卷 SHA256 通过。</p>'
    block += '<table><thead><tr><th>缺口范围</th><th>原有缺失小时</th><th>日线及网格通过</th><th>分钟价量通过</th><th>与五分钟同时通过的诊断小时</th><th>新增纳入</th></tr></thead><tbody><tr><td>2023：29股、218股票日</td><td>872</td><td>852</td><td>58</td><td>3</td><td>0</td></tr><tr><td>2024—2026：16股票年份、31股票日</td><td>124</td><td>108</td><td>1</td><td>0</td><td>0</td></tr></tbody></table>'
    block += '<p>表中“分钟价量通过”仅统计已通过日线检查的股票日。3 个严格诊断小时均为航天智造 2023-06-19、06-21、06-29 的第一小时，尚未纳入候选面板。日线一致并不代表分钟分配一致；大量小时的成交额、报价区间与按手换算的成交量冲突，或不能与独立五分钟参考一致。五分钟数据只作核验，没有展开为一分钟。以上 249 股票日、996 缺失小时已独立复算原始字节、当前缺口掩码、精确十进制算术及实际五分钟响应。</p>'
    block += '<p>进一步以原档案中已完整的航天智造 2023-12-11、12-12 作对照：新档案全天量分别只差 −12、+15 股，累计量按手四舍五入与原始累计股数相符分别为 240/240、239/240 点；逐分钟股数精度和部分报价仍不同。此结论仅限该股两日，不能推广为全市场生产认证，也没有据此反推缺失股数。已检查的白云机场、正元地信两个 2026 文件均止于 2 月 27 日，未提供目标 8—9 月尾段，不据此认证整卷最后日期。最低范围仍缺 <strong>3,042</strong> 小时，并集非成员尾段仍缺 <strong>84,860</strong> 小时；V5 指纹及原有验收门槛保持。</p>'
    block += '<p>' + '；'.join(f'<a href="{p.relative_to(BASE)}">{label}</a>' for label, p in [
        ('来源元数据调查', inspection_path), ('2023整卷核验', archive_path), ('2023独立缺口复算', bp),
        ('2024—2026独立复算', lp), ('两日原档案价量对照', references['original_quantity_controls']),
        ('原档案精度独立复算', op)]) + '</p>'
    block += f'<p>Chrome 扩展访问公开百度网盘分享页时，浏览器工具因 site-safety policy blocks action 拒绝该入口；没有弹出用户许可请求，也没有进行自动审批审查。该入口已停止操作，未通过其他浏览器或间接途径绕过。随后使用的是独立公开的 Hugging Face 档案。另查到的 alphat04 逐笔订单档案需要访问审核，实际元数据只覆盖 2026-01-05 至 06-05，尚未取得文件，也不能直接解决 8—9 月尾段。<a href="{browser_path.relative_to(BASE)}">浏览器策略拒绝观察记录</a></p>'
    html = html.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    links = Links()
    links.feed(html)
    assert all((BASE / value).exists() for value in links.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(html)
    receipt = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
               'files_updated': [{'path': str(p), 'sha256': sha(p)} for p in files],
               'prior_reports_snapshot': str(snapshot), 'local_report_links_checked': len(links.paths),
               'candidate_fingerprint_unchanged': data['new_X_fingerprint'], 'candidate_changed': False,
               'new_hours_admitted': 0, 'minimum_trading_gap_unchanged': 3042,
               'nonmember_tail_gap_unchanged': 84860, 'goal_complete': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

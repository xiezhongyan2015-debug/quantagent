"""Publish completed whole-source proofs, preserving all older snapshots/gates."""
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
WHOLE = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths.extend(v for k, v in attrs if k == 'href' and not v.startswith(('http:', 'https:', '#')))


def main():
    output = WHOLE / 'whole_source_completion_progress_receipt.json'
    assert not output.exists()
    paths = {'archive': WHOLE / 'complete_archive_acquisition.json',
             'native_members': WHOLE / 'full_union_day_native_members/complete_native_member_acquisition.json',
             'native_members_independent': WHOLE / 'full_union_day_native_members/independent_complete_native_member_verification.json',
             'original_controls_extraction': WHOLE / 'all_original_day_controls/extraction_receipt.json',
             'original_controls_audit': WHOLE / 'all_original_day_controls/all_original_control_reference_audit.json',
             'original_controls_independent': WHOLE / 'all_original_day_controls/independent_all_original_control_verification.json',
             'phase_diagnostic': WHOLE / 'quote_phase_control_audit/all_499_native_quote_phase_control_diagnostics.json',
             'phase_independent': WHOLE / 'quote_phase_control_audit/independent_phase_and_fixed_venue_boundary_verification.json',
             'full_union_prefix_audit': WHOLE / 'full_union_trade_prefix_audit/full_union_native_trade_prefix_diagnostics.json',
             'full_union_prefix_independent': WHOLE / 'full_union_trade_prefix_audit/independent_full_union_trade_prefix_verification.json',
             'known_control_range_audit': WHOLE / 'known_control_quote_range_and_session_end/known_original_quote_ranges_and_fixed_session_end_diagnostics.json',
             'known_control_range_independent': WHOLE / 'known_control_quote_range_and_session_end/independent_known_control_quote_range_verification.json'}
    receipts = {k: json.loads(p.read_text()) for k, p in paths.items()}
    archive, native, native_proof, original, original_proof, phase_proof, prefix, prefix_proof, ranges, range_proof = [receipts[k] for k in
        ['archive', 'native_members', 'native_members_independent', 'original_controls_audit', 'original_controls_independent', 'phase_independent',
         'full_union_prefix_audit', 'full_union_prefix_independent', 'known_control_range_audit', 'known_control_range_independent']]
    for proof in [native_proof, original_proof, phase_proof, prefix_proof, range_proof]:
        assert proof['passed'] and proof['candidate_hours_admitted'] == 0 and not proof['production_ready']
    assert archive['status'] == 'complete_whole_archive_SHA256_and_gzip_CRC_verified'
    assert archive['whole_published_LFS_SHA256_verified'] and archive['whole_gzip_CRC_verified'] and archive['whole_gzip_EOF_observed']
    assert native['complete_archive_receipt_sha256'] == sha(paths['archive'])
    assert native_proof['native_member_acquisition_receipt_sha256'] == sha(paths['native_members'])
    assert original_proof['audited_reference_receipt_sha256'] == sha(paths['original_controls_audit'])
    assert phase_proof['audited_phase_diagnostic_receipt_sha256'] == sha(paths['phase_diagnostic'])
    assert prefix_proof['audited_full_union_trade_prefix_receipt_sha256'] == sha(paths['full_union_prefix_audit'])
    assert range_proof['audited_known_original_range_receipt_sha256'] == sha(paths['known_control_range_audit'])
    assert prefix['complete_native_pairs_audited'] == prefix_proof['all_complete_native_pairs_independently_verified'] == 1123
    assert prefix['actual_missing_target_stockdays_audited'] == native['actual_missing_stockdays_with_complete_source_pairs'] == 624
    assert prefix_proof['all_positive_quote_prefix_witnesses_verified'] == 5206443 and prefix_proof['daily_all_six_fields_exact_stockdays'] == 1123
    assert ranges['fixed_quote_session_end_rule_all_240_minutes_exact_stockdays'] == range_proof['fixed_quote_session_end_all_240_exact_stockdays'] == 357
    assert ranges['known_original_minutes_failing_unchanged_actual_amount_price_range_gate'] == range_proof['known_quote_minutes_rejected_by_unchanged_actual_amount_range_gate'] == 2326
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    meta = json.loads(meta_path.read_text())
    assert sha(meta_path) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    assert meta['data_fingerprint'] == '25682d83bc5ec34d219b3155c0fd1df14de26956c328392f03904e45f4128bec'
    assert sha(meta_path.parent / 'hour_complete.npy') == '56b016d3a33d5c3a4c640de156ea2e716e16c304c7b307879c34810cc095eb59'
    expected = {'execution_manifest.json': 'fa90daa9b318d1e79210c072e335be4383d5a94f18b609e362013fbffd0569bb',
                '执行进度_2026-10-08.csv': '4af2e0bbadc3eae01bdcaba4919766b3dcb5b3f0290588c2e8410d7b6ba1ff12',
                '数据补齐核验与实验进度_2026-10-09.html': '37da9bb3612ecc81f422d3496cd3fae2eda6cd926203bd859319afce420607c1'}
    files = [BASE / name for name in expected]
    assert all(sha(p) == expected[p.name] for p in files)
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/whole_native_complete_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for p in files:
        shutil.copyfile(p, snapshot / p.name)
    provenance = {k: {'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for k, p in paths.items()}
    review = {'scope_date': '2026-09-23', 'repository': archive['repository'], 'pinned_revision': archive['pinned_revision'],
              'complete_archive_published_bytes': 15803513411, 'whole_published_LFS_SHA256_and_gzip_CRC_verified': True,
              'full_historical_union_requested_symbols': 1178, 'complete_native_event_snapshot_stockdays': 1123,
              'retained_native_members': len(native['records']), 'retained_native_member_bytes': native['written_bytes'],
              'current_actual_missing_stockdays_with_complete_native_pairs': 624, 'current_actual_missing_hours_with_complete_native_pairs': 2496,
              'all_original_complete_stockday_controls_verified': 499, 'all_original_native_minutes_verified': 119760,
              'regular_actual_trade_rows_verified': 40588594, 'all_positive_source_quote_whole_actual_trade_prefixes_verified': 5206443,
              'all_1123_native_daily_OHLC_V_A_exact_retained_primary_daily': True,
              'all_119760_original_cum_qty_and_closes_exact_whole_actual_trade_cutpoints': True,
              'all_119760_original_cum_qty_and_closes_match_some_actual_source_quote': True,
              'quotes_last_included_actual_trade_event_after_reported_quote_time': 1222680,
              'maximum_last_actual_trade_after_reported_quote_ms': 1490,
              'prior_boundary_rule_complete_original_control_stockdays': 146,
              'afternoon_opening_rule_complete_original_control_stockdays': 234,
              'fixed_venue_boundary_rule_complete_original_control_stockdays': 287,
              'fixed_venue_and_corroborated_morning_close_rule_complete_original_control_stockdays': 357,
              'final_fixed_rule_all_five_fields_exact_original_minutes': 95907,
              'known_original_quote_minutes_rejected_by_unchanged_actual_prefix_amount_range_gate': 2326,
              'known_original_five_minute_quote_aggregates_rejected_by_unchanged_actual_prefix_amount_range_gate': 26,
              'range_counterexample_does_not_qualify_historical_precision_or_rewrite_original_gate_results': True,
              'original_minute_amount_not_available_and_not_created': True,
              'no_per_stock_or_minute_favorable_rule_choice_or_quantity_inference': True,
              'full_requested_2018_2026_historical_range_complete': False, 'strict_nominal_signal_PIT_or_source_independence_certified': False,
              'all_3042_minimum_and_84860_tail_gaps_still_remaining': True, 'proofs': provenance,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    manifest = json.loads(files[0].read_text()); data = manifest['data_backfill']
    assert data['new_source_findings_hours_admitted'] == 8
    archive_record = data['complete_native_event_archive_acquisition']
    assert archive_record['status_at_capture'] == 'acquiring_complete_archive'
    archive_record['current_status'] = archive['status']
    archive_record['current_whole_archive_SHA256_and_gzip_CRC_verified'] = True
    archive_record['actual_completed_extractor_path'] = 'gru_beta_neutral_20261008/extract_complete_archive_full_union_day.py'
    archive_record['actual_completed_extractor_sha256'] = native['source_code_sha256']
    archive_record['actual_completed_scope_requested_union_symbols'] = 1178
    archive_record['actual_completed_controls'] = 499
    archive_record['completion_and_full_union_audit'] = review
    data['whole_native_source_control_and_clock_review'] = review
    data['native_quote_boundary_review']['whole_archive_false_flags_describe_prior_small_sample_stage'] = True
    data['native_quote_boundary_review']['whole_source_followup_proof'] = provenance['full_union_prefix_independent']
    manifest['latest_local_updated_utc'] = manifest['checked_utc'] = now.isoformat()
    manifest['required_work_remaining'][0] = 'Resolve all 84860 actual nonmember primary-trading tail hours. Whole Sep23 public archive15,803,513,411bytes is SHA256/gzipCRC certified; all1178 union requested,1123 complete pairs extracted including all624 missing stockdays/2496hours and499 original controls. All40,588,594 regular trades/5,206,443 positive quotes and1123 daily OHLCVA independently verified. Final fixed quote boundary/session-end rule reproduces357/499 controls; remaining minute definitions and PIT/source admission unresolved. No new hours admitted; Sep23 remains a subset of whole2018–2026 scope.'
    manifest['required_work_remaining'][1] += ' Whole Sep23 known-control audit also provides2,326 real quote-minute and26 aggregated quote-5m counterexamples to the unchanged actual-turnover-within-quote-OHLC test. Preserve original failed gate results; assess historical OHLC/amount source semantics and quantity precision using real source evidence rather than automatically declaring all those failures impossible trades. This evidence has not qualified historical source quantities or admitted any of the3042 minimum gaps.'
    manifest['required_work_remaining'] = [r for r in manifest['required_work_remaining'] if not r.startswith('已启动固定版本9月23日整份')]
    manifest['required_work_remaining'].append('整卷完整性与499对照来源均已完成独立核验；继续处理并集2018—2026完整分钟历史、历史股数精度、统一来源时间定义，以及624个该日真实缺失股票日的研究准入。报价标注时间可早于所含最后成交事件1.49秒；不能将原生时间字段直接认证为整点已知或实盘已就绪。')
    with files[1].open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    for row in rows:
        if row[0] == '9月23日完整公开事件档案':
            row[1:] = ['整卷SHA256与gzip CRC认证、完整并集当日成员提取及独立复算完成',
                       '1178并集全请求；1123完整成交/报价对；覆盖624缺失股票日2496小时及499完整对照',
                       provenance['native_members_independent']['path'], '当日源文件取得不等于分钟准入；全历史与3042/84860缺口仍保留；0新小时']
    rows.extend([
        ['完整当日逐笔与报价核验', '全部1123股票日独立复算通过', '40588594实际成交、5206443正股数报价全对应；1123日线六字段全部一致', provenance['full_union_prefix_independent']['path'], '1222680报价所含末笔事件晚于标注时刻，最大1490毫秒；时钟同步与整点PIT未认证'],
        ['全部已有分钟正对照', '499股票日119760原始分钟重新取得并认证', '原始分卷整卷指纹/CRC与原始分钟逐字段复算；所有累计股数和收盘价匹配实际成交及真实报价', provenance['original_controls_independent']['path'], '原始分钟成交额缺失，未创建或重构该字段'],
        ['统一报价与两段收盘规则', '固定规则诊断及独立复算完成', '357/499完整复现；95907/119760分钟五字段全一致', provenance['known_control_range_independent']['path'], '未逐股逐分钟挑选规则；其余差异仍保留，0新增准入'],
        ['报价高低价量额门槛反例', '2326分钟与26五分钟已知对照反例独立复算完成', '真实未拆分成交前缀的量额可超出原报价高低价范围加减1元', provenance['known_control_range_independent']['path'], '保留历史失败结果和分母；须核实历史报价/量额定义及股数精度，不据此放行缺口']])
    page = files[2].read_text(); assert 'id="whole-native-source-complete"' not in page
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    block = '<h2 id="whole-native-source-complete">最新：整卷与完整当日并集核验已完成</h2>'
    block += '<p>9 月 23 日完整公开成交与报价档案共 15,803,513,411 字节，发布 SHA256、全卷 gzip CRC 与 EOF 均通过。按完整 1,178 股并集逐一取出 2,247 个原始成员，共 4,028,940,185 字节；1,123 个股票日具有完整成交与报价对，包含全部 624 个实际缺失股票日（2,496 小时）及全部 499 个已有分钟对照。其余 55 只当日无完整成员对的并集股票逐一保留清单；不能据当日缺席补造历史成交。</p>'
    block += '<p>独立复算验证 40,588,594 笔正常交易时段真实成交、5,206,443 条正成交量报价。每条报价累计股数、最新价和累计量额，都恰好对应真实完整成交前缀；1,123 个股票日的开高低收、股数、量额均与保存的主日线逐字段一致，没有拆分成交、倒推股数或按价格反推数量。</p>'
    block += '<p>499 个已有股票日从原始五段完整档案再次提取并认证，共 119,760 根真正的原始分钟。所有累计成交股数及收盘价都对应真实成交前缀，并能在保留的真实报价中找到；原始分钟没有成交额字段，本次未创建该字段。现有报价采样高低价不能当作逐笔成交的完整极值。</p>'
    block += '<table><thead><tr><th>对全部 499 个原始股票日采用的固定规则</th><th>240 根分钟五字段全相同的股票日</th></tr></thead><tbody><tr><td>前边界开盘与明确收盘报价</td><td>146／499</td></tr><tr><td>增加真实午后第一条报价开盘</td><td>234／499</td></tr><tr><td>沪市左闭、深市右闭的分钟边界</td><td>287／499</td></tr><tr><td>再增加真实上午完整成交所证实的午间收盘报价</td><td>357／499</td></tr></tbody></table>'
    block += '<p>最终固定诊断规则有 95,907／119,760 根分钟的开高低收、成交量五字段全一致；没有逐股或逐分钟选择有利定义。其他差异仍需要解释。1,222,680 条报价包含的最后成交事件时间晚于其标注时间，最大 1,490 毫秒；报价时间、实际成交时间和收到时间分别保留，未认证严格整点已知、时钟同步或实盘延迟。</p>'
    block += '<p><strong>新发现：报价型高低价的量额检查存在真实反例。</strong>按已知原始成交量对应的真实完整成交前缀计算量额，在完全不改动“低价×股数−1 元至高价×股数＋1 元”条件下，2,326 根已认证原分钟、26 个原始五分钟汇总仍被该条件拒绝。原因可能是报价采样遗漏实际成交价格。此处量额属于实际成交前缀诊断，不冒充原分钟字段；历史来源的原门槛失败结果与分母保留，须进一步核实字段定义和数量精度，不能据此直接放行历史缺口。</p>'
    block += '<p>完整当日取得与认证已有进展，但本轮新增训练面板小时仍为 0。第六版指纹保持；最低缺口仍为 3,042，完整并集非成分尾段仍为 84,860。1,178 股 2018—2026 完整历史、统一分钟来源定义、历史数量精度及原覆盖率验收仍属于原任务，尚未完成。</p><p>'
    labels = {'archive': '完整公开档案认证', 'native_members_independent': '全部并集成员字节独立核验',
              'original_controls_independent': '499 原分钟对照独立认证', 'full_union_prefix_independent': '全部成交与报价独立复算',
              'phase_independent': '全部固定报价规则复算', 'known_control_range_independent': '午间规则及量额反例独立复算'}
    block += '；'.join('<a href="' + provenance[k]['path'] + '">' + label + '</a>' for k, label in labels.items()) + '</p>'
    stamp_end = page.index('</p>', page.index('<p class="stamp">')) + len('</p>')
    page = page[:stamp_end] + block + page[stamp_end:]
    page = page.replace('<strong>公开分钟来源尾段不足。</strong>', '<strong>原有分钟来源尾段不足，新增逐笔来源尚待分钟准入。</strong>新增 9 月 23 日完整成交／报价已认证，见本页最新核验；该日尚未填入面板。')
    page = page.replace('<h2 id="whole-native-archive-start">整份公开档案核验已启动</h2>', '<h2 id="whole-native-archive-start">整份档案的启动记录（后续整卷核验已完成）</h2>')
    page = page.replace('当前记录为正在取数，整卷发布 SHA256 与 gzip CRC 尚未通过；运行记录会继续变化，不能把启动快照当作整卷认证。', '启动时记录为正在取数，当时整卷发布 SHA256 与 gzip CRC 尚未通过；现已完成整卷认证及完整当日并集核验，见本页最新核验。启动快照保留为历史记录。')
    page = page.replace('整卷核验后，按当前第六版真实缺失掩码逐一提取该日 624 个缺失股票日、2,496 个缺失小时及两只正对照的完整成交事件与报价成员。', '原启动计划提取该日 624 个缺失股票日、2,496 个缺失小时及两只正对照；实际完成时已扩展为整个 1,178 股并集及全部 499 个已有分钟对照。')
    page = page.replace('整份远端事件档案的发布指纹与 gzip CRC、跨来源独立性、统一报价定义与严格时点准入仍未完成；', '在该两股样本阶段，整份远端事件档案发布指纹与 gzip CRC 尚未完成；本次整卷认证已完成，跨来源独立性、统一报价定义与严格时点准入仍未完成；')
    parser = Links(); parser.feed(page)
    assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    files[2].write_text(page)
    assert sha(meta_path) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    result = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__), 'prior_reports_snapshot': str(snapshot),
              'completed_proofs': provenance, 'files_updated': [{'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for p in files],
              'local_links_checked': len(parser.paths), 'candidate_meta_sha256': sha(meta_path),
              'full_original_scope_preserved': True, 'candidate_hours_admitted': 0, 'goal_complete': False, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'completed_proofs'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

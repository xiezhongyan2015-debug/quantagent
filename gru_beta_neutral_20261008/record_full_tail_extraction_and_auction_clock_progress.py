"""Publish actual full-tail payload progress and independently verified clocks."""
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/phields_trades_20261009'
EXTRACT = SOURCE / 'full_actual_missing_tail_trade_payloads'
PROFILE = EXTRACT / 'fixed_all_stock_auction_second_profiles_20261009T134450Z'


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
    op = PROFILE / 'full_tail_extraction_and_auction_clock_progress_receipt.json'
    assert not op.exists()
    proofp = PROFILE / 'independent_fixed_auction_clock_and_native_subset_verification.json'
    proof = json.loads(proofp.read_text()); assert proof['passed']
    diagnosticp = PROFILE / 'fixed_all_stock_auction_second_diagnostics.json'
    assert sha(diagnosticp) == proof['audited_fixed_auction_clock_diagnostics_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_tail_auction_clock_diagnostics.py') == proof['source_code_sha256']
    assert proof['all_captured_target_stockdays_independently_profiled'] == 3120 and proof['actual_absent_payload_stockdays'] == 136
    assert proof['all_fixed_uniform_clock_profile_counts_reproduced']['uniform_2s_all_six_exact'] == 2984
    expected = {'execution_manifest.json': 'd89fade030bac260598ec16e78511f15fd33806771ddc4eee28129009f808716',
                '执行进度_2026-10-08.csv': '19f7da6954cd1df3071f51df4b0337415b7947c8c54dda1f8c2529af3b5d6bd5',
                '数据补齐核验与实验进度_2026-10-09.html': 'bd3e18ff9acf2c652d90081b45975919dba05d5426637795f88aa6edd4eb9d46'}
    files = [BASE / name for name in expected]; assert all(sha(p) == expected[p.name] for p in files)
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/full_tail_extraction_and_auction_clocks_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for p in files:
        shutil.copyfile(p, snapshot / p.name)
    paths = {'acquisition': SOURCE / 'all_actual_missing_tail_partitions/all_actual_missing_tail_partition_acquisition.json',
             'extraction': EXTRACT / 'full_actual_missing_tail_trade_payload_extraction.json'}
    captures = {}; current = {}
    for name, p in paths.items():
        raw = p.read_bytes(); obj = json.loads(raw); current[name] = obj
        dest = snapshot / (name + '_at_capture.json'); dest.write_bytes(raw)
        captures[name] = {'path': str(dest.relative_to(BASE)), 'sha256': sha(dest)}
    acquisition, extraction = current['acquisition'], current['extraction']
    assert acquisition['all_needed_published_partitions_requested'] == 226 and extraction['all_actual_missing_tail_stockdays_requested'] == 21215
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_all_phields_actual_missing_tail_trades.py') == extraction['source_code_sha256']
    mp = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(mp) == extraction['candidate_meta_sha256']
    review = {'full_original_missing_tail_stockdays_requested': 21215, 'full_original_missing_tail_hours_requested': 84860,
              'live_extractor_session_id': 35154, 'live_acquisition_session_id': 11354,
              'source_code_path': 'gru_beta_neutral_20261008/extract_all_phields_actual_missing_tail_trades.py',
              'source_code_sha256': extraction['source_code_sha256'], 'mutable_current_extraction_path': str(paths['extraction'].relative_to(BASE)),
              'complete_source_partitions_processed_at_capture': extraction['complete_source_partitions_processed'],
              'native_actual_trade_rows_retained_at_capture': extraction['native_actual_trade_rows_retained'],
              'stockdays_full_published_parts_processed_at_capture': extraction['requested_stockdays_all_published_parts_processed'],
              'whole_file_certified_bytes_at_capture': acquisition['complete_published_bytes'],
              'whole_file_certified_partitions_at_capture': acquisition['complete_published_partitions'],
              'clock_diagnostic_scope_is_intermediate_full_task_not_reduced_objective': True,
              'stockdays_all_clock_profiles_independently_verified': 3120, 'uniform_clock_profile_counts': proof['all_fixed_uniform_clock_profile_counts_reproduced'],
              'all_source_present_2984_stockdays_uniform_2s_and_5s_daily_six_fields_exact': True,
              'actual_absent_payload_stockdays': 136, 'actual_absent_payload_hours': 544,
              'full_source_partitions_independently_scanned_for_this_clock_study': proof['all_full_source_partitions_fresh_SHA256_and_all_row_groups_scanned'],
              'native_subset_rows_independently_identical_full_source': proof['native_core_subset_rows_identical_to_full_source'],
              'clock_profile_not_minute_definition_PIT_or_panel_admission': True,
              'original_precise_clock_gate_results_and_denominators_preserved': True,
              'native_prices_quantity_and_events_not_changed_or_inferred': True,
              'current_dynamic_captures': captures, 'clock_independent_proof': {'path': str(proofp.relative_to(BASE)), 'sha256': sha(proofp)},
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    manifest = json.loads(files[0].read_text()); data = manifest['data_backfill']
    assert data['new_source_findings_hours_admitted'] == 8
    data['full_actual_missing_tail_trade_payload_extraction'] = review
    data['full_actual_missing_tail_trade_acquisition']['actual_extraction_followup'] = review
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    manifest['required_work_remaining'].append('完整尾段实际成交提取已随整文件下载推进。首批3,120股票日的全部固定竞价时间窗口已独立复算；有原始成交的2,984日统一2秒/5秒窗口均与主日线六字段精确一致，136日实无原始成交，对应544小时。原精确时刻诊断未改写；这解决当批总量核验的竞价排除原因，不代替分钟来源时钟、可用时间、全2018—2026范围及原门槛验收，0新增面板小时。')
    with files[1].open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    rows.extend([
        ['全尾段真实成交提取', '全部21215目标股票日逐步处理，提取进程运行中', f"已处理{extraction['complete_source_partitions_processed']}完整文件、保留{extraction['native_actual_trade_rows_retained']}实际源成交；快照状态非最终总数", str(paths['extraction'].relative_to(BASE)), '全部来源字段原样保留，含交易时段外记录；不生成或推断分钟价格股数，0准入'],
        ['统一竞价源时钟诊断', '3120股票日与全部37源文件独立复算完成', '原精确窗口1908日；统一1秒2901日；统一2秒/5秒各2984日六字段精确；136日实际无源成交', str(proofp.relative_to(BASE)), '不逐股挑规则；完整原记录、精确窗口失败结果和分母均保留；时钟诊断不是分钟准入']])
    page = files[2].read_text(); assert 'id="full-tail-actual-extraction"' not in page
    stamp = now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M')
    block = '<h2 id="full-tail-actual-extraction">最新：完整尾段实际成交正在提取，竞价时间差已定位</h2>'
    block += f"<p>{stamp} 保存快照：226个完整源文件中，{acquisition['complete_published_partitions']}个通过发布指纹与完整格式核验，共{acquisition['complete_published_bytes']:,}字节。提取已处理{extraction['complete_source_partitions_processed']}个完整源文件，原样保存{extraction['native_actual_trade_rows_retained']:,}条目标股票真实成交。后续文件仍在取得；当前状态以动态记录为准。</p>"
    block += '<p>实际记录中有部分集合竞价成交记在09:25:01、15:00:01和15:00:02。精确整秒窗口会将这些真实成交排除。对首批全部3,120个目标股票日统一测试1、2、5秒竞价窗口，不按股票选择有利口径：原精确窗口有1,908日六字段精确；统一1秒有2,901日；统一2秒与5秒均有2,984日。后两项覆盖全部有源成交的股票日，开高低收、股数和真实价格乘股数所得量额均精确对应主日线。余下136日原始成交确实缺席，对应544小时。</p>'
    block += '<p>独立重新读取37个完整原文件、核对发布SHA256并扫描全部行组，保存的核心成交子集与源文件逐字段一致。所有原始数量、价格、竞价与盘后成交均保留；没有拆分、倒推股数或创造报价。竞价时间诊断解释了该批总量差异，原精确窗口与原验收失败结果继续保留。该发现未认证每根分钟的定义、收到时间、PIT或实盘条件，仍新增0个训练面板小时。</p>'
    block += '<p>全部21,215尾段目标股票日与84,860缺失小时继续处理；1,178股2018—2026完整分钟及日线、最低3,042小时缺口与原覆盖率要求继续保留。</p><p>'
    block += '<a href="' + str(proofp.relative_to(BASE)) + '">固定竞价窗口及完整原成交子集独立复核</a>；<a href="' + str(paths['extraction'].relative_to(BASE)) + '">全部尾段动态提取记录</a></p>'
    end = page.index('</p>', page.index('<p class="stamp">')) + 4
    page = page[:end] + block + page[end:]
    parser = Links(); parser.feed(page); assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    files[2].write_text(page)
    assert sha(mp) == extraction['candidate_meta_sha256']
    result = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__), 'prior_reports_snapshot': str(snapshot),
              'source_progress_captures': captures, 'independent_clock_proof_sha256': sha(proofp), 'local_links_checked': len(parser.paths),
              'files_updated': [{'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for p in files],
              'full_original_task_scope_retained': True, 'candidate_hours_admitted': 0, 'goal_complete': False, 'production_ready': False}
    op.write_text(json.dumps(result, ensure_ascii=False, indent=2)); print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

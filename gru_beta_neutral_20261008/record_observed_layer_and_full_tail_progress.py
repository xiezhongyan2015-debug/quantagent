"""Publish verified whole-day observation layer and actual full-tail acquisition.

Snapshots preserve previous gates, denominators, source failures and V6 arrays.
"""
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
PUBLIC = BASE / 'public_source_probes'
WHOLE = PUBLIC / 'additional_native_inventory_20261009/whole_event_archive'
LAYER = WHOLE / 'full_union_observed_quote_minute_layer_v2'


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
    op = LAYER / 'observed_layer_and_full_tail_progress_receipt.json'
    assert not op.exists()
    lp = LAYER / 'full_union_observed_quote_minute_layer_receipt.json'
    vp = LAYER / 'independent_full_union_observed_quote_minute_verification.json'
    sp = PUBLIC / 'documented_edb_history_20261009/independent_edb_and_unchecked_coarse_followup_verification.json'
    proof = json.loads(vp.read_text()); source_proof = json.loads(sp.read_text())
    assert proof['passed'] and source_proof['passed']
    assert proof['audited_observed_minute_layer_receipt_sha256'] == sha(lp)
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_full_union_observed_quote_minutes.py') == proof['source_code_sha256']
    layer = json.loads(lp.read_text())
    assert layer['all_complete_source_pairs_processed'] == 1123 and layer['actual_missing_target_stockdays_in_layer'] == 624
    assert proof['minute_grid_rows_verified'] == 269520 and proof['actual_observed_quote_candle_minutes_verified'] == 269513
    assert proof['actual_missing_target_hours_before_recorded_execution'] == 2491 and proof['actual_missing_target_hours_ready_by_nominal_signal_end'] == 667
    assert len(proof['actual_missing_target_incomplete_hours']) == 5
    edbp = PUBLIC / 'documented_edb_history_20261009/anonymous_documented_history_probe.json'
    coarsep = PUBLIC / 'additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/all_unchecked_coarse_followup/all_unchecked_native_eligible_hour_coarse_acquisition.json'
    assert sha(edbp) == source_proof['documented_anonymous_route_receipt_sha256']
    assert sha(coarsep) == source_proof['all_unchecked_coarse_acquisition_receipt_sha256']
    assert source_proof['actual_native_CSV_rows_futures_SH_stock_SZ_stock'] == [11, 0, 0]
    mp = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(mp) == proof['current_candidate_meta_sha256']
    expected = {'execution_manifest.json': '48ee2fc7e181fb74b0b58a049927f60e5b65802bdff4a6161e6cf602826324a8',
                '执行进度_2026-10-08.csv': 'dc9666df4dbdadedf21ac0b574746f029967d5589054e1be518e7bca934bfc71',
                '数据补齐核验与实验进度_2026-10-09.html': '0b1d65af2793688021c49ff6024af990e9bd1fe4c8fe6b482f6db9d2038ebbae'}
    files = [BASE / x for x in expected]
    assert all(sha(p) == expected[p.name] for p in files)
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/observed_layer_and_full_tail_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for p in files:
        shutil.copyfile(p, snapshot / p.name)
    acqp = PUBLIC / 'phields_trades_20261009/all_actual_missing_tail_partitions/all_actual_missing_tail_partition_acquisition.json'
    captured = acqp.read_bytes()
    acq = json.loads(captured)
    assert acq['pinned_revision'] == '3acef8b18f56786e253ccd034072a8f1845b507e'
    assert acq['all_needed_published_partitions_requested'] == 226 and acq['all_needed_published_partition_bytes'] == 41458253700
    assert sha(ROOT / 'gru_beta_neutral_20261008/fetch_all_phields_missing_tail_partitions.py') == acq['source_code_sha256']
    acqsnap = snapshot / 'full_tail_partition_acquisition_at_capture.json'; acqsnap.write_bytes(captured)
    certified = [r for r in acq['records'] if r['status'] == 'complete_whole_published_LFS_SHA256_and_Parquet_footer_verified']
    for r in certified:
        assert sha(r['path']) == r['published_LFS_SHA256'] and Path(r['path']).stat().st_size == r['published_bytes']
    provenance = {k: {'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for k, p in
                  [('observed_layer', lp), ('observed_layer_independent', vp), ('other_sources_independent', sp),
                   ('EDB_anonymous_probe', edbp), ('all_unchecked_coarse_session', coarsep), ('tail_acquisition_at_capture', acqsnap)]}
    review = {'date': '2026-09-23', 'full_union_requested_symbols': 1178, 'all_complete_native_pairs_processed': 1123,
              'minute_grid_rows': 269520, 'genuine_native_quote_candles': 269513, 'missing_quote_intervals_retained_null': 7,
              'actual_missing_target_hours': 2496, 'full_quote_hours_and_recorded_ready_before_execution': 2491,
              'full_quote_hours_recorded_ready_by_nominal_signal_end': 667, 'actual_missing_target_incomplete_hours': 5,
              'all_5206443_native_quote_Open_High_Low_exact_whole_actual_trade_prefix': True,
              'independent_verification_completed': True, 'original_producer_pending_flag_is_capture_time_state': True,
              'recorded_time_only_not_clock_synchronisation_compute_latency_or_live_PIT': True,
              'uniform_original_provider_minute_definition_not_certified': True,
              'full_historical_2018_2026_task_retained_and_not_complete': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False, 'proofs': provenance}
    acquisition = {'repository': acq['repository'], 'pinned_revision': acq['pinned_revision'],
                   'original_full_tail_stockdays_requested': 21215, 'original_full_tail_hours_requested': 84860,
                   'source_metadata_present_stockdays': 21079, 'source_metadata_present_hours': 84316,
                   'source_metadata_absent_stockdays': 136, 'source_metadata_absent_hours': 544,
                   'all_needed_partitions_requested': 226, 'all_needed_partition_bytes': 41458253700,
                   'status_at_capture': acq['status'], 'whole_file_certified_partitions_at_capture': len(certified),
                   'whole_file_certified_bytes_at_capture': sum(r['published_bytes'] for r in certified),
                   'acquisition_process_session_id': 11354, 'mutable_current_receipt_path': str(acqp.relative_to(BASE)),
                   'immutable_capture': provenance['tail_acquisition_at_capture'],
                   'source_code_path': 'gru_beta_neutral_20261008/fetch_all_phields_missing_tail_partitions.py',
                   'source_code_sha256': acq['source_code_sha256'],
                   'payload_directory_presence_is_not_minute_admission': True, 'candidate_hours_admitted': 0}
    manifest = json.loads(files[0].read_text()); data = manifest['data_backfill']
    assert data['new_source_findings_hours_admitted'] == 8
    data['full_union_observed_quote_minute_layer_review'] = review
    data['full_actual_missing_tail_trade_acquisition'] = acquisition
    data['documented_anonymous_history_and_unchecked_coarse_followup'] = {
        'official_documentation_scope_futures': True, 'actual_futures_native_rows': 11,
        'SH_stock_native_rows': 0, 'SZ_stock_native_rows': 0, 'original_minute_provider_identity_not_established': True,
        'all_40_unchecked_coarse_hours_retained': True, 'all_31_stockdays_requested': True,
        'coarse_session_terminal_login_TimeoutError': True, 'actual_reference_queries_sent': 0,
        'candidate_hours_admitted': 0, 'proof': provenance['other_sources_independent']}
    data['phields_public_trade_archive_review']['full_exact_tail_acquisition_followup'] = acquisition
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    manifest['required_work_remaining'][0] = ('Resolve all84860 current nonmember primary-trading tail hours; full2018–2026 union scope remains. '
        'All226 published phields partitions intersecting21215 exact missing stockdays are now being acquired,41458253700bytes; '
        'metadata covers21079stockdays/84316hours,136stockdays/544hours still absent. WholeSep23 source and all1123native pairs independently certified; '
        '269513genuine quote candles,7unobserved intervals retained null.2491/2496target hours complete and recorded-ready before execution; '
        'only667ready by nominal end. No new panel hours admitted. Finish full-tail payload intersection, true minute definition, source time admission and research panel integration.')
    manifest['required_work_remaining'].append('全部1,123个当日完整来源对已生成并独立核验分钟观察层。2,491个目标小时仅通过记录时间早于执行时点核验；严格整点PIT、计算/实盘链延迟与统一供应商分钟定义仍待解决。新增41.458GB全尾段下载不等于已填补84,316小时；最低3,042和尾段84,860原缺口及原失败门槛保留。')
    with files[1].open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    rows.extend([
        ['完整当日分钟观察层', '1123股票日全量生成并独立复算完成', '269520网格行，269513真实报价分钟，7无报价分钟留空；5206443源报价开高低全部对应真实成交前缀', provenance['observed_layer_independent']['path'], '2491/2496目标小时完整且记录可用时间早于执行；仅667整点前可用；0面板小时准入'],
        ['完整非成分尾段逐笔档案', '全部所需226公开分区下载与整文件认证进行中', '41458253700字节；覆盖全部21215目标股票日请求，目录21079日84316小时，544小时目录缺失', str(acqp.relative_to(BASE)), '下载不是分钟准入；原84860小时缺口保留；当前状态见动态取得记录'],
        ['匿名历史接口股票适用性', '官方文档及实际响应已独立核验', '期货对照11行；沪深两个股票试查均0行', provenance['other_sources_independent']['path'], '不能补A股；共同字段不证明原分钟供应商身份'],
        ['剩余历史粗频参照', '31股票日40目标小时的单次公开会话已结束', '登录TimeoutError，0查询与0新增参照；原不可用结果保存', provenance['all_unchecked_coarse_session']['path'], '40小时继续未核验；保留原3042最低缺口与失败分母']])
    page = files[2].read_text(); assert 'id="full-native-observation-layer"' not in page
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    block = '<h2 id="full-native-observation-layer">最新：当日分钟观察层已复算，完整尾段取数已启动</h2>'
    block += '<p>完整1,178股并集逐一请求；9月23日具有完整来源对的全部1,123股票日已处理。269,520分钟网格中，269,513根具有真实报价，另外7分钟留空并保留真实来源累计计数。它们集中于000669.SZ、002122.SZ、002431.SZ，影响5个目标小时。来源记录保存字段遗漏已修正为第二版，旧结果与修正记录均保留。</p>'
    block += '<p>独立读取全部2,246成交／快照文件，重新计算40,588,594笔真实成交。全部5,206,443条正成交量报价的当日开、高、低均精确对应实际完整成交前缀；每根观察分钟的实际未拆分成交股数、量额、报价索引和收到时间逐项核对。1,120个股票日有完整240根观察分钟，其余3日不补造报价。</p>'
    block += '<p><strong>624个目标股票日、2,496个缺失小时中，2,491小时具有完整观察分钟、正成交量，记录依赖时间早于现有执行时点。</strong>只有667小时在名义整点前已记录就绪。报价标注时间、实际事件时间及收到时间各自保留；记录时间比较不认证时钟同步、计算延迟、严格整点PIT或实盘链。本次仍新增0个训练面板小时，原第六版未改动。</p>'
    block += '<p><strong>取得范围已扩展为整个8—9月实际缺失尾段。</strong>固定公开版本共226个所需完整成交分区、41,458,253,700字节正在下载，逐文件核对发布SHA256与完整格式。请求对应全部21,215个缺失股票日、84,860小时；目录记录覆盖21,079日、84,316小时，另136日、544小时仍缺少目录记录。目录覆盖、已下载文件数和真正分钟准入分别记录，不能混同。当前下载状态见动态取得记录。</p>'
    block += '<p>另一个匿名官方历史接口实测有11根期货对照，沪深两个股票均只返回表头；不能补A股。历史40个未核验小时的参照查询在匿名登录时超时，31个股票日尚未发出查询，0新参照，原结果保存。</p>'
    block += '<p>原任务仍为1,178股2018—2026全部分钟与日线、最低窗口、2019供应商洞、原覆盖率门槛及训练／组合验收。最低缺口3,042、非成分尾段84,860仍为原面板真实剩余；不把当日观察层或正在下载当作完成。</p><p>'
    block += '；'.join('<a href="' + provenance[k]['path'] + '">' + label + '</a>' for k, label in [('observed_layer_independent', '全部观察分钟独立复核'), ('other_sources_independent', '匿名接口与未完成参照核验')])
    block += '；<a href="' + str(acqp.relative_to(BASE)) + '">完整尾段动态下载记录</a></p>'
    end = page.index('</p>', page.index('<p class="stamp">')) + 4
    page = page[:end] + block + page[end:]
    parser = Links(); parser.feed(page)
    assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    files[2].write_text(page)
    assert sha(mp) == proof['current_candidate_meta_sha256']
    result = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__), 'prior_reports_snapshot': str(snapshot),
              'proofs': provenance, 'local_links_checked': len(parser.paths),
              'files_updated': [{'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for p in files],
              'whole_tail_completed_files_at_capture_independently_SHA256_checked': len(certified),
              'candidate_meta_sha256': sha(mp), 'full_original_task_scope_retained': True,
              'candidate_hours_admitted': 0, 'goal_complete': False, 'production_ready': False}
    op.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'proofs'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

"""Publish bounded ms/stockyear findings, retaining the full original scope."""
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
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths.extend(v for k, v in attrs if k == 'href' and not v.startswith(('http:', 'https:', '#')))


def main():
    output = OUT / 'progress_recording_receipt.json'; assert not output.exists()
    ms_path = OUT / 'native_millisecond_trade_join_verification.json'; ms = json.loads(ms_path.read_text())
    matrix_path = OUT / 'native_matrix_pilot/independent_native_matrix_pilot_verification.json'; matrix = json.loads(matrix_path.read_text())
    assert ms['passed'] and matrix['passed'] and ms['candidate_hours_admitted'] == matrix['candidate_hours_admitted'] == 0
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_native_millisecond_sample.py') == ms['source_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_native_matrix_pilot_findings.py') == matrix['source_code_sha256']
    native_path = OUT / 'native_matrix_pilot/actual_minimum_gap_pilot_with_verified_alias_diagnostics.json'
    coarse_path = OUT / 'native_matrix_pilot/native_matrix_coarse_diagnostics.json'
    assert sha(native_path) == matrix['audited_native_receipt_sha256'] and sha(coarse_path) == matrix['audited_coarse_receipt_sha256']
    coarse = json.loads(coarse_path.read_text())
    strict_hours = [{'symbol': r['symbol'], 'date': r['date'], 'slot': r['slot']} for r in coarse['results'] if r.get('all_12_buckets_passed')]
    assert strict_hours == [{'symbol': '302132.SZ', 'date': date, 'slot': 0} for date in ['2023-06-19', '2023-06-21', '2023-06-29']]
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == matrix['current_candidate_meta_sha256']
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text()); data = manifest['data_backfill']
    assert data['new_X_fingerprint'] == matrix['candidate_fingerprint'] and data['new_source_findings_hours_admitted'] == 8
    assert 'suncong_native_matrix_pilot_review' not in data
    now = datetime.now(timezone.utc); snapshot = BASE / ('progress_snapshots/native_millisecond_matrix_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, snapshot / path.name)

    def ref(path):
        return {'path': str(path.relative_to(BASE)), 'sha256': sha(path)}

    data['suncong_millisecond_event_sample_review'] = {
        'repository': 'suncong/Ashare-events', 'pinned_revision': ms['pinned_event_archive_revision'],
        'source_archives_actual_published_dates': ['2026-09-22', '2026-09-23', '2026-09-24', '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-08'],
        'target_sample_symbol': '000006.SZ', 'target_sample_date': '2026-09-23',
        'complete_event_member_rows': 61744, 'actual_trade_rows': 22282,
        'nonzero_actual_event_milliseconds': 21889, 'same_ids_and_exact_price_quantity_as_whole_phields_sample': 22282,
        'phields_seconds_are_exact_floor_of_observed_milliseconds': True,
        'floor_minute_volume_assignments_identical': True,
        'millisecond_precision_alone_does_not_resolve_observed_bar_concordance': True,
        'native_snapshot_rows': 4413, 'outer_archive_compressed_prefix_retained_bytes': 41943040,
        'whole_archive_LFS_SHA256_verified': False, 'whole_gzip_CRC_verified': False,
        'archive_sources_not_claimed_independent_origins': True,
        'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
        'verification': ref(ms_path),
    }
    data['suncong_native_matrix_pilot_review'] = {
        'repository': 'suncong/ashare_1min', 'pinned_revision': 'c05c0da3940e167d689392852e9d490107a2c3e1',
        'native_index_first': '2018-01-02T09:31:00', 'native_index_last': '2026-03-26T15:00:00',
        'native_timestamp_rows_each_field': 478800, 'native_days_each_field': 1995,
        'six_native_fields_same_unique_240_minute_index': True,
        'selected_current_code_columns': ['600167', '600985', '302132', '600004'],
        'officially_verified_historical_code_column': '300114', 'code_change_effective_date': '2025-02-17',
        'historical_code_used_only_for_2023_targets': True,
        'actual_target_stockyears': ['600167.SH_2018', '600985.SH_2019', '302132.SZ_2023'],
        'actual_target_stockdays': 155, 'actual_missing_target_hours': 620,
        'daily_and_selected_positive_hours': 496, 'native_amount_pass_hours': 56,
        'native_hours_coarse_checked': 55, 'native_hours_coarse_not_checked': 1,
        'coarse_checked_conflict_hours': 52, 'strict_coarse_diagnostic_hours': 3,
        'strict_hours': strict_hours, 'strict_hours_overlap_already_known_wind17_diagnostics': True,
        'whole_remote_field_LFS_SHA256_verified': False, 'source_independence_certified': False,
        'minute_quantity_precision_certified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
        'production_ready': False, 'verification': ref(matrix_path), 'native_diagnostics': ref(native_path), 'coarse_diagnostics': ref(coarse_path),
    }
    data['phields_public_trade_archive_review']['millisecond_sample_followup'] = ref(ms_path)
    manifest['required_work_remaining'][0] = 'Resolve all 84860 actual nonmember primary-trading tail hours. phields inventory covers stockdates corresponding to 84316 hours; 544 are absent on Aug12. Actual millisecond events and snapshots were now retained for Sep23 000006; all 22282 trades have identical IDs, prices and quantities, and integer seconds exactly floor original milliseconds, leaving floor-minute quantities unchanged. Millisecond precision alone does not resolve native/reference bar definitions. Continue actual snapshot/transaction chronology verification and full-tail payload/source admission; no new source hours admitted.'
    manifest['required_work_remaining'].append('suncong历史原生分钟已按公告确认的300114→302132代码关系核对三个股票年份、155股票日、620实际缺失小时：496通过日线与选定正成交量、56原生量额通过；55取得五分钟参照，52冲突、3仅严格诊断，另1无实际参照行。这3小时与wind17既有诊断重叠。整份远端字段文件尚未认证、来源独立性与分钟股数精度仍未完成准入，最低3042与尾段84860保持。')
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.reader(stream))
    rows.extend([
        ['原始毫秒成交与秒钟字段对应', '22,282笔逐笔交叉核验完成', '编号价格股数均完全一致；秒钟是毫秒向下取整，左区间分钟量归属不变', str(ms_path.relative_to(BASE)), '只保留40MiB压缩前缀内完整样本，整卷指纹与CRC未核验；0新增纳入'],
        ['另一历史原生分钟档案试点', '公告确认旧代码后，155股票日620缺失小时独立核验完成', '496日线及选定正成交量通过；56原生量额通过；55核对参照后仅3严格诊断', str(matrix_path.relative_to(BASE)), '52参照冲突、1未取得实际参照行；3与既有诊断重叠；0新增纳入；整字段文件未认证'],
    ])
    page = files[2].read_text(); assert 'id="native-millisecond-matrix"' not in page
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    block = '<h2 id="native-millisecond-matrix">继续核对：原始毫秒成交与另一历史分钟档案</h2>'
    block += '<p>公开的 <a href="https://huggingface.co/datasets/suncong/Ashare-events/tree/301397490bea1a168083298da77f750a083a4ef1">suncong 事件档案</a>实际保留毫秒成交时间。为控制取数范围，固定版本后保留 9 月 23 日压缩包前 40 MiB 的原始范围字节，取得其中完整的 000006 成交事件和快照成员：61,744 事件，其中 22,282 笔成交；21,889 笔确有非零毫秒，另有 4,413 条真实快照。外层整个压缩包的发布 SHA256 和 gzip CRC 尚未认证，不能将完整成员样本称为整个档案认证。</p>'
    block += '<p>把全部 22,282 笔逐一与已认证完整文件的 phields 样本对应，成交编号、价格、股数完全一致；phields 的秒钟字段恰好是这些原始毫秒时间向下取整。左区间分钟成交量归属完全相同，故仅补回毫秒不能解释先前差异。两份档案未被宣称为独立交易来源；原报价更新与 K 线生成定义仍待核对，未新增纳入任何小时。</p>'
    block += '<p>另一公开 <a href="https://huggingface.co/datasets/suncong/ashare_1min/tree/c05c0da3940e167d689392852e9d490107a2c3e1">原生分钟字段档案</a>已固定版本。按真实字段块偏移读取六个字段的四只股票列及时间列，另读取公告确认的历史 300114 列；没有下载整个约 25.9 GB 的远端字段文件集合。六个字段及历史代码列都保留同一组 478,800 个真实分钟时点，覆盖 2018 年 1 月 2 日至 2026 年 3 月 26 日的 1,995 股票日，每日 240 时点。该档案不能用于填 8—9 月尾段。</p>'
    block += '<p>2023 年的 302132 对应公告确认的旧代码 300114，生效变更日为 2025 年 2 月 17 日；本次只对变更前目标使用实际旧代码列。没有把现代码列的历史空值直接解释成无数据，也没有填补价格。三个股票年份——600167 / 2018、600985 / 2019、302132 / 2023——共 155 股票日，恰好对应当前 620 个实际缺失小时，均与第六版缺失掩码独立对应。</p>'
    block += '<table><thead><tr><th>试点检查</th><th>小时数</th></tr></thead><tbody><tr><td>实际检查的缺失小时</td><td>620</td></tr><tr><td>通过日线及选定小时正成交量</td><td>496</td></tr><tr><td>通过原生分钟量额</td><td>56</td></tr><tr><td>原生量额通过，五分钟参照冲突</td><td>52</td></tr><tr><td>原生量额通过，未取得实际参照行</td><td>1</td></tr><tr><td>严格对照通过，仅为诊断</td><td>3</td></tr></tbody></table>'
    block += '<p>55 个原生量额通过小时已核对实际五分钟参照，3 个严格诊断仍是 2023 年 6 月 19、21、29 日的第一个小时，与 wind17 既有结果重叠，不能累加为新增小时。另一个 2019 年 4 月 16 日的小时发起了一次有界参照请求，响应成功但没有取得该日实际五分钟行，保留为“未核对”。原生字段、历史代码、真实范围字节和逐分钟量额已由另一套读取及十进制运算独立复算；整份远端文件、来源独立性和分钟股数精度仍未完成认证。</p>'
    block += '<p>本轮全部新来源新增纳入为 0，当前第六版及原门槛保持；最低缺口仍为 3,042，非成分尾段仍为 84,860。后续继续核对实际快照与成交的时间顺序、报价和 K 线定义，并推进完整并集历史的真实来源准入。</p>'
    block += '<p><a href="public_source_probes/additional_native_inventory_20261009/native_millisecond_trade_join_verification.json">毫秒逐笔对应核验</a>；<a href="public_source_probes/additional_native_inventory_20261009/native_matrix_pilot/independent_native_matrix_pilot_verification.json">历史分钟试点独立核验</a>；<a href="public_source_probes/additional_native_inventory_20261009/native_matrix_pilot/native_matrix_coarse_diagnostics.json">逐小时五分钟参照结果</a></p>'
    assert page.count('<h2>可复核文件</h2>') == 1
    page = page.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    parser = Links(); parser.feed(page); assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(page)
    receipt = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
               'millisecond_proof_sha256': sha(ms_path), 'matrix_independent_proof_sha256': sha(matrix_path),
               'prior_reports_snapshot': str(snapshot), 'files_updated': [ref(p) for p in files],
               'local_links_checked': len(parser.paths), 'candidate_fingerprint': matrix['candidate_fingerprint'],
               'new_source_hours_admitted': 0, 'minimum_missing_hours': 3042, 'tail_missing_hours': 84860,
               'goal_complete': False, 'production_ready': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

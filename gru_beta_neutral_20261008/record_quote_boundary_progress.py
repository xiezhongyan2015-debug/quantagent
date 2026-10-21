"""Publish the verified quote convention evidence without changing admission gates."""
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
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/quote_boundary_audit'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths.extend(v for k, v in attrs if k == 'href' and not v.startswith(('http:', 'https:', '#')))


def main():
    receipt_path = OUT / 'progress_recording_receipt.json'
    assert not receipt_path.exists()
    diagnostic_path = OUT / 'quote_trade_boundary_diagnostics.json'
    proof_path = OUT / 'independent_quote_trade_boundary_verification.json'
    diagnostic = json.loads(diagnostic_path.read_text())
    proof = json.loads(proof_path.read_text())
    assert proof['passed'] and sha(diagnostic_path) == proof['producer_receipt_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_quote_trade_boundaries.py') == proof['producer_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_quote_trade_boundaries.py') == proof['source_code_sha256']
    assert proof['candidate_hours_admitted'] == diagnostic['candidate_hours_admitted'] == 0
    target, control = diagnostic['results']
    target_models = {r['definition']: r for r in target['models']}
    control_models = {r['definition']: r for r in control['models']}
    assert target['symbol'] == '000006.SZ' and control['symbol'] == '000009.SZ'
    assert target_models['quote_prices_closed_right']['five_minute_reference_comparison']['all_fields_exact_intervals'] == 48
    assert control_models['original_quote_boundary_and_new_daily_extrema']['minute_original_control_comparison']['all_fields_exact_intervals'] == 240
    assert target_models['original_quote_boundary_and_new_daily_extrema']['five_minute_reference_comparison']['strict_existing_O_C_range_V_gate_pass_intervals'] == 31
    assert control_models['original_quote_boundary_and_new_daily_extrema']['five_minute_reference_comparison']['strict_existing_O_C_range_V_gate_pass_intervals'] == 36
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == proof['current_candidate_meta_sha256']
    expected = {
        'execution_manifest.json': '1e8bb6bcff28f52f6459d02b68a94578b98feae9d42d4310e6e4a673d7684c1e',
        '执行进度_2026-10-08.csv': '178a330762014da9fa2acb3ea2291874bbc3a7d215395952ef2c56cbfe2ff4b8',
        '数据补齐核验与实验进度_2026-10-09.html': '72f2d5c68d9d0c7e50097cb0a4371c68e7611dbe684696db7e10dc92af376169',
    }
    files = [BASE / name for name in expected]
    for path in files:
        assert sha(path) == expected[path.name], 'Publish against the current reports only'
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    assert data['new_source_findings_hours_admitted'] == 8
    assert data['new_X_fingerprint'] == proof['candidate_fingerprint']
    assert 'native_quote_boundary_review' not in data
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/quote_boundary_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, snapshot / path.name)

    def ref(path):
        return {'path': str(path.relative_to(BASE)), 'sha256': sha(path)}

    data['native_quote_boundary_review'] = {
        'repository': 'suncong/Ashare-events', 'pinned_revision': '301397490bea1a168083298da77f750a083a4ef1',
        'date': '2026-09-23', 'sample_symbols': ['000006.SZ', '000009.SZ'],
        'whole_file_backed_actual_trades_rejoined': 35225, 'all_positive_source_quote_prefixes_verified': 8653,
        'all_quote_cumulative_shares_last_price_and_calculated_prefix_amount_exact': True,
        'no_trade_split_or_missing_quantity_reconstruction': True,
        'missing_target_000006_quote_sampling_5m_all_six_fields_exact': 48,
        'original_control_000009_quote_definition_minutes_all_OHLC_V_exact': 240,
        'original_control_copied_from_fresh_SHA_verified_complete_original_split_archive': True,
        'quote_sampling_and_original_minute_candle_definitions_kept_separate': True,
        'original_compatible_missing_target_5m_existing_strict_gate_pass': 31,
        'original_compatible_positive_control_5m_existing_strict_gate_pass': 36,
        'target_000006_reported_14h_quote_LocalTime': '14:00:00.238',
        'target_000006_earliest_execution': '14:01:00',
        'all_eight_sample_hour_stored_receipts_before_current_earliest_execution': True,
        'strict_at_nominal_signal_PIT_certified': False,
        'stored_receipts_do_not_certify_clock_synchronisation_or_live_latency': True,
        'closing_auction_two_minutes_each_sample_have_no_actual_trades_but_real_source_quotes': True,
        'source_quote_freshness_not_claimed_latest_actual_trade_freshness': True,
        'whole_event_archive_LFS_SHA256_verified': False, 'whole_event_gzip_CRC_verified': False,
        'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
        'diagnostics': ref(diagnostic_path), 'independent_verification': ref(proof_path),
    }
    data['suncong_millisecond_event_sample_review']['quote_boundary_followup'] = ref(proof_path)
    manifest['required_work_remaining'][0] = (
        'Resolve all 84860 actual nonmember primary-trading tail hours. Exact phields inventory covers stockdates corresponding to 84316 hours; 544 are absent on Aug12. '
        'Actual quotes now explain the sample bar conventions: 8653 positive quotes match exact whole actual trade prefixes for two Sep23 stocks; quote sampling matches all six fields of 48 public 5m target bars, '
        'while a separate original-compatible quote convention exactly reproduces all 240 original control minutes. The original-compatible convention still passes only31/48 target and36/48 control strict public 5m gates. '
        'Retain the definitions and failed gates explicitly; finish whole archive integrity and source/PIT admission before any candidate fill. '
        'The target14h quote was received at14:00:00.238 before current14:01 execution, which does not certify exact14:00 availability or live readiness. No new source hours admitted.'
    )
    manifest['required_work_remaining'].append('已定位快照更新、逐笔成交和参照K线的口径差异。继续扩展既有分钟正对照与缺失样本，保留原有严格准入失败结果，验证整份事件档案和实际可用时间；并集全历史、最低3042及尾段84860缺口仍须处理，不能把样本口径复现当作全范围补齐。')
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.reader(stream))
    rows.extend([
        ['报价快照与实际成交前缀', '两股票日8,653条正成交量快照全部对应', '35,225笔完整文件成交逐一对应；累积股数、末笔价格与计算量额全一致', str(proof_path.relative_to(BASE)), '原始整卷指纹与gzip CRC未核验；保持0新增纳入'],
        ['参照K线与原始分钟口径', '缺失样本48根五分钟六字段全吻合；正对照240根原始分钟五字段全复现', '按固定报价采样定义与原始报价边界定义分别核对；完整价格量额逐项复算', str(diagnostic_path.relative_to(BASE)), '原始兼容定义仍仅31/48、36/48通过原严格五分钟门槛；不得合并口径或绕过失败'],
        ['原报价收到时间与信号执行', '两个股票日8小时的存储收到时间均早于现有执行时刻', '000006在14:00的快照实收14:00:00.238，现有最早执行14:01:00', str(proof_path.relative_to(BASE)), '不能宣称严格14:00已知、时钟已同步或实盘延迟已认证；集合竞价零成交分钟保留真实报价及成交时间'],
    ])
    page = files[2].read_text()
    assert 'id="quote-boundary-proof"' not in page
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    block = '<h2 id="quote-boundary-proof">新证据：报价边界解释分钟量与价格差异</h2>'
    block += '<p>已通过 Chrome 访问并确认公开事件档案的固定版本。继续使用先前保留的同一 40 MiB 原始压缩范围，无新增大档案下载，取得完整的 000009 成交及报价成员作为正对照；其原始分钟也从完整五段原档案重新提取，全部原档案 SHA256 与 gzip 尾部均重新核验。</p>'
    block += '<p>000006 与 000009 两个 9 月 23 日股票日合计 35,225 笔实际成交，逐一重新扫描已认证完整文件的 phields 成交档案后，编号、价格、股数和秒钟字段均对应。全部 8,653 条正成交量真实报价快照的累积股数，恰好对应完整实际成交的前缀；最新价等于末笔实际成交价，快照量额也等于该前缀实际价格乘股数。没有拆分成交、倒推补股数或制造成交。</p>'
    block += '<table><thead><tr><th>固定口径与对象</th><th>实际复核结果</th></tr></thead><tbody><tr><td>000006：实际报价采样的五分钟线</td><td>48/48 的开高低收、成交股数及量额六字段完全吻合公开参照</td></tr><tr><td>000009：原始报价边界与新增日内极值口径</td><td>240/240 原始分钟的开高低收及成交量五字段完全复现</td></tr><tr><td>000009：报价采样的五分钟线</td><td>47/48 六字段完全吻合；11:25–11:30 区间开盘价仍冲突</td></tr><tr><td>原始兼容分钟汇总后核对原严格五分钟门槛</td><td>000006：31/48；000009：36/48，失败结果保留</td></tr></tbody></table>'
    block += '<p>这说明两家 K 线的开盘价与极值定义存在差异。能复现原始分钟的报价定义，与能吻合另一公开五分钟线的报价采样定义，分别保留为固定、可复算的诊断；不能逐分钟挑选有利定义，不能把两种结果合并成“全部原始分钟通过参照”。逐笔成交实际极值也单独保留，防止将快照未采到的实际成交价格当作源数据缺坏。</p>'
    block += '<p>原始报价时间也存在可观察的先后差异：448 条报价中，末笔所含成交的事件时间晚于报价标注的 UpdateTime，最大 140 毫秒。000006 的 14:00 报价在源数据中记录收到于 14:00:00.238；源记录中的小时数据均早于现有 14:01 执行，但不能据此声称它在严格 14:00 已知，或源时钟同步、计算延迟与实盘链路已经认证。两个样本收盘报价分别延迟 2.474 秒、1.474 秒。</p>'
    block += '<p>每个样本均有 240 个真实分钟区间报价，最新报价相对分钟末最多分别落后 15 秒、12 秒；14:57–14:58 两分钟确无实际成交，保留当时真实报价及最后成交时间。这些报价可以证明采样价格的来源，但不能冒充“60 秒内一定有实际成交”。</p>'
    block += '<p>独立核验程序重新解压原始范围字节、核对完整成员、重新扫描完整成交文件，并复算每条报价前缀、全部三种分钟诊断、逐字段参照结果和现有执行时间。证据复现通过。整份远端事件档案的发布指纹与 gzip CRC、跨来源独立性、统一报价定义与严格时点准入仍未完成；本轮新增纳入 0，第六版指纹保持，最低缺口 3,042、非成分尾段 84,860 不变，原覆盖率门槛与分母保留。并集完整历史仍为后续任务。</p>'
    block += '<p><a href="public_source_probes/additional_native_inventory_20261009/quote_boundary_audit/quote_trade_boundary_diagnostics.json">报价与成交边界逐项诊断</a>；<a href="public_source_probes/additional_native_inventory_20261009/quote_boundary_audit/independent_quote_trade_boundary_verification.json">独立复算证明</a>；<a href="public_source_probes/additional_native_inventory_20261009/quote_boundary_audit/original_control/extraction_receipt.json">完整原档案分钟正对照提取证明</a></p>'
    assert page.count('<h2>可复核文件</h2>') == 1
    page = page.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    parser = Links()
    parser.feed(page)
    assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(page)
    receipt = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
               'diagnostics_sha256': sha(diagnostic_path), 'independent_verification_sha256': sha(proof_path),
               'prior_reports_snapshot': str(snapshot), 'files_updated': [ref(p) for p in files],
               'local_links_checked': len(parser.paths), 'candidate_fingerprint': proof['candidate_fingerprint'],
               'new_source_hours_admitted': 0, 'minimum_missing_hours': 3042, 'tail_missing_hours': 84860,
               'original_gate_failures_and_denominators_preserved': True, 'goal_complete': False, 'production_ready': False}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

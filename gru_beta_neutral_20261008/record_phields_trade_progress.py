"""Publish verified new-source facts while preserving every original data gate."""
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
OUT = BASE / 'public_source_probes/phields_trades_20261009'


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
    output = OUT / 'progress_recording_receipt.json'
    assert not output.exists()
    proof_path = OUT / 'independent_phields_findings_verification.json'
    proof = json.loads(proof_path.read_text())
    assert proof['passed'] and proof['candidate_hours_admitted'] == 0
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_phields_trade_findings.py') == proof['source_code_sha256']
    sample_path = OUT / 'actual_trade_sample_diagnostics.json'
    coverage_path = OUT / 'exact_tail_metadata_coverage_receipt.json'
    assert sha(sample_path) == proof['audited_sample_receipt_sha256']
    assert sha(coverage_path) == proof['audited_coverage_receipt_sha256']
    sample = json.loads(sample_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_phields_trade_samples.py') == sample['source_code_sha256']
    coverage = json.loads(coverage_path.read_text())
    assert sha(coverage['coverage_csv_path']) == coverage['coverage_csv_sha256']
    meta_path = BASE / sample['current_candidate'] / 'dataset/meta.json'
    assert sha(meta_path) == proof['candidate_meta_sha256']
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text()); data = manifest['data_backfill']
    assert data['latest_candidate_version'] == sample['current_candidate']
    assert data['new_X_fingerprint'] == proof['candidate_fingerprint']
    assert data['new_source_findings_hours_admitted'] == 8
    assert 'phields_public_trade_archive_review' not in data
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/phields_trade_samples_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, snapshot / path.name)

    def ref(path):
        return {'path': str(path.relative_to(BASE)), 'sha256': sha(path)}

    data['phields_public_trade_archive_review'] = {
        'repository': 'phields/a-share-l2-trades', 'pinned_revision': proof['pinned_revision'],
        'source_access': 'public_ungated_anonymous_verified_TLS',
        'actual_missing_tail_hours': 84860, 'actual_missing_tail_stockdays': 21215,
        'source_inventory_present_stockdays': 21079, 'source_inventory_present_hours': 84316,
        'source_inventory_absent_hours': 544, 'source_inventory_absent_dates': {'2026-08-12': 136},
        'inventory_presence_is_not_verified_payload_coverage_or_hour_admission': True,
        'whole_sample_files_downloaded_and_published_LFS_SHA256_independently_verified': 2,
        'whole_sample_total_bytes': sum(p['whole_source_bytes'] for p in sample['whole_payloads']),
        'sample_stockdays': proof['checks'], 'all_three_regular_samples_daily_OHLC_volume_calculated_amount_exact': True,
        'amount_calculated_from_actual_integer_price_times_volume_not_independent_observed_amount_field': True,
        'sample_clock_models': ['floor_second_left_interval', 'closed_right_second_interval'],
        'sample_hours_passing_all_12_independent_5m_diagnostics_across_either_clock_model': 0,
        'source_trade_clock_resolution': 'integer_seconds', 'millisecond_clock_certified': False,
        'unobserved_trade_minute_prices_filled': False,
        'after_close_sample_rows': 2, 'after_close_sample_volume': 1900,
        'after_close_trade_type_inference': 'time_and_close_price_consistent_with_2026_SSE_fixed_price_session_not_independently_type_certified',
        'after_close_rows_preserved_separately_and_not_merged_into_four_hour_bars': True,
        'companion_market_depth_revision': proof['companion_market_depth_revision'],
        'companion_market_depth_actual_last_partition': '2026-07-30',
        'full_tail_trade_payload_coverage_certified': False, 'source_minute_precision_certified': False,
        'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
        'receipts': {'independent_verification': ref(proof_path), 'sample_diagnostics': ref(sample_path),
                     'exact_tail_inventory': ref(coverage_path),
                     'every_missing_tail_stockday_inventory': ref(Path(coverage['coverage_csv_path']))},
    }
    manifest['required_work_remaining'][0] = 'Resolve all 84860 actual nonmember primary-trading tail hours. New ungated phields real-trade inventory covers stockdates corresponding to 84316 hours; 544 hours are absent from its full ticker metadata on 2026-08-12. Two whole Sep23 sample files and three actual stockdays were independently checked: regular-session OHLC, volume and calculated trade amount agree with daily references, but neither tested second-clock model passes all 12 coarse buckets in any sample hour. No source hours admitted; investigate original timestamp precision and bar semantics, obtain genuinely observed empty-minute/auction quotes, and verify full-tail payload coverage.'
    manifest['required_work_remaining'].append('phields 新逐笔来源的目录覆盖与三个真实样本已独立复算。数据仅保留整数秒；五分钟与原有分钟仍有分桶差异，空成交分钟未补价格。沪市样本盘后两笔1900股已分开保留；同作者毫秒快照/委托档案实际只到7月30日，不能据此证明8—9月分钟边界。当前最低缺口3042、非成分尾段84860保持，继续解决原始分钟时间语义及来源准入。')
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.reader(stream))
    rows.extend([
        ['新逐笔档案尾段目录核验', '完整股票日期目录与第六版缺失掩码独立对应',
         '21,215缺失股票日中21,079日存在；对应84,316/84,860小时',
         str(proof_path.relative_to(BASE)), '544小时对应日期未出现，集中8月12日；目录存在不等于有效分钟已补齐'],
        ['新逐笔档案真实样本核验', '两个完整文件、三个股票日独立复算完成',
         '常规时段开高低收、成交量和逐笔计算金额均与日线一致；原价格股数保留',
         str(sample_path.relative_to(BASE)), '两种秒钟分桶均无小时通过全部12个五分钟对照；0新增纳入，毫秒精度待解决'],
    ])
    page = files[2].read_text()
    assert 'id="phields-native-trades"' not in page
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    block = '<h2 id="phields-native-trades">新逐笔成交来源：尾段目录覆盖广，分钟切分尚未通过</h2>'
    block += '<p>找到可匿名读取的 <a href="https://huggingface.co/datasets/phields/a-share-l2-trades/tree/3acef8b18f56786e253ccd034072a8f1845b507e">phields 逐笔成交档案</a>，版本已固定。完整股票日期目录、格式说明和每日清单已下载并核对发布指纹，再与当前第六版真实缺失时段独立对应：21,215 个缺失股票日中，21,079 日在目录中存在，对应 84,316 / 84,860 个缺失小时。另有 136 股票日、544 小时未在目录出现，集中于 2026 年 8 月 12 日的深市 00 前缀股票。这是目录覆盖情况，完整尾段成交文件尚未逐一核验。</p>'
    block += '<p>9 月 23 日的两个完整成交文件已取得，共 547,279,670 字节；整个文件的发布 SHA256 已重新核验。按实际股票范围逐个读取相关数据块，不依赖全文件排序假设。下列三只股票的全部样本成交记录数与完整股票日期目录一致，未发现重复成交编号、非正价格或非正数量，保留原始价格与股数。</p>'
    block += '<table><thead><tr><th>样本</th><th>逐笔记录数</th><th>常规时段日线核对</th><th>当前候选状态</th></tr></thead><tbody><tr><td>000006 / 9月23日</td><td>22,282</td><td>开高低收、量、逐笔计算额均一致</td><td>四个非成分小时缺失</td></tr><tr><td>000099 / 9月23日</td><td>7,875</td><td>开高低收、量、逐笔计算额均一致</td><td>四个非成分小时缺失</td></tr><tr><td>600004 / 9月23日</td><td>7,533（含两笔盘后）</td><td>常规时段六项均一致</td><td>四个成分小时已完整，作对照</td></tr></tbody></table>'
    block += '<p>成交额由实际整数价格乘实际股数计算，档案没有独立的成交额字段。600004 的两笔 15:19:31 成交共 1,900 股，价格等于收盘价；其时间和价格与 2026 年 7 月 6 日实施的沪市盘后固定价格规则相符，成交类型没有得到另一份原始记录认证。两笔数据单独保留，未并入四个小时。<a href="https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml">上交所规则及暂缓条文</a>已保存核对。</p>'
    block += '<p>实际逐笔只有整数秒时间。按左区间秒钟与右闭秒钟两种明确方式分别聚合，三只样本均未在任何小时通过全部 12 个独立五分钟对照。左区间下，600004 的 236 个有成交分钟中，164 个成交量与原分钟精确一致，最大差异 4,800 股；四个无成交分钟在原档案中均为零量，本次没有为它们补价格。差异还涉及开盘定义、范围和收盘边界，不能仅凭日线一致宣称分钟精度合格。同作者的毫秒快照及委托档案实际目录只到 7 月 30 日，未提供本尾段的毫秒补证。</p>'
    block += '<p>三个股票日、两个文件及目录覆盖结论均已由另一套整数与十进制运算独立复算。新来源新增纳入为 0，第六版保持；最低缺口仍为 3,042，非成分尾段仍为 84,860。继续核对原始时间与分桶语义、真实无成交时段报价以及完整尾段文件；原验收门槛保留。</p>'
    block += '<p><a href="public_source_probes/phields_trades_20261009/independent_phields_findings_verification.json">独立核验结果</a>；<a href="public_source_probes/phields_trades_20261009/actual_trade_sample_diagnostics.json">实际成交与分钟对照</a>；<a href="public_source_probes/phields_trades_20261009/every_missing_tail_stockday_metadata_coverage.csv">逐个缺失股票日的目录覆盖</a></p>'
    assert page.count('<h2>可复核文件</h2>') == 1
    page = page.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    parser = Links(); parser.feed(page)
    assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(page)
    receipt = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
               'independent_receipt_sha256': sha(proof_path), 'prior_reports_snapshot': str(snapshot),
               'files_updated': [ref(p) for p in files], 'local_links_checked': len(parser.paths),
               'candidate_version': sample['current_candidate'], 'candidate_fingerprint': proof['candidate_fingerprint'],
               'new_source_hours_admitted': 0, 'minimum_missing_hours': 3042, 'tail_missing_hours': 84860,
               'goal_complete': False, 'production_ready': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

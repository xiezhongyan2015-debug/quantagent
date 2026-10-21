"""Publish exact source-audit scope without promoting uncertified source hours."""
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
SOURCE = BASE / 'public_source_probes/additional_public_archives_20261009/wind17'
OUT = SOURCE / 'all_minimum_gap_consolidation'
NAME = 'backfilled_panel_candidate_canonical_ipo_v6'


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
    audit_path = OUT / 'verified_minimum_gap_source_scope.json'
    audit = json.loads(audit_path.read_text())
    assert audit['passed'] and audit['actual_missing_hours'] == 3042 and audit['candidate_hours_admitted'] == 0
    assert sha(ROOT / 'gru_beta_neutral_20261008/consolidate_verified_minimum_gap_sources.py') == audit['source_code_sha256']
    assert sha(audit['hour_diagnostics_csv_path']) == audit['hour_diagnostics_csv_sha256']
    early_path = SOURCE / 'remaining_2019_2022/independent_native_gap_verification.json'
    early = json.loads(early_path.read_text())
    assert early['passed'] and early['actual_missing_hours'] == 829 and early['native_amount_pass_hours'] == 16
    meta_path = BASE / NAME / 'dataset/meta.json'
    assert sha(meta_path) == audit['candidate_meta_sha256']
    meta = json.loads(meta_path.read_text())
    assert meta['data_fingerprint'] == audit['candidate_fingerprint']
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    assert data['latest_candidate_version'] == NAME and data['new_X_fingerprint'] == audit['candidate_fingerprint']
    assert data['new_source_findings_hours_admitted'] == 8
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/all_minimum_source_diagnostics_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for p in files:
        shutil.copyfile(p, snapshot / p.name)

    def ref(p):
        return {'path': str(p.relative_to(BASE)), 'sha256': sha(p)}

    data['wind17_2019_2022_public_archive_review'] = {
        'pinned_revision': 'ca4c51770f1fafb40317cc35bfd92e7c2be17827',
        'target_stockyears': 94, 'target_stockdays': 208, 'actual_missing_hours': 829,
        'observed_target_stockdays': 208, 'daily_and_selected_positive_hour_pass_hours': 512,
        'native_amount_pass_hours': 16, 'coarse_qualified_hours': None,
        'coarse_qualified_hours_null_means_not_tested': True,
        'whole_archive_LFS_SHA256_verified': False, 'retained_member_range_bytes_and_CRC_verified': True,
        'source_minute_quantity_precision_certified': False, 'candidate_hours_admitted': 0,
        'candidate_changed': False, 'production_ready': False,
        'independent_receipt': ref(early_path),
    }
    data['all_remaining_minimum_gap_public_source_scope'] = {
        k: audit[k] for k in ['unique_stocks', 'stockyears', 'stockdays', 'actual_missing_hours',
                              'all_actual_minimum_gap_dates_have_observed_native_source_records',
                              'daily_and_selected_positive_hour_pass_hours', 'native_amount_pass_hours',
                              'coarse_checked_native_hours', 'coarse_not_tested_native_hours',
                              'strict_diagnostic_only_hours', 'remaining_reason_hour_counts', 'batches',
                              'candidate_hours_admitted', 'candidate_changed', 'source_minute_quantity_precision_certified',
                              'full_union_source_history_completed', 'production_ready']
    }
    data['all_remaining_minimum_gap_public_source_scope'].update(
        candidate_version=NAME, candidate_fingerprint=audit['candidate_fingerprint'],
        scope_receipt=ref(audit_path), every_missing_hour_diagnostics=ref(Path(audit['hour_diagnostics_csv_path'])))
    review = data['wind17_new_public_archive_review']
    review['receipts']['2019_2022_independent_verification'] = ref(early_path)
    review['receipts']['all_minimum_scope_consolidation'] = ref(audit_path)
    manifest['required_work_remaining'][1] = 'Resolve quality and independent-source concordance for all 3042 remaining minimum primary-daily trading hours. Another native source now contains all 767 target stockdays, but 1422 hours fail daily/selected positive-volume checks, 1521 fail native amount checks, 56 fail independent coarse concordance, 40 have no independent coarse check, and 3 remain diagnostic-only with uncertified source precision. V6 has 8 applied native IPO hours outside the minimum interval; full action-history and live certification remain incomplete.'
    manifest['required_work_remaining'][-1] = '2019—2022 新档案的 94 股票年份、208 股票日、829 实际缺失小时已独立核验。全部年度合计 767 股票日、3042 小时已与 V6 实际缺口逐一绑定；99 小时通过原生量额，其中 40 未取得独立五分钟参照、56 参照冲突、3 仅严格诊断。来源精度、完整并集历史和 84860 小时非成分尾段仍待解决。'
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.reader(stream))
    assert not any(r[0] == '最低缺口全年度来源核验' for r in rows)
    rows.extend([
        ['2019—2022新档案缺口核验', '94股票年份、208股票日独立复算完成',
         '829小时对应日期均存在；512日线与选定正成交小时通过，16原生量额通过',
         str(early_path.relative_to(BASE)), '16小时未取得独立参照；0新增纳入，整卷指纹未核验'],
        ['最低缺口全年度来源核验', '767股票日、3042实际缺失小时逐一绑定第六版',
         '全部日期有另一原生来源；1620日线与选定小时通过，99原生量额通过，3仅严格诊断',
         str(audit_path.relative_to(BASE)), '40小时尚未核对参照、56参照冲突；0新增纳入；最低3042、尾段84860保持'],
    ])
    page = files[2].read_text()
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    old = '分钟股数精度和来源语义仍未完成准入，2019—2022 年此新来源另有 829 个实际缺失小时尚待审计。最低缺口仍为 3,042，非成员尾段仍为 84,860，V5 面板保持。'
    new = '本节最初待审的 2019—2022 年 829 小时现已完成独立核验，见下方全年度汇总。分钟股数精度和来源语义仍未完成准入，wind17 来源没有新增小时进入候选；当前第六版最低缺口仍为 3,042，非成员尾段仍为 84,860。'
    assert page.count(old) == 1
    page = page.replace(old, new)
    block = '<h2>最低缺口全年度核验：所有日期已有另一来源，质量冲突仍未解决</h2>'
    block += '<p>新完成 2019—2022 年的 94 股票年份、208 股票日、829 个实际缺失小时核验。保留的远端压缩字节已重新解压，CSV 字节及 CRC、逐分钟原始量额和当前缺失时段均独立复算；所有目标日期都存在。这些年份只提取并保留目标成员的范围字节，未认证整个年度压缩包的 SHA256。512 小时通过日线及选定小时正成交量，16 小时通过原生分钟量额，独立五分钟参照尚未取得，新增纳入为 0。</p>'
    block += '<p>随后把所有年度的独立记录与第六版实际缺失掩码逐一对应：205 只股票、232 股票年份、767 股票日，恰好覆盖全部 3,042 个最低区间缺失小时，没有重复、遗漏或额外时段。全部对应日期都有另一份原生分钟记录；其中 1,620 小时通过日线及选定小时正成交量，99 小时通过原生分钟量额检查。下表列出这些小时目前仍未纳入候选的原因，每个缺失小时只计一次。</p>'
    block += '<table><thead><tr><th>仍未纳入的原因</th><th>小时数</th></tr></thead><tbody><tr><td>日线或选定小时正成交量检查失败</td><td>1,422</td></tr><tr><td>原生分钟量额检查失败</td><td>1,521</td></tr><tr><td>通过原生量额，但独立五分钟参照冲突</td><td>56</td></tr><tr><td>通过原生量额，独立五分钟参照尚未核对</td><td>40</td></tr><tr><td>严格对照通过，仅作诊断；来源精度尚未认证</td><td>3</td></tr><tr><td>合计</td><td>3,042</td></tr></tbody></table>'
    block += '<p>“尚未核对”的 40 小时包括 2018 年 24 小时、2019—2022 年 16 小时，没有按失败或通过报告。本次合并核验重新计算来源记录和代码指纹，并验证每个当前缺失时段；分钟数值与原始压缩字节核验引用已完成的独立证据链。它没有重新训练模型或改写第六版面板，也不表示完整 1178 股历史已补完。最低缺口仍为 3,042，完整并集非成分尾段仍为 84,860；原准入门槛继续保留。</p>'
    block += '<p><a href="public_source_probes/additional_public_archives_20261009/wind17/remaining_2019_2022/independent_native_gap_verification.json">2019—2022独立核验</a>；<a href="public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/verified_minimum_gap_source_scope.json">全年度与当前缺口对应核验</a>；<a href="public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/every_actual_missing_hour_diagnostics.csv">逐个缺失小时的结果</a></p>'
    assert page.count('<h2>可复核文件</h2>') == 1
    page = page.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    parser = Links(); parser.feed(page)
    assert all((BASE / p).exists() for p in parser.paths)
    assert '正元地信' not in page and '佳驰科技（688708）' in page
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(page)
    receipt = {
        'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
        'consolidated_scope_receipt_sha256': sha(audit_path), '2019_2022_independent_receipt_sha256': sha(early_path),
        'candidate_version': NAME, 'candidate_fingerprint': audit['candidate_fingerprint'],
        'prior_reports_snapshot': str(snapshot), 'files_updated': [ref(p) for p in files],
        'local_report_links_checked': len(parser.paths), 'this_source_batch_new_hours_admitted': 0,
        'minimum_missing_hours': 3042, 'full_union_tail_missing_hours': 84860,
        'goal_complete': False, 'production_ready': False,
    }
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

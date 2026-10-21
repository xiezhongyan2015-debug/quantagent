"""Record an ongoing full-source acquisition separately from completed proofs."""
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
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'


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
    output = OUT / 'acquisition_started_progress_receipt.json'
    assert not output.exists()
    live = OUT / 'complete_archive_acquisition.json'
    captured = json.loads(live.read_text())
    assert captured['status'] == 'acquiring_complete_archive'
    assert sha(ROOT / 'gru_beta_neutral_20261008/fetch_complete_native_event_archive.py') == captured['source_code_sha256']
    assert captured['published_bytes'] == 15803513411
    assert not captured['whole_published_LFS_SHA256_verified'] and not captured['whole_gzip_CRC_verified']
    assert captured['candidate_hours_admitted'] == 0
    expected = {'execution_manifest.json': 'c297fc75de8be41cfbdd7ae14877421b7e1d3513f6fdda74dc3c15715b5a1b57',
                '执行进度_2026-10-08.csv': '66f845c4e3f1c75a84bec83b7f071fb81831f7832e96257bba31b9a3647f279e',
                '数据补齐核验与实验进度_2026-10-09.html': '112f6be8f1575db0551b7e710fc678d5f818c1b55fa20d6ef1f3e0bbc2033bd6'}
    files = [BASE / name for name in expected]
    assert all(sha(p) == expected[p.name] for p in files)
    now = datetime.now(timezone.utc)
    snapshot = BASE / ('progress_snapshots/whole_archive_start_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, snapshot / path.name)
    frozen_launch = OUT / 'archive_acquisition_launch_snapshot.json'
    assert not frozen_launch.exists()
    frozen_launch.write_text(json.dumps({'captured_utc': now.isoformat(), 'mutable_live_receipt': str(live),
                                      'acquisition_at_capture': captured}, ensure_ascii=False, indent=2))
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    assert data['new_source_findings_hours_admitted'] == 8
    assert 'complete_native_event_archive_acquisition' not in data
    data['complete_native_event_archive_acquisition'] = {
        'repository': captured['repository'], 'pinned_revision': captured['pinned_revision'],
        'filename': captured['filename'], 'published_bytes': captured['published_bytes'],
        'published_LFS_sha256': captured['published_LFS_sha256'],
        'status_at_capture': captured['status'], 'captured_utc': now.isoformat(),
        'received_bytes_at_capture': captured['received_total_bytes'],
        'launch_snapshot_path': str(frozen_launch.relative_to(BASE)), 'launch_snapshot_sha256': sha(frozen_launch),
        'mutable_live_receipt_path': str(live.relative_to(BASE)),
        'whole_SHA256_and_gzip_CRC_verified_at_capture': False,
        'next_member_extraction_scope_actual_missing_stockdays': 624,
        'next_member_extraction_scope_actual_missing_hours': 2496,
        'next_member_extraction_scope_is_subset_of_all_84860_tail_hours': True,
        'prepared_extractor_path': 'gru_beta_neutral_20261008/extract_complete_archive_current_tail.py',
        'prepared_extractor_sha256': sha(ROOT / 'gru_beta_neutral_20261008/extract_complete_archive_current_tail.py'),
        'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
    }
    manifest['latest_local_updated_utc'] = manifest['checked_utc'] = now.isoformat()
    manifest['required_work_remaining'].append('已启动固定版本9月23日整份15,803,513,411字节公开事件档案的获取及整卷SHA256/gzip CRC核验。完成后按当前掩码逐一提取624个缺失股票日、2496实际缺失小时及2只正对照的完整事件与报价成员。当前为正在取数，不是整卷认证或准入完成；仍须处理并集全时段、最低3042与全部尾段84860。')
    with files[1].open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    rows.append(['9月23日完整公开事件档案', '整卷获取正在进行，尚未认证',
                 '固定版本15,803,513,411字节；后续对应624缺失股票日、2496实际缺失小时及2只正对照',
                 str(live.relative_to(BASE)), '记录为运行中；0新增准入；不替代并集完整历史与全部尾段缺口'])
    page = files[2].read_text()
    assert 'id="whole-native-archive-start"' not in page
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), page)
    block = '<h2 id="whole-native-archive-start">整份公开档案核验已启动</h2>'
    block += '<p>已开始获取固定版本的 9 月 23 日完整事件压缩档案，发布大小为 15,803,513,411 字节（约 15.8 GB）；复用先前已核验的 40 MiB 原始范围字节，其余部分通过校验证书与主机名的公开 HTTPS 获取。当前记录为正在取数，整卷发布 SHA256 与 gzip CRC 尚未通过；运行记录会继续变化，不能把启动快照当作整卷认证。</p>'
    block += '<p>整卷核验后，按当前第六版真实缺失掩码逐一提取该日 624 个缺失股票日、2,496 个缺失小时及两只正对照的完整成交事件与报价成员。所有数据须继续经过逐字段、时点与来源准入核验。该日仅是全部尾段的一部分，并集全时段、最低 3,042 与尾段 84,860 缺口仍须处理；当前本项新增准入 0。</p>'
    block += '<p><a href="public_source_probes/additional_native_inventory_20261009/whole_event_archive/complete_archive_acquisition.json">整卷取数实时状态</a>；<a href="public_source_probes/additional_native_inventory_20261009/whole_event_archive/archive_acquisition_launch_snapshot.json">启动时状态快照</a></p>'
    page = page.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    parser = Links(); parser.feed(page)
    assert all((BASE / p).exists() for p in parser.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    files[2].write_text(page)
    result = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(__file__),
              'launch_snapshot_sha256': sha(frozen_launch), 'prior_reports_snapshot': str(snapshot),
              'files_updated': [{'path': str(p.relative_to(BASE)), 'sha256': sha(p)} for p in files],
              'local_links_checked': len(parser.paths), 'acquisition_status_at_capture': captured['status'],
              'candidate_hours_admitted': 0, 'goal_complete': False, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

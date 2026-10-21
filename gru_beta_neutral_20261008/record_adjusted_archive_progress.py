"""Publish raw-source diagnostics and failed recovery evidence with no admission."""
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
OUT = PROBES / 'huggingface/joinquant_targeted_concordance_20261009'


def main():
    now = datetime.now(timezone.utc)
    audit_path = OUT / 'targeted_concordance_receipt.json'
    verification_path = OUT / 'independent_findings_verification.json'
    recovery_path = OUT / 'singleton_partial_recovery/isolated_recovery_receipt.json'
    audit, verification, recovery = [json.loads(path.read_text()) for path in [audit_path, verification_path, recovery_path]]
    assert verification['passed'] and verification['audited_receipt_sha256'] == sha(audit_path)
    assert verification['recovery_hypothesis_receipt_sha256'] == sha(recovery_path)
    assert sha(ROOT / 'gru_beta_neutral_20261008/verify_adjusted_archive_findings.py') == verification['source_code_sha256']
    assert audit['all_secondary_daily_open_and_close_agree_with_primary'] and audit['target_missing_stockdays_checked'] == 13
    assert not audit['candidate_changed'] and audit['candidate_hours_admitted'] == 0
    assert recovery['research_donor_hours'] == 0 and len(recovery['additional_amount_conflicts']) == 9
    tdx_path = PROBES / 'tdx_remaining_documented_peers_20261009/probe_receipt.json'
    tdx = json.loads(tdx_path.read_text())
    assert tdx['bounded_run_complete'] and len(tdx['results']) == 7
    assert all(row['status'] == 'public_probe_failed' and not row['requests'] for row in tdx['results'])
    assert sha(tdx['invocation_basis_path']) == tdx['invocation_basis_sha256']
    assert sha(tdx['wrapper_source_code_path']) == tdx['wrapper_source_code_sha256']
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    meta = BASE / data['latest_candidate_version'] / 'dataset/meta.json'
    assert json.loads(meta.read_text())['data_fingerprint'] == data['new_X_fingerprint']
    assert data['minimum_requested_missing_primary_trading_hours'] == 3042
    assert data['full_union_tail_missing_nonmember_primary_trading_hours'] == 84860
    assert data['official_subset_coverage_target_proven_unattainable_years'] == [2020, 2021, 2022]
    snapshot = BASE / ('progress_snapshots/adjusted_archive_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for file in files:
        shutil.copyfile(file, snapshot / file.name)
    data.update({'adjusted_archive_targeted_source_audit_receipt': str(audit_path.relative_to(BASE)),
        'adjusted_archive_independent_verification_receipt': str(verification_path.relative_to(BASE)),
        'adjusted_archive_unique_inverse_hypothesis_receipt': str(recovery_path.relative_to(BASE)),
        'TDX_remaining_seven_documented_peers_receipt': str(tdx_path.relative_to(BASE)),
        'new_source_findings_hours_admitted': 0, 'production_ready': False})
    manifest['required_work_remaining'][2] += '; public adjusted-minute archive on 13 remaining conflict dates corroborates the daily reference but contains lossy adjusted price/volume fields; unique inversion hypothesis fails minute amount/range consistency, so it is not an admitted substitute'
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    with files[1].open(newline='') as stream:
        rows = list(csv.reader(stream))
    rows.append(['复权分钟档案定点核验', '两股13个缺口股票日及原始分钟对照完成', '腾讯日线开收盘与原参考一致；复权价格有11580个单元无法唯一反推；唯一反推试验仍有9分钟成交额矛盾', str(verification_path.relative_to(BASE)), '0新小时；缺口3042及84860保持；另7个通达信节点均失败'])
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    assert '<h2>复权分钟档案：定点取数与恢复假设核验</h2>' not in html
    block = '<h2>复权分钟档案：定点取数与恢复假设核验</h2><p>从固定公开快照取得 000099、002831 两股文件，约 164 MB，全部通过 LFS SHA256。实际文件目录有 1,450 只深市股票，并未证实说明书所称 5,267 股；另一个公开镜像的全部文件名及 LFS 元数据相同，样本实际截至 2022 年底，不能延长 2026 年尾段。</p><p>核对剩余缺口中的 13 个股票日，以及 002831 的 2022-06-06 原始分钟对照。新增腾讯不复权日线在所有样本的开盘与收盘上均与现有日线参考一致，成交量差在 100 股内。复权档案的价格与成交量均受因子影响：14 个样本共有 11,580 个价格单元对应多个可能原始分价格，不作任意选值。</p><p>在原始分钟对照中，收盘投影 240 根一致，成交量唯一反推 240 根一致，但这不能证明其它字段及股票均正确。进一步尝试中信海直 2018-06-27 的唯一反推：全天成交量与最后收盘可与日线精确核对，下午两个小时的价格与量也各有唯一反推值；然而逐分钟成交额／价量范围仍出现矛盾，全日共 9 个明确冲突分钟，故四个小时均未准入。首次失败代码与原因已保存，没有补入原始价格或分钟。</p>'
    block += f'<p><a href="{audit_path.relative_to(BASE)}">复权与原始来源核验</a>；<a href="{verification_path.relative_to(BASE)}">独立十进制枚举复算</a>；<a href="{recovery_path.relative_to(BASE)}">唯一反推假设未通过的证据</a></p>'
    block += f'<p>文档列出的其余七个通达信公开节点也已完成单次有界查询，均连接或查询失败，没有取得行情。<a href="{tdx_path.relative_to(BASE)}">七节点访问记录</a>。本批 0 个新小时纳入，最低范围仍缺 3,042 小时、并集尾段仍缺 84,860 小时；停牌官方上限结论保持。</p>'
    html = html.replace('<h2>可复核文件</h2>', block + '<h2>可复核文件</h2>')
    links = Links()
    links.feed(html)
    assert all((BASE / value).exists() for value in links.paths)
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open('w', newline='') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(html)
    result = {'checked_utc': now.isoformat(), 'source_code_sha256': sha(Path(__file__)),
        'files_updated': [{'path': str(file), 'sha256': sha(file)} for file in files],
        'prior_reports_snapshot': str(snapshot), 'report_local_links_checked': len(links.paths),
        'candidate_fingerprint_unchanged': data['new_X_fingerprint'], 'candidate_changed': False,
        'new_hours_admitted': 0, 'minimum_trading_gap_unchanged': 3042,
        'nonmember_tail_gap_unchanged': 84860, 'goal_complete': False}
    (OUT / 'progress_recording_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

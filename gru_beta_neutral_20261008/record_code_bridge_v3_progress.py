"""Publish only completed, independently checked local research progress."""
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
NAME = 'backfilled_panel_candidate_code_bridge_v3'
OUT = BASE / NAME


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths += [value for key, value in attrs if key == 'href' and not value.startswith(('https:', 'http:', '#'))]


def main():
    audit = json.loads((OUT / 'independent_completion_audit.json').read_text())
    assert audit['passed'] and not audit['production_ready']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_code_bridge_v3.py') == audit['source_code_sha256']
    meta = json.loads((OUT / 'dataset/meta.json').read_text())
    assert sha(OUT / 'dataset/meta.json') == audit['candidate_meta_sha256']
    gap = json.loads((OUT / 'gap_audit/receipt.json').read_text())
    hour = json.loads((OUT / 'hour_panel_receipt.json').read_text())
    causes = list(csv.DictReader((OUT / 'gap_audit/member_window_causes_by_year.csv').open()))
    cause_by_year = {int(r['year']): int(r['incomplete_member_windows_containing_cause']) for r in causes if r['cause'] == 'primary_suspension'}
    ceilings = []
    for r in gap['coverage_by_year']:
        if r['year'] in [2020, 2021, 2022]:
            upper = 1 - cause_by_year[r['year']] / r['original_member_stockhour_denominator']
            ceilings.append({'year': r['year'], 'original_member_stockhour_denominator': r['original_member_stockhour_denominator'],
                             'windows_containing_observed_primary_suspension': cause_by_year[r['year']],
                             'actual_complete80_coverage': r['coverage'], 'optimistic_ceiling_if_all_other_gaps_fixed': upper,
                             'unchanged_target': r['target'], 'target_above_ceiling': r['target'] > upper})
    now = datetime.now(timezone.utc)
    ceiling = {'checked_at_utc': now.isoformat(), 'rows': ceilings,
               'formula': '1 - distinct incomplete member windows containing observed primary suspension / unchanged original historical-member stockhour denominator',
               'limitations': 'Conditional on independently acquired vendor daily suspension statuses; not an exchange-certified suspension audit.',
               'gap_receipt_sha256': sha(OUT / 'gap_audit/receipt.json'), 'cause_table_sha256': sha(OUT / 'gap_audit/member_window_causes_by_year.csv'),
               'original_denominator_and_targets_preserved': True}
    (OUT / 'gap_audit/strict_window_suspension_coverage_ceiling.json').write_text(json.dumps(ceiling, indent=2))
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    snapshot = BASE / ('progress_snapshots/code_bridge_v3_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, snapshot / path.name)
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    data.update({'latest_candidate_version': NAME, 'new_X_fingerprint': meta['data_fingerprint'], 'new_X_shape': [8476, 1178, 122],
                 'candidate_panel_receipt': NAME + '/dataset/dataset_audit.json', 'independent_array_sha_checks': NAME + '/independent_completion_audit.json',
                 'gap_audit_receipt': NAME + '/gap_audit/receipt.json', 'coverage_by_year': hour['coverage_by_year'],
                 'minimum_requested_missing_primary_trading_hours': gap['minimum_intervals']['missing_primary_trading_hours'],
                 'old_code_factor_bridges_applied_to_candidate': True, 'new_official_old_code_hours': 1132,
                 'old_code_minute_delivery_receipt': 'official_old_code_minute_delivery_20261009/receipt.json',
                 'old_code_minute_audit_receipt': 'official_old_code_minute_audit_20261009/receipt.json',
                 'code_bridge_candidate_independent_receipt': NAME + '/independent_completion_audit.json',
                 'strict_window_suspension_coverage_ceiling_receipt': NAME + '/gap_audit/strict_window_suspension_coverage_ceiling.json',
                 'previous_candidate_preserved': 'backfilled_panel_candidate/dataset/meta.json',
                 'previous_candidate_fingerprint': '36961dd4cd09f3f6cb82afbb348bb2f980d5e1e68e204b39b60fb554c6fc86ce',
                 'Chrome_extension_connected': True})
    manifest['required_work_remaining'] = [
        'Recover or independently resolve full-union minute tail (84860 nonmember primary-daily trading hours still absent)',
        'Resolve minimum-interval minute source conflicts and unavailable pre-first-factor states (10218 primary-daily trading hours currently missing)',
        'Independent source extreme/identity/live quality admission before production certification; resolve 001872.SZ historical daily-price disagreement',
        'Independently verify suspension statuses and address unchanged 2020-2022 coverage gate failures; retain original denominators and never fabricate suspension trades',
    ]
    manifest['checked_utc'] = now.isoformat()
    files[0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with files[1].open(newline='') as handle:
        rows = list(csv.reader(handle))
    for row in rows:
        if row[0] == '并集行情补齐':
            row[1:] = ['第三版候选X与标签组装并独立核验完成；全范围仍有缺口', '额外补回旧代码283日1132真实小时；2024/2025窗口覆盖99.73%/99.69%', NAME + '/independent_completion_audit.json', '最低区间仍缺10218小时；全并集非成分股尾段仍缺84860小时；2020—2022门槛未过']
        elif row[0] == '慢因子与PIT':
            row[1:] = ['第三版完整重算与独立核验完成', '64因子ID公式不变；1059478原成分PIT行核验；19列与上一候选完全一致', NAME + '/slow_features/slow_panel_receipt.json', '8项日期核验0违规；156截面归一化独立复算误差0']
        elif row[0] == '代码沿革与旧因子':
            row[1:] = ['两只因子衔接已应用到独立第三版；一只待核验', '001914/302132因子已衔接；300114原分钟283日补回1132小时及1132完整窗口', NAME + '/dataset/meta.json', '原始文件、冻结面板及此前候选保持不变；001872在2017-10-24仍有价量冲突']
    with files[1].open('w', newline='') as handle:
        csv.writer(handle).writerows(rows)
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    html = html.replace('1178 股的新研究候选面板已组装。', '1178 股的第三版研究候选面板已组装并独立核验，旧代码分钟额外补回 1,132 个真实小时。')
    html = html.replace('包含真实停牌', '包含独立日线标记的停牌')
    html = html.replace('backfilled_panel_candidate/', NAME + '/')
    old = r'<li><strong>旧代码因子衔接已有两只准备完成。</strong>.*?</li>'
    new = '<li><strong>旧代码分钟及因子已应用到独立第三版。</strong>官方公告确认三组代码对应关系。001914 和 302132 的旧因子衔接已应用，64 个慢因子及完整 X 重新生成。原始交付包中找到 300114 的 283 个交易日分钟文件，全部通过原始分钟有效性及独立日线开收盘价、成交量、价格范围核验，额外补回 1,132 个小时和 1,132 个完整窗口。2024 年增加 949 个成分窗口，2025 年增加 183 个。001872 的 2017-10-24 价量冲突仍保留待核验。原始文件、冻结面板及此前候选版本保持不变。<a href="official_old_code_minute_audit_20261009/receipt.json">逐日原始分钟核验</a>；<a href="' + NAME + '/independent_completion_audit.json">第三版独立复核</a></li>'
    html, replaced = re.subn(old, new, html, count=1)
    assert replaced == 1
    html = html.replace('目前完整 4,671,586', '目前完整 4,672,718').replace('为 11,350', '为 10,218')
    html = html.replace('35,236,254 个有限标签', f"{hour['label_checks']['finite_labels_checked']:,} 个有限标签" if 'finite_labels_checked' in hour['label_checks'] else '全部有限标签')
    oldfingerprint = data['previous_candidate_fingerprint']
    html = html.replace(oldfingerprint, meta['data_fingerprint'])
    table = '<table><thead><tr><th>年份</th><th>实际覆盖率</th><th>计划目标</th><th>结果</th><th>失效窗口</th><th>其中含停牌</th></tr></thead><tbody>'
    for row in gap['coverage_by_year']:
        year = row['year'];missing = row['original_member_stockhour_denominator'] - row['complete80_stockhours']
        status = '参考年，无设定目标' if row['target'] is None else '达到目标' if row['target_passed'] else '未达到目标'
        cls = '' if row['target'] is None else 'pass' if row['target_passed'] else 'fail'
        target = '—' if row['target'] is None else f"{row['target']:.1%}"
        table += f'<tr><td>{year}</td><td>{row["coverage"]:.2%}</td><td>{target}</td><td class="{cls}">{status}</td><td>{missing:,}</td><td>{cause_by_year[year]/missing:.1%}</td></tr>'
    table += '</tbody></table>'
    html, replaced = re.subn(r'<table><thead><tr><th>年份</th><th>实际覆盖率</th>.*?</table>', lambda _: table, html, count=1)
    assert replaced == 1
    html = html.replace('<h2>已完成的补齐与核验</h2>', '<p class="notes">第三版逐个复核全部 1,132 个补回小时，156 个完整截面的归一化独立复算误差为零。财务 PIT 19 列与上一候选完全一致，3,528 份沿用股票文件及原始、此前候选文件指纹保持一致。覆盖率上限仍依赖供应商停牌分类，尚未取得交易所级停牌认证。</p><h2>已完成的补齐与核验</h2>')
    parser = Links();parser.feed(html)
    assert all((BASE / p).exists() for p in parser.paths), [p for p in parser.paths if not (BASE / p).exists()]
    files[2].write_text(html)
    result = {'checked_utc': now.isoformat(), 'candidate_version': NAME, 'candidate_fingerprint': meta['data_fingerprint'],
              'files_updated': [{'path': str(p), 'sha256': sha(p)} for p in files], 'prior_reports_snapshot': str(snapshot),
              'local_report_links_checked': len(parser.paths), 'goal_complete': False, 'production_ready': False,
              'source_code_sha256': sha(Path(__file__))}
    (OUT / 'progress_recording_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

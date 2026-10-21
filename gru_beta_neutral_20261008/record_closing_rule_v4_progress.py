"""Record the independently verified historical closing-statistic candidate."""
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
NAME = 'backfilled_panel_candidate_closing_rule_v4'
PREVIOUS = 'backfilled_panel_candidate_code_bridge_v3'
OUT = BASE / NAME
RULE = BASE / 'public_source_probes/sse_historical_close_rule_20261009'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths += [v for k, v in attrs if k == 'href' and not v.startswith(('https:', 'http:', '#'))]


def replace_one(text, pattern, value):
    text, count = re.subn(pattern, lambda _: value, text, count=1, flags=re.S)
    assert count == 1, pattern
    return text


def main():
    audit = json.loads((OUT / 'independent_completion_audit.json').read_text())
    assert audit['passed'] and not audit['production_ready']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_closing_rule_v4.py') == audit['source_code_sha256']
    assert sha(OUT / 'dataset/meta.json') == audit['candidate_meta_sha256']
    meta = json.loads((OUT / 'dataset/meta.json').read_text())
    hour = json.loads((OUT / 'hour_panel_receipt.json').read_text())
    gap = json.loads((OUT / 'gap_audit/receipt.json').read_text())
    old_meta = json.loads((BASE / PREVIOUS / 'dataset/meta.json').read_text())
    added = audit['added_raw_minute_quote_hours_verified']
    missing = gap['minimum_intervals']['missing_primary_trading_hours']
    tail = gap['full_union_august_september']['missing_nonmember_primary_trading_hours']
    assert added == hour['new_historical_closing_statistic_hours']
    with (OUT / 'gap_audit/member_window_causes_by_year.csv').open(newline='') as handle:
        cause_rows = list(csv.DictReader(handle))
    suspension = {int(r['year']): int(r['incomplete_member_windows_containing_cause'])
                  for r in cause_rows if r['cause'] == 'primary_suspension'}
    now = datetime.now(timezone.utc)
    ceilings = []
    for row in gap['coverage_by_year']:
        if row['year'] in [2020, 2021, 2022]:
            upper = 1 - suspension[row['year']] / row['original_member_stockhour_denominator']
            ceilings.append({'year': row['year'], 'original_member_stockhour_denominator': row['original_member_stockhour_denominator'],
                             'windows_containing_observed_primary_suspension': suspension[row['year']],
                             'actual_complete80_coverage': row['coverage'], 'optimistic_ceiling_if_all_other_gaps_fixed': upper,
                             'unchanged_target': row['target'], 'target_above_ceiling': row['target'] > upper})
    ceiling = {'checked_at_utc': now.isoformat(), 'rows': ceilings,
               'formula': '1 - distinct incomplete member windows containing observed primary suspension / unchanged original historical-member stockhour denominator',
               'limitations': 'Conditional on independently acquired vendor daily suspension statuses; not an exchange-certified suspension audit.',
               'gap_receipt_sha256': sha(OUT / 'gap_audit/receipt.json'),
               'cause_table_sha256': sha(OUT / 'gap_audit/member_window_causes_by_year.csv'),
               'original_denominator_and_targets_preserved': True}
    ceiling_path = OUT / 'gap_audit/strict_window_suspension_coverage_ceiling.json'
    ceiling_path.write_text(json.dumps(ceiling, indent=2))
    files = [BASE / 'execution_manifest.json', BASE / '执行进度_2026-10-08.csv', BASE / '数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    assert manifest['data_backfill']['latest_candidate_version'] == PREVIOUS
    snapshot = BASE / ('progress_snapshots/closing_rule_v4_' + now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, snapshot / path.name)
    data = manifest['data_backfill']
    data.update({'latest_candidate_version': NAME, 'new_X_fingerprint': meta['data_fingerprint'],
                 'new_X_shape': hour['shape'] + [len(meta['features'])], 'candidate_panel_receipt': NAME + '/dataset/dataset_audit.json',
                 'independent_array_sha_checks': NAME + '/independent_completion_audit.json',
                 'gap_audit_receipt': NAME + '/gap_audit/receipt.json', 'coverage_by_year': hour['coverage_by_year'],
                 'new_donor_hours': hour['new_donor_hours'], 'minimum_requested_missing_primary_trading_hours': missing,
                 'full_union_tail_missing_nonmember_primary_trading_hours': tail,
                 'new_historical_closing_statistic_hours': added,
                 'historical_closing_provenance': hour['historical_closing_provenance'],
                 'historical_closing_candidate_independent_receipt': NAME + '/independent_completion_audit.json',
                 'strict_window_suspension_coverage_ceiling_receipt': NAME + '/gap_audit/strict_window_suspension_coverage_ceiling.json',
                 'previous_candidate_preserved': PREVIOUS + '/dataset/meta.json',
                 'previous_candidate_fingerprint': old_meta['data_fingerprint'],
                 'public_mirror_comparison_receipt': 'public_source_probes/huggingface/latest_public_check_20261009/vessel888_canonical_file_comparison.json',
                 'all_later_year_coverage_gates_passed': all(r['target_passed'] for r in gap['coverage_by_year'] if r['year'] >= 2020),
                 'production_ready': False})
    manifest['required_work_remaining'] = [
        f'Recover or independently resolve full-union minute tail ({tail} nonmember primary-daily trading hours still absent; latest public mirror has byte-identical canonical files)',
        f'Resolve remaining minimum-interval minute source conflicts and missing observed stockdays ({missing} primary-daily trading hours currently missing); separately verify five IPO factor prefixes outside these intervals',
        'Independent source extreme/identity/live quality admission before production certification; resolve 001872.SZ historical daily-price disagreement; final clock-minute concordance does not certify exact trailing minute relative to last tick',
        'Independently verify suspension statuses and address unchanged 2020-2022 coverage gate failures; retain original denominators and never fabricate suspension trades',
    ]
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    with files[1].open(newline='') as handle:
        rows = list(csv.reader(handle))
    for row in rows:
        if row[0] == '并集行情补齐':
            row[1:] = ['第四版X与标签组装并独立核验完成；全范围仍有缺口', f'历史沪市收盘统计核对新增{added}真实小时；最低区间缺口降至{missing}', NAME + '/independent_completion_audit.json', f'尾段仍缺{tail}小时；2020—2022门槛未过；分钟原始价格未改写']
        elif row[0] == '慢因子与PIT':
            row[1:] = ['第四版沿用逐字节相同日频/PIT并重新组装X；独立复核通过', '全部98列慢特征及其标准化X值与第三版完全一致；64因子公式不变', NAME + '/independent_completion_audit.json', f'{audit["normalization_sample_full_crosssections"]}完整截面归一化复算；2019年以后标签与执行价格逐字一致；均线特征差异至多{audit["rolling_ma_max_absolute_difference_v3"]:.3g}，全部差异独立复算']
        elif row[0] == '代码沿革与旧因子':
            row[1:] = ['第三版两只因子与1132旧代码真实小时在第四版保留', '001914/302132因子已衔接；300114原分钟283日补回1132小时', NAME + '/dataset/meta.json', '001872在2017-10-24仍有价量冲突；五只IPO早期因子待独立核验']
    html = files[2].read_text()
    html = re.sub(r'更新时间：2026-10-09 \d\d:\d\d', '更新时间：' + now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'), html)
    lead = f'<p class="lead">1178 股的第四版研究候选面板已组装并独立核验。历史沪市收盘统计口径核对额外纳入 {added:,} 个原始分钟聚合小时；最低补齐区间缺口由 10,218 降到 {missing:,}。2019 年窗口覆盖率仍为 98.89%；2020—2022 年仍未达到 99.5% 目标，绝大部分失效窗口包含独立日线标记的停牌。完整并集的 2026 年 8—9 月非成分股分钟尾段仍未补齐。</p>'
    html = replace_one(html, r'<p class="lead">1178 股.*?</p>', lead)
    conflict = f'<li><strong>部分来源仍不能相互验证，最低区间缺口剩 {missing:,} 股小时。</strong>2018 年部分沪市官方日线收盘价采用成交均价，与分钟最后报价不同。按该时期的独立收盘统计口径核对后，最低区间补回 7,176 股小时。剩余缺口包括历史首分钟开盘冲突、未观测到的分钟股票日等。09:30 零成交量的前收盘报价不能替代开盘。五只新股上市最初数日的因子状态在最低区间之外另行核验，尚未应用。</li>'
    html = replace_one(html, r'<li><strong>部分来源不能相互验证。</strong>.*?</li>', conflict)
    new = f'<h2>历史沪市收盘统计：第四版新增核验</h2><p>上交所原收盘价规则与分钟最后成交价口径不同；2018-08-20 起采用收盘集合竞价。新规则仅用于 2018-01-01 至 2018-08-17 的沪市来源核对。438 只股票的 32,596 个股票日通过原有分钟网格、开盘价、成交量、价格范围及因子门槛，并额外通过末分钟成交均价明确按分取整、独立日线收盘价和全天成交额核验。<a href="https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988782.shtml">历史交易规则</a>；<a href="https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20180806_4607055.shtml">2018 年实施公告</a>；<a href="public_source_probes/sse_historical_close_rule_20261009/audit_receipt_v2.json">逐日来源核验</a></p><p>第四版额外纳入 {added:,} 个小时，完整并集增加 {audit["added_complete80_windows"]:,} 个完整窗口；2018 年成分窗口增加 1,007 个，覆盖率由 84.77% 升至 84.97%。原始分钟开高低收和成交量保持观察值，没有用日线收盘价或成交均价改写分钟。2019 年以后小时、标签、执行价格完全等于第三版；全部 98 个慢特征及其标准化值逐项相同。<a href="{NAME}/independent_completion_audit.json">第四版独立复核</a></p><p class="notes">观察到的分钟报价高低点可能遗漏实际成交极值，该差异保留；成交额核验要求落在独立日线真实价格范围内。钟表分钟并不能证明与最后一笔成交对应的精确前 60 秒窗口。这是历史来源一致性核对，尚未取得逐笔极值或实时准入认证。</p>'
    new = new.replace('2019 年以后小时、标签、执行价格完全等于第三版', f'2019 年以后标签与执行价格逐字等于第三版；21 个小时特征和全部缺失掩码相同，3 个均线偏离特征有 {audit["rolling_ma_floating_point_differences"]:,} 个浮点尾数差异，最大 {audit["rolling_ma_max_absolute_difference_v3"]:.3g}，全部用原始窗口独立复算，未改写候选值')
    html = html.replace('<h2>原始交付与代码沿革：本次新增核验</h2>', new + '<h2>原始交付与代码沿革：已完成核验</h2>')
    table = '<table><thead><tr><th>年份</th><th>实际覆盖率</th><th>计划目标</th><th>结果</th><th>失效窗口</th><th>其中含停牌</th></tr></thead><tbody>'
    for row in gap['coverage_by_year']:
        year = row['year']; invalid = row['original_member_stockhour_denominator'] - row['complete80_stockhours']
        status = '参考年，无设定目标' if row['target'] is None else '达到目标' if row['target_passed'] else '未达到目标'
        cls = '' if row['target'] is None else 'pass' if row['target_passed'] else 'fail'
        target = '—' if row['target'] is None else f'{row["target"]:.1%}'
        table += f'<tr><td>{year}</td><td>{row["coverage"]:.2%}</td><td>{target}</td><td class="{cls}">{status}</td><td>{invalid:,}</td><td>{suspension[year]/invalid:.1%}</td></tr>'
    table += '</tbody></table>'
    html = replace_one(html, r'<table><thead><tr><th>年份</th><th>实际覆盖率</th>.*?</table>', table)
    html = html.replace(f'{PREVIOUS}/gap_audit/', f'{NAME}/gap_audit/').replace(f'{PREVIOUS}/dataset/', f'{NAME}/dataset/')
    html = html.replace(f'{PREVIOUS}/slow_features/', f'{NAME}/slow_features/')
    html = html.replace('候选新增 4,647,944 个完整小时', f'公开分钟来源累计新增 {hour["new_donor_hours"]:,} 个完整小时，另保留旧代码原始交付的 1,132 个小时')
    html = html.replace('目前完整 4,672,718', f'目前完整 {gap["minimum_intervals"]["complete_hours"]:,}')
    html = html.replace('为 10,218，主要是早期价格冲突及少量复权状态缺口', f'为 {missing:,}，主要是剩余来源价格冲突与缺少原始分钟股票日')
    html = html.replace('全部有限标签与执行价格独立复算一致', f'{hour["label_checks"]["compared_finite_labels"]:,} 个有限标签与执行价格复算一致')
    html = html.replace(old_meta['data_fingerprint'], meta['data_fingerprint'])
    mirror = '<p class="notes">最新公开分钟镜像也已检查：vessel888 的 1178 个并集股票文件全部与当前快照的文件指纹和大小相同，无法延长尾段。<a href="public_source_probes/huggingface/latest_public_check_20261009/vessel888_canonical_file_comparison.json">全部文件逐一比对证据</a>。新浪取得五只 IPO 股票的因子日期记录，仍需处理口径对齐，未替换现有因子。</p>'
    html = html.replace('<h2>可复核文件</h2>', mirror + '<h2>可复核文件</h2>')
    parser = Links(); parser.feed(html)
    assert all((BASE / p).exists() for p in parser.paths), [p for p in parser.paths if not (BASE / p).exists()]
    # Write only after the complete report and all references have been validated.
    files[0].write_text(manifest_text)
    with files[1].open('w', newline='') as handle:
        csv.writer(handle).writerows(rows)
    files[2].write_text(html)
    result = {'checked_utc': now.isoformat(), 'candidate_version': NAME, 'candidate_fingerprint': meta['data_fingerprint'],
              'files_updated': [{'path': str(p), 'sha256': sha(p)} for p in files], 'prior_reports_snapshot': str(snapshot),
              'local_report_links_checked': len(parser.paths), 'goal_complete': False, 'production_ready': False,
              'source_code_sha256': sha(Path(__file__))}
    (OUT / 'progress_recording_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

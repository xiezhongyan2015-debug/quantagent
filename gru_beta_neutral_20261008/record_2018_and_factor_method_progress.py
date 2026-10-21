"""Publish new independently verified findings, retaining the immutable V5 panel."""
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
PROBES = BASE / 'public_source_probes'
WIND = PROBES / 'additional_public_archives_20261009/wind17'
IPO = PROBES / 'ipo_factor_rounding_method_20261009'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths.extend(v for k,v in attrs if k == 'href' and not v.startswith(('http:', 'https:', '#')))


def main():
    now = datetime.now(timezone.utc)
    receipt_path = IPO / '2018_and_factor_method_progress_recording_receipt.json'
    assert not receipt_path.exists()
    wp = WIND / 'all_2018_minimum_gaps/independent_all_2018_native_verification.json'
    ip = IPO / 'independent_canonical_basis_prefix_verification.json'
    w, i = [json.loads(p.read_text()) for p in [wp,ip]]
    assert w['passed'] and i['passed']
    assert sha(ROOT/'gru_beta_neutral_20261008/verify_wind17_2018_native_findings.py') == w['source_code_sha256']
    assert sha(ROOT/'gru_beta_neutral_20261008/verify_ipo_factor_rounding_basis.py') == i['source_code_sha256']
    dp = wp.parent/'all_2018_gap_audit_receipt.json'
    npth = wp.parent/'all_native_missing_hours_receipt.json'
    cp = wp.parent/'bounded_coarse_eligible_days_receipt.json'
    assert sha(dp) == w['daily_audit_sha256'] and sha(npth) == w['native_audit_sha256']
    assert sha(cp) == w['coarse_terminal_failure_sha256']
    assert sha(i['audited_receipt_path']) == i['audited_receipt_sha256']
    assert sha(i['official_table_observation_path']) == i['official_table_observation_sha256']
    assert sha(IPO/'source_acquisition_receipt.json') == i['source_acquisition_receipt_sha256']
    assert w['actual_missing_hours'] == 1217 and w['native_amount_pass_hours'] == 24
    assert w['coarse_reference_qualified_hour_count'] is None and w['coarse_reference_rows_acquired'] == 0
    assert i['canonical_values_reproduced_exactly_at_six_decimals'] == 10
    assert i['Sina_values_reproduced_exactly_at_thirteen_decimals'] == 10
    assert i['official_cash_rows_confirmed'] == 8 and i['qualified_unapplied_hours'] == 8
    assert w['candidate_hours_admitted'] == i['candidate_hours_admitted'] == 0
    files = [BASE/'execution_manifest.json',BASE/'执行进度_2026-10-08.csv',BASE/'数据补齐核验与实验进度_2026-10-09.html']
    manifest = json.loads(files[0].read_text())
    data = manifest['data_backfill']
    meta = BASE/data['latest_candidate_version']/'dataset/meta.json'
    assert sha(meta) == w['candidate_meta_sha256'] == i['candidate_meta_sha256']
    assert json.loads(meta.read_text())['data_fingerprint'] == data['new_X_fingerprint'] == w['candidate_fingerprint'] == i['candidate_fingerprint']
    assert data['minimum_requested_missing_primary_trading_hours'] == 3042
    assert data['full_union_tail_missing_nonmember_primary_trading_hours'] == 84860
    archive_path = WIND/'verified_whole_2018_archive/archive_acquisition_receipt.json'
    archive = json.loads(archive_path.read_text())
    assert archive['whole_LFS_SHA256_verified'] and archive['expected_whole_LFS_SHA256'] == w['whole_archive_sha256']
    snapshot = BASE/('progress_snapshots/2018_and_factor_method_'+now.strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir(parents=True)
    for file in files:
        shutil.copyfile(file,snapshot/file.name)
    references = {'whole_2018_archive':archive_path,'all_2018_independent_verification':wp,
                  '2018_coarse_terminal_failure':cp,'IPO_factor_independent_verification':ip,
                  'official_rendered_cash_table_observation':Path(i['official_table_observation_path'])}
    data['wind17_2018_public_archive_review'] = {
        'pinned_revision':'ca4c51770f1fafb40317cc35bfd92e7c2be17827',
        'whole_archive_bytes':2310398709,'whole_archive_sha256':w['whole_archive_sha256'],
        'whole_archive_sha256_verified':True,'stocks':93,'stockdays':310,'actual_missing_hours':1217,
        'daily_and_selected_positive_hour_pass_hours':148,'native_amount_pass_hours':24,
        'coarse_login_attempts_terminally_timed_out':2,'coarse_rows_acquired':0,
        'coarse_qualified_hours':None,'coarse_qualified_hours_null_means_not_tested':True,
        'candidate_hours_admitted':0,'quantity_precision_certified':False}
    data['IPO_factor_method_rounding_review'] = {
        'symbols':['688582.SH','688709.SH'],'canonical_factors_reproduced_at_six_decimals':10,
        'source_factors_reproduced_at_thirteen_decimals':10,
        'maximum_source_absolute_error':i['maximum_Sina_absolute_error'],
        'official_rendered_cash_rows_confirmed':8,'qualified_unapplied_hours':8,
        'calculated_prefix_factors':{r['symbol']:r['calculated_prefix_factor'] for r in i['results']},
        'prefix_values_calculated_not_directly_observed_at_IPO':True,
        'known_canonical_factors_preserved':True,'candidate_hours_admitted':0,
        'complete_company_action_history_certified':False,'first_dividend_issuer_PDF_acquired':False,
        'provider_method_explanation_is_inference_not_provider_algorithm_certification':True}
    data['new_review_references'] = {k:{'path':str(p.relative_to(BASE)),'sha256':sha(p)} for k,p in references.items()}
    data['Chrome_extension_connected'] = True
    data['new_source_findings_hours_admitted'] = 0
    data['production_ready'] = False
    manifest['checked_utc'] = manifest['latest_local_updated_utc'] = now.isoformat()
    manifest['required_work_remaining'][1] = ('Resolve remaining minimum-interval source conflicts and absent observed minute stockdays (3042 primary-daily trading hours still missing). The 688582.SH and 688709.SH factor-method difference is now numerically explained and corroborated by eight official rendered cash-table rows; their calculated canonical-basis eight-hour IPO proposal remains unapplied, outside the minimum interval, and not production-certified.')
    manifest['required_work_remaining'].append('2018 新整卷独立核验覆盖 93 股、310 股票日、1217 个实际缺失小时；仅 24 小时通过原生量额检查，五分钟参照两次登录超时而未核对，不能按通过或失败计数。2019—2022 新来源尚余 829 个实际缺失小时待核验；来源数量精度认证和完整并集尾段仍未解决。')
    with files[1].open(newline='') as stream:
        rows = list(csv.reader(stream))
    for row in rows:
        if row[0] == '代码沿革与旧因子':
            row[4] = '688582/688709方法差异独立复算已解释；官方分红表8行一致，计算前缀8小时尚未应用；001872历史价量冲突保留'
    rows.extend([
        ['2018新公开分钟整卷','93股、310股票日独立复算完成','2.31GB整卷SHA256通过；1217小时中148日线及选定小时量通过，24原生量额通过',str(wp.relative_to(BASE)),'五分钟参照两次连接超时，24小时未核对；0新增纳入'],
        ['两股IPO复权方法差异','数值解释与独立核验通过','10既有因子按6位复现；10新浪因子按13位复现；上交所Chrome分红表8行一致',str(ip.relative_to(BASE)),'计算前缀8小时未应用；未认证全部公司行动或实盘；最低与尾段缺口保持']
    ])
    page = files[2].read_text()
    page = re.sub(r'更新时间：2026-10-09 \d\d:\d\d','更新时间：'+now.astimezone(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M'),page)
    page = page.replace('另外两只的复权方法冲突仍待解决。','另外两只的复权方法差异已独立复算出数值解释，8 小时计算前缀尚未应用。')
    page = page.replace('688582.SH、688709.SH 因两来源复权方法存在冲突，未应用其 IPO 前缀。','688582.SH、688709.SH 的复权差异现已数值解释，并获官方渲染分红表核对；8 小时计算前缀仍未应用，详见下方新增核验。')
    page = page.replace('其他年份只获取选定文件的压缩字节范围并校验 CRC，没有将局部检查表述为整卷 SHA256 通过。','前次更新时其他年份只获取选定文件的压缩字节范围并校验 CRC；本次另完成了 2018 整卷指纹核验，见下节。其余年份仍未宣称整卷指纹通过。')
    block = '<h2>2018 新整卷：1217 个缺失小时的实际核验</h2><p>固定同一公开版本的 2018.zip 已完整下载 2,310,398,709 字节，整卷 SHA256 与发布元数据一致。93 只目标股票全部找到，310 个缺口股票日都有原生分钟文件；独立复核重新计算整卷指纹、全部选定 CSV 字节与 CRC，并逐个对照当前 V5 的实际缺失时段。</p>'
    block += '<table><thead><tr><th>检查</th><th>实际结果</th></tr></thead><tbody><tr><td>目标缺失小时</td><td>1217</td></tr><tr><td>日线、网格和选定小时正成交量通过</td><td>148</td></tr><tr><td>60 根原生分钟量额范围同时通过</td><td>24</td></tr><tr><td>独立五分钟参照</td><td>未核对：两次登录终态超时，0 行参照数据</td></tr><tr><td>新增纳入候选</td><td>0</td></tr></tbody></table>'
    block += '<p>271 个股票日超过原有全天成交量差 100 股门槛；量额按来源原值检查，未反推丢失股数。24 个小时只保留为待核对，不能将参照未取得写成已通过或已失败。分钟股数精度和来源语义仍未完成准入，2019—2022 年此新来源另有 829 个实际缺失小时尚待审计。最低缺口仍为 3,042，非成员尾段仍为 84,860，V5 面板保持。</p>'
    block += '<p>'+ '；'.join(f'<a href="{p.relative_to(BASE)}">{label}</a>' for label,p in [('2018整卷核验',archive_path),('2018独立复算',wp),('有界参照连接终态',cp)])+'</p>'
    block += '<h2>Chrome 官方分红表与两股复权方法解释</h2><p>Chrome 已成功读取上交所芯动联科、成都华微的动态公司信息页面。官方页面显示的 5 次及 3 次现金分红，登记日、除息日、每股金额都与已保存来源表完全一致；上市日期分别为 2023-06-30、2024-02-07。保存的是浏览器实际显示表格的逐格观察记录，没有将它标作原始 HTTP 响应或已下载的实施公告 PDF。</p>'
    block += '<p>以原始日线前收盘参考价按“分”取整的比值累乘，10 个既有复权值都在原 6 位精度精确复现；以未取整现金分红额计算，10 个新浪除数都在 13 位精度精确复现，最大差约 4.83×10⁻¹⁴。两种计算口径足以解释已观察的差异，但这是从记录作出的方法推断，没有声称获得供应商算法说明。</p>'
    block += '<table><thead><tr><th>股票</th><th>实际上市日</th><th>既有口径计算前缀</th><th>原生分钟检查</th><th>候选状态</th></tr></thead><tbody><tr><td>芯动联科 688582</td><td>2023-06-30</td><td>0.985996</td><td>240 根，4 小时</td><td>未应用</td></tr><tr><td>成都华微 688709</td><td>2024-02-07</td><td>0.992700</td><td>240 根，4 小时</td><td>未应用</td></tr></tbody></table>'
    block += '<p>这两个值是在既有回顾性复权基准下计算的值，并非 IPO 当日直接观察到的发布值；只提出实际 IPO 日事件，未作通用向前填充，所有后来既有因子原值保持。独立复算核对了原始因子、现金表、分钟字节、实际缺失掩码和事件取值边界。8 个小时均在最低区间之外，目前仍未改变面板。首次分红实施公告 PDF 尚未取得，完整公司行动史与实盘准入没有认证。</p>'
    block += '<p>'+ '；'.join(f'<a href="{p.relative_to(BASE)}">{label}</a>' for label,p in [('方法独立复算',ip),('Chrome官方表格观察',Path(i['official_table_observation_path'])),('原始来源字节核验',IPO/'source_acquisition_receipt.json')])+'</p>'
    block += '<p><a href="https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=688582">芯动联科官方公司信息</a>；<a href="https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=688709">成都华微官方公司信息</a>（进入“公司信息”后查看利润分配）。本次上交所公告 PDF 导航超时，上述官方网页可正常读取；没有将单一页面超时推断为 Chrome 整体不可用。</p>'
    assert '<h2>2018 新整卷：1217 个缺失小时的实际核验</h2>' not in page
    assert page.count('<h2>可复核文件</h2>') == 1
    page = page.replace('<h2>可复核文件</h2>',block+'<h2>可复核文件</h2>')
    links = Links()
    links.feed(page)
    assert all((BASE/value).exists() for value in links.paths)
    assert '佳驰科技（688708）' in page and '正元地信' not in page
    files[0].write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    with files[1].open('w',newline='') as stream:
        csv.writer(stream).writerows(rows)
    files[2].write_text(page)
    receipt = {'checked_utc':now.isoformat(),'source_code_sha256':sha(__file__),
               'files_updated':[{'path':str(p),'sha256':sha(p)} for p in files],
               'prior_reports_snapshot':str(snapshot),'local_report_links_checked':len(links.paths),
               'independent_2018_receipt_sha256':sha(wp),'independent_factor_receipt_sha256':sha(ip),
               'candidate_fingerprint_unchanged':data['new_X_fingerprint'],'candidate_changed':False,
               'new_hours_admitted':0,'minimum_trading_gap_unchanged':3042,'nonmember_tail_gap_unchanged':84860,
               'goal_complete':False}
    receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

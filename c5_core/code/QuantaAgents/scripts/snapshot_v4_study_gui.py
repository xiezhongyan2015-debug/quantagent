"""Manually refresh the existing GUI summary from read-only study evidence.

No server lifecycle, background loop, model call, strategy run, source verifier,
or canonical registry writer is invoked. Default mode previews; --write saves a
new numbered summary and updates only the GUI display configuration.
"""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
from contextlib import closing

import psutil


ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / 'docs/research/meta_framework_v4_handoff/gui_status_001.json'


def build(status_path=STATUS):
    spec = importlib.util.spec_from_file_location('study_gui_reader', ROOT / 'scripts/serve_research_v3.py')
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    saved = json.loads(status_path.read_text(encoding='utf-8-sig'))
    if saved.get('study_id') != 'v4s1':
        raise ValueError('This manual display refresh is restricted to the explicitly configured v4s1.')
    study = viewer.study_snapshot('v4s1')
    if not study.get('registered') or len(study['slots']) != 9:
        raise ValueError('Canonical nine-slot registration is unavailable; no display files changed.')
    producer_results = {}
    registry = ROOT / 'experiment_traces/meta_framework_v3/study_registry.sqlite3'
    with closing(sqlite3.connect(registry.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        for tid, receipt_text, receipt_hash in db.execute(
                "SELECT trial_id,receipt,receipt_hash FROM producer_runs WHERE study_id='v4s1'"):
            if receipt_text:
                receipt = json.loads(receipt_text)
                if viewer._digest(receipt) != receipt_hash:
                    raise ValueError('Canonical producer receipt hash mismatch')
                producer_results[tid] = receipt['result']
    now = datetime.now(timezone.utc)
    stamp = now.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S 香港时间')
    workers, rows, fixed_facts = {}, [], []
    for slot in study['slots']:
        state = slot['state']
        delivery = slot.get('result_summary') or '尚无交付'
        if slot['status'] == 'started':
            try:
                identity = json.loads((Path(slot['root']) / 'worker_identity.json').read_text(encoding='utf-8'))
                process = psutil.Process(identity['pid'])
                live = (process.is_running() and process.create_time() == identity['create_time']
                        and process.cmdline() == identity['cmdline'])
                workers[slot['id']] = {'live_at_snapshot': live, 'pid': identity['pid'], 'observed_at': now.isoformat()}
                if live and not slot['errors']:
                    state = '运行中（快照时身份已核验）'
            except (OSError, ValueError, KeyError, psutil.Error):
                workers[slot['id']] = {'live_at_snapshot': False, 'observed_at': now.isoformat()}
        producer = slot.get('producer') or {}
        selection = producer.get('development_selection')
        if selection:
            reviews = selection.get('candidate_reviews', [])
            complete = sum(row.get('status') == 'completed' for row in reviews)
            unknown = [row['candidate_id'] for row in reviews if row.get('status') == 'unknown']
            details = []
            for candidate in unknown:
                if not candidate.isalnum():
                    raise ValueError('Unexpected producer candidate identifier')
                path = Path(slot['root']) / 'control/arms/fixed/batches/frozen/candidates' / candidate / 'process_exit.json'
                try:
                    reason = json.loads(path.read_text(encoding='utf-8')).get('killed_reason')
                except (OSError, ValueError):
                    reason = None
                details.append(candidate + ' 未知' + ('（' + reason + '）' if reason else ''))
            outcome = '弃权' if selection['outcome'] == 'abstain' else '开发候选 ' + str(selection['selected_candidate_id'])
            # Started count comes from its saved receipt, never slot allocation.
            started = producer_results[slot['id']]['started_candidates']
            delivery = f"{started} 已启动 / {complete} 完整完成"
            if details:
                delivery += ' / ' + '、'.join(details)
            delivery += '；' + outcome
            state = '固定搜索已结束；' + outcome
            fixed_facts.append(slot['id'] + '：' + delivery + '。完成数不等于策略成功；固定结果不是模型报告。')
        if slot['errors']:
            delivery += '；读取异常：' + '；'.join(slot['errors'])
        rows.append([slot['id'], slot['label'], slot['repeat'], state,
            slot['call_count'], slot['known_tokens'], slot['unknown_reserve'], delivery])
    totals = study['totals']
    closure = None
    if saved.get('cohort_status') in ('closed_pending_final_audits', 'closed_audited'):
        path = Path(saved['controller_disposition_path']).resolve()
        if path.parent != (ROOT / 'experiment_traces/v4s1').resolve():
            raise ValueError('Cohort disposition is outside the configured study')
        closure = json.loads(path.read_text(encoding='utf-8'))
        if (closure.get('closure_status') != saved['cohort_status']
                or closure.get('cohort_execution_stopped') is not True
                or closure.get('successful_comparison') is not False
                or closure['canonical_closure_snapshot']['totals'] != totals):
            raise ValueError('Saved cohort closure does not match the current canonical totals')
        retained = set(closure['unstarted_retained'])
        if retained != {slot['id'] for slot in study['slots'] if slot['status'] == 'not_started'}:
            raise ValueError('Closed cohort unstarted opportunities changed')
        for row in rows:
            if row[0] in retained:
                row[3] = '未启动，原机会保留（工具总额度耗尽）'
                row[7] = 'study_tool_headroom_exhausted；不补位、不重开'
    summary = {'title': 'V4 三组局部比较：完整九槽进度（手动保存快照）',
        'summary': f'读取时间：{stamp}。同一已暴露的 2019 年案例，三组各三次预声明启动；终态 {totals["terminal"]}/9，尚未启动 {totals["not_started"]}/9。',
        'facts': [
            '每个 trial 的父任务与扩展共用最多 24 次 AI 调用、140 万名义 token、8 个候选及 2 小时时限。A/B 同为 Astra / xhigh；固定模板组不用模型，未用额度不转移。',
            f'截至快照全九槽 {totals["call_count"]} 次模型调用，{totals["known_tokens"]:,} 已知 token，{totals["unknown_reserve"]:,} 未知用量预留；父子费用计入原 trial 一次。',
            *fixed_facts,
            '8777 仍为旧服务，新版 study 查看器代码尚未加载。本表是手动保存快照；网页刷新不会重新计算九槽总账。当前动态 V4 账本和进程核验只覆盖所配置的 v1。',
            '服务重启和新端口启动被自动审批拒绝（blocked by policy）；没有更换方式启动服务。',
            '旧 v4c1 的 22 次调用和七账户证据保存在原 RESULTS_001.md，未纳入本轮九槽的结果与费用。'],
        'columns': ['槽位', '架构', '重复', '阶段', '父子累计调用', '已知 token', '未知用量预留', '交付 / 固定搜索结果'],
        'rows': rows,
        'limitations': '一个已暴露案例的九次局部开发，不等于正式 54 次验证，正式成功分母贡献为 0。244 日不足 252 日门槛；真实执行与容量、独立样本外及冻结后的前瞻证据尚未满足。进程存活仅对应本快照时点；交付和账户完成不等于科学验收。',
        'next_step': '由原已启动句柄自主研究，保留全部失败、未知结果与费用；结束后独立复核。此入口只手动更新展示，不启动研究或服务。',
        'observed_at': now.isoformat(), 'study_id': 'v4s1', 'study_plan_hash': study['plan_hash'],
        'worker_observations': workers, 'viewer_code_loaded': False, 'canonical_totals': totals,
        'formal_target_success': False, 'formal_success_denominator_contribution': 0}
    if closure:
        summary.update(title='V4 三组局部比较：本轮已停止，等待最终审计',
            summary=f'终态快照：{stamp}。9 个原登记槽位完整保留：7 个已启动任务终结，v3/s3 因工具总额度耗尽保留未启动；本 cohort 不再运行或补位。',
            next_step='继续独立终态审计与工程修复；本 cohort 不再新增模型调用、策略执行或补位。7 个进程退出成功不代表报告、策略或公平比较成功。',
            cohort_status='closed_pending_final_audits',
            controller_disposition_path=saved['controller_disposition_path'])
        summary['facts'].insert(0,
            '7 个原研究句柄均 exit 0、0 活动任务；v1/s1 保存研究终稿，v2/s2 为终稿交付失败，f1/f2/f3 均弃权。最终独立审计仍待完成，本轮不是成功比较。')
        summary['limitations'] += ' 全局工具资源耗尽对后启动槽位产生不对称影响；两个未启动机会不能删除、补位或记为成功。'
        if closure['closure_status'] == 'closed_audited':
            summary.update(title='V4 三组局部比较：实跑结束，独立审计完成',
                cohort_status='closed_audited',
                next_step='原研究与全部失败已保全；三项实跑后工程修复通过156项综合检查。后续研究须另行准入并冻结，原cohort不重开。')
            summary['facts'][0] = ('7 个原任务均已结束；独立核对40个候选机会：37个完整账户、3个中断账户。'
                'V4修订不及三个事前固定对照，完整采用条件未通过。本轮未证明架构更优或正式策略成功。')
            if saved.get('next_action'):
                summary['next_step'] = saved['next_action']
            summary['facts'] += saved.get('latest_progress_facts', [])
    return saved, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Save one new snapshot and update the GUI pointer')
    args = parser.parse_args()
    saved, summary = build()
    output = None
    if args.write:
        folder = ROOT / 'experiment_traces/v4s1'
        numbers = [int(p.stem.rsplit('_', 1)[1]) for p in folder.glob('gui_summary_*.json')
                   if p.stem.rsplit('_', 1)[1].isdigit()]
        output = folder / f'gui_summary_{max(numbers, default=0) + 1:03d}.json'
        with output.open('x', encoding='utf-8') as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        # Re-read to preserve any display-only edits made during the snapshot read.
        saved = json.loads(STATUS.read_text(encoding='utf-8-sig'))
        if saved.get('study_id') != 'v4s1':
            raise ValueError('GUI selector changed; new snapshot retained without replacing its pointer.')
        saved.update(observed_at=summary['observed_at'], study_summary_observed_at=summary['observed_at'],
            cycle_results_summary_path=str(output), viewer_code_loaded=False)
        STATUS.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'written': bool(output), 'summary_path': str(output) if output else None,
        'observed_at': summary['observed_at'], 'totals': summary['canonical_totals'],
        'rows': summary['rows'], 'new_model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()

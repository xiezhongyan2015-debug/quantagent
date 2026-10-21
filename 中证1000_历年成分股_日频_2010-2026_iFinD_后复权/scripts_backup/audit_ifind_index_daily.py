"""Read-only dataset validation, publishing a separate quality report."""
import argparse
import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
from download_ifind_index_daily import FIELDS, AVAILABLE
from download_ifind_csi500_1min import atomic_json, members_from_result

ROOT = Path(r'E:\中证500_历年成分股_日频_2010-2026_iFinD_后复权')


def main():
    meta = json.loads((ROOT / "dataset.json").read_text(encoding="utf-8"))
    expected_count = meta["expected_constituents"]
    db = sqlite3.connect(ROOT.joinpath('manifest.sqlite').as_uri() + '?mode=ro', uri=True)
    records = db.execute('SELECT date,batch,status,rows,path,sha256,detail FROM batches').fetchall()
    db.close()
    calendar = json.loads((ROOT / 'audit/trading_calendar.json').read_text(encoding='utf-8'))['data']['time']
    calendar = sorted(d for d in calendar if max(meta['requested_start'], meta['membership_start']) <= d <= meta['requested_end'])
    months = sorted({d[:7] for d in calendar})
    failures, monthly, anomalies = [], [], []
    nulls, statuses = Counter(), Counter()
    total = 0
    codes_seen = set()
    paths_seen = set()
    for month in months:
        dates = [d for d in calendar if d.startswith(month)]
        members = {}
        for date in dates:
            with gzip.open(ROOT / 'memberships' / (date + '.json.gz'), 'rt', encoding='utf-8') as stream:
                raw = json.load(stream)
            codes = members_from_result(raw, date, expected_count=None)
            members[date] = set(codes)
            if len(codes) != expected_count:
                evidence_path = ROOT / 'audit/membership_count_anomalies' / (date + '.json')
                evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
                confirmed = members_from_result(evidence['confirmation_response'], date, expected_count=None)
                if evidence['state'] != 'confirmed_provider_set' or set(confirmed) != set(codes) or set(evidence['codes']) != set(codes):
                    failures.append(f'Membership evidence mismatch: {date}')
                anomalies.append(dict(date=date, count=len(codes), needs_independent_review=True))
        frames = []
        for rec in [r for r in records if r[0] == month]:
            _, batch, state, rows, relative, sha, detail = rec
            statuses[state] += 1
            if not relative:
                failures.append(f'No data file: {month}/{batch}')
                continue
            if relative in paths_seen:
                failures.append(f'Repeated file in manifest: {relative}')
            paths_seen.add(relative)
            path = ROOT / relative
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                failures.append(f'Checksum mismatch: {relative}')
            frame = pd.read_parquet(path)
            if len(frame) != rows or list(frame.columns) != ['thscode', 'date'] + FIELDS:
                failures.append(f'Row count/schema mismatch: {relative}')
            frames.append(frame)
        if not frames:
            failures.append(f'Missing month: {month}')
            continue
        frame = pd.concat(frames, ignore_index=True)
        expected = {(code, date) for date, codes in members.items() for code in codes}
        actual = set(zip(frame.thscode, frame.date))
        missing, extra = expected - actual, actual - expected
        duplicate_count = int(frame.duplicated(['thscode', 'date']).sum())
        if duplicate_count or extra:
            failures.append(f'Duplicates/out-of-membership rows: {month}')
        if not frame[['sellVolume', 'buyVolume']].isna().all().all():
            failures.append(f'Inner/outer columns unexpectedly populated: {month}')
        valid = frame[['open', 'high', 'low', 'close']].notna().all(axis=1)
        prices = frame.loc[valid]
        inconsistent = ((prices['high'] + 1e-8 < prices[['open', 'close', 'low']].max(axis=1)) | (prices['low'] - 1e-8 > prices[['open', 'close', 'high']].min(axis=1)))
        if inconsistent.any():
            failures.append(f'OHLC inconsistency: {month}')
        for field in FIELDS:
            nulls[field] += int(frame[field].isna().sum())
        total += len(frame)
        codes_seen.update(frame.thscode)
        monthly.append(dict(month=month, rows=len(frame), expected_rows=len(expected),
                            missing_rows=len(missing), extra_rows=len(extra), duplicate_rows=duplicate_count,
                            missing_observations=sorted(missing)))
    orphan_paths = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'bars').rglob('*.parquet') if str(p.relative_to(ROOT)) not in paths_seen)
    if orphan_paths:
        failures.append('Untracked parquet files exist')
    if set(r[0] for r in records) != set(months):
        failures.append('Manifest month coverage differs from calendar')
    progress = json.loads((ROOT / 'progress.json').read_text(encoding='utf-8'))
    if total != progress['total_rows']:
        failures.append('Progress total differs from actual data')
    report = dict(checked_at=datetime.now().isoformat(), integrity_passed=not failures, failures=failures,
                  rows=total, unique_securities=len(codes_seen), months=len(months), trading_days=len(calendar),
                  first_date=calendar[0], last_date=calendar[-1], files=len(paths_seen), batch_states=dict(statuses),
                  null_counts=dict(nulls), missing_observations=sum(m['missing_rows'] for m in monthly),
                  duplicate_rows=sum(m['duplicate_rows'] for m in monthly), out_of_membership_rows=sum(m['extra_rows'] for m in monthly),
                  membership_anomalies=anomalies, orphan_paths=orphan_paths, monthly=monthly,
                  limitations=['Membership coverage checked against iFinD daily historical sets, not an independent official constituent archive.',
                               'Unexpected constituent counts are repeat-confirmed with provider but require independent review.',
                               'Provider null values retained; sellVolume and buyVolume intentionally all NULL.',
                               'Back-adjustment verified by request configuration and earlier samples, not independently reconstructed corporate actions.'])
    atomic_json(ROOT / 'audit/final_quality_report.json', report)
    lines = [f'# {meta["index"]}日频数据最终核验', '', f'核验时间：{report["checked_at"]}', '',
             f'文件及记录完整性：{"通过" if not failures else "未通过"}。完整性通过不代表供应商全部字段非空或成分名单已获独立确认。', '',
             f'范围：{calendar[0]} 至 {calendar[-1]}；{len(months)} 个月，{len(calendar)} 个交易日。',
             f'共 {total:,} 行、{len(codes_seen):,} 只历史证券、{len(paths_seen)} 个 Parquet 文件。',
             f'重复行：{report["duplicate_rows"]}；在供应商成分范围外：{report["out_of_membership_rows"]}；相对供应商名单缺行：{report["missing_observations"]}。', '',
             '## 字段空值', '', '|字段|空值行数|', '|---|---:|']
    lines += [f'|{f}|{nulls[f]:,}|' for f in FIELDS]
    lines += ['', '内盘、外盘按用户要求全部留空。其余空值为接口原始缺失，没有补造或前向填充。', '',
              f'## 成分数量异常：{len(anomalies)} 个交易日', '',
              '各异常日期的两次供应商返回已比对一致，详情见 membership_count_anomalies/。仍需独立核实官方成分完整性，不应把重复查询一致解读为无遗漏证明。', '',
              '|日期|成分数量|', '|---|---:|']
    lines += [f'|{a["date"]}|{a["count"]}|' for a in anomalies]
    lines += ['', '后复权参数：CPS:3（分红再投），baseDate:1900-01-01。只保存供应商每日成分在池期间的记录。', '',
              '校验涵盖文件 SHA256、清单行数、字段结构、逐月重复、在池日期范围、交易日覆盖、OHLC 与缺失统计。未独立重建复权因子。']
    if failures:
        lines += ['', '## 核验错误', ''] + ['- ' + x for x in failures]
    (ROOT / 'audit/最终质量报告.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['monthly','membership_anomalies','limitations']}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    ROOT = Path(parser.parse_args().output).resolve()
    if ROOT.drive.upper() != 'E:':
        raise ValueError('Expected E drive dataset')
    main()

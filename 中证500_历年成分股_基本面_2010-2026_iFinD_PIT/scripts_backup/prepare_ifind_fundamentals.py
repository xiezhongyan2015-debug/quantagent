"""Create the exact historical weekly universe without making API requests."""
import gzip
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path('E:/中证500_历年成分股_日频_2010-2026_iFinD_后复权')
DEST = Path('E:/中证500_历年成分股_基本面_2010-2026_iFinD_PIT')


def prepare():
    dates = json.loads((SOURCE / 'audit/trading_calendar.json').read_text(encoding='utf-8'))['data']['time']
    dates = sorted(d for d in dates if '2010-01-01' <= d <= '2026-09-24')
    weeks = {}
    for d in dates:
        stamp = pd.Timestamp(d)
        weeks[tuple(stamp.isocalendar()[:2])] = d
    rows, candidates = [], defaultdict(list)
    for d in weeks.values():
        with gzip.open(SOURCE / 'memberships' / (d + '.json.gz'), 'rt', encoding='utf-8') as stream:
            raw = json.load(stream)
        if raw['errorcode'] != 0:
            raise ValueError(f'Membership error: {d}')
        tables = [v['table'] for v in raw['data']]
        codes = [code for t in tables for date, code in zip(t['DATE'], t['THSCODE']) if date == d]
        if len(codes) != len(set(codes)) or not 499 <= len(codes) <= 500:
            raise ValueError(f'Unexpected membership count: {d}: {len(codes)}')
        periods = [pd.Timestamp(d).to_period('Q') - lag for lag in range(9)]
        periods = [p.end_time.strftime('%Y-%m-%d') for p in periods if p.end_time.strftime('%Y-%m-%d') <= '2026-06-30']
        for code in sorted(codes):
            rows.append(dict(asof_date=d, thscode=code, member_count=len(codes), partial_week=d == dates[-1]))
            for period in periods:
                candidates[(code, period)].append(d)
    universe = pd.DataFrame(rows)
    financial = pd.DataFrame([dict(thscode=code, report_period=period,
                                   first_needed_asof=min(days), last_needed_asof=max(days))
                              for (code, period), days in sorted(candidates.items())])
    for folder in ['plan', 'audit', 'raw', 'weekly', 'financial', 'scripts_backup']:
        (DEST / folder).mkdir(parents=True, exist_ok=True)
    universe.to_parquet(DEST / 'plan/weekly_universe.parquet', index=False)
    financial.to_parquet(DEST / 'plan/financial_candidates.parquet', index=False)
    budget = dict(weekly_points=universe.asof_date.nunique(), market_rows=len(universe),
                  max_members=int(universe.member_count.max()),
                  last_point_partial_week=dates[-1],
                  unique_stock_report_period_candidates=len(financial),
                  financial_fields=20, financial_cells=20 * len(financial),
                  metadata_fields=3, metadata_cells=3 * len(financial),
                  weekly_fields=2, weekly_cells=2 * len(universe),
                  estimated_base_cells=23 * len(financial) + 2 * len(universe),
                  total_budget_ceiling_cells=2500000,
                  note='Estimate only: financial revisions and quota accounting must be checked before bulk dispatch.')
    text = json.dumps(budget, ensure_ascii=False, indent=2)
    (DEST / 'plan/budget_estimate.json').write_text(text, encoding='utf-8')
    (ROOT / 'data_expansion/fundamentals_budget_plan/budget_estimate.json').write_text(text, encoding='utf-8')
    (DEST / 'progress.json').write_text(json.dumps(dict(status='preparing_field_metadata',
        planned_weekly_points=budget['weekly_points'], planned_weekly_rows=len(universe),
        completed_weekly_points=0, completed_financial_requests=0,
        message='Membership plan saved; indicator definitions pending verification. Bulk download not started.'), ensure_ascii=False, indent=2), encoding='utf-8')
    print(text)


if __name__ == '__main__':
    prepare()

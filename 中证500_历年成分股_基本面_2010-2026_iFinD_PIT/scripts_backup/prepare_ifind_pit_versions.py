"""Schedule each required financial version at its first in-pool weekly point."""
from bisect import bisect_right
import json

import pandas as pd

from download_ifind_fundamental_market import DEST, save


def main():
    universe=pd.read_parquet(DEST/'plan/weekly_universe.parquet')
    inventory=pd.read_parquet(DEST/'plan/report_event_inventory.parquet')
    stock_dates={c:sorted(d.asof_date.tolist()) for c,d in universe.groupby('thscode')}
    rows=[]
    for item in inventory.itertuples(index=False):
        events=json.loads(item.event_dates_json)
        last_version=None
        for date in stock_dates[item.thscode]:
            if not item.first_needed_asof<=date<=item.last_needed_asof:
                continue
            index=bisect_right(events,date)-1
            if index<0 or index==last_version:
                continue
            last_version=index
            rows.append(dict(asof_date=date,thscode=item.thscode,report_period=item.report_period,
                source_update_date=events[index],first_provider_event_date=events[0]))
    plan=pd.DataFrame(rows).sort_values(['asof_date','report_period','thscode']).reset_index(drop=True)
    if plan.duplicated(['asof_date','thscode','report_period']).any():
        raise RuntimeError('Duplicate planned version')
    keys=plan[['asof_date','thscode']].drop_duplicates()
    membership=keys.merge(universe[['asof_date','thscode']],how='left',indicator=True,validate='one_to_one')
    if not (membership['_merge']=='both').all():
        raise RuntimeError('Financial query outside weekly membership')
    plan.to_parquet(DEST/'plan/pit_version_requests.parquet',index=False)
    result=dict(version_rows=len(plan),request_groups=plan.groupby(['asof_date','report_period']).ngroups,
        max_securities_at_any_asof=int(keys.groupby('asof_date').size().max()),
        estimated_all_twenty_financial_cells=len(plan)*20,
        estimated_weekly_market_and_industry_cells=len(universe)*2,
        downloaded_update_metadata_cells=len(inventory),
        estimated_base_total_cells=len(plan)*20+len(universe)*2+len(inventory),
        all_financial_query_asofs_in_weekly_membership=True,
        temporal_convention='As-of end of day; use for trading no earlier than the next trading session.',
        note='Within-week provider updates collapse to the latest visible version at that week-end. No unobserved earlier version is synthesized.')
    save(DEST/'audit/weekly_pit_budget.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()

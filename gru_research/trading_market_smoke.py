"""Replay input contract on real quotes/members, without forecasts or performance."""
from datetime import date
import json
from pathlib import Path
from trading_inputs import load_base_provider
from research_market_provider import MARKET_ONLY
from pretax_replay_account import PretaxReplayAccount
from extended_costs import research_costs
from trading_provider import FrozenMinuteTWAPSource,CN
import pandas as pd

ROOT=Path(__file__).resolve().parent


def smoke():
    meta=json.loads((ROOT/'artifacts/minute_feature_metadata.json').read_text())
    calendar=[date.fromisoformat(d) for d in meta['calendar_dates'] if d>='2024-12-01']+[date(2026,9,24)]
    base=load_base_provider(ROOT/'trading_data/full_2025_2026',official_calendar=calendar,
        calendar_source='HISTORICAL_IFIND_SESSIONS_WITH_SSE_OFFICIAL_2025_2026_CLOSED_CROSS_CHECK_AND_VERIFIED_SEP24_TAIL',
        protocol_path=ROOT/'artifacts/trading_research_protocol_v2.json')
    costs=research_costs()
    book=PretaxReplayAccount(1000000,'NO_FORECAST_MARKET_INTEGRITY_SMOKE_SCENARIO',sessions=calendar,
        calendar_source=base.calendar_source,fee_source=costs.source)
    previous=date(2025,1,2); day=date(2025,1,3)
    book.begin(previous);book.close()
    snapshot=base.planning_snapshot(previous,day,day,book,MARKET_ONLY,costs)
    good=[s.symbol for s in snapshot.securities if s.grid and s.raw_close and s.adv20_shares and s.can_submit][:5]
    context=base.market_context(day,good,'OPEN')
    window=FrozenMinuteTWAPSource().window(pd.Timestamp('2025-01-02 15:00',tz=CN),good)
    if any(not window.quotes[s].complete for s in good):
        raise ValueError('selected actual sample lacks genuine required minute window')
    report={'status':'ACTUAL_FULL_MARKET_PROVIDER_CONTRACT_SMOKE_PASS',
        'members':len([s for s in snapshot.securities if s.member]),
        'score_fields_all_none':all(s.score is None for s in snapshot.securities),
        'prior_ADV_available':sum(s.adv20_shares is not None for s in snapshot.securities),
        'actual_sample_symbols':good,'dated_rules':len(context.rules),
        'genuine_raw_TWAP':{s:str(window.quotes[s].raw_price) for s in good},
        'window_end_utc_ns':window.execution_end,'cash_unchanged':str(snapshot.cash),
        'orders_or_strategy_returns_computed':False,'source_identity':base.source,
        'input_fingerprints':base.input_fingerprints}
    (ROOT/'artifacts/trading_market_smoke.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    print(json.dumps(smoke(),indent=2))

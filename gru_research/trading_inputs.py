"""Build replay providers from separately frozen genuine trading evidence.

No prediction fitting, score selection, future labels or price repair occurs.
Caller supplies its official calendar evidence and separately fixed protocol.
"""
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
sys.path.insert(0,str(ROOT/'research_v3/src'))
import numpy as np
import pandas as pd
from research_action_catalog import ResearchActionCatalog
from research_market_provider import ResearchMarketProvider, MARKET_ONLY
from trading_provider import GRUTradingProviderAdapter, FrozenMinuteTWAPSource, ARRIVAL_ASSUMPTION


def historical_members(calendar,artifacts=ROOT/'gru_research/artifacts'):
    artifacts=Path(artifacts)
    metadata=json.loads((artifacts/'minute_feature_metadata.json').read_text())
    dates=metadata['calendar_dates'];symbols=metadata['union_symbols']
    dense=np.load(artifacts/'minute_members.npy',mmap_mode='r')
    requested={d.isoformat() for d in calendar}
    rows=[]
    for i,day in enumerate(dates):
        if day not in requested:continue
        for u in np.flatnonzero(dense[4*i]):
            symbol=symbols[u]
            rows.append({'date':pd.Timestamp(day),'symbol':symbol[-2:].lower()+symbol[:6]})
    return pd.DataFrame(rows)


def load_base_provider(directory,*,official_calendar,calendar_source,protocol_path):
    directory=Path(directory).resolve();n=directory/'normalized'
    protocol_path=Path(protocol_path).resolve()
    protocol_sha=hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    audit=json.loads((n/'coverage_audit.json').read_text())
    if audit['query_status'].get('not_requested_yet',0) or audit['query_status'].get('provider_failed',0):
        raise ValueError('complete declared vendor queries required before sealing trading evidence')
    if not audit['catalog_structural_reconciliation_ready'] or audit['action_conflicts']:
        raise ValueError('corporate raw query reconciliation unresolved')
    if not (n/'cross_source_audit.json').exists():
        raise ValueError('actual raw-price cross-source audit required')
    source_paths=['raw_daily.parquet','lifecycle.parquet','corporate_actions.parquet',
        'corporate_action_query_coverage.parquet','calendar_source.parquet','cross_source_audit.json']
    fingerprints={name:hashlib.sha256((n/name).read_bytes()).hexdigest() for name in source_paths}
    cross=json.loads((n/'cross_source_audit.json').read_text())
    if cross.get('raw_daily_sha256')!=fingerprints['raw_daily.parquet']:
        raise ValueError('cross-source audit does not bind these exact raw daily bytes')
    calendar=list(official_calendar)
    if calendar != sorted(set(calendar)) or any(type(d)is not date for d in calendar) or not calendar_source:
        raise ValueError('explicit complete official calendar/source required')
    observed=pd.read_parquet(n/'calendar_source.parquet')
    observed_days=set(pd.to_datetime(observed.loc[observed.is_trading_day.eq('1'),'calendar_date']).dt.date)
    if set(calendar)!=observed_days:
        raise ValueError('official calendar and actual vendor session flags disagree')
    raw=pd.read_parquet(n/'raw_daily.parquet')
    lifecycle=pd.read_parquet(n/'lifecycle.parquet')
    coverage=pd.read_parquet(n/'corporate_action_query_coverage.parquet').to_dict('records')
    actions=pd.read_parquet(n/'corporate_actions.parquet').to_dict('records')
    query_bytes={}
    for row in coverage:
        path=Path(row['source_path']);data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['source_sha256']:
            raise ValueError('immutable corporate response bytes changed')
        query_bytes[row['source_sha256']]=data
    catalog=ResearchActionCatalog(actions,coverage,source_identity=audit['catalog_source_identity'],query_bytes_by_sha256=query_bytes)
    members=historical_members(calendar)
    source_identity='sha256:'+hashlib.sha256(json.dumps({'data':fingerprints,
        'members_sha256':hashlib.sha256((ROOT/'gru_research/artifacts/minute_members.npy').read_bytes()).hexdigest(),
        'protocol_sha256':protocol_sha,'calendar':[str(d) for d in calendar]},sort_keys=True).encode()).hexdigest()
    base=ResearchMarketProvider(raw=raw,members=members,official_calendar=calendar,lifecycle=lifecycle,catalog=catalog,
        protocol_sha256=protocol_sha,source_identity=source_identity,calendar_source=calendar_source)
    base.input_fingerprints=fingerprints
    return base


def bind_frozen_scores(base,scores,*,model_version,score_source):
    return GRUTradingProviderAdapter(base,scores,model_version=model_version,planning_model=MARKET_ONLY,
        score_source=score_source,arrival_policy=ARRIVAL_ASSUMPTION,minute_source=FrozenMinuteTWAPSource())

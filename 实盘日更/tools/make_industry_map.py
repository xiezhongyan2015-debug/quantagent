"""Build config/industry_sw_l1_<asof>.json = {dotcode: 申万一级 provider code} from the iFinD weekly PIT industry file (latest row per
name) for the live --refill industry option. Names without a row are left out (they refill pro-rata). Usage: python make_industry_map.py"""
import json, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]
mk = pd.read_parquet(ROOT / '中证500_历年成分股_基本面_2010-2026_iFinD_PIT/weekly_market_and_industry.parquet')
mk = mk.dropna(subset=['sw_l1_provider_code']).sort_values('asof_date'); last = mk.groupby('thscode').tail(1)
asof = str(last.asof_date.max())[:10]; out = {r.thscode: str(r.sw_l1_provider_code) for r in last.itertuples()}
path = ROOT / f'实盘日更/config/industry_sw_l1_{asof.replace("-", "")}.json'; path.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding='utf-8')
print(json.dumps(dict(path=str(path.relative_to(ROOT)), names=len(out), asof=asof, industries=len(set(out.values()))), ensure_ascii=False))

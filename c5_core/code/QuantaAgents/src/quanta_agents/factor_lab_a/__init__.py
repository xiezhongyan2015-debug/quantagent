"""A-layer factor laboratory (v11 redesign, 2026-09-13).

Design principles (see docs/research/framework_review_20260913):
- universe: all A-share tradeable (point-in-time), CSI300/500 kept as diagnostics
- metric: daily cross-sectional RankIC (Spearman), annual mean / ICIR / worst year;
  Pearson on winsorised z-scores kept as diagnostic
- open information set: OHLC, volume, amount, vwap, float/total shares & caps, derived turnover
- exploration space: mechanism x data source x transformation x condition (coverage map)
- LLM nodes are file-based requests answered by an external research sub-agent
- numeric kernel frozen in this package; protocol is a JSON file, not code
"""
__version__ = "11.0.0"

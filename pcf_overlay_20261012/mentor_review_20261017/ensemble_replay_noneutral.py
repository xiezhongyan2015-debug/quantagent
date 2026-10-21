"""Same replay as pcf_overlay_20261012/ensemble_replay.py (V5 + k=0.5 blend tilt, 2025-01 -> 2026-09) but the GRU post-processing
skips the OLS neutralisation on volatility/turnover/log cap/beta60 (导师 2026-10-16: alpha 因子一般不再对风险因子取残差).
EMA .2 and member z are kept.  Only ctrl5 (live set) and all15.  Output: outputs/pcf_overlay_20261012/ensemble_replay_v1_noneutral/"""
import sys; sys.path.insert(0, '.')
import pcf_overlay_20261012.ensemble_replay as E
E.neutralize = lambda raw, expo: raw
E.DEST = E.OUT / 'ensemble_replay_v1_noneutral'
E.ENSEMBLES = {'ctrl5': ['wf2026'], 'all15': ['wf2025', 'wf2026', 'oldrank']}
E.main()

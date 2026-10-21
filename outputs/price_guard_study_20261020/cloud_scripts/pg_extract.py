# Read-only extraction of executor ledgers -> redacted JSON (no account ids, no tokens, no oids).
import json, sys, pathlib, shutil, datetime
RUNS = pathlib.Path(r'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs')
TMP = pathlib.Path(r'C:/ChoiceBridge/live/patches/tmp_guard')
OUT = pathlib.Path(r'C:/ChoiceBridge/live/patches/pg_extract_out.json')
ACTIVE = '094015_438e7161'
SESS = {'2026-10-12': '105511_611ab86a', '2026-10-13': '102604_6c2547db', '2026-10-14': '183431_bb8b4540',
        '2026-10-15': '100907_1253211f', '2026-10-16': '104653_cc742889', '2026-10-19': '094006_329b6c06',
        '2026-10-20': ACTIVE}
def shape(x, d=0):
    if isinstance(x, dict):
        return {k: shape(v, d + 1) for k, v in list(x.items())[:6]} if d < 2 else 'dict'
    if isinstance(x, list):
        return ['list', len(x)]
    return type(x).__name__
res = {'extracted_at': datetime.datetime.now().isoformat(), 'days': {}}
for day, s in SESS.items():
    p = RUNS / day / s / 'private_state.json'
    st_p = RUNS / day / s / 'status_redacted.json'
    if s == ACTIVE:
        TMP.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, TMP / 'private_state.json')
        if st_p.exists(): shutil.copy2(st_p, TMP / 'status_redacted.json')
        p = TMP / 'private_state.json'; st_p = TMP / 'status_redacted.json'
    st = json.loads(p.read_text(encoding='utf-8'))
    cfg = st.get('config_snapshot', {}) or {}
    la = st.get('last_audit', {}) or {}
    rec = dict(session=s, day=st.get('day'),
               config={k: cfg.get(k) for k in ('price_guard', 'max_attempts_per_code', 'target_fraction', 'spacing_seconds', 'strategy', 'allow_sells')},
               baseline_shape=shape(st.get('baseline')), prior_shape=shape(st.get('prior')),
               nav=la.get('total_assets'), market_value=la.get('market_value'), checked_at=la.get('checked_at'),
               stock_fraction=la.get('stock_fraction'),
               positions=la.get('positions'), sellable=la.get('sellable'),
               targets=st.get('targets'), floor_reference_targets=st.get('floor_reference_targets'),
               deferred=st.get('deferred'),
               position_topups=st.get('position_topups'),
               transition_at=(st.get('rebalance_workflow') or {}).get('transition_at'),
               quotes={k: dict(last=v.get('last'), ask=(v.get('ask') or [None])[:2] if isinstance(v.get('ask'), list) else v.get('ask'),
                               bid=(v.get('bid') or [None])[:2] if isinstance(v.get('bid'), list) else v.get('bid'),
                               previous_close=v.get('previous_close'), timestamp=v.get('timestamp'),
                               upper_limit=v.get('upper_limit'), lower_limit=v.get('lower_limit')) for k, v in (st.get('quotes') or {}).items()},
               attempts=[dict(code=a['order'].get('code'), side=a['order'].get('side'), quantity=a['order'].get('quantity'),
                              price=a['order'].get('price'), reference_price=a['order'].get('reference_price'),
                              state=a.get('state'), filled=a.get('filled'), phase=a.get('phase'),
                              submitted_at=a.get('submitted_at'), terminal_at=a.get('terminal_at'),
                              cancel_requested_at=a.get('cancel_requested_at')) for a in st.get('attempts', [])])
    b = st.get('baseline')
    if isinstance(b, dict):
        rec['baseline_positions'] = b.get('positions')
    if st_p.exists():
        try:
            sr = json.loads(st_p.read_text(encoding='utf-8'))
            rec['status_keys'] = sorted(sr.keys()) if isinstance(sr, dict) else None
            rec['status_phase'] = sr.get('phase') if isinstance(sr, dict) else None
            rec['status_updated'] = sr.get('updated_at') if isinstance(sr, dict) else None
            rec['status_deferred'] = sr.get('deferred') if isinstance(sr, dict) else None
        except Exception as e:
            rec['status_err'] = type(e).__name__
    res['days'][day] = rec
if TMP.exists():
    shutil.rmtree(TMP)
OUT.write_text(json.dumps(res, ensure_ascii=False), encoding='utf-8')
print('ok', OUT, OUT.stat().st_size, 'tmp_removed', not TMP.exists())

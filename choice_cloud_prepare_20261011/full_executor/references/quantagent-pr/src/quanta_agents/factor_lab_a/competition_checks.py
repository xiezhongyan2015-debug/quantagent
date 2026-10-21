"""Pure competition arithmetic, detached from research-only dependencies."""
def precheck(holdings: dict, targets: dict, nav: float, is500: dict, fill_rates=(1.0, 0.8, 0.5, 0.0), lim=None) -> dict:
    """Constraint ratios for a target book under partial fills. holdings/targets: {code: market value}; is500: {code: bool}.
    Scenario 'sells first, buys filled at rate f': every sell completes, every buy fills a fraction f."""
    lim = lim or {"share500": 0.82, "invested": 0.92, "single": 0.095}
    codes = set(holdings) | set(targets)
    out = {}
    for f in fill_rates:
        mv = {}
        for c in codes:
            h = holdings.get(c, 0.0); tgt = targets.get(c, 0.0)
            mv[c] = tgt if tgt <= h else h + f * (tgt - h)
        tot = sum(mv.values()); s500 = sum(v for c, v in mv.items() if is500.get(c, False))
        single = max(mv.values()) / nav if mv else 0.0
        ok = (s500 / nav > lim["share500"]) and (tot / nav >= lim["invested"]) and (single <= lim["single"])
        out[f"fill_{f:.2f}"] = {"invested": tot / nav, "share500": s500 / nav, "max_single": single, "ok": bool(ok)}
    # sells done, nothing bought yet (worst intraday point of a sells-first execution)
    return out

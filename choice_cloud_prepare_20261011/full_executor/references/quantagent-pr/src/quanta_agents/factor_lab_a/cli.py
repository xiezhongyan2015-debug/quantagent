"""Command line entry for the A-layer factor laboratory.

python -m quanta_agents.factor_lab_a.cli <command> --root <research root> [options]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import numpy as np
import pandas as pd

from .panel import Panel, build_panel
from .dsl import Compiler
from .evaluate import Evaluator, signature_corr, classify_failure, TRAIN_YEARS, TARGET_YEARS
from .ledger import Store
from . import library, llm, external, expand as expand_mod
from .combine import rolling_combination, LazyRanks
from . import portfolio as pf
from . import crossover as xo

DEFAULT_PROTOCOL = {
    "version": "A11.0", "universe": "all_a", "label": "label_5", "min_stocks": 100,
    "train_years": list(TRAIN_YEARS), "target_years": list(TARGET_YEARS),
    "purge": "last 6 signal dates per calendar year excluded from evaluation and training labels",
    "metric": "daily cross-sectional Spearman RankIC, annual mean; Pearson on winsorised z-scores reported as diagnostic",
    "thresholds": {"single_factor_mean": 0.05, "single_factor_worst_strong": 0.05, "combination_mean": 0.10,
                   "combination_worst_strong": 0.10},
    "admission": {"min_train_abs_t": 2.0, "min_target_mean_rank_ic": 0.015, "max_pool_corr": 0.90, "pool_max": 80},
    "exposed_history": "2019-2024 are exposed after each batch; only 'six-year historical attainment' can be claimed",
    "never_load": "2025 numeric values", "dsl": "factor_lab_a.dsl (superset of causal_factor_algebra_v6_1)",
}


def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def get_ctx(root):
    store = Store(root)
    proto = store.protocol()
    panel = Panel(proto["panel_dir"])
    panel._dtype = np.float32  # the workers already compute on float32 fields; the parent only needs masks/labels
    compiler = Compiler(resolver=lambda name: panel[name])
    ev = Evaluator(panel, universe=proto["universe"], label=proto["label"], min_stocks=proto["min_stocks"],
                   train_years=proto["train_years"], target_years=proto["target_years"])
    ev._store = store
    return store, proto, panel, compiler, ev


def cmd_build_panel(a):
    info = build_panel(a.source, a.panel_dir, a.instruments, start=a.start, end=a.end)
    print(json.dumps(info, ensure_ascii=False))


def cmd_init(a):
    os.makedirs(a.root, exist_ok=True)
    store = Store(a.root)
    if os.path.exists(store.protocol_path) and not a.force:
        print("protocol exists"); return
    proto = dict(DEFAULT_PROTOCOL, panel_dir=a.panel_dir, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with open(store.protocol_path, "w", encoding="utf-8") as fh:
        json.dump(proto, fh, ensure_ascii=False, indent=1)
    store.save_state({"batch": 0})
    print(json.dumps(proto, ensure_ascii=False, indent=1))


def add_set(store, compiler, items: dict[str, tuple[str, str, str]], source: str, batch: int):
    added = 0
    for name, (expr, mech, note) in items.items():
        try:
            canon = compiler.canonical(expr)
        except Exception as exc:
            _log(f"skip {name}: {exc}"); continue
        rec = {"id": compiler.identity(canon), "expression": expr, "canonical": canon, "name": name, "source": source,
               "batch": batch, "mechanism_id": mech, "hypothesis": note, "status": "pending"}
        added += store.add_candidate(rec)
    return added


def cmd_run_set(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    batch = store.state().get("batch", 0)
    if a.set == "classic":
        n = add_set(store, compiler, library.CLASSIC, "classic", batch)
    elif a.set == "alpha158":
        n = add_set(store, compiler, {k: (v, "alpha158", "Alpha158-lite feature") for k, v in library.alpha158_lite().items()}, "alpha158", batch)
    elif a.set == "legacy":
        cat = library.legacy_catalog(a.legacy_path)
        n = add_set(store, compiler, {k: (v["expression"], "legacy", ",".join(v.get("roles", []))) for k, v in cat.items()}, "legacy", batch)
    elif a.set == "minute":
        missing = [f for f in __import__("quanta_agents.factor_lab_a.minute_features", fromlist=["MINUTE_FIELDS"]).MINUTE_FIELDS if not panel.has(f)]
        if missing:
            raise SystemExit(f"panel lacks minute fields (run minute_features assemble first): {missing[:5]}")
        n = add_set(store, compiler, library.minute_set(), "minute", batch)
    else:
        raise SystemExit("unknown set")
    _log(f"added {n} candidates from {a.set}")
    if not a.no_eval:
        evaluate_pending(store, proto, panel, compiler, ev, limit=a.limit)


def compute_candidate(c: dict, panel, compiler, ev) -> tuple[pd.DataFrame, dict]:
    """Compute a candidate's factor frame: DSL expression, plugin, or plugin/expression + transform chain."""
    meta = {}
    if c.get("composite"):
        comp = c["composite"]
        store_ = getattr(ev, "_store", None)
        cands = {x["id"]: x for x in store_.candidates()} if store_ is not None else {}
        ranks = []
        for pid in comp["parents"]:
            pc = cands[pid]
            pf_, _ = compute_candidate(pc, panel, compiler, ev)
            d = (store_.load_result(pid) or {}).get("direction") or 1
            ranks.append((pf_ * d).where(ev.mask).rank(axis=1, pct=True).to_numpy(np.float32))
        arr = xo.composite(ranks[0], ranks[1], comp["op"])
        f = pd.DataFrame(arr, index=panel.dates, columns=panel.codes)
        return f, {"composite": comp}
    if c.get("plugin"):
        f, meta = external.compute_plugin(c["plugin"], panel, membership=ev._mask_np)
    else:
        # registered candidates are recomputed as written: the registration-time style rules (added after batch 2)
        # must not block members admitted before them
        strict = compiler.strict; compiler.strict = False
        try:
            f = compiler.evaluate(c["canonical"])
        finally:
            compiler.strict = strict
    if not isinstance(f, pd.DataFrame):
        raise ValueError("constant expression")
    if c.get("transform"):
        f = expand_mod.apply_transform_frames(f, c["transform"], panel)
    return f, meta


def pct_ranks(f: pd.DataFrame, ev) -> np.ndarray:
    return f.where(ev.mask).rank(axis=1, pct=True).to_numpy(np.float16)


def _admit(store, proto, ev, c, res, sig, rank_path, sigs, t0):
    """Serial part of evaluation: redundancy vs pool, failure class, admission/replacement, ledger writes."""
    adm = proto["admission"]
    direction = res.get("direction") or 0
    max_corr, arg = 0.0, None
    if sig is not None:
        sig_np = sig.to_numpy(np.float32) if isinstance(sig, pd.DataFrame) else np.asarray(sig, dtype=np.float32)
        checked = False
        for attempt in range(3):
            try:
                for pid, s in sigs.items():
                    r = signature_corr(sig_np, s)
                    if np.isfinite(r) and r > max_corr:
                        max_corr, arg = r, pid
                checked = True
                break
            except MemoryError:
                _log("memory error in redundancy check; sleeping 120s"); max_corr, arg = 0.0, None; time.sleep(120)
        if not checked:
            _log(f"redundancy check impossible for {c.get('name')}; left pending"); return 0
        del sig_np
    res["max_pool_corr"] = float(max_corr); res["redundant_with"] = arg
    res["failure_class"] = classify_failure(res, proto["thresholds"])
    if max_corr >= adm["max_pool_corr"]:
        res["failure_class"] = "redundant"
    mean_ric = res.get("target_mean_rank_ic") or -1
    min_cov = adm.get("min_stock_coverage", 0.8)
    cov_ok = (res.get("stock_coverage") or 0) >= min_cov
    replace = None
    if arg is not None and max_corr >= adm["max_pool_corr"] and cov_ok and direction != 0:
        old = store.load_result(arg) or {}
        old_cov = old.get("stock_coverage") or ((old.get("median_stocks") or 0) / max(res.get("median_stocks") or 1, 1) * (res.get("stock_coverage") or 1))
        if old_cov < min_cov and (mean_ric >= 0.6 * (old.get("target_mean_rank_ic") or 0) or mean_ric >= 0.05):
            replace = arg
    admit = (direction != 0 and res.get("train_t") is not None and abs(res["train_t"]) >= adm["min_train_abs_t"]
             and cov_ok and (max_corr < adm["max_pool_corr"] or replace is not None) and len(sigs) < adm["pool_max"]
             and (mean_ric >= adm.get("min_mean_for_correlated", 0.03)
                  or (mean_ric >= adm["min_target_mean_rank_ic"] and max_corr <= adm.get("orthogonal_corr", 0.5))
                  or ((res.get("long_hold5") or {}).get("net_sharpe") or 0) >= adm.get("long_route_net_sharpe", 1.0)))
    res["in_pool"] = bool(admit)
    if admit and replace is not None:
        old = store.load_result(replace) or {}
        old["in_pool"] = False; old["replaced_by"] = c["id"]; old["failure_class"] = "low_coverage_replaced"
        store.save_result(replace, old); store.drop_pool_frame(replace); sigs.pop(replace, None)
        res["replaces"] = replace; res["failure_class"] = "replacement"
    if admit and rank_path and sig is not None:
        np.save(store.rank_path(c["id"]), np.load(rank_path))
        store.save_signature(c["id"], sig); sigs[c["id"]] = sig.to_numpy(np.float16) if isinstance(sig, pd.DataFrame) else np.asarray(sig, dtype=np.float16)
    store.save_result(c["id"], res)
    if c.get("parents"):
        best_parent = max(((store.load_result(pid) or {}).get("target_mean_rank_ic") or -1) for pid in c["parents"])
        xo.append_lineage(store.root, {"child": c["id"], "parents": c["parents"], "op": (c.get("composite") or {}).get("op") or str(c.get("hypothesis", ""))[:40],
                                       "source": c.get("source"), "child_mean": res.get("target_mean_rank_ic"), "child_worst": res.get("target_worst_rank_ic"),
                                       "best_parent_mean": best_parent, "delta": (res.get("target_mean_rank_ic") or 0) - best_parent,
                                       "in_pool": res.get("in_pool"), "max_pool_corr": res.get("max_pool_corr")})
    _lh = res.get("long_hold5") or {}
    _log(f"{str(c['name'])[:28]:28s} mean {res.get('target_mean_rank_ic') or 0:+.4f} worst {res.get('target_worst_rank_ic') or 0:+.4f} "
         f"t {res.get('train_t') or 0:+.1f} corr {max_corr:.2f} long {_lh.get('net_excess_ann') or 0:+.3f}/{_lh.get('net_sharpe') or 0:.2f} {res['failure_class']} pool={admit} {time.time() - t0:.1f}s")
    return 1


def evaluate_pending(store, proto, panel, compiler, ev, limit=None, ids=None, workers=None):
    adm = proto["admission"]
    pending = [c for c in store.candidates() if not store.has_result(c["id"])]
    if ids:
        pending = [c for c in pending if c["id"] in ids]
    if limit:
        pending = pending[:limit]
    workers = workers if workers is not None else int(os.environ.get("FLA_WORKERS", "6"))
    _log(f"evaluating {len(pending)} pending candidates (workers={workers})")
    pool_ids = store.pool_ids()
    # pool signatures kept as float16 arrays (~6MB each) instead of float32 DataFrames: the parent process
    # otherwise holds >2GB for a 400-member pool, which exhausts the commit limit next to the workers
    sigs = {}
    for pid in pool_ids:
        sp = os.path.join(store.root, "signatures", f"{pid}.npy")
        if os.path.exists(sp):
            sigs[pid] = np.load(sp).astype(np.float16)
    done = 0
    if workers > 1 and len(pending) > 1:
        from .parallel import run_parallel
        gen = run_parallel(pending, store.root, proto["panel_dir"], proto["target_years"], proto["train_years"], workers=workers, full=True, log=_log,
                           universe=proto["universe"], label=proto["label"], min_stocks=proto["min_stocks"])
        cand_by_id = {c["id"]: c for c in pending}
        for w in gen:
            c = cand_by_id[w["id"]]; t0 = time.time()
            if "error" in w or "result" not in w:
                err = w.get("error", "no result")
                if "allocate" in err or "MemoryError" in err or "malloc" in err:
                    _log(f"transient memory error, left pending: {c['name']}"); continue
                store.save_result(c["id"], {"status": "error", "error": err[:300], "failure_class": "implementation_error"})
                _log(f"error {c['name']}: {err[:100]}"); continue
            res = w["result"]; res["status"] = "ok"; direction = res["direction"]
            res["long_hold5"] = w.get("long_hold5")
            sig = None
            if w.get("sig_path"):
                sig = pd.DataFrame(np.load(w["sig_path"]).astype(np.float32), index=ev.sample_idx, columns=panel.codes)
            done += _admit(store, proto, ev, c, res, sig, w.get("rank_path"), sigs, t0)
            del sig
            for k in ("rank_path", "sig_path"):
                if w.get(k) and os.path.exists(w[k]):
                    try:
                        os.remove(w[k])
                    except OSError:
                        pass
        st = store.state(); st["pool_size"] = len(store.pool_ids()); store.save_state(st)
        return done
    for c in pending:
        t0 = time.time()
        try:
            f, meta = compute_candidate(c, panel, compiler, ev)
            res = ev.evaluate(f)
            res.update({k: v for k, v in meta.items() if k != "plugin"})
        except MemoryError:
            from .parallel import _CORE_FIELDS
            f = None; panel.release(keep=tuple(k for k in panel._cache if k in _CORE_FIELDS))
            _log(f"transient memory error, left pending: {c['name']}; sleeping 120s"); time.sleep(120); continue
        except Exception as exc:
            store.save_result(c["id"], {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "failure_class": "implementation_error"})
            _log(f"error {c['name']}: {str(exc)[:100]}")
            continue
        res["status"] = "ok"
        direction = res["direction"]
        # redundancy vs pool
        sig = ev.signature(f * (direction or 1))
        max_corr, arg = 0.0, None
        for pid, s in sigs.items():
            r = signature_corr(sig, s)
            if np.isfinite(r) and r > max_corr:
                max_corr, arg = r, pid
        res["max_pool_corr"] = float(max_corr); res["redundant_with"] = arg
        try:
            if direction:
                _pct = pct_ranks(f * direction, ev).astype(np.float32)
                _ps = pf.portfolio_series(_pct, panel["label_1"].to_numpy(np.float64), ev._mask_np, panel.dates, top_frac=0.1, hold=5)
                _sm = pf.summarize(_ps, tuple(proto["target_years"]), cost_rate=0.001)
                res["long_hold5"] = {k: _sm[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "years_net_positive")}
        except Exception as _exc:
            res["long_hold5"] = {"error": str(_exc)[:80]}
        res["failure_class"] = classify_failure(res, proto["thresholds"])
        if max_corr >= adm["max_pool_corr"]:
            res["failure_class"] = "redundant"
        mean_ric = res.get("target_mean_rank_ic") or -1
        min_cov = adm.get("min_stock_coverage", 0.8)
        cov_ok = (res.get("stock_coverage") or 0) >= min_cov
        # replacement channel: a full-coverage rewrite of a low-coverage pool member takes its place
        replace = None
        if arg is not None and max_corr >= adm["max_pool_corr"] and cov_ok and direction != 0:
            old = store.load_result(arg) or {}
            old_cov = old.get("stock_coverage") or ((old.get("median_stocks") or 0) / max(res.get("median_stocks") or 1, 1) * (res.get("stock_coverage") or 1))
            if old_cov < min_cov and (mean_ric >= 0.6 * (old.get("target_mean_rank_ic") or 0) or mean_ric >= 0.05):
                replace = arg
        admit = (direction != 0 and res.get("train_t") is not None and abs(res["train_t"]) >= adm["min_train_abs_t"]
                 and cov_ok and (max_corr < adm["max_pool_corr"] or replace is not None) and len(sigs) < adm["pool_max"]
                 and (mean_ric >= adm.get("min_mean_for_correlated", 0.03)
                      or (mean_ric >= adm["min_target_mean_rank_ic"] and max_corr <= adm.get("orthogonal_corr", 0.5))
                      or ((res.get("long_hold5") or {}).get("net_sharpe") or 0) >= adm.get("long_route_net_sharpe", 1.0)))
        res["in_pool"] = bool(admit)
        if admit and replace is not None:
            old = store.load_result(replace) or {}
            old["in_pool"] = False; old["replaced_by"] = c["id"]; old["failure_class"] = "low_coverage_replaced"
            store.save_result(replace, old)
            store.drop_pool_frame(replace)
            sigs.pop(replace, None)
            res["replaces"] = replace
            res["failure_class"] = "replacement"
        if admit:
            store.save_pool_ranks(c["id"], pct_ranks(f * direction, ev))
            store.save_signature(c["id"], sig)
            sigs[c["id"]] = sig.to_numpy(np.float16)
        store.save_result(c["id"], res)
        if c.get("parents"):
            best_parent = max(((store.load_result(pid) or {}).get("target_mean_rank_ic") or -1) for pid in c["parents"])
            xo.append_lineage(store.root, {"child": c["id"], "parents": c["parents"], "op": (c.get("composite") or {}).get("op") or str(c.get("hypothesis", ""))[:40],
                                           "source": c.get("source"), "child_mean": res.get("target_mean_rank_ic"), "child_worst": res.get("target_worst_rank_ic"),
                                           "best_parent_mean": best_parent, "delta": (res.get("target_mean_rank_ic") or 0) - best_parent,
                                           "in_pool": res.get("in_pool"), "max_pool_corr": res.get("max_pool_corr")})
        done += 1
        _lh = res.get("long_hold5") or {}
        _log(f"{c['name'][:28]:28s} mean {res.get('target_mean_rank_ic') or 0:+.4f} worst {res.get('target_worst_rank_ic') or 0:+.4f} "
             f"t {res.get('train_t') or 0:+.1f} corr {max_corr:.2f} long {_lh.get('net_excess_ann') or 0:+.3f}/{_lh.get('net_sharpe') or 0:.2f} {res['failure_class']} pool={admit} {time.time() - t0:.1f}s")
        from .parallel import _CORE_FIELDS
        panel.release(keep=tuple(k for k in panel._cache if k in _CORE_FIELDS))
    st = store.state()
    st["pool_size"] = len(store.pool_ids())
    store.save_state(st)
    return done


def cmd_evaluate(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    evaluate_pending(store, proto, panel, compiler, ev, limit=a.limit)


def cmd_eval_expr(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    f = compiler.evaluate(a.expr)
    res = ev.evaluate(f)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def train_slice(panel, proto):
    end = f"{max(proto['train_years'])}-12-31"
    return end


def cmd_probe(a):
    """Training-years-only statistics for the research sub-agent. Reveals nothing after the training years."""
    store, proto, panel, compiler, ev = get_ctx(a.root)
    end = train_slice(panel, proto)
    cache = {}
    def resolver(name):
        if name not in cache:
            cache[name] = panel[name].loc[:end]
        return cache[name]
    comp = Compiler(resolver=resolver)
    label = ev.label.loc[:end]; mask = ev.mask.loc[:end]
    from .evaluate import rank_ic
    top = store.table()
    top_ids = []
    if len(top) and "mean_ric" in top:
        top_ids = list(top[top["in_pool"] == True].sort_values("mean_ric", ascending=False)["id"].head(10))
    train_dates = [d for d in ev.sample_idx if d <= pd.Timestamp(end)]
    sigs = {pid: store.load_signature(pid, ev.sample_idx, panel.codes) for pid in top_ids}
    out = []
    for expr in a.expr:
        rec = {"expression": expr}
        try:
            canon = comp.canonical(expr)
            f = comp.evaluate(canon)
            if not isinstance(f, pd.DataFrame):
                raise ValueError("constant expression")
            ric, n = rank_ic(f, label, mask, proto["min_stocks"])
            s = ric.dropna()
            rec.update({"canonical": canon, "train_days": int(len(s)), "train_mean_rank_ic": float(s.mean()) if len(s) else None,
                        "train_t": float(s.mean() / s.std(ddof=1) * np.sqrt(len(s))) if len(s) > 1 else None,
                        "coverage": float((n >= proto["min_stocks"]).mean()),
                        "by_year": {str(y): float(s[s.index.year == y].mean()) for y in proto["train_years"]}})
            sig = f.loc[train_dates].where(mask.loc[train_dates]).rank(axis=1, pct=True)
            corr = {}
            for pid, sg in sigs.items():
                if sg is None: continue
                corr[pid] = round(signature_corr(sig, sg.loc[train_dates]), 3)
            rec["abs_corr_with_top_pool"] = corr
            rec["already_registered"] = canon in store.by_canonical()
        except Exception as exc:
            rec["error"] = str(exc)[:200]
        out.append(rec)
    # probe ledger
    with open(os.path.join(store.root, "probes.jsonl"), "a", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(dict(r, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), ensure_ascii=False) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_gp(a):
    from .gp import GPSearch
    store, proto, panel, compiler, ev = get_ctx(a.root)
    end = train_slice(panel, proto)
    names = ["close", "open", "high", "low", "volume", "amount", "turnover", "ret", "vwap", "log_cap", "prev_close", "mkt_ret"]
    if a.minute:
        from .minute_features import MINUTE_FIELDS
        from . import gp as gp_mod
        names += [f for f in MINUTE_FIELDS if panel.has(f)]
        gp_mod.TERMINALS = list(gp_mod.TERMINALS) + [f for f in MINUTE_FIELDS if panel.has(f)]
    fields = {k: panel[k].loc[:end] for k in names}
    gp = GPSearch(compiler, fields, ev.label.loc[:end], ev.mask.loc[:end], seed=a.seed, min_stocks=proto["min_stocks"])
    scored = gp.run(population=a.population, generations=a.generations, elite=a.elite, log=_log)
    batch = store.state().get("batch", 0)
    added = 0
    for r in scored[: a.top]:
        rec = {"id": compiler.identity(r["canonical"]), "expression": r["canonical"], "canonical": r["canonical"],
               "name": f"gp{a.seed}_{added:02d}", "source": "gp_minute" if a.minute else "gp", "batch": batch, "mechanism_id": "gp_search_minute" if a.minute else "gp_search",
               "hypothesis": f"GP search seed {a.seed}; train t {r['t']:.2f}", "status": "pending"}
        added += store.add_candidate(rec)
    _log(f"gp added {added} candidates")
    if not a.no_eval:
        evaluate_pending(store, proto, panel, compiler, ev)


def cmd_harvest(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    batch = store.state().get("batch", 0)
    added = 0
    for it in external.list_plugins():
        if a.sets and it["source"] not in a.sets:
            continue
        canon = "plugin:" + it["plugin"]
        rec = {"id": compiler.identity(canon), "expression": it["formula"][:400] or it["plugin"], "canonical": canon,
               "name": it["name"], "source": it["source"], "batch": batch, "mechanism_id": it["source"],
               "hypothesis": "external library factor", "plugin": it["plugin"], "status": "pending"}
        added += store.add_candidate(rec)
    _log(f"harvest added {added} plugin candidates")
    if not a.no_eval:
        evaluate_pending(store, proto, panel, compiler, ev, limit=a.limit)


def cmd_backfill_ranks(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    n = 0
    for cid in store.pool_ids():
        if store.has_pool_ranks(cid) and not a.force:
            continue
        f = store.load_pool_frame(cid)
        store.save_pool_ranks(cid, pct_ranks(f, ev))
        n += 1
        if n % 20 == 0:
            _log(f"backfilled {n}")
    _log(f"backfilled ranks for {n} members")


def cmd_expand(a):
    """Generate variants of seed factors, screen on training years, register survivors."""
    store, proto, panel, compiler, ev = get_ctx(a.root)
    end = train_slice(panel, proto)
    cache = {}
    def resolver(name):
        if name not in cache:
            cache[name] = panel[name].loc[:end]
        return cache[name]
    comp = Compiler(resolver=resolver)
    label = ev.label.loc[:end]; mask = ev.mask.loc[:end]
    screen = expand_mod.TrainScreen(label, mask, proto["min_stocks"])
    class TrainPanel:
        dates = panel.dates[panel.dates <= pd.Timestamp(end)]; codes = panel.codes
        def __getitem__(self, k): return resolver(k)
        def has(self, k): return panel.has(k)
    tpanel = TrainPanel()
    t = store.table()
    cands = {c["id"]: c for c in store.candidates()}
    # pool signatures restricted to training dates
    train_dates = [d for d in ev.sample_idx if d <= pd.Timestamp(end)]
    for pid in store.pool_ids():
        sg = store.load_signature(pid, ev.sample_idx, panel.codes)
        if sg is not None:
            screen.sigs[pid] = sg.loc[train_dates].to_numpy(np.float32)
    screen.sample_rows = np.array([i for i, d in enumerate(tpanel.dates) if d in set(train_dates)])
    seeds = t.dropna(subset=["mean_ric"]) if len(t) else t
    if a.seeds == "pool":
        seeds = seeds[seeds["in_pool"] == True]
    elif a.seeds.startswith("source:"):
        seeds = seeds[seeds["source"] == a.seeds.split(":", 1)[1]]
    elif a.seeds.startswith("ids:"):
        seeds = seeds[seeds["id"].isin(a.seeds[4:].split(","))]
    seeds = seeds.sort_values("mean_ric", ascending=False).head(a.top)
    _log(f"expanding {len(seeds)} seeds; transforms {a.transforms or 'all'}; windows={not a.no_windows}")
    batch = store.state().get("batch", 0)
    registered, screened = 0, 0
    for _, srow in seeds.iterrows():
        c = cands[srow["id"]]
        variants = []
        if c.get("plugin"):
            base_key = c["plugin"]
            for key in (a.transforms or list(expand_mod.TRANSFORMS)):
                variants.append({"plugin": c["plugin"], "transform": (c.get("transform") or []) + [key], "tag": key,
                                 "canonical": f"plugin:{c['plugin']}|" + "|".join((c.get("transform") or []) + [key]), "expression": c["expression"]})
        else:
            base = c["canonical"]
            if not a.no_windows:
                for v, tag in expand_mod.window_variants(base):
                    variants.append({"expression": v, "tag": tag})
            for v, tag in expand_mod.transform_variants(base, a.transforms):
                variants.append({"expression": v, "tag": tag})
        for var in variants:
            try:
                if var.get("plugin"):
                    canon = var["canonical"]
                    f, _ = external.compute_plugin(var["plugin"], tpanel, membership=mask.to_numpy(bool))
                    f = expand_mod.apply_transform_frames(f, var["transform"], tpanel)
                else:
                    canon = comp.canonical(var["expression"])
                    if canon in store.by_canonical():
                        continue
                    f = comp.evaluate(canon)
                if canon in store.by_canonical():
                    continue
                st = screen.stats(f)
                screened += 1
                if not st.get("ok"):
                    continue
                corr, arg = screen.novelty(f)
                ok = (st["worst"] >= a.min_worst and abs(st["train_t"]) >= a.min_t and st["coverage"] >= 0.8 and corr < a.max_corr)
                seed_mean = float(srow.get("train_t") or 0)
                _log(f"  {c['name'][:22]:22s} {var['tag']:12s} train {st['train_mean']:+.4f} worst {st['worst']:+.4f} t {st['train_t']:+.1f} cov {st['coverage']:.2f} corr {corr:.2f} {'KEEP' if ok else 'drop'}")
                if not ok:
                    continue
                screen.add_signature(canon, f)
                rec = {"id": compiler.identity(canon), "expression": var.get("expression") or c["expression"], "canonical": canon,
                       "name": f"{c['name'][:24]}~{var['tag']}", "source": "expand", "batch": batch, "mechanism_id": c.get("mechanism_id"),
                       "hypothesis": f"variant of {c['id']} ({var['tag']})", "parents": [c["id"]], "status": "pending",
                       "train_screen": {k: st[k] for k in ("train_mean", "train_t", "worst", "coverage")}, "screen_corr": corr}
                if var.get("plugin"):
                    rec["plugin"] = var["plugin"]; rec["transform"] = var["transform"]
                registered += store.add_candidate(rec)
            except Exception as exc:
                _log(f"  {c['name'][:22]:22s} {var['tag']:12s} error {str(exc)[:80]}")
        cache.clear()
    _log(f"screened {screened} variants, registered {registered}")
    if not a.no_eval:
        evaluate_pending(store, proto, panel, compiler, ev)


def cmd_portfolio(a):
    """Pure-factor portfolio statistics for pool members (from rank arrays) or a saved prediction."""
    store, proto, panel, compiler, ev = get_ctx(a.root)
    label1 = panel["label_1"].to_numpy(np.float64); pmask = ev.mask.to_numpy(bool)
    years = tuple(proto["target_years"])
    if a.prediction:
        pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index)
        m = ev.mask.copy()
        if a.buyable:
            # cannot buy at next open if it opens limit-up: main board 10%, ChiNext/STAR 20% (from 2020-08-24 ChiNext; STAR always)
            gap = panel["open"].shift(-1) / panel["close"] - 1
            codes = pd.Index(panel.codes)
            wide = codes.str.startswith(("SZ300", "SZ301", "SH688", "SH689"))
            lim = pd.DataFrame(np.broadcast_to(np.where(np.asarray(wide)[None, :], 0.195, 0.095), (len(panel.dates), len(panel.codes))).copy(), index=panel.dates, columns=panel.codes)
            lim.loc[lim.index < "2020-08-24", codes[codes.str.startswith(("SZ300", "SZ301"))]] = 0.095
            m = m & ~(gap > lim)
        if a.smooth and a.smooth > 1:
            # time-smoothing of scores (per-date ranks averaged with exponential decay) to cut turnover
            pred = pred.where(m).rank(axis=1, pct=True).ewm(span=a.smooth, adjust=False, min_periods=1).mean()
        pct = pred.where(m).rank(axis=1, pct=True).to_numpy(np.float32)
        res = pf.evaluate_scores(pct, label1, m.to_numpy(bool), panel.dates, years=years, cost_rate=a.cost, holds=tuple(a.holds))
        out = {k: {kk: v[kk] for kk in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "max_drawdown_net", "years_net_positive")} for k, v in res.items()}
        out["annual_long_top10_hold5"] = {y: {kk: round(vv, 4) for kk, vv in v.items()} for y, v in res["long_top10_hold5"]["annual"].items()}
        out["cost_rate"] = a.cost; out["buyable_filter"] = a.buyable; out["smooth"] = a.smooth
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if a.save:
            with open(a.save, "w", encoding="utf-8") as fh:
                json.dump(res | {"cost_rate": a.cost, "buyable_filter": a.buyable, "prediction": a.prediction}, fh, ensure_ascii=False, indent=1)
        return
    t = store.table(); t = t[t["in_pool"] == True] if len(t) else t
    ids = list(t["id"]) if not a.ids else a.ids
    rows = []
    for i, cid in enumerate(ids):
        if not store.has_pool_ranks(cid):
            continue
        pct = np.asarray(np.load(store.rank_path(cid), mmap_mode="r"), dtype=np.float32)
        res = pf.evaluate_scores(pct, label1, pmask, panel.dates, years=years, holds=(5,), cost_rate=a.cost)
        r5 = res["long_top10_hold5"]; ls = res["long_short_hold5"]
        _r = store.load_result(cid)
        if _r is not None:
            _r["long_hold5"] = {k: r5[k] for k in ("gross_excess_ann", "gross_sharpe", "net_excess_ann", "net_sharpe", "daily_turnover", "years_net_positive")}
            store.save_result(cid, _r)
        rows.append({"id": cid, "name": t.set_index("id").loc[cid, "name"] if cid in t["id"].values else cid,
                     "mean_ric": float(t.set_index("id").loc[cid, "mean_ric"]) if cid in t["id"].values else None,
                     "long_gross_ann": r5["gross_excess_ann"], "long_gross_sharpe": r5["gross_sharpe"], "long_net_ann": r5["net_excess_ann"],
                     "long_net_sharpe": r5["net_sharpe"], "turnover": r5["daily_turnover"], "years_net_pos": r5["years_net_positive"],
                     "ls_gross_ann": ls["gross_excess_ann"], "ls_gross_sharpe": ls["gross_sharpe"]})
        if (i + 1) % 25 == 0:
            _log(f"portfolio stats {i + 1}/{len(ids)}")
    df = pd.DataFrame(rows).sort_values("long_net_sharpe", ascending=False)
    out = os.path.join(store.root, "batches", "pool_portfolio_stats.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(df.head(25).round(4).to_string(index=False)); print(out)


def cmd_crossover(a):
    """Cross-retrieve the pool: pick parents (quality/novelty/under-explored), pair across clusters, build
    composites, screen on training years, register survivors."""
    store, proto, panel, compiler, ev = get_ctx(a.root)
    end = train_slice(panel, proto)
    label = ev.label.loc[:end]; mask = ev.mask.loc[:end]
    t = store.table()
    lineage = xo.load_lineage(store.root)
    children = {}
    for r in lineage:
        for pid in r.get("parents", []):
            children[pid] = children.get(pid, 0) + 1
    cands = {c["id"]: c for c in store.candidates()}
    depth = {}
    for cid, c in cands.items():
        d = 0; cur = c
        while cur.get("parents") and d < 6:
            d += 1; cur = cands.get(cur["parents"][0], {})
        depth[cid] = d
    parents = xo.parent_scores(t, None, children, depth).head(a.parents)
    _log(f"selected {len(parents)} parents; top: {list(parents['name'].head(8))}")
    def sig_loader(cid):
        sg = store.load_signature(cid, ev.sample_idx, panel.codes)
        return None if sg is None else sg.to_numpy(np.float32)
    pairs = xo.cluster_pairs(parents, sig_loader, max_corr=a.max_pair_corr, max_pairs=a.pairs)
    _log(f"{len(pairs)} cross-cluster pairs (corr < {a.max_pair_corr})")
    screen = expand_mod.TrainScreen(label, mask, proto["min_stocks"])
    train_dates = [d for d in ev.sample_idx if d <= pd.Timestamp(end)]
    # novelty is checked against the selected parents only (composites mostly correlate with their parents);
    # full-pool redundancy is enforced later by evaluate_pending
    for pid in list(parents["id"]):
        sg = store.load_signature(pid, ev.sample_idx, panel.codes)
        if sg is not None:
            screen.sigs[pid] = sg.loc[train_dates].to_numpy(np.float32)
    n_train = int((panel.dates <= pd.Timestamp(end)).sum())
    tset = set(train_dates)
    screen.sample_rows = np.array([i for i, d in enumerate(panel.dates[:n_train]) if d in tset])
    rank_cache = {}
    def train_ranks(cid):
        if cid not in rank_cache:
            rank_cache[cid] = np.asarray(np.load(store.rank_path(cid), mmap_mode="r")[:n_train], dtype=np.float32)
        return rank_cache[cid]
    batch = store.state().get("batch", 0)
    registered = screened = 0
    ops = a.ops or list(xo.OPS)
    for a_id, b_id, corr_ab in pairs:
        ra, rb = train_ranks(a_id), train_ranks(b_id)
        for op in ops:
            for x_id, y_id, rx, ry in ((a_id, b_id, ra, rb), (b_id, a_id, rb, ra)):
                if op in ("sum", "prod", "min") and x_id > y_id:
                    continue
                canon = f"composite:{op}({x_id},{y_id})"
                if canon in store.by_canonical():
                    continue
                arr = xo.composite(rx, ry, op)
                f = pd.DataFrame(arr, index=panel.dates[:n_train], columns=panel.codes)
                st = screen.stats(f); screened += 1
                if not st.get("ok"):
                    continue
                corr, arg = screen.novelty(f)
                ok = st["worst"] >= a.min_worst and abs(st["train_t"]) >= a.min_t and st["coverage"] >= 0.5 and corr < a.max_corr
                _log("  %-8s %-18s x %-18s train %+.4f worst %+.4f t %+.1f cov %.2f corr %.2f %s" % (op, cands[x_id]["name"][:18], cands[y_id]["name"][:18], st["train_mean"], st["worst"], st["train_t"], st["coverage"], corr, "KEEP" if ok else "drop"))
                if not ok:
                    continue
                screen.add_signature(canon, f)
                rec = {"id": compiler.identity(canon), "expression": canon, "canonical": canon,
                       "name": "%s(%s,%s)" % (op, cands[x_id]["name"][:14], cands[y_id]["name"][:14]), "source": "crossover", "batch": batch,
                       "mechanism_id": "composite", "hypothesis": "%s of %s and %s (pair corr %.2f)" % (op, x_id, y_id, corr_ab),
                       "parents": [x_id, y_id], "composite": {"op": op, "parents": [x_id, y_id]}, "status": "pending",
                       "train_screen": {k: st[k] for k in ("train_mean", "train_t", "worst", "coverage")}, "screen_corr": corr}
                registered += store.add_candidate(rec)
    _log(f"screened {screened} composites, registered {registered}")
    if not a.no_eval:
        evaluate_pending(store, proto, panel, compiler, ev)


def load_rank_store(store, members: list[str]) -> dict:
    out = {}
    for m in members:
        if not store.has_pool_ranks(m):
            raise SystemExit(f"missing pool ranks for {m}; run backfill-ranks")
        out[m] = LazyRanks(store.rank_path(m))
    return out


def select_members(store, how: str, n: int) -> list[str]:
    t = store.table()
    if not len(t) or "in_pool" not in t:
        return []
    t = t[t["in_pool"] == True].dropna(subset=["mean_ric"])
    cov = {}
    for cid in t["id"]:
        r = store.load_result(cid) or {}
        cov[cid] = r.get("median_stocks") or 0
    if cov:
        ref = max(cov.values())
        t = t[[cov[c] >= 0.8 * ref for c in t["id"]]]
    if how.startswith("source:"):
        t = t[t["source"] == how.split(":", 1)[1]]
    if how.startswith("diverse"):
        # greedy: order by training |t| (not exposed years), accept if max |corr| with chosen < cap
        cap = float(how.split(":", 1)[1]) if ":" in how else 0.7
        t = t.assign(abs_t=t["train_t"].abs()).sort_values("abs_t", ascending=False)
        chosen, sigs = [], []
        for _, r in t.iterrows():
            if len(chosen) >= n:
                break
            p = store.rank_path(r["id"]).replace("pool_ranks", "signatures")
            if not os.path.exists(p):
                continue
            sg = np.load(p).astype(np.float32)
            ok = True
            for other in sigs:
                m = np.isfinite(sg) & np.isfinite(other)
                nn = m.sum(axis=1)
                a = np.where(m, sg, 0); b = np.where(m, other, 0)
                with np.errstate(all="ignore"):
                    am = a.sum(axis=1) / np.maximum(nn, 1); bm = b.sum(axis=1) / np.maximum(nn, 1)
                    ac = np.where(m, a - am[:, None], 0); bc = np.where(m, b - bm[:, None], 0)
                    rr = (ac * bc).sum(axis=1) / np.sqrt((ac ** 2).sum(axis=1) * (bc ** 2).sum(axis=1))
                if np.nanmean(np.abs(rr[nn >= 100])) >= cap:
                    ok = False; break
            if ok:
                chosen.append(r["id"]); sigs.append(sg)
        return chosen
    t = t.sort_values("mean_ric", ascending=False)
    return list(t["id"].head(n))


def cmd_combine(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    if a.members.startswith("file:"):
        with open(a.members[5:], encoding="utf-8") as fh:
            members = json.load(fh)
    else:
        members = select_members(store, a.members, a.n)
    if len(members) < 2:
        raise SystemExit("not enough pool members")
    _log(f"loading {len(members)} pool rank arrays (memmap)")
    frames = load_rank_store(store, members)
    results = []
    for model in a.model:
        for target in a.target:
            for update in a.update:
                _log(f"combination {model}/{target}/{update} n={len(members)}")
                res, pred = rolling_combination(panel, ev, frames, members, model=model, target=target, update=update,
                                                train_years=tuple(proto["train_years"]), target_years=tuple(proto["target_years"]),
                                                day_step=a.day_step, params=json.loads(a.params) if a.params else None,
                                                context_fields=tuple(a.context or ()), n_seeds=a.seeds)
                res["members"] = a.members; res["tag"] = a.tag
                if a.save_prediction:
                    ppath = os.path.join(store.root, "batches", f"pred_{a.tag}_{model}_{target}_{update}.parquet")
                    pred.astype(np.float32).to_parquet(ppath)
                    res["prediction_path"] = ppath
                    pmask = ev.mask.to_numpy(bool)
                    pct = pred.where(ev.mask).rank(axis=1, pct=True).to_numpy(np.float32)
                    res["portfolio"] = pf.evaluate_scores(pct, panel["label_1"].to_numpy(np.float64), pmask, panel.dates, years=tuple(proto["target_years"]))
                    for k, v in res["portfolio"].items():
                        _log("  %-22s gross %+.3f (sh %.2f) net %+.3f (sh %.2f) turnover %.3f years_net_pos %d" % (k, v["gross_excess_ann"], v["gross_sharpe"], v["net_excess_ann"], v["net_sharpe"], v["daily_turnover"], v["years_net_positive"]))
                results.append(res)
                ar = res.get("annual_rank_ic", {}); tr = res.get("trading", {})
                _log("  mean %.4f worst %.4f | %s | top10 excess %.4f turnover %.3f" % (res.get("target_mean_rank_ic", 0), res.get("target_worst_rank_ic", 0),
                                                       " ".join(f"{y}:{ar[str(y)]['mean']:+.3f}" for y in proto["target_years"] if str(y) in ar),
                                                       tr.get("top_decile_excess_5d", float("nan")), tr.get("top_decile_daily_turnover", float("nan"))))
    path = os.path.join(store.root, "batches", f"combinations_{a.tag}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    st = store.state()
    best = max((r for r in results if r.get("status") == "ok"), key=lambda r: r["target_mean_rank_ic"], default=None)
    if best and best["target_mean_rank_ic"] > (st.get("best_combination", {}).get("target_mean_rank_ic") or -1):
        st["best_combination"] = {k: best[k] for k in ("model", "target", "update", "n_features", "target_mean_rank_ic", "target_worst_rank_ic", "annual_rank_ic", "tag")}
    store.save_state(st)


def cmd_propose_request(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    st = store.state()
    batch = a.batch if a.batch is not None else st.get("batch", 0) + 1
    anchor = st.get("anchor")
    d = llm.write_propose_request(store, batch, anchor, n_min=a.n_min, n_max=a.n_max, max_probes=a.max_probes,
                                  probe_cmd=f"cd \"{repo_root()}\" && PYTHONPATH=src \"{sys.executable}\" -m quanta_agents.factor_lab_a.cli probe")
    st["batch"] = batch; st["phase"] = "awaiting_proposal"; store.save_state(st)
    print(d)


def cmd_ingest_proposal(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    batch = a.batch if a.batch is not None else store.state().get("batch", 0)
    d = os.path.join(store.root, "requests", f"batch{batch:03d}_propose")
    summary = llm.ingest_proposal(store, compiler, d, batch)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if not a.no_eval:
        evaluate_pending(store, proto, panel, compiler, ev)
    st = store.state(); st["phase"] = "evaluated"; store.save_state(st)


def cmd_review_request(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    batch = a.batch if a.batch is not None else store.state().get("batch", 0)
    combos = []
    bdir = os.path.join(store.root, "batches")
    for f in sorted(os.listdir(bdir)):
        if f.startswith("combinations_") and f.endswith(".json"):
            with open(os.path.join(bdir, f), encoding="utf-8") as fh:
                combos.extend(json.load(fh))
    d = llm.write_review_request(store, batch, combos[-12:])
    st = store.state(); st["phase"] = "awaiting_review"; store.save_state(st)
    print(d)


def cmd_ingest_review(a):
    store, proto, panel, compiler, ev = get_ctx(a.root)
    batch = a.batch if a.batch is not None else store.state().get("batch", 0)
    d = os.path.join(store.root, "requests", f"batch{batch:03d}_review")
    print(json.dumps(llm.ingest_review(store, d, batch), ensure_ascii=False))
    st = store.state(); st["phase"] = "reviewed"; store.save_state(st)


def cmd_set_anchor(a):
    store = Store(a.root)
    st = store.state()
    with open(a.file, encoding="utf-8") as fh:
        combos = json.load(fh)
    ok = [c for c in combos if c.get("status") == "ok"]
    st["anchor"] = {c["model"] + "/" + c["target"] + "/" + c["update"]: {"n": c["n_features"], "mean_rank_ic": round(c["target_mean_rank_ic"], 4),
                    "worst_rank_ic": round(c["target_worst_rank_ic"], 4), "annual": {y: round(v["mean"], 4) for y, v in c["annual_rank_ic"].items() if v["mean"] is not None}} for c in ok}
    st["anchor"]["description"] = a.description
    store.save_state(st)
    print(json.dumps(st["anchor"], ensure_ascii=False, indent=1))


def cmd_status(a):
    store = Store(a.root)
    st = store.state()
    t = store.table()
    proto = store.protocol()
    thr = proto["thresholds"]
    out = {"state": st, "candidates": int(len(t)), "evaluated": int(t["mean_ric"].notna().sum()) if "mean_ric" in t else 0,
           "pool": len(store.pool_ids())}
    if "mean_ric" in t and t["mean_ric"].notna().any():
        e = t.dropna(subset=["mean_ric"]).sort_values("mean_ric", ascending=False)
        out["best_single_by_mean"] = e.head(10)[["id", "name", "source", "mechanism", "mean_ric", "worst_ric", "icir", "expression"]].to_dict(orient="records")
        out["pass_single_mean"] = int((e["mean_ric"] > thr["single_factor_mean"]).sum())
        out["pass_single_worst"] = int((e["worst_ric"] > thr["single_factor_worst_strong"]).sum())
        out["by_source"] = e.groupby("source")["mean_ric"].agg(["count", "max", "mean"]).round(4).to_dict(orient="index")
        out["failure_counts"] = t["failure"].fillna("pending").value_counts().to_dict()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def cmd_report(a):
    store = Store(a.root)
    t = store.table()
    st = store.state(); proto = store.protocol(); thr = proto["thresholds"]
    lines = [f"# A层因子研究状态（{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}）", "",
             f"协议 {proto['version']}：池 {proto['universe']}，标签 {proto['label']}，指标 RankIC；验收 单因子六年均值 > {thr['single_factor_mean']}（最差年 > {thr['single_factor_worst_strong']} 为强通过），组合 > {thr['combination_mean']}。2019—2024 为已曝光历史。", ""]
    if "mean_ric" in t:
        e = t.dropna(subset=["mean_ric"])
        lines.append(f"候选 {len(t)}，已评 {len(e)}，池内 {len(store.pool_ids())}；单因子均值过线 {(e['mean_ric'] > thr['single_factor_mean']).sum()}，最差年过线 {(e['worst_ric'] > thr['single_factor_worst_strong']).sum()}。")
        lines.append("")
        lines.append("## 单因子前 20（六年均值 RankIC）")
        lines.append("| 名称 | 来源 | 机制 | 均值 | 最差 | ICIR | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 表达式 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in e.sort_values("mean_ric", ascending=False).head(20).iterrows():
            lines.append("| " + " | ".join([str(r["name"]), str(r["source"]), str(r["mechanism"]), f"{r['mean_ric']:+.4f}", f"{r['worst_ric']:+.4f}", f"{(r['icir'] or 0):+.2f}"]
                                            + [f"{(r.get(f'ric{y}') or 0):+.4f}" for y in range(2019, 2025)] + [str(r["expression"])[:90]]) + " |")
        lines.append("")
        lines.append("## 按来源")
        g = e.groupby("source")["mean_ric"].agg(["count", "max", "mean"]).round(4)
        lines.append("| 来源 | 数量 | 最高均值 | 平均均值 |"); lines.append("|---|---|---|---|")
        for src, row in g.iterrows():
            lines.append(f"| {src} | {int(row['count'])} | {row['max']:+.4f} | {row['mean']:+.4f} |")
    if st.get("best_combination"):
        b = st["best_combination"]
        lines += ["", "## 最佳组合", f"{b['model']}/{b['target']}/{b['update']} 成员 {b['n_features']}：六年均值 {b['target_mean_rank_ic']:+.4f}，最差 {b['target_worst_rank_ic']:+.4f}",
                  "逐年: " + ", ".join(f"{y}: {v['mean']:+.4f}" for y, v in b["annual_rank_ic"].items())]
    if st.get("anchor"):
        lines += ["", "## 锚定基线", "```", json.dumps(st["anchor"], ensure_ascii=False, indent=1), "```"]
    path = os.path.join(store.root, "REPORT.zh-CN.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(path)


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build-panel"); s.add_argument("--source", required=True); s.add_argument("--instruments", required=True)
    s.add_argument("--panel-dir", required=True); s.add_argument("--start", default="2015-01-01"); s.add_argument("--end", default="2024-12-31"); s.set_defaults(fn=cmd_build_panel)
    s = sub.add_parser("init"); s.add_argument("--root", required=True); s.add_argument("--panel-dir", required=True); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("run-set"); s.add_argument("--root", required=True); s.add_argument("--set", required=True); s.add_argument("--legacy-path", default=r"F:/V10A_Factor_Research/reference_catalog.json")
    s.add_argument("--no-eval", action="store_true"); s.add_argument("--limit", type=int); s.set_defaults(fn=cmd_run_set)
    s = sub.add_parser("evaluate"); s.add_argument("--root", required=True); s.add_argument("--limit", type=int); s.set_defaults(fn=cmd_evaluate)
    s = sub.add_parser("eval-expr"); s.add_argument("--root", required=True); s.add_argument("--expr", required=True); s.set_defaults(fn=cmd_eval_expr)
    s = sub.add_parser("probe"); s.add_argument("--root", required=True); s.add_argument("--expr", action="append", required=True); s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("gp"); s.add_argument("--root", required=True); s.add_argument("--population", type=int, default=120); s.add_argument("--generations", type=int, default=4)
    s.add_argument("--elite", type=int, default=20); s.add_argument("--top", type=int, default=15); s.add_argument("--seed", type=int, default=0); s.add_argument("--no-eval", action="store_true"); s.add_argument("--minute", action="store_true"); s.set_defaults(fn=cmd_gp)
    s = sub.add_parser("combine"); s.add_argument("--root", required=True); s.add_argument("--members", default="all"); s.add_argument("--n", type=int, default=80)
    s.add_argument("--model", nargs="+", default=["lgbm"]); s.add_argument("--target", nargs="+", default=["raw"]); s.add_argument("--update", nargs="+", default=["expanding"])
    s.add_argument("--day-step", type=int, default=2); s.add_argument("--tag", default="run"); s.add_argument("--params", default=None); s.add_argument("--context", nargs="*", default=None); s.add_argument("--seeds", type=int, default=1); s.add_argument("--save-prediction", action="store_true"); s.set_defaults(fn=cmd_combine)
    s = sub.add_parser("harvest"); s.add_argument("--root", required=True); s.add_argument("--sets", nargs="*", default=None); s.add_argument("--no-eval", action="store_true"); s.add_argument("--limit", type=int); s.set_defaults(fn=cmd_harvest)
    s = sub.add_parser("backfill-ranks"); s.add_argument("--root", required=True); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_backfill_ranks)
    s = sub.add_parser("expand"); s.add_argument("--root", required=True); s.add_argument("--seeds", default="pool"); s.add_argument("--top", type=int, default=30)
    s.add_argument("--transforms", nargs="*", default=None); s.add_argument("--no-windows", action="store_true"); s.add_argument("--min-worst", type=float, default=0.01)
    s.add_argument("--min-t", type=float, default=4.0); s.add_argument("--max-corr", type=float, default=0.9); s.add_argument("--no-eval", action="store_true"); s.set_defaults(fn=cmd_expand)
    s = sub.add_parser("crossover"); s.add_argument("--root", required=True); s.add_argument("--parents", type=int, default=40); s.add_argument("--pairs", type=int, default=60)
    s.add_argument("--max-pair-corr", type=float, default=0.5); s.add_argument("--ops", nargs="*", default=None); s.add_argument("--min-worst", type=float, default=0.01)
    s.add_argument("--min-t", type=float, default=4.0); s.add_argument("--max-corr", type=float, default=0.9); s.add_argument("--no-eval", action="store_true"); s.set_defaults(fn=cmd_crossover)
    s = sub.add_parser("portfolio"); s.add_argument("--root", required=True); s.add_argument("--prediction", default=None); s.add_argument("--ids", nargs="*", default=None); s.add_argument("--cost", type=float, default=0.001); s.add_argument("--buyable", action="store_true"); s.add_argument("--save", default=None); s.add_argument("--smooth", type=int, default=0); s.add_argument("--holds", nargs="*", type=int, default=[1, 5]); s.set_defaults(fn=cmd_portfolio)
    s = sub.add_parser("set-anchor"); s.add_argument("--root", required=True); s.add_argument("--file", required=True); s.add_argument("--description", default=""); s.set_defaults(fn=cmd_set_anchor)
    s = sub.add_parser("propose-request"); s.add_argument("--root", required=True); s.add_argument("--batch", type=int); s.add_argument("--n-min", type=int, default=8)
    s.add_argument("--n-max", type=int, default=14); s.add_argument("--max-probes", type=int, default=15); s.set_defaults(fn=cmd_propose_request)
    s = sub.add_parser("ingest-proposal"); s.add_argument("--root", required=True); s.add_argument("--batch", type=int); s.add_argument("--no-eval", action="store_true"); s.set_defaults(fn=cmd_ingest_proposal)
    s = sub.add_parser("review-request"); s.add_argument("--root", required=True); s.add_argument("--batch", type=int); s.set_defaults(fn=cmd_review_request)
    s = sub.add_parser("ingest-review"); s.add_argument("--root", required=True); s.add_argument("--batch", type=int); s.set_defaults(fn=cmd_ingest_review)
    s = sub.add_parser("status"); s.add_argument("--root", required=True); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("report"); s.add_argument("--root", required=True); s.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()

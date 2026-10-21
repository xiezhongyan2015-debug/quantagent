"""File-based research requests answered by an external research sub-agent.

propose: program writes requests/<batch>_propose/prompt.md + context.json; the sub-agent may run
`probe` (training-years-only statistics) and then writes response.json with candidates.
review: program writes the batch outcome; sub-agent writes diagnosis + next hypotheses.
No 2019-2024 statistics are revealed through probe; full results are revealed only in review.
"""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd
from .dsl import Compiler, dsl_reference


def load_mechanisms(path: str | None = None) -> dict:
    path = path or os.path.join(os.path.dirname(__file__), "mechanisms.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def coverage_map(store, mechanisms: dict) -> list[dict]:
    table = store.table()
    rows = []
    for m in mechanisms["mechanisms"]:
        sub = table[table["mechanism"] == m["id"]] if len(table) else table
        tried = int(len(sub))
        evaluated = sub.dropna(subset=["mean_ric"]) if tried and "mean_ric" in sub else sub.iloc[0:0]
        best = None
        if len(evaluated):
            b = evaluated.sort_values("mean_ric", ascending=False).iloc[0]
            best = {"id": b["id"], "mean_ric": round(float(b["mean_ric"]), 4), "worst_ric": round(float(b["worst_ric"]), 4) if pd.notna(b["worst_ric"]) else None,
                    "expression": b["expression"]}
        if tried == 0:
            status = "unexplored"
        elif best is None:
            status = "tried_no_result"
        elif best["mean_ric"] >= 0.05:
            status = "pass_mean"
        elif best["mean_ric"] >= 0.03:
            status = "moderate"
        else:
            status = "weak"
        rows.append({"id": m["id"], "family": m["family"], "name": m["name"], "tried": tried, "status": status, "best": best,
                     "mechanism": m["mechanism"], "fields": m.get("fields", [])})
    return rows


def _fmt(v, nd=3):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "NA"
    return f"{v:+.{nd}f}" if isinstance(v, (int, float)) else str(v)


def pool_summary(store, top=25) -> str:
    t = store.table()
    if not len(t) or "mean_ric" not in t:
        return "(no evaluated candidates yet)"
    t = t.dropna(subset=["mean_ric"])
    if "in_pool" in t:
        t = t[t["in_pool"] == True]
    t = t.sort_values("mean_ric", ascending=False).head(top)
    lines = ["| id | name | mechanism | source | mean RankIC 19-24 | worst | ICIR | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | size exp | turn exp | vol exp | expression |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in t.iterrows():
        lines.append("| " + " | ".join([str(r["id"]), str(r["name"]), str(r["mechanism"]), str(r["source"]), _fmt(r["mean_ric"]), _fmt(r["worst_ric"]), _fmt(r["icir"], 2)]
                                        + [_fmt(r.get(f"ric{y}")) for y in range(2019, 2025)]
                                        + [_fmt(r.get("exp_size"), 2), _fmt(r.get("exp_turnover20"), 2), _fmt(r.get("exp_vol20"), 2), str(r["expression"])[:110]]) + " |")
    return "\n".join(lines)


def failure_summary(store) -> str:
    t = store.table()
    if not len(t) or "failure" not in t:
        return "(none)"
    counts = t["failure"].fillna("pending").value_counts()
    lines = [f"- {k}: {v}" for k, v in counts.items()]
    weak = t[t["failure"].fillna("").str.startswith(("no_signal", "weak", "style_proxy"))]
    if len(weak):
        lines.append("Recent weak/failed expressions (do not resubmit near-duplicates):")
        for _, r in weak.tail(25).iterrows():
            lines.append(f"  - [{r['mechanism']}] {r['expression'][:100]} -> mean {_fmt(r['mean_ric'])} worst {_fmt(r['worst_ric'])} ({r['failure']})")
    return "\n".join(lines)


PROPOSE_INSTRUCTIONS = """# A层因子研究：提案请求（批次 {batch}）

你是本框架的研究模型。目标：找到在**全A可交易池**上、2019—2024 六年**逐年日度截面 RankIC 年均值**尽可能高且稳定的因子。验收：单因子六年均值 > 0.05（最差年 > 0.05 为强通过）；组合 > 0.10。标签为次日开盘起五个交易日收益 open[t+6]/open[t+1]−1。方向由 2016—2018 决定后冻结。

## 你能做什么
1. 阅读本目录 context.json（机制覆盖图、当前因子池、失败清单、锚定基线）。
2. 用探针命令在**训练期(2016—2018)**上快速检验想法，最多 {max_probes} 次；探针只返回训练期 RankIC 均值、t 值、覆盖率、与池内前 10 因子的相关性，不返回 2019 以后的任何数字：
   ```
   {probe_cmd} --expr "<expression>"
   ```
   可以一次传多个 --expr。
3. 写出 response.json（格式见 response_schema.json），提交 {n_min}—{n_max} 个正式候选。

## 提案要求
- 每个候选给出：expression（DSL）、name、mechanism_id（引用目录 id，或 new:<id> 并写清机制）、hypothesis（经济机制，一两句）、expected_sign 说明、why_different（与池内已有因子的区别，引用池内 id）、falsifier（什么结果说明假设错误）。
- 优先填补覆盖图上 unexplored / weak 的机制格子，或把 moderate 机制改造成更稳的形式（条件化、市值中性化、换手/波动缩放、残差化）。不要提交只改窗口的近重复。
- 至少 1/3 的候选使用换手率、市值或 vwap 字段（这是旧框架无法触及的信息）。
- 允许对已有池成员做条件交互或中性化，但要说明预期增量来自哪里。
- 表达式必须只用 DSL 列出的字段和函数；时间序列函数只能回看。

## DSL
{dsl}

## 机制覆盖图（摘要）
{coverage}

## 当前因子池（按六年均值 RankIC 排序，已曝光历史，仅供避免重复与寻找互补）
{pool}

## 失败模式统计
{failures}

## 锚定基线
{anchor}

## 上一批复盘（研究模型自己的判断，含下一批假设草案；可采纳、修改或反驳）
{last_review}

完成后把 response.json 写到本目录。只写文件，不要修改框架代码或协议。
"""

REVIEW_INSTRUCTIONS = """# A层因子研究：批次复盘请求（批次 {batch}）

本批次已完成完整评价（2016—2024 已曝光历史）。请基于 context.json 与下表回答，并写 response.json。

## 需要回答的研究问题
1. 本批次哪些候选有真实增量？判断依据：六年均值与最差年、ICIR、分组单调性、多空两端是否对称、与池内最大相关、市值/换手/波动暴露是否解释了它。
2. 失败候选的失败模式归类：无信号 / 被风格解释 / 只在个别年份 / 尾部驱动 / 与池内冗余 / 实现与假设不符。每类各给一个例子。
3. 机制覆盖图上还有哪些格子没有探索？哪些 moderate 机制值得做条件化、中性化或不同数据来源的改造？
4. 组合层：当前 LightGBM/Ridge 组合的逐年 RankIC 与最差年；瓶颈是成员数量、冗余还是某些年份共同失效？
5. 给出下一批 5—8 个可证伪假设（每个附表达式草案、预期方向、证伪条件）。

## 上一轮假设执行台账
{ledger}

## 本批次结果
{batch_table}

## 组合结果
{combo_table}

## 机制覆盖图
{coverage}

## 因子池前 25
{pool}

response.json 格式见 response_schema.json。只写文件。
"""

PROPOSE_SCHEMA = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {"type": "array", "items": {"type": "object", "required": ["expression", "name", "mechanism_id", "hypothesis"],
                       "properties": {"expression": {"type": "string"}, "name": {"type": "string"}, "mechanism_id": {"type": "string"},
                                      "hypothesis": {"type": "string"}, "expected_sign": {"type": "string"}, "why_different": {"type": "string"},
                                      "falsifier": {"type": "string"}, "parents": {"type": "array", "items": {"type": "string"}}}}},
        "new_mechanisms": {"type": "array", "items": {"type": "object", "required": ["id", "name", "family", "mechanism"]}},
        "notes": {"type": "string"}}}

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["increments", "failure_modes", "unexplored", "combination_diagnosis", "next_hypotheses"],
    "properties": {"increments": {"type": "array", "items": {"type": "object", "required": ["id", "verdict", "reason"]}},
                   "failure_modes": {"type": "object"}, "unexplored": {"type": "array"}, "combination_diagnosis": {"type": "string"},
                   "next_hypotheses": {"type": "array", "items": {"type": "object", "required": ["expression", "mechanism_id", "hypothesis", "falsifier"]}},
                   "notes": {"type": "string"}}}


def write_propose_request(store, batch: int, anchor: dict | None, n_min=8, n_max=14, max_probes=15, probe_cmd="python -m quanta_agents.factor_lab_a.cli probe") -> str:
    mech = load_mechanisms()
    cov = coverage_map(store, mech)
    d = os.path.join(store.root, "requests", f"batch{batch:03d}_propose")
    os.makedirs(d, exist_ok=True)
    cov_lines = ["| id | family | status | tried | best mean RankIC | best expression |", "|---|---|---|---|---|---|"]
    for r in cov:
        b = r["best"] or {}
        cov_lines.append(f"| {r['id']} | {r['family']} | {r['status']} | {r['tried']} | {_fmt(b.get('mean_ric'))} | {str(b.get('expression', ''))[:90]} |")
    anchor_txt = json.dumps(anchor, ensure_ascii=False) if anchor else "(not computed yet)"
    last_review = "(none)"
    prev = store.decision_path(f"batch{batch - 1:03d}_review")
    if os.path.exists(prev):
        with open(prev, encoding="utf-8") as fh:
            rv = json.load(fh)
        parts = [f"组合诊断：{rv.get('combination_diagnosis', '')}"]
        if rv.get("failure_modes"):
            parts.append("失败模式：" + json.dumps(rv["failure_modes"], ensure_ascii=False)[:1500])
        if rv.get("unexplored"):
            parts.append("未探索：" + json.dumps(rv["unexplored"], ensure_ascii=False)[:1500])
        for h in rv.get("next_hypotheses", []):
            parts.append(f"- [{h.get('mechanism_id')}] {h.get('expression')} | {str(h.get('hypothesis', ''))[:160]} | 证伪：{str(h.get('falsifier', ''))[:120]}")
        if rv.get("notes"):
            parts.append("备注：" + str(rv["notes"])[:800])
        last_review = chr(10).join(parts)
    prompt = PROPOSE_INSTRUCTIONS.format(batch=batch, max_probes=max_probes, probe_cmd=probe_cmd + f" --root \"{store.root}\"", n_min=n_min, n_max=n_max,
                                         dsl=dsl_reference(), coverage="\n".join(cov_lines), pool=pool_summary(store),
                                         failures=failure_summary(store), anchor=anchor_txt, last_review=last_review)
    with open(os.path.join(d, "prompt.md"), "w", encoding="utf-8") as fh:
        fh.write(prompt)
    with open(os.path.join(d, "context.json"), "w", encoding="utf-8") as fh:
        json.dump({"batch": batch, "coverage": cov, "mechanisms": mech["mechanisms"], "anchor": anchor,
                   "protocol": store.protocol()}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(d, "response_schema.json"), "w", encoding="utf-8") as fh:
        json.dump(PROPOSE_SCHEMA, fh, ensure_ascii=False, indent=1)
    return d


def ingest_proposal(store, compiler: Compiler, request_dir: str, batch: int) -> dict:
    with open(os.path.join(request_dir, "response.json"), encoding="utf-8") as fh:
        resp = json.load(fh)
    added, rejected = [], []
    for c in resp.get("candidates", []):
        try:
            canon = compiler.canonical(c["expression"])
        except Exception as exc:
            rejected.append({"expression": c.get("expression"), "reason": f"invalid: {exc}"}); continue
        rec = {"id": compiler.identity(canon), "expression": c["expression"], "canonical": canon, "name": c.get("name"),
               "source": "llm", "batch": batch, "mechanism_id": c.get("mechanism_id"), "hypothesis": c.get("hypothesis"),
               "expected_sign": c.get("expected_sign"), "why_different": c.get("why_different"), "falsifier": c.get("falsifier"),
               "parents": c.get("parents", []), "status": "pending"}
        if store.add_candidate(rec):
            added.append(rec["id"])
        else:
            rejected.append({"expression": c["expression"], "reason": "duplicate canonical"})
    if resp.get("new_mechanisms"):
        path = os.path.join(store.root, "new_mechanisms.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            for m in resp["new_mechanisms"]:
                fh.write(json.dumps(dict(m, batch=batch), ensure_ascii=False) + "\n")
    summary = {"added": added, "rejected": rejected, "notes": resp.get("notes")}
    with open(os.path.join(request_dir, "ingest.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    return summary


def write_review_request(store, batch: int, combos: list[dict]) -> str:
    mech = load_mechanisms()
    cov = coverage_map(store, mech)
    d = os.path.join(store.root, "requests", f"batch{batch:03d}_review")
    os.makedirs(d, exist_ok=True)
    t = store.table()
    bt = t[t["batch"] == batch] if len(t) else t
    lines = ["| id | name | mechanism | mean | worst | ICIR | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | LS | top turn | max pool corr | failure | size | turn | vol | expression |", "|" + "---|" * 20]
    for _, r in bt.sort_values("mean_ric", ascending=False).iterrows():
        lines.append("| " + " | ".join([str(r["id"]), str(r["name"]), str(r["mechanism"]), _fmt(r.get("mean_ric")), _fmt(r.get("worst_ric")), _fmt(r.get("icir"), 2)]
                                        + [_fmt(r.get(f"ric{y}")) for y in range(2019, 2025)]
                                        + [_fmt(r.get("ls"), 4), _fmt(r.get("top_turnover"), 2), _fmt(r.get("max_pool_corr"), 2), str(r.get("failure")),
                                           _fmt(r.get("exp_size"), 2), _fmt(r.get("exp_turnover20"), 2), _fmt(r.get("exp_vol20"), 2), str(r["expression"])[:100]]) + " |")
    cl = ["| model | target | update | n | mean RankIC | worst | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |", "|" + "---|" * 12]
    for c in combos:
        if c.get("status") != "ok":
            continue
        ar = c["annual_rank_ic"]
        cl.append("| " + " | ".join([c["model"], c["target"], c["update"], str(c["n_features"]), _fmt(c["target_mean_rank_ic"]), _fmt(c["target_worst_rank_ic"])]
                                     + [_fmt(ar[str(y)]["mean"]) for y in range(2019, 2025)]) + " |")
    cov_lines = ["| id | family | status | tried | best mean | best expression |", "|---|---|---|---|---|---|"]
    for r in cov:
        b = r["best"] or {}
        cov_lines.append(f"| {r['id']} | {r['family']} | {r['status']} | {r['tried']} | {_fmt(b.get('mean_ric'))} | {str(b.get('expression', ''))[:80]} |")
    ledger = "(none)"
    prev = store.decision_path(f"batch{batch - 1:03d}_review")
    if os.path.exists(prev):
        with open(prev, encoding="utf-8") as fh:
            rv = json.load(fh)
        comp = Compiler()
        known = store.by_canonical()
        rows = []
        for h in rv.get("next_hypotheses", []):
            try:
                canon = comp.canonical(h.get("expression", ""))
                hit = known.get(canon)
                status = f"executed as {hit['id']} ({hit.get('name')})" if hit else "NOT executed"
            except Exception as exc:
                status = f"invalid expression: {str(exc)[:60]}"
            rows.append(f"- [{h.get('mechanism_id')}] {str(h.get('expression', ''))[:100]} -> {status}")
        ledger = chr(10).join(rows) if rows else "(no hypotheses recorded)"
    prompt = REVIEW_INSTRUCTIONS.format(batch=batch, batch_table="\n".join(lines), combo_table="\n".join(cl), coverage="\n".join(cov_lines), pool=pool_summary(store), ledger=ledger)
    with open(os.path.join(d, "prompt.md"), "w", encoding="utf-8") as fh:
        fh.write(prompt)
    with open(os.path.join(d, "context.json"), "w", encoding="utf-8") as fh:
        json.dump({"batch": batch, "coverage": cov, "combinations": combos, "batch_results": bt.to_dict(orient="records")}, fh, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(d, "response_schema.json"), "w", encoding="utf-8") as fh:
        json.dump(REVIEW_SCHEMA, fh, ensure_ascii=False, indent=1)
    return d


def ingest_review(store, request_dir: str, batch: int) -> dict:
    with open(os.path.join(request_dir, "response.json"), encoding="utf-8") as fh:
        resp = json.load(fh)
    with open(store.decision_path(f"batch{batch:03d}_review"), "w", encoding="utf-8") as fh:
        json.dump(resp, fh, ensure_ascii=False, indent=1)
    return {"next_hypotheses": len(resp.get("next_hypotheses", [])), "increments": len(resp.get("increments", []))}

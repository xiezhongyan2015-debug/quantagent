"""Compact factual reporting from preserved single-factor evidence."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from quanta_agents.research_kernel.store import write_json
from .study import read, sha


def summarize(study):
    cfg = study.config
    development = study.development_summary() if study.state["phase"] == "development" else read(study.root / "development_summary.json")
    confirmations = [read(p) for p in sorted((study.root / "confirmation").glob("*.json"))]
    selected = read(study.root / "frozen_candidates.json")["selected"] if (study.root / "frozen_candidates.json").exists() else []
    statuses = Counter(r["status"] for r in development["records"])
    resources = [read(p).get("resources", {}) for p in (study.root / "development").glob("*.json")]
    result = {"version": "v9a_report_1", "study_id": cfg["study_id"], "state": study.status(),
        "protocol": cfg, "attempts": study.expansion["summary"], "development_status_counts": dict(statuses),
        "selected": selected, "confirmation": confirmations,
        "historical_target_met": any(r["historical_target_met"] for r in confirmations),
        "historical_target_successes": sum(r["historical_target_met"] for r in confirmations),
        "independent_stability_proven": False, "profitability_proven": False,
        "compute": {"formula_wall_seconds": sum(r.get("wall_seconds", 0) for r in resources),
            "formula_cpu_seconds": sum(r.get("cpu_seconds", 0) for r in resources),
            "maximum_observed_private_bytes": max((r.get("private_bytes", 0) for r in resources), default=0),
            "all_artifact_bytes": sum(p.stat().st_size for p in study.root.parent.rglob("*") if p.is_file()),
            "workers": 1, "account_executions": 0, "extra_gateway_calls": 0,
            "money_cost": None, "money_cost_status": "unknown_no_billing_receipt"}}
    write_json(study.root / "result.json", result)
    def number(value):
        return "未知" if value is None else f"{value:+.5f}"
    lines = ["# V9A 因子研究结果", "", f"研究 ID：`{cfg['study_id']}`。", "",
        "单因子年 IC 为固定训练方向后的原因子值与下一开盘起五日未排名收益的日度截面 Pearson 均值。",
        "每年末六个交易日按当年完整退出规则排除；RankIC、覆盖和分组收益另报。", "",
        f"当前状态：{study.state['phase']}。历史目标达到：{result['historical_target_met']}。独立稳定性和盈利均未证明。", "",
        f"总尝试：{study.expansion['summary']['attempts']}；不同公式：{study.expansion['summary']['unique_formulas']}；"
        f"公式运行状态：{dict(statuses)}。", "",
        "| 因子 | 年份 | Pearson IC | RankIC | 有效日 | 覆盖 |", "|---|---:|---:|---:|---:|---:|"]
    for row in confirmations:
        identity = row["factor_id"]
        source = read(study.root / "development" / (identity + ".json"))
        for annual in [*source["train"]["annual"], *source["development"]["annual"], *row["report"]["annual"]]:
            lines.append(f"| {row['candidate']['spec']['name']} `{identity[:8]}` | {annual['year']} | "
                f"{number(annual['mean_pearson_ic'])} | {number(annual['mean_rank_ic'])} | {annual['valid_days']} | "
                f"{annual['evaluation_coverage']:.1%} |")
    lines += ["", "固定基线增量为独立预测器指标，不能替代上表单因子结果。", "",
              "| 因子 | 阶段 | 配对 ΔPearson IC | 配对 ΔRankIC | 状态 |", "|---|---|---:|---:|---|"]
    for selected_row in selected:
        identity = selected_row["factor_id"]
        for phase in ("development", "confirmation"):
            path = study.root / (phase + "_incremental") / (identity + ".json")
            if path.exists():
                inc = read(path)["result"]
                lines.append(f"| `{identity[:8]}` | {phase} | {number(inc.get('paired_delta_pearson_ic'))} | "
                             f"{number(inc.get('paired_delta_rank_ic'))} | {inc.get('status')} |")
    lines += ["", "2015—2024 全为已曝光历史；2025 数值未读取。日线来源修订和历史到达时间尚未认证。",
              "单轮三组比较不证明一般框架优势。未运行账户，未测成本、容量或可交易盈利。", "",
              f"完整逐日/逐年、失败、对照、定义与来源：`{study.root}`。",
              f"累计数值墙钟（含面板加载、比较、导出）：{study.state['numeric_wall_seconds']:.1f} 秒。",
              f"最大观测进程私有内存：{result['compute']['maximum_observed_private_bytes'] / 1024**3:.3f} GiB。",
              "模型 token 以父目录 receipts/team_receipts.json 的本地回执为准；货币费用无账单，保持未知。"]
    (study.root / "REPORT.zh-CN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {str(p.relative_to(study.root)): {"sha256": sha(p), "bytes": p.stat().st_size}
        for p in study.root.rglob("*") if p.is_file() and p.name not in ("artifact_manifest.json", "study.lock")}
    write_json(study.root / "artifact_manifest.json", manifest)
    return result

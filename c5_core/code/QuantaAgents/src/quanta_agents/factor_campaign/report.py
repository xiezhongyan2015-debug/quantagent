"""Saved-evidence campaign report, without model calls or recomputation."""
from pathlib import Path
import json


def render_campaign(campaign):
    from .campaign import read
    from .selection import annual_floor
    state = campaign.status()
    root = campaign.version_root
    rows = read(root / "catalog.json")
    factors = read(root / "summaries.json")
    combos = read(root / "combination_summaries.json")
    names = {r["factor_id"]: r["spec"]["name"] for r in rows}
    lines = [f"# {state['version']} 因子与组合研究状态", "",
        f"当前阶段：{state['phase']}；批次：{state['batch']}。六年历史目标达标：{'是' if state['historical_target_met'] else '否'}。",
        "独立样本外稳定性及交易盈利均未证明。2025数值不加载。", "",
        f"主要因子已评 {state['primary_evaluated']} / 最低300；其中新定义 {state['new_evaluated']}、旧库参考 {state['reference_evaluated']}、继承重评 {state['inherited_evaluated']}。",
        f"完整且非数值重复的组合规格 {state['combinations_evaluated']} / 最低200；控制项 {state['controls_evaluated']}；数值重复 {state['numeric_duplicates']}；实现失败记录 {state['failures']}。",
        "定义/数值重复、失败、控制、年度预测和权重更新次数不充当新增研究规模。", "",
        "验收为同一实体2019—2024六年逐年日度截面Pearson IC年均严格过线：固定无监督因子 >0.05，组合 >0.10。方向仅由2016—2018确定。", ""]
    for label, reports in (("因子", factors), ("组合", combos)):
        lines += [f"## 当前{label}证据", "", "| 定义或规格 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 最差年度 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        leaders = sorted(reports, key=lambda k: annual_floor(reports[k]) if annual_floor(reports[k]) is not None else -2, reverse=True)[:8]
        for key in leaders:
            annual = {r["year"]: r.get("mean_pearson_ic") for r in reports[key].get("annual", [])}
            values = [annual.get(y) for y in range(2019, 2025)] + [annual_floor(reports[key])]
            display = ["未知" if v is None else f"{v:.5f}" for v in values]
            lines.append("| " + names.get(key, key) + " | " + " | ".join(display) + " |")
        if not leaders:
            lines.append("| 尚无已计算证据 | — | — | — | — | — | — | — |")
        lines.append("")
    cost = state["cost"]
    lines += ["## 成本与恢复", "",
        f"累计研究网关模型调用 {cost['model_calls']} 次；已知输入 {cost['input_tokens']}、输出 {cost['output_tokens']} token；未知用量调用 {cost['unknown_usage_calls']} 次。",
        f"登记CPU {cost['cpu_seconds']:.2f}秒、墙钟 {cost['wall_seconds']:.2f}秒。货币费用未知，未推测账单。",
        "模型缓存输入属于输入、推理输出属于输出，不重复相加。主任务及开发/审计子代理用量在团队回执中另计，不能把研究网关数字称作全部开发成本。", "",
        f"当前阻塞：{json.dumps(state.get('blocker'), ensure_ascii=False)}", "",
        "每版先完成最低规模，再按50因子和40组合扩展；连续两扩展批次无足够改善且无新互补项后复盘。资源上限触发复盘或可恢复等待，均不是金融目标完成。", "",
        f"完整状态：{campaign.root / 'state.json'}", f"原始模型回执：{campaign.root / 'model_calls'}",
        f"逐因子、逐日和公式包：{root / 'factors'}", f"组合模型系数、更新窗口与逐日预测：{root / 'combinations'}",
        f"独立验收源：{campaign.root / 'acceptance'}", f"历次批复盘：{root / 'decisions'}", ""]
    reviews = sorted((root / "decisions").glob("*_review.json"))
    if reviews:
        decision = read(reviews[-1])
        lines += ["## 最新证据复盘", ""]
        for key, label in (("failure_analysis", "为什么尚未达标"), ("framework_diagnosis", "问题来源"),
                           ("proposed_improvement", "修改及反证"), ("cross_version_comparison", "相较上一版")):
            value = decision.get(key, {})
            lines += [f"**{label}：** {value.get('answer', '未提供')}",
                      "证据：" + ", ".join(value.get("evidence_ids", [])), ""]
    text = "\n".join(lines)
    (root / "REPORT.zh-CN.md").write_text(text, encoding="utf-8")
    (campaign.root / "LATEST_REPORT.zh-CN.md").write_text(text, encoding="utf-8")
    return str(root / "REPORT.zh-CN.md")

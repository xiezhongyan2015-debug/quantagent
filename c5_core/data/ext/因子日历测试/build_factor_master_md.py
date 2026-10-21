from __future__ import annotations

import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG_DIR = ROOT / "因子日历Markdown" / "因子清单"
OUTPUT = CATALOG_DIR / "2022-2024_分钟及Level2因子总表.md"

CLASS_NAMES = {
    "分钟OHLCV可精确复现",
    "分钟数据只能近似",
    "Level2待数据",
}


def read_rows(year: int) -> list[dict[str, str]]:
    path = CATALOG_DIR / f"{year}_分钟及Level2因子.md"
    rows: list[dict[str, str]] = []
    date_pattern = re.compile(rf"^{year}-\d{{2}}-\d{{2}}")

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or f"{year}-" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        date_index = next(
            (index for index, value in enumerate(cells) if date_pattern.match(value)),
            None,
        )
        if date_index is None or date_index + 7 >= len(cells):
            continue

        factor_class = cells[date_index + 4]
        if factor_class not in CLASS_NAMES:
            continue

        rows.append(
            {
                "year": str(year),
                "date": cells[date_index],
                "name": cells[date_index + 1],
                "formula": cells[date_index + 2],
                "fields": cells[date_index + 3],
                "class": factor_class,
                "backtest": cells[date_index + 5],
                "reason": cells[date_index + 6],
                "source": cells[date_index + 7],
            }
        )
    return rows


def main() -> None:
    rows_by_year = {year: read_rows(year) for year in (2022, 2023, 2024)}
    all_rows = [row for year in (2022, 2023, 2024) for row in rows_by_year[year]]
    class_counts = Counter(row["class"] for row in all_rows)

    lines = [
        "# 2022—2024 年分钟及 Level2 因子总表",
        "",
        "## 数量",
        "",
        "| 年份 | 分钟可精确复现 | 分钟只能近似 | Level2 待数据 | 合计 |",
        "|---:|---:|---:|---:|---:|",
    ]

    for year in (2022, 2023, 2024):
        counts = Counter(row["class"] for row in rows_by_year[year])
        lines.append(
            f"| {year} | {counts['分钟OHLCV可精确复现']} | "
            f"{counts['分钟数据只能近似']} | {counts['Level2待数据']} | "
            f"{len(rows_by_year[year])} |"
        )

    lines.extend(
        [
            f"| **合计** | **{class_counts['分钟OHLCV可精确复现']}** | "
            f"**{class_counts['分钟数据只能近似']}** | "
            f"**{class_counts['Level2待数据']}** | **{len(all_rows)}** |",
            "",
            "分钟可精确复现与分钟只能近似的项目都可以开展分钟回测，其中近似项目需要接受时间粒度带来的误差。Level2 项目本次不回测。",
            "",
            "年度清单：[2022](2022_分钟及Level2因子.md)、[2023](2023_分钟及Level2因子.md)、[2024](2024_分钟及Level2因子.md)。",
            "",
            "转换正文：[2022](../2022/因子日历2022.md)、[2023](../2023/因子日历2023.md)、[2024](../2024/因子日历2024.md)。",
            "",
            "## 全部因子",
            "",
            "| 年份 | 日期 | 因子名称 | 原文计算思路或公式简述 | 所需字段 | 类别 | 分钟回测 | 不能回测原因 | 来源位置 |",
            "|---:|---|---|---|---|---|---|---|---|",
        ]
    )

    for row in all_rows:
        values = [
            row["year"],
            row["date"],
            row["name"],
            row["formula"],
            row["fields"],
            row["class"],
            row["backtest"],
            row["reason"],
            row["source"],
        ]
        lines.append("| " + " | ".join(values) + " |")

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote={OUTPUT}")
    print(f"rows={len(all_rows)}")
    print(f"classes={dict(class_counts)}")


if __name__ == "__main__":
    main()

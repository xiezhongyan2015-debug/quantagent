# -*- coding: utf-8 -*-
"""合并并检查九月至十二月因子目录。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PARTS = [
    BASE_DIR / "september_november_factor_catalog_part.json",
    BASE_DIR / "october_december_factor_catalog_part.json",
]
OLD_CATALOG = BASE_DIR / "july_august_factor_catalog.json"
OUTPUT = BASE_DIR / "september_december_factor_catalog.json"
REQUIRED_FIELDS = {
    "calendar_date",
    "name",
    "code",
    "category",
    "source_frequency",
    "signal_frequency",
    "window",
    "formula",
    "meaning",
    "required_data",
    "current_data",
    "strict_status",
    "expected_direction",
    "source",
    "original_image",
    "note",
}


def main() -> int:
    factors: list[dict] = []
    excluded: list[dict] = []
    duplicate_or_invalid: list[dict] = []
    for path in PARTS:
        data = json.loads(path.read_text("utf-8"))
        factors.extend(data.get("factors", []))
        excluded.extend(data.get("excluded", []))
        duplicate_or_invalid.extend(data.get("duplicate_or_invalid", []))

    factors.sort(key=lambda item: (item["calendar_date"], item["code"]))
    codes = [item["code"] for item in factors]
    if len(codes) != len(set(codes)):
        raise ValueError("九月至十二月目录存在重复代码。")
    old_codes = {
        item["code"]
        for item in json.loads(OLD_CATALOG.read_text("utf-8"))["factors"]
    }
    overlap = sorted(old_codes & set(codes))
    if overlap:
        raise ValueError(f"与七八月目录存在重复代码：{overlap}")

    for item in factors:
        missing = REQUIRED_FIELDS - item.keys()
        if missing:
            raise ValueError(f"{item.get('code')} 缺字段：{sorted(missing)}")
        if any(item[field] is None or str(item[field]).strip() == "" for field in REQUIRED_FIELDS):
            raise ValueError(f"{item['code']} 有空字段。")
        image_path = BASE_DIR / item["original_image"]
        if not image_path.exists():
            raise FileNotFoundError(image_path)

    month_counts: dict[str, int] = {}
    for item in factors:
        month = item["calendar_date"][5:7]
        month_counts[month] = month_counts.get(month, 0) + 1
    expected = {"09": 27, "10": 28, "11": 28, "12": 28}
    if month_counts != expected:
        raise ValueError(f"分月数量不正确：{month_counts}")

    result = {
        "created_at": datetime.now().astimezone().isoformat(),
        "included_count": len(factors),
        "month_counts": month_counts,
        "excluded": excluded,
        "duplicate_or_invalid": duplicate_or_invalid,
        "factors": factors,
    }
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        "utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "included": len(factors),
                "month_counts": month_counts,
                "excluded": len(excluded),
                "duplicate_or_invalid": len(duplicate_or_invalid),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

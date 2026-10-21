from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "因子日历Markdown"
PAGE_RANGES = {
    "2022": ((1, 190), (191, 378)),
    "2023": ((1, 190), (191, 379)),
    "2024": ((1, 190), (191, 379)),
}


def rewrite_image_paths(text: str, part: str) -> str:
    prefix = f"parts/{part}/mineru_result/images/"
    return re.sub(r"(?<=\()images/", prefix, text)


def merge_year(year: str) -> Path:
    sections: list[str] = [f"# 因子日历{year}\n"]
    for index, (start, end) in enumerate(PAGE_RANGES[year], start=1):
        part = f"part{index:02d}"
        source = ROOT / year / "parts" / part / "mineru_result" / "full.md"
        text = source.read_text(encoding="utf-8").strip()
        text = rewrite_image_paths(text, part)
        sections.append(
            f"\n<!-- 原 PDF 第 {start}-{end} 页；MinerU 分段：{part} -->\n\n{text}\n"
        )

    output = ROOT / year / f"因子日历{year}.md"
    output.write_text("\n".join(sections), encoding="utf-8", newline="\n")
    return output


def check_images(markdown: Path) -> tuple[int, list[str]]:
    text = markdown.read_text(encoding="utf-8")
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    missing = [ref for ref in refs if not (markdown.parent / ref).is_file()]
    return len(refs), missing


if __name__ == "__main__":
    for current_year in PAGE_RANGES:
        merged = merge_year(current_year)
        image_count, missing_images = check_images(merged)
        print(
            current_year,
            merged.stat().st_size,
            image_count,
            len(missing_images),
            merged,
        )

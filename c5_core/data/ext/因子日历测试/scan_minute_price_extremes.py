#!/usr/bin/env python3
"""区分零成交的零价格分钟和仍然可疑的价格记录。"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def load_candidates(manifest: Path) -> list[str]:
    latest: dict[str, dict[str, object]] = {}
    with manifest.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            latest[str(item["code"])] = item
    return sorted(
        code
        for code, item in latest.items()
        if item.get("status") == "ok" and int(item.get("extreme_price_rows") or 0) > 0
    )


def sample_rows(table, mask: np.ndarray, limit: int = 5) -> list[dict[str, object]]:
    indices = np.flatnonzero(mask)[:limit]
    result: list[dict[str, object]] = []
    for index in indices:
        result.append(
            {
                "datetime": table["datetime"][int(index)].as_py().isoformat(),
                "open": table["open"][int(index)].as_py(),
                "high": table["high"][int(index)].as_py(),
                "low": table["low"][int(index)].as_py(),
                "close": table["close"][int(index)].as_py(),
                "volume": table["volume"][int(index)].as_py(),
                "amount": table["amount"][int(index)].as_py(),
            }
        )
    return result


def scan_one(path: Path) -> dict[str, object]:
    table = pq.read_table(
        path,
        columns=["datetime", "open", "high", "low", "close", "volume", "amount"],
    )
    prices = np.column_stack(
        [table[name].to_numpy(zero_copy_only=False) for name in ("open", "high", "low", "close")]
    ).astype(np.float64, copy=False)
    volume = table["volume"].to_numpy(zero_copy_only=False).astype(np.float64, copy=False)
    amount = table["amount"].to_numpy(zero_copy_only=False).astype(np.float64, copy=False)
    nonfinite = ~np.isfinite(prices).all(axis=1)
    nonpositive = (prices <= 0).any(axis=1)
    huge = (prices > 10000).any(axis=1)
    no_trade = (volume == 0) & (amount == 0)
    zero_no_trade = nonpositive & no_trade
    zero_with_trade = nonpositive & ~no_trade
    return {
        "code": path.stem,
        "nonfinite": int(nonfinite.sum()),
        "nonpositive": int(nonpositive.sum()),
        "zero_no_trade": int(zero_no_trade.sum()),
        "zero_with_trade": int(zero_with_trade.sum()),
        "huge": int(huge.sum()),
        "maximum_price": float(np.nanmax(prices)) if prices.size else None,
        "zero_no_trade_examples": sample_rows(table, zero_no_trade),
        "zero_with_trade_examples": sample_rows(table, zero_with_trade),
        "huge_examples": sample_rows(table, huge),
    }


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    metadata_dir = output_dir / "_metadata"
    candidates = load_candidates(metadata_dir / "conversion_manifest.jsonl")
    paths = [output_dir / "data" / f"{code}.parquet" for code in candidates]
    results: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [(path, executor.submit(scan_one, path)) for path in paths]
        for index, (path, future) in enumerate(futures, start=1):
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append({"code": path.stem, "error": str(exc)})
            if index % 500 == 0:
                print(f"已扫描 {index}/{len(paths)}", flush=True)
    fields = ["nonfinite", "nonpositive", "zero_no_trade", "zero_with_trade", "huge"]
    summary = {
        "candidate_codes": len(paths),
        "scan_errors": errors,
        "totals": {field: sum(int(item[field]) for item in results) for field in fields},
        "codes": {
            field: sum(int(item[field]) > 0 for item in results)
            for field in fields
        },
        "largest_price": max(
            ({"code": item["code"], "value": item["maximum_price"]} for item in results),
            key=lambda item: float(item["value"] or 0),
            default=None,
        ),
        "zero_no_trade_items": [item for item in results if int(item["zero_no_trade"]) > 0],
        "zero_with_trade_items": [item for item in results if int(item["zero_with_trade"]) > 0],
        "huge_items": [item for item in results if int(item["huge"]) > 0],
    }
    report = metadata_dir / "price_extremes_summary.json"
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

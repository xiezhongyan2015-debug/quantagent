"""Fixed eight-stock historical event-source pilot; no market-price reads or models.

The collection is bounded by request count, bytes and a parent-enforced wall clock.
Offline re-extraction never accesses the network. Failed requests are not retried.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import multiprocessing as mp
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

STOCKS = ("600006", "603786", "600653", "600741", "002296", "002377", "002576", "002527")
MAX_REQUESTS, MAX_BYTES, MAX_SECONDS = 30, 8 * 1024 * 1024, 300
OFFICIAL_DOCS = "https://akshare.akfamily.xyz/data/stock/stock.html"
SINA = "https://vip.stock.finance.sina.com.cn"
REQUIRED = ("announcement_date", "record_date", "ex_date", "cash_payment_date", "bonus_listing_date",
            "bonus_per_10", "capitalization_per_10", "cash_before_tax_per_10")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, body):
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def fetch_worker(connection, url, method, form):
    """One request in an expendable child so slow streaming cannot escape the clock."""
    import requests
    result = {}
    raw = bytearray()
    try:
        with requests.request(method, url, data=form, timeout=(4, 6), stream=True, allow_redirects=False,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; bounded-public-event-audit)",
                                       "Accept-Encoding": "identity", "Referer": "https://www.cninfo.com.cn/" if "cninfo" in url else SINA + "/"}) as response:
            result.update(http_status=response.status_code, final_url=response.url,
                          content_type=response.headers.get("Content-Type"),
                          content_length=response.headers.get("Content-Length"))
            while len(raw) < MAX_BYTES:
                chunk = response.raw.read(min(32768, MAX_BYTES - len(raw)), decode_content=True)
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) == MAX_BYTES:
                raise ValueError("8 MiB byte ceiling reached; response is not accepted as complete")
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}; no redirect or alternative retry")
        result["status"] = "downloaded"
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    connection.send((result, bytes(raw)))
    connection.close()


class Collector:
    def __init__(self, output):
        self.output, self.started, self.records = output, time.monotonic(), []
        self.context = mp.get_context("spawn")

    def fetch(self, key, url, kind, *, method="GET", form=None):
        if len(self.records) >= MAX_REQUESTS or MAX_SECONDS - (time.monotonic() - self.started) < 1:
            return None
        item = {"source_id": key, "url": url, "method": method, "form": form,
                "kind": kind, "requested_at": now(), "status": "requested"}
        self.records.append(item)
        self.save()
        receiver, sender = self.context.Pipe(duplex=False)
        process = self.context.Process(target=fetch_worker, args=(sender, url, method, form))
        process.start()
        sender.close()
        remaining = max(0.01, MAX_SECONDS - (time.monotonic() - self.started))
        timeout = min(12.0, remaining)
        raw = b""
        try:
            if receiver.poll(timeout):
                metadata, raw = receiver.recv()
                item.update(metadata)
            else:
                item.update(status="failed", error="parent_enforced_request_or_total_time_limit")
        except (EOFError, OSError) as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.1)
            receiver.close()
        if raw:
            suffix = ".pdf" if raw.startswith(b"%PDF-") else ".html" if "html" in item.get("content_type", "").lower() else ".bin"
            name = key + suffix
            (self.output / name).write_bytes(raw)
            item.update(file=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        else:
            item.update(file=None, bytes=0, sha256=None)
        item.update(completed_at=now(), elapsed_total_seconds=round(time.monotonic() - self.started, 3))
        self.save()
        print(json.dumps({k: item.get(k) for k in ("source_id", "status", "bytes", "http_status", "error")}, ensure_ascii=False), flush=True)
        return raw if item["status"] == "downloaded" else None

    def save(self):
        write_json(self.output / "sources.json", {"requests_used": len(self.records), "requests_limit": MAX_REQUESTS,
            "wall_seconds": round(time.monotonic() - self.started, 3), "wall_limit_seconds": MAX_SECONDS,
            "bytes_limit_per_source": MAX_BYTES, "sources": self.records})


def decode(raw, sina=False):
    return raw.decode("gb18030" if sina else "utf-8", errors="replace")


def clean(value):
    value = re.sub(r"\s+", " ", value).strip()
    return None if value in {"", "--", "-"} else value


def parse_index(raw, code, url):
    soup = BeautifulSoup(decode(raw, True), "lxml")
    records, rejected, tables = [], [], []
    fields = {
        "sharebonus_1": ("announcement_date", "bonus_per_10", "capitalization_per_10", "cash_before_tax_per_10",
                         "progress", "ex_date", "record_date", "bonus_listing_date"),
        "sharebonus_2": ("announcement_date", "rights_per_10", "subscription_price_cny", "base_shares",
                         "ex_date", "record_date", "subscription_start", "subscription_end", "rights_listing_date",
                         "funds_raised_cny"),
    }
    for table_id, names in fields.items():
        table = soup.find("table", id=table_id)
        if table is None:
            tables.append({"table": table_id, "status": "missing"})
            continue
        data_rows = 0
        for position, tr in enumerate(table.select("tbody tr")):
            cells = tr.find_all("td", recursive=False)
            texts = [clean(td.get_text(" ", strip=True)) for td in cells]
            if len(texts) != len(names) + 1 or not texts[0] or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texts[0]):
                rejected.append({"table": table_id, "position": position, "cells": texts})
                continue
            data_rows += 1
            row = dict(zip(names, texts))
            link = cells[-1].find("a", href=True)
            href = urljoin(url, link["href"]) if link else None
            row.update(code=code, event_type="dividend" if table_id.endswith("1") else "rights_issue",
                       source_url=url, source_row=position, detail_url=href,
                       raw_cells=texts, source_kind="secondary_index", completeness_proven=False)
            # All index rows in the predeclared announcement interval are retained,
            # including proposals and explicit non-distribution records.
            if "2017-01-01" <= row["announcement_date"] <= "2021-12-31":
                records.append(row)
        tables.append({"table": table_id, "status": "present", "all_year_date_rows": data_rows})
    return {"code": code, "events": records, "tables": tables, "rejected_rows": rejected,
            "index_complete": False, "no_rows_meaning": "not_observed_not_proven_absent"}


def select_detail(index):
    candidates = [row for row in index["events"] if row.get("progress") == "实施" and row.get("detail_url")]
    return min(candidates, key=lambda row: (row["announcement_date"], row["event_type"], row["source_row"])) if candidates else None


def parse_detail(raw, selected):
    soup = BeautifulSoup(decode(raw, True), "lxml")
    # Restrict extraction to event-detail tables; never interpret quote/nav blocks.
    tables = []
    for table in soup.find_all("table", id="sharebonusdetail"):
        rows = [[clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"], recursive=False)]
                for tr in table.find_all("tr")]
        if any(any(value and any(key in value for key in ("股权登记", "送股", "派息", "转增", "配股")) for value in row) for row in rows):
            tables.append(rows)
    pairs = {}
    for rows in tables:
        for cells in rows:
            if len(cells) == 2 and cells[0]:
                pairs[cells[0]] = cells[1]
    normalized = {
        "index_announcement_date": selected["announcement_date"],
        "original_announcement_date_verified": None,
        "shareholder_resolution_announcement_date": pairs.get("股东大会决议公告日期"),
        "record_date": pairs.get("登记日"), "ex_date": pairs.get("除息日"),
        "ambiguous_distribution_start_date": pairs.get("红利/配股起始日（送、转股到账日）"),
        "cash_payment_date_verified": None, "shares_available_date_verified": None,
        "listing_date_raw": pairs.get("上市日"),
        "bonus_per_10_index": selected.get("bonus_per_10"),
        "capitalization_per_10_index": selected.get("capitalization_per_10"),
        "bonus_per_10_detail": pairs.get("送股比例（10送）"),
        "capitalization_per_10_detail": pairs.get("转增比例（10转增）"),
        "cash_before_tax_per_10_index": selected.get("cash_before_tax_per_10"),
        "cash_before_tax_quoted_detail": pairs.get("税前红利（报价币种）"),
        "cash_after_tax_quoted_detail": pairs.get("税后红利（报价币种）"),
        "cash_per_share_candidate": str(Decimal(selected["cash_before_tax_per_10"]) / 10)
                                    if selected.get("cash_before_tax_per_10") is not None else None,
        "cash_conversion_basis": "Sina history header explicitly states per 10 shares; detail quote currency label alone has no per-share basis",
        "rights_per_10_detail": pairs.get("配股比例（10配）"),
        "rights_subscription_price_detail": pairs.get("配股价"),
        "rights_subscription_period_detail": pairs.get("配股有效期"),
        "account_tax_liability_verified": None,
        "usable_as_complete_ledger_event": False,
    }
    return {"code": selected["code"], "selection_announcement_date": selected["announcement_date"],
            "source_url": selected["detail_url"], "event_tables": tables, "label_value_pairs": pairs,
            "normalized_candidates": normalized,
            "primary_announcement_verified": False}


def primary_probe(collector, code, selected):
    """Fixed first two stocks only; one search, one index query, at most one original."""
    raw = collector.fetch(f"{code}_cninfo_identity", "https://www.cninfo.com.cn/new/information/topSearch/query?" +
                          urlencode({"keyWord": code, "maxNum": 10}), "official_security_lookup")
    if not raw:
        return
    try:
        body = json.loads(raw)
        options = body if isinstance(body, list) else body.get("data", [])
        match = next(item for item in options if item.get("code") == code and item.get("orgId"))
        event_date = datetime.strptime(selected["announcement_date"], "%Y-%m-%d")
        window = (event_date - timedelta(days=1)).strftime("%Y-%m-%d") + "~" + (event_date + timedelta(days=1)).strftime("%Y-%m-%d")
        form = {"stock": code + "," + match["orgId"], "column": "sse", "plate": "sh", "tabName": "fulltext",
                "pageSize": "30", "pageNum": "1", "seDate": window, "searchkey": "", "category": ""}
        raw = collector.fetch(f"{code}_cninfo_announcements", "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                              "official_announcement_index", method="POST", form=form)
        if not raw:
            return
        announcements = json.loads(raw).get("announcements") or []
        candidates = [item for item in announcements if ("权益分派" in item.get("announcementTitle", "") or
                      ("分配" in item.get("announcementTitle", "") and "实施" in item.get("announcementTitle", ""))) and item.get("adjunctUrl")]
        if not candidates:
            write_json(collector.output / f"{code}_primary_selection.json", {"status": "not_found_in_fixed_window", "window": window,
                       "announcement_count": len(announcements), "absence_proven": False})
            return
        chosen = min(candidates, key=lambda item: (item.get("announcementTime", 0), str(item.get("announcementId", ""))))
        write_json(collector.output / f"{code}_primary_selection.json", chosen)
        url = urljoin("https://static.cninfo.com.cn/", chosen["adjunctUrl"])
        if not url.startswith("https://static.cninfo.com.cn/"):
            raise ValueError("unrecognized original-announcement host")
        collector.fetch(f"{code}_original_announcement", url, "official_original_announcement")
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        write_json(collector.output / f"{code}_primary_selection.json", {"status": "parse_or_lookup_failed", "error": str(exc)})


def summarize(output):
    ledger = json.loads((output / "sources.json").read_text(encoding="utf-8"))
    sources = {row["source_id"]: row for row in ledger["sources"]}
    summary, all_events, details = [], [], []
    for code in STOCKS:
        source = sources.get(code + "_history")
        if not source or source["status"] != "downloaded":
            summary.append({"code": code, "index_status": "failed_or_unattempted", "rows": None,
                            "failure": source.get("error") if source else "network_budget_exhausted", "complete": False})
            continue
        index = parse_index((output / source["file"]).read_bytes(), code, source["url"])
        write_json(output / f"{code}_index.json", index)
        all_events.extend(index["events"])
        chosen = select_detail(index)
        detail_source = sources.get(code + "_detail")
        detail = None
        if chosen and detail_source and detail_source["status"] == "downloaded":
            detail = parse_detail((output / detail_source["file"]).read_bytes(), chosen)
            write_json(output / f"{code}_detail.json", detail)
            details.append(detail)
        statuses = Counter(row.get("progress", "not_provided") for row in index["events"])
        summary.append({"code": code, "index_status": "parsed" if all(t["status"] == "present" for t in index["tables"]) else "incomplete_layout",
                        "rows": len(index["events"]), "progress_counts": dict(statuses),
                        "rights_rows": sum(row["event_type"] == "rights_issue" for row in index["events"]),
                        "years_observed": sorted({row["announcement_date"][:4] for row in index["events"]}),
                        "detail_selected_date": chosen["announcement_date"] if chosen else None,
                        "detail_status": detail_source["status"] if detail_source else "no_implemented_row_selected",
                        "detail_pairs": detail["label_value_pairs"] if detail else None, "complete": False})
    # Document extraction is deliberately offline and independent of market data.
    doc = sources.get("akshare_official_docs")
    if doc and doc["status"] == "downloaded":
        soup = BeautifulSoup(decode((output / doc["file"]).read_bytes()), "lxml")
        paragraph = next((p for p in soup.find_all("p") if "接口：stock_history_dividend_detail" in p.get_text()), None)
        section, anchor = [], None
        if paragraph:
            heading = paragraph.find_previous(["h1", "h2", "h3", "h4"])
            anchor = heading.get("id")
            for sibling in heading.next_siblings:
                if getattr(sibling, "name", None) in ["h1", "h2", "h3", "h4"]:
                    break
                if hasattr(sibling, "get_text"):
                    section.append(sibling.get_text(" ", strip=True))
        (output / "akshare_interface_excerpt.txt").write_text("\n".join(section) if section else "interface_not_found", encoding="utf-8")
        write_json(output / "akshare_interface_contract.json", {"url": OFFICIAL_DOCS + ("#" + anchor if anchor else ""),
                   "section_found": bool(section), "source_sha256": doc["sha256"], "retrieved_at": doc["completed_at"]})
    for source in ledger["sources"]:
        if source["kind"] == "official_original_announcement" and source.get("file"):
            raw = (output / source["file"]).read_bytes()
            if raw.startswith(b"%PDF-"):
                from pypdf import PdfReader
                pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages]
                write_json(output / (source["source_id"] + "_pages.json"), pages)
    write_json(output / "events.json", all_events)
    write_json(output / "selected_event_candidates.json", [row["normalized_candidates"] | {"code": row["code"]} for row in details])
    coverage = []
    for code in STOCKS:
        for year in range(2017, 2022):
            rows = [row for row in all_events if row["code"] == code and row["announcement_date"].startswith(str(year))]
            coverage.append({"code": code, "announcement_year": year, "observed_rows": len(rows),
                             "progress_counts": dict(Counter(row.get("progress", "not_provided") for row in rows)),
                             "source_coverage_complete": False, "no_event_proven": False})
    write_json(output / "coverage_by_stock_year.json", coverage)
    result = {"stocks": summary, "fixed_stock_denominator": 8, "announcement_interval": ["2017-01-01", "2021-12-31"],
              "requests_used": ledger["requests_used"], "network_wall_seconds": ledger["wall_seconds"],
              "events_extracted": len(all_events), "details_extracted": len(details),
              "progress_counts": dict(Counter(row.get("progress", "not_provided") for row in all_events)),
              "observed_stock_year_cells": sum(bool(row["observed_rows"]) for row in coverage),
              "planned_stock_year_cells": 40, "no_distribution_rows_retained": sum(row.get("progress") == "不分配" for row in all_events),
              "extraction_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "all_indices_complete": False, "event_coverage_complete": False,
              "absence_rule": "No row is not evidence of no event. An index may omit earlier proposal versions, corrections and cancelled actions.",
              "prices_or_returns_requested": False, "model_calls": 0, "execution_valid": False}
    write_json(output / "result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "experiment_traces/meta_corporate_action_pilot"
    if args.summarize_only:
        summarize(output)
        return
    output.mkdir(parents=True, exist_ok=True)
    if (output / "plan.json").exists():
        raise FileExistsError("pilot already frozen; use --summarize-only, never silently repeat collection")
    plan = {"created_at": now(), "stocks": list(STOCKS), "sample_rule": "Fixed L2 audit code-hash sample; no return or event-based substitution",
            "announcement_interval": ["2017-01-01", "2021-12-31"], "request_limit": MAX_REQUESTS,
            "bytes_per_source_limit": MAX_BYTES, "total_network_wall_seconds": MAX_SECONDS,
            "detail_selection": "Earliest announcement-date dividend row marked 实施 in 2017-2021; ties event_type then source row; no replacement on failure",
            "primary_probe_stocks": list(STOCKS[:2]), "primary_probe_rule": "One CNINFO security lookup, then one fixed +/-1-day announcement query, then earliest matching implementation original; no retry",
            "completeness_claim": False, "price_reads": False, "model_calls": 0,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(output / "plan.json", plan)
    collector = Collector(output)
    collector.fetch("akshare_official_docs", OFFICIAL_DOCS, "official_interface_documentation")
    selected = {}
    for code in STOCKS:
        url = SINA + f"/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml"
        raw = collector.fetch(code + "_history", url, "secondary_historical_event_index")
        if raw:
            index = parse_index(raw, code, url)
            chosen = select_detail(index)
            if chosen:
                selected[code] = chosen
    for code in STOCKS:
        if code in selected:
            url = selected[code]["detail_url"]
            if url.startswith(SINA + "/corp/view/vISSUE_ShareBonusDetail.php?") and f"stockid={code}" in url:
                collector.fetch(code + "_detail", url, "secondary_historical_event_detail")
    for code in STOCKS[:2]:
        if code in selected:
            primary_probe(collector, code, selected[code])
    collector.save()
    summarize(output)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()

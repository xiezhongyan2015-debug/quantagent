"""Bounded original-notice retrieval for two frozen corporate-action events.

Only explicitly discovered URLs are accepted; no automatic retry or stock changes.
Extraction/verification of saved PDFs is offline and never imports trading code.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

from audit_historical_corporate_actions import Collector, write_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "experiment_traces/meta_corporate_notices_verified"


def repair_fetch_worker(connection, url):
    """Exactly one request; capture migration evidence without executing content."""
    import requests
    result, raw = {}, bytearray()
    try:
        with requests.get(url, stream=True, allow_redirects=False, timeout=(4, 6),
                          headers={"User-Agent": "Mozilla/5.0 (compatible; bounded-public-event-audit)",
                                   "Accept-Encoding": "identity"}) as response:
            result.update(http_status=response.status_code, final_url=response.url,
                          response_headers=dict(response.headers), location=response.headers.get("Location"))
            for chunk in response.iter_content(32768):
                if len(raw) + len(chunk) >= 8 * 1024 * 1024:
                    raise ValueError("8 MiB ceiling; incomplete source rejected")
                raw.extend(chunk)
        result["status"] = "downloaded" if result["http_status"] == 200 else "redirect" if result["http_status"] in {301, 302, 303, 307, 308} else "failed"
        if result["status"] == "failed":
            result["error"] = "HTTP " + str(result["http_status"])
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    connection.send((result, bytes(raw)))
    connection.close()


def allowed_redirect(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme in {"http", "https"} and parsed.port in {None, 80, 443}
                and not parsed.username and not parsed.password
                and any(host == domain or host.endswith("." + domain)
                        for domain in ("sohu.com", "sse.com.cn", "cninfo.com.cn")))
    except ValueError:
        return False


def summarize():
    """Offline evidence inventory; secondary values never activate the adapter."""
    from bs4 import BeautifulSoup
    plan = json.loads((OUTPUT / "plan.json").read_text(encoding="utf8"))
    initial = json.loads((OUTPUT / "initial_sources_closed.json").read_text(encoding="utf8"))
    repair_dir = OUTPUT / "redirect_repair"
    repair = json.loads((repair_dir / "sources.json").read_text(encoding="utf8"))
    repair_plan = json.loads((repair_dir / "plan.json").read_text(encoding="utf8"))
    pages, originals = [], []
    for directory, ledger in [(OUTPUT, initial), (repair_dir, repair)]:
        for row in ledger["sources"]:
            if row.get("file"):
                raw = (directory / row["file"]).read_bytes()
                assert len(raw) == row["bytes"]
                assert hashlib.sha256(raw).hexdigest() == row["sha256"]
                if raw.startswith(b"%PDF-") and row["status"] == "downloaded":
                    originals.append({"source_id": row["source_id"], "file": str(directory / row["file"])})
    # Inspect only announcement lists and event chronology; never quote/price fields.
    for row in initial["sources"]:
        if row["status"] != "downloaded" or not row["source_id"].startswith("sohu_"):
            continue
        soup = BeautifulSoup((OUTPUT / row["file"]).read_bytes().decode("gb18030", errors="replace"), "lxml")
        dates = [td.get_text(" ", strip=True) for td in soup.find_all("td")]
        dates = [value for value in dates if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value)]
        targets = []
        for anchor in soup.find_all("a", href=True):
            if any(title in anchor.get_text() for title in ("2016年年度权益分派实施公告", "2019年年度权益分派实施公告")):
                targets.append({"title": anchor.get_text(" ", strip=True),
                    "links": [urljoin(row["url"], a["href"]) for a in anchor.parent.find_all("a", href=True)],
                    "source_row_text": anchor.parent.get_text(" ", strip=True)})
        pages.append({"source_id": row["source_id"], "source_sha256": row["sha256"],
                      "observed_date_min": min(dates) if dates else None, "observed_date_max": max(dates) if dates else None,
                      "fixed_target_links": targets, "pagination_links": [urljoin(row["url"], a["href"]) for a in soup.find_all("a", href=True) if re.fullmatch(r"bw_\d+\.shtml", a["href"])]})
    write_json(OUTPUT / "saved_page_evidence.json", pages)
    prior_file = ROOT / "experiment_traces/meta_corporate_action_pilot/selected_event_candidates.json"
    prior = json.loads(prior_file.read_text(encoding="utf8"))
    candidates = []
    for event in plan["fixed_events"]:
        matching = next(row for row in prior if row["code"] == event["code"] and row["index_announcement_date"] == event["index_date"])
        candidates.append({"symbol": "sh" + event["code"], "fixed_index_date": event["index_date"],
            "secondary_candidate": matching, "candidate_file_sha256": hashlib.sha256(prior_file.read_bytes()).hexdigest(),
            "cash_dividend_announcement": None, "facts_verified": False, "original_pdf_verified": False,
            "correction_chain_status": "not_established", "usable_for_ledger": False,
            "unverified_fields": ["original_announcement_date", "cash_payment_date", "account_type",
                                  "account_tax_rule", "stock_distribution_kind", "rights_issue", "correction_chain"]})
    write_json(OUTPUT / "verification_results.json", candidates)
    assert not originals, "New PDF requires manual fact verification; do not auto-mark it verified"
    validation = {
        "fixed_two_events": plan["fixed_events"] == [{"code": "600006", "index_date": "2017-07-10"}, {"code": "603786", "index_date": "2020-05-29"}],
        "plan_script_hash": hashlib.sha256((OUTPUT / "plan_script.py").read_bytes()).hexdigest() == plan["script_sha256"],
        "source_hashes_and_lengths": True,
        "initial_request_and_byte_limits": len(initial["sources"]) + 6 <= 30 and all(row["bytes"] < 8388608 for row in initial["sources"]),
        "initial_actual_network_completion_limit": max(row["elapsed_total_seconds"] for row in initial["sources"]) <= 300,
        "repair_request_byte_time_limits": len(repair["sources"]) <= 8 and repair["wall_seconds"] <= 120 and all(row["bytes"] < 8388608 for row in repair["sources"]),
        "no_duplicate_request_within_each_batch": all(len({row["url"] for row in x["sources"]}) == len(x["sources"]) for x in (initial, repair)),
        "repair_header_and_redirect_evidence": all("response_headers" in row and row["hop"] <= 3 and allowed_redirect(row["url"]) for row in repair["sources"]),
        "repair_script_hash": hashlib.sha256((repair_dir / "collection_script.py").read_bytes()).hexdigest() == repair_plan["script_sha256"],
        "failed_events_remain_in_denominator": len(candidates) == 2 and all(not row["facts_verified"] and row["cash_dividend_announcement"] is None for row in candidates),
        "homepage_success_not_original_pdf": any(row["status"] == "downloaded" for row in repair["sources"]) and len(originals) == 0,
    }
    assert all(validation.values()), validation
    write_json(OUTPUT / "validation.json", {"checks": validation, "passed": len(validation), "failed": 0, "network_calls": 0})
    result = {"fixed_events": 2, "original_verified": 0, "usable_adapter_records": 0,
        "initial_batch": {"web_queries": 6, "direct_http_requests": len(initial["sources"]),
                          "logical_requests": len(initial["sources"]) + 6, "successful_http": sum(row["status"] == "downloaded" for row in initial["sources"]),
                          "failed_http": sum(row["status"] == "failed" for row in initial["sources"]),
                          "last_actual_network_completion_seconds": max(row["elapsed_total_seconds"] for row in initial["sources"]),
                          "ledger_saved_elapsed_seconds": initial["wall_seconds"],
                          "saved_bytes": sum(row["bytes"] for row in initial["sources"])},
        "separately_authorized_redirect_repair": {"http_requests": len(repair["sources"]), "wall_seconds": repair["wall_seconds"],
                                                  "saved_bytes": sum(row["bytes"] for row in repair["sources"])},
        "model_calls": 0, "prices_or_returns_analyzed": False, "coverage_complete": False,
        "verification_status": "not_verified", "current_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(OUTPUT / "result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def redirect_repair():
    """Separately authorized child batch: <=8 requests, <=120 s, <=3 redirects."""
    output = OUTPUT / "redirect_repair"
    output.mkdir(exist_ok=True)
    if (output / "plan.json").exists():
        raise FileExistsError("redirect repair already attempted; no automatic retry")
    started, records = time.monotonic(), []
    urls = ["https://q.stock.sohu.com/newpdf/201727603382.pdf",
            "https://q.stock.sohu.com/cn,gg,600006,27603382.shtml"]
    write_json(output / "plan.json", {"started_at": datetime.now(timezone.utc).isoformat(),
        "fixed_event": {"code": "600006", "index_date": "2017-07-10"}, "urls": urls,
        "max_requests": 8, "max_seconds": 120, "max_bytes_per_source": 8388608, "max_redirects_per_chain": 3,
        "authorization": "parent explicitly approved separate redirect-evidence repair after original 300s batch closed",
        "allowed_domains": ["sohu.com", "sse.com.cn", "cninfo.com.cn"], "model_calls": 0,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    context = mp.get_context("spawn")
    for chain, initial in enumerate(urls):
        url = initial
        for hop in range(4):
            remaining = 120 - (time.monotonic() - started)
            if len(records) >= 8 or remaining < 1 or any(row["url"] == url for row in records):
                break
            if not allowed_redirect(url):
                break
            item = {"source_id": f"chain{chain}_hop{hop}", "chain": chain, "hop": hop,
                    "url": url, "requested_at": datetime.now(timezone.utc).isoformat(), "status": "requested"}
            records.append(item)
            write_json(output / "sources.json", {"sources": records})
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=repair_fetch_worker, args=(sender, url))
            process.start()
            sender.close()
            raw = b""
            try:
                if receiver.poll(min(12, remaining)):
                    metadata, raw = receiver.recv()
                    item.update(metadata)
                else:
                    item.update(status="failed", error="parent_enforced_request_or_total_time_limit")
            except (OSError, EOFError) as exc:
                item.update(status="failed", error=str(exc))
            finally:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.1)
                receiver.close()
            suffix = ".pdf" if raw.startswith(b"%PDF-") else ".html"
            filename = item["source_id"] + suffix if raw else None
            if raw:
                (output / filename).write_bytes(raw)
            item.update(file=filename, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest() if raw else None,
                        is_pdf=raw.startswith(b"%PDF-"), completed_at=datetime.now(timezone.utc).isoformat(),
                        elapsed_total_seconds=round(time.monotonic() - started, 3))
            if item["status"] == "redirect" and item.get("location"):
                target = urljoin(url, item["location"])
                item.update(redirect_target=target, redirect_allowed=allowed_redirect(target))
            write_json(output / "sources.json", {"requests_used": len(records), "sources": records,
                "wall_seconds": round(time.monotonic() - started, 3), "completed": False})
            print(json.dumps(item, ensure_ascii=False), flush=True)
            if item["status"] != "redirect" or not item.get("redirect_allowed") or hop >= 3:
                break
            url = item["redirect_target"]
    write_json(output / "sources.json", {"requests_used": len(records), "sources": records,
                "wall_seconds": round(time.monotonic() - started, 3), "completed": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--started-at")
    parser.add_argument("--fetch", nargs=3, action="append", metavar=("KEY", "URL", "KIND"))
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--redirect-repair", action="store_true")
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.redirect_repair:
        redirect_repair()
        return
    if args.summarize:
        summarize()
        return
    if args.initialize:
        if (OUTPUT / "plan.json").exists():
            raise FileExistsError("plan already frozen")
        write_json(OUTPUT / "plan.json", {"started_at": args.started_at,
            "fixed_events": [{"code": "600006", "index_date": "2017-07-10"}, {"code": "603786", "index_date": "2020-05-29"}],
            "max_logical_network_requests": 30, "web_discovery_queries_reserved": 6,
            "max_direct_http_requests": 24, "max_bytes_per_source": 8388608, "max_network_wall_seconds": 300,
            "request_count_scope": "6 initial search queries plus explicit HTTP requests; search-provider internal HTTP fanout is not observable",
            "failed_cninfo_identity_retry": False, "market_price_reads": False, "model_calls": 0,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    plan = json.loads((OUTPUT / "plan.json").read_text(encoding="utf-8"))
    if args.fetch:
        c = Collector(OUTPUT)
        prior = OUTPUT / "sources.json"
        if prior.exists():
            c.records = json.loads(prior.read_text(encoding="utf-8"))["sources"]
        elapsed = time.time() - datetime.fromisoformat(plan["started_at"].replace("Z", "+00:00")).timestamp()
        if elapsed >= plan["max_network_wall_seconds"]:
            raise TimeoutError("frozen original network window closed; no request made")
        c.started = time.monotonic() - elapsed
        for key, url, kind in args.fetch:
            if any(row["source_id"] == key or row["url"] == url for row in c.records):
                raise ValueError("this URL/source was already attempted; no retry")
            if len(c.records) >= plan["max_direct_http_requests"]:
                raise ValueError("network request budget exhausted")
            c.fetch(key, url, kind)
        c.save()
    if args.extract:
        from pypdf import PdfReader
        ledger = json.loads((OUTPUT / "sources.json").read_text(encoding="utf-8"))
        for source in ledger["sources"]:
            if source["status"] != "downloaded" or not source.get("file"):
                continue
            path = OUTPUT / source["file"]
            if path.read_bytes().startswith(b"%PDF-"):
                pages = [page.extract_text() or "" for page in PdfReader(path).pages]
                write_json(OUTPUT / (source["source_id"] + "_pages.json"), pages)
                print(json.dumps({"source_id": source["source_id"], "pages": pages}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()

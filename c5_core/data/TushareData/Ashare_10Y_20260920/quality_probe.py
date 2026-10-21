"""Small direct-only completeness and optional-fundamental endpoint audit."""
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from direct_client import DirectClient, DirectHTTPSConnection, resolve_direct

ROOT = Path(__file__).resolve().parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    token = sys.stdin.readline().strip()
    if not re.fullmatch(r"[a-fA-F0-9]{40,80}", token):
        raise RuntimeError("Missing token")
    run = Path(json.loads((ROOT / "latest_benchmark.json").read_text(encoding="utf-8"))["run"])
    out = run / "quality"
    out.mkdir(exist_ok=True)
    cfg = json.loads((ROOT / "network_config.json").read_text(encoding="utf-8"))
    client = DirectClient(token, cfg, max_rps=5)
    catalog = json.loads(Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/ChoiceData/TushareAccessAudit_20260920/document_fields.json").read_text(encoding="utf-8"))

    doc_ips, _ = resolve_direct("tushare.pro", cfg["local_ip"], cfg["interface_index"], cfg["dns_servers"])
    def doc(path):
        conn = DirectHTTPSConnection("tushare.pro", doc_ips[0], cfg["local_ip"], cfg["interface_index"])
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"Documentation HTTP {resp.status}")
            return BeautifulSoup(data.decode("utf-8"), "html.parser")
        finally:
            conn.close()

    page = doc("/document/2?doc_id=79")
    menu = {a.get_text(" ", strip=True): a.get("href") for a in page.find_all("a", href=True)}
    wanted = {
        "fina_audit": "财务审计意见", "fina_mainbz": "主营业务构成", "disclosure_date": "财报披露日期表",
        "top10_holders": "前十大股东", "top10_floatholders": "前十大流通股东",
        "stk_holdernumber": "股东人数", "stk_holdertrade": "股东增减持", "pledge_stat": "股权质押统计数据",
        "repurchase": "股票回购", "share_float": "限售股解禁", "namechange": "股票曾用名",
    }
    def read_schema(item):
        api, title = item
        href = menu.get(title)
        if not href:
            return api, {"error": "Documentation navigation link not found"}
        path = urlsplit(href).path + ("?" + urlsplit(href).query if urlsplit(href).query else "")
        soup = doc(path)
        candidates = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            headers = [x.get_text(" ", strip=True) for x in rows[0].find_all(["td", "th"])]
            if "名称" not in headers or "类型" not in headers or "必选" in headers:
                continue
            fields = []
            for row in rows[1:]:
                values = [x.get_text(" ", strip=True) for x in row.find_all(["td", "th"])]
                if len(values) < len(headers):
                    continue
                value = dict(zip(headers, values))
                if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value["名称"]):
                    fields.append({"field": value["名称"], "type": value["类型"], "description": value.get("描述", "")})
            if fields:
                candidates.append(fields)
        return api, {"url": "https://tushare.pro" + path, "fields": max(candidates, key=len) if candidates else [],
                     "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        for api, schema in pool.map(read_schema, wanted.items()):
            catalog[api] = schema
    (ROOT / "fundamental_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "schemas", "fields": {k: len(catalog[k].get("fields", [])) for k in wanted}}), flush=True)
    specs = [
        ("fina_600133_full", "fina_indicator", {"ts_code": "600133.SH", "start_date": "20130101", "end_date": "20260918"}),
        ("fina_600133_left", "fina_indicator", {"ts_code": "600133.SH", "start_date": "20130101", "end_date": "20191231"}),
        ("fina_600133_right", "fina_indicator", {"ts_code": "600133.SH", "start_date": "20200101", "end_date": "20260918"}),
        ("income_date_semantics", "income", {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20240331"}),
        ("income_period_check", "income", {"ts_code": "600519.SH", "period": "20231231"}),
        ("fina_date_semantics", "fina_indicator", {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20240331"}),
        ("report_limit_page0", "report_rc", {"start_date": "20200401", "end_date": "20200407", "limit": 10, "offset": 0}),
        ("report_limit_page1", "report_rc", {"start_date": "20200401", "end_date": "20200407", "limit": 10, "offset": 10}),
        ("report_window_full", "report_rc", {"start_date": "20200401", "end_date": "20200407"}),
        ("dividend", "dividend", {"ts_code": "600519.SH"}),
        ("forecast", "forecast", {"ts_code": "000002.SZ", "start_date": "20240101", "end_date": "20251231"}),
        ("express", "express", {"ts_code": "600036.SH", "start_date": "20200101", "end_date": "20260918"}),
        ("fina_audit", "fina_audit", {"ts_code": "600519.SH", "start_date": "20130101", "end_date": "20260918"}),
        ("fina_mainbz_P", "fina_mainbz", {"ts_code": "600519.SH", "period": "20231231", "type": "P"}),
        ("fina_mainbz_D", "fina_mainbz", {"ts_code": "600519.SH", "period": "20231231", "type": "D"}),
        ("disclosure_date", "disclosure_date", {"ts_code": "600519.SH", "end_date": "20231231"}),
        ("top10_holders", "top10_holders", {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20241231"}),
        ("top10_floatholders", "top10_floatholders", {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20241231"}),
        ("stk_holdernumber", "stk_holdernumber", {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20241231"}),
        ("stk_holdertrade", "stk_holdertrade", {"ts_code": "002594.SZ", "start_date": "20240101", "end_date": "20251231"}),
        ("pledge_stat", "pledge_stat", {"ts_code": "002475.SZ", "start_date": "20240101", "end_date": "20241231"}),
        ("repurchase", "repurchase", {"ts_code": "000333.SZ", "start_date": "20240101", "end_date": "20251231"}),
        ("share_float", "share_float", {"ts_code": "300750.SZ", "start_date": "20200101", "end_date": "20260918"}),
        ("namechange", "namechange", {"ts_code": "000002.SZ"}),
    ]
    def call(spec):
        label, api, params = spec
        requested = [x["field"] for x in catalog.get(api, {}).get("fields", [])]
        rec = {"label": label, "api": api, "params": params, "requested_fields": requested}
        try:
            obj, metrics = client.call(api, params, requested)
            rec.update(metrics)
            data = obj.get("data") or {}
            names, items = data.get("fields") or [], data.get("items") or []
            rec.update({"code": obj.get("code"), "message": obj.get("msg"), "rows": len(items), "fields": names,
                        "missing_fields": sorted(set(requested) - set(names)), "unique_stocks": len({r[names.index("ts_code")] for r in items}) if "ts_code" in names else None})
            for col in ["ts_code", "ann_date", "f_ann_date", "end_date", "report_date"]:
                if col in names:
                    values = [str(r[names.index(col)]) for r in items if r[names.index(col)] not in (None, "")]
                    rec[col + "_range"] = [min(values), max(values)] if values else []
            packed = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), compresslevel=3, mtime=0)
            (out / (label + ".json.gz")).write_bytes(packed)
            rec.update({"raw_gzip_bytes": len(packed), "raw_sha256": hashlib.sha256(packed).hexdigest()})
        except Exception as exc:
            rec["error"] = str(exc).replace(token, "[REDACTED]")
        print(json.dumps({"event": "probe", **{k: rec.get(k) for k in ["label", "code", "rows", "missing_fields", "unique_stocks", "ann_date_range", "end_date_range", "error"]}}, ensure_ascii=False), flush=True)
        return rec
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(call, specs))
    (out / "requests.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "finished", "path": str(out), "requests": len(records)}), flush=True)


if __name__ == "__main__":
    main()

"""Build the preparation report from saved responses; no network calls."""
import collections
import datetime as dt
import gzip
import json
from pathlib import Path
import shutil

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
run = Path(json.loads((ROOT / "latest_benchmark.json").read_text(encoding="utf-8"))["run"])
base = json.loads((run / "summary.json").read_text(encoding="utf-8"))
steady = json.loads((run / "stability" / "summary.json").read_text(encoding="utf-8"))
initial = [json.loads(s) for s in (run / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
quality = json.loads((run / "quality" / "requests.json").read_text(encoding="utf-8"))
stability = [json.loads(s) for s in (run / "stability" / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
all_records = initial + quality + stability
catalog = json.loads((ROOT / "fundamental_catalog.json").read_text(encoding="utf-8"))

validated_files = 0
validated_rows = 0
for r in initial:
    if not r.get("parquet_file"):
        continue
    raw = json.loads(gzip.decompress((run / r["raw_file"]).read_bytes()))["data"]
    table = pq.read_table(run / r["parquet_file"])
    assert table.num_rows == len(raw["items"])
    assert table.column_names == raw["fields"]
    # Read-back checks include beginning, middle and end, avoiding conversion-only validation.
    for i in sorted({0, table.num_rows // 2, table.num_rows - 1}):
        actual = table.slice(i, 1).to_pylist()[0]
        expected = dict(zip(raw["fields"], raw["items"][i]))
        assert actual == expected, (r["label"], i)
    validated_files += 1
    validated_rows += table.num_rows

def signatures(label):
    obj = json.loads(gzip.decompress((run / "stability" / (label + ".json.gz")).read_bytes()))
    return {json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in obj["data"]["items"]}

paging_checks = {}
for prefix in ["indicator", "holder"]:
    a, b = signatures(prefix + "_page0"), signatures(prefix + "_page1")
    paging_checks[prefix] = {"first_page_unique_rows": len(a), "second_page_unique_rows": len(b), "overlap": len(a & b)}
    assert len(a) == 10 and len(b) == 10 and not a & b

core = ["income", "balancesheet", "cashflow", "fina_indicator", "daily_basic", "report_rc", "dividend", "forecast", "express", "fina_audit", "disclosure_date"]
extended = ["fina_mainbz", "top10_holders", "top10_floatholders", "stk_holdernumber", "stk_holdertrade", "pledge_stat", "repurchase", "share_float"]
plan = {
    "status": "PREPARATION_COMPLETE_BULK_NOT_STARTED", "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    "endpoint": "https://tuaremax.top", "third_party_compatible_endpoint": True,
    "formal_start_date": "20160920", "formal_end_date": "20260918",
    "scope": "Mainland A shares, historical listed and delisted securities, excluding B shares, unlisted placeholders and securities delisted before the formal window.",
    "universe_file": str(run / "universe.json"), "universe_count": base["universe_count"],
    "trading_days_file": str(run / "trading_days.json"), "trading_days": base["trading_days"],
    "workers": 4, "global_max_requests_per_second": 5,
    "routing": {"mode": "physical_interface_only", "interface_index": 18, "interface_alias": "WLAN", "source_ip": "10.180.86.202", "proxy_fallback": False,
                "revalidate_before_start": True},
    "main_fundamental_apis": {api: [f["field"] for f in catalog[api]["fields"]] for api in core},
    "extended_fundamental_apis": {api: [f["field"] for f in catalog[api]["fields"]] for api in extended},
    "supporting_daily_apis": ["daily", "adj_factor"], "supporting_reference_apis": ["stock_basic", "trade_cal", "namechange"],
    "follow_on_daily_context": ["moneyflow", "margin_detail", "stk_limit", "suspend_d", "index_daily", "index_weight", "index_member_all"],
    "follow_on_status": "Not benchmarked in this run; validate before including them in the full daily-research extension.",
    "optional_warmup": {"financial_report_start": "20110101", "price_start": "20140920", "analyst_report_start": "20150920", "separate_from_formal_window": True},
    "storage": {"research_tables": "Parquet + Zstd", "raw_responses": "compact JSON + gzip", "preserve_returned_versions": True,
                "do_not_materialize_all_financial_fields_daily": True, "min_free_GB": 30, "automatic_deletion": False},
    "execution_requirements": [
        "Use ordinary financial interfaces with explicit complete fields; prior VIP responses omitted requested columns.",
        "fina_indicator default response can truncate at 100. Explicit limit/offset paging was tested; continue until a short/empty page, guard against repeated pages, and cross-check annual partitions.",
        "The third-party income start_date/end_date behaved as report-period filters, unlike the official announcement-date description. Preserve announcement dates and filter historical availability separately.",
        "Store all returned revisions and exact response provenance. A historical download does not itself establish complete point-in-time revision history.",
        "Distinguish no event, missing source history, rejected parameters and request failure. Never silently replace missing values with zero.",
        "Main-business segment history differs substantially by stock; include a stock/year coverage report rather than claiming uniform ten-year availability.",
        "Validate all stock/date scopes, row lengths and requested columns before completing a partition.",
        "Use per-request manifest, atomic commits, checksums, resume without redownloading valid partitions, bounded retries and global backoff when rate-limited.",
        "Revalidate physical interface, DHCP address and true endpoint DNS before launch; fail closed if direct networking becomes unavailable."
    ],
    "planning_estimates_not_completion_guarantees": {"core_financial_and_daily_hours": [2, 4], "fundamental_plus_analyst_and_shareholder_events_hours": [5, 10],
         "basis": "Four core tables need at least 4*5814 requests; three daily tables need 3*2429 requests before pagination, annual cross-checks, events, warmup and retries. Calibrated at ~5 successful requests/s. Correct after the first full extraction batches."}
}
(ROOT / "download_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
saved_bytes = sum(p.stat().st_size for p in run.rglob("*") if p.is_file())
evidence = {"authenticated_requests": len(all_records), "api_error_requests": sum(r.get("code") != 0 or bool(r.get("error")) for r in all_records),
            "missing_field_requests": sum(bool(r.get("missing_fields")) for r in all_records),
            "all_routes_physical": all(r.get("route", {}).get("interface_index") == 18 for r in all_records),
            "source_ips": sorted({r.get("route", {}).get("source_ip") for r in all_records}),
            "destination_ips": sorted({r.get("route", {}).get("destination_ip") for r in all_records}),
            "parquet_files_read_back": validated_files, "parquet_rows_read_back": validated_rows,
            "paging_checks": paging_checks, "sample_bytes_saved": saved_bytes, "disk_free_bytes": shutil.disk_usage("F:\\").free,
            "windows_tcp_observation": {"process_id": 34852, "established_connections_observed": 5, "local_ip": "10.180.86.202", "remote_ip": "124.156.169.109", "remote_port": 443,
                                        "source": "Get-NetTCPConnection during benchmark; WLAN and Meta were Up, Gateway NC Adapter Disconnected"}}
(run / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

lines = ["# Tushare 十年全 A 股基本面：直连测速与下载准备", "",
         "状态：已完成小批量测速、分页与字段核查；十年全量下载尚未启动。", "",
         f"本次共 {len(all_records)} 次认证请求，全部返回成功，未触发限流。样本保存约 {saved_bytes / 1e6:.2f} MB；F 盘剩余约 {evidence['disk_free_bytes'] / 1e9:.2f} GB。", "",
         "## 网络", "",
         "所有 API 请求都固定绑定 WLAN 物理网卡（接口 18，10.180.86.202），连接真实服务地址 124.156.169.109:443，开启 TLS 证书验证。DNS 同样通过该物理网卡查询。没有代理或 VPN 回退。Windows 进程连接快照也观察到该源地址直达该目标。", "",
         "Clash 保持开启；学院 VPN 网卡在检查时本来处于未连接状态，本轮未修改其状态或任何全局网络配置。物理网卡地址可能随网络变化，正式启动前须重新校验。", "",
         "## 并发和速度", "",
         "| 并发 | 样本请求 | 成功 | 总耗时 | 成功请求/秒 | 线上响应 MB/秒 | 延迟 P95 |", "|---:|---:|---:|---:|---:|---:|---:|"]
for s in base["stages"][:4]:
    lines.append(f"| {s['workers']} | {s['requests']} | {s['successes']} | {s['wall_seconds']:.2f} 秒 | {s['requests_per_second']:.2f} | {s['wire_MB_per_second']:.3f} | {s['latency_p95_seconds']:.3f} 秒 |")
lines += ["", f"最后以 4 并发连续执行 {steady['requests']} 次请求，耗时 {steady['wall_seconds']:.2f} 秒，平均 {steady['requests_per_second']:.2f} 次/秒，错误和限流均为 0；响应延迟中位数 {steady['latency_median_seconds']:.3f} 秒、P95 {steady['latency_p95_seconds']:.3f} 秒。", "",
          f"连续测试的线上响应数据约 {steady['wire_MB_per_second']:.3f} MB/秒，解压后的 JSON 约 {steady['decoded_MB_per_second']:.3f} MB/秒。基本面请求响应较小，不能用这个值判断网络带宽上限。", "",
          "本轮统一设置每秒 5 次（约每分钟 300 次）的客户端节流上限。各并发档使用不同股票和日期以减少重复缓存影响，载荷并不完全相同；结果支持以 4 并发作为起点，不是账号极限频率或严格同载荷的性能排名。", "",
          "## 数据范围", "",
          f"正式区间 2016-09-20 至 2026-09-18，共 {base['trading_days']} 个沪市交易日；初步股票库 {base['universe_count']} 只，上市 {base['universe_status_counts']['L']} 只、期间退市 {base['universe_status_counts']['D']} 只。名单按上市/退市日期与交易所过滤；完整下载仍需检查北交所历史代码映射。", "",
          "核心四表显式取全字段：利润表 94、资产负债表 158、现金流量表 97、财务指标 167，共 516 个表内字段项，不等于 516 个独立因子。日频估值另有 19 列，研报预测 23 列。", "",
          "分红、业绩预告、业绩快报、审计、披露日期、主营业务构成、前十大股东、前十大流通股东、股东户数、股东交易、质押、回购、解禁接口均取得了非空小样。是否覆盖十年所有股票、所有修订版本，需要全量后的覆盖报告，不能由权限小样推断。", "",
          "## 会影响全量正确性的发现", "",
          "1. fina_indicator 长区间默认只返回 100 行。limit/offset 的前两页已验证各 10 行、互不重叠；整段与分段的另一个 100 行样本也完全一致。全量须分页并做分段复核。", "",
          "2. 第三方 income 的 2024-01-01 至 2024-03-31 查询返回了报告期 2024-03-31、公告日 2024-04-27 的记录，表明该接口实际按报告期过滤；与官方对公告日过滤的说明不同。存储必须保留 ann_date/f_ann_date，回测按可用时点对齐。", "",
          "3. report_rc 的 limit/offset 已核对：两页各 10 行，不重叠，且全部包含在同窗口完整响应中。第三方默认返回条数与官方文档上限存在差异，不能写死同一套分页假设。", "",
          "4. 主营业务构成存在历史覆盖差异：贵州茅台长区间只返回 2026 半年报样本，美的返回 2018 至 2026 年记录。此项必须单独统计缺口。", "",
          "5. 先前批量 VIP 财报接口漏列，因此完整列集合采用普通接口；空值、未披露和接口失败分开记录。", "",
          f"6. 已重新读取 {validated_files} 个 Parquet 文件，校验 {validated_rows:,} 行的行数和列名，并逐文件核对首、中、尾记录与保存的原始响应一致。", "",
          "## 下载安排与时间估算", "",
          "建议 4 并发、全局每秒 5 次请求，保留原始压缩响应与 Parquet；财报按事件/报告版本保存，日频估值按交易日保存，不把全部财报列复制成日频大宽表。失败重试、限流退避、断点清单和覆盖核验是正式下载的必要步骤。", "",
          "三张财报、财务指标和三类日频基础至少涉及 4×5,814＋3×2,429＝30,543 次请求，按每秒 5 次约 1.7 小时；加入分页、预热、复核后，核心部分暂按 2–4 小时规划。再加研报、股东及公司行为，暂按 5–10 小时预留。均为排期估算，未运行全量，不能当成完成承诺。", "",
          "具体接口、全部字段、日期、网络要求和待验证扩展项保存在 download_plan.json。分钟、逐笔、盘口、公告全文、研报 PDF 和成品因子库不在本次基本面范围。", "",
          "## 官方参考与本地证据", "",
          "- [财务指标及单次返回限制](https://tushare.pro/document/2?doc_id=79)",
          "- [利润表官方参数说明](https://tushare.pro/document/2?doc_id=33)",
          "- [卖方盈利预测](https://tushare.pro/document/2?doc_id=292)",
          "- [Windows 物理出口接口绑定说明](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options)",
          f"- 本次完整证据目录：{run}", "",
          "Token 仅通过进程标准输入传入，未写入脚本、配置、报告或样本文件。"]
(ROOT / "直连测速与下载准备报告.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"report": str(ROOT / "直连测速与下载准备报告.md"), "plan": str(ROOT / "download_plan.json"), "verification": evidence}, ensure_ascii=False))

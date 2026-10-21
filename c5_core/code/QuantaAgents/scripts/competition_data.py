"""A 股比赛策略 —— 日频数据补齐管线（单脚本多子命令）。

把冻结日线源（/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025，截至 2025-12-31）
+ 本机 vendor CSV（截至 2026-07-17）+ 东方财富公共接口（截至最新交易日）
拼成一份可以被 src/quanta_agents/factor_lab_a/panel.py::build_panel 直接消费的日线源。

全部产物写到 /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live/（外置盘）。不修改 src/ 下任何文件。

子命令
------
fetch-index         三大指数原始日线 -> index_daily.parquet（并与 qlib 二进制对账）
fetch-constituents  中证官方当前成分股 -> constituents_current.csv + instruments/*.txt
fetch-daily-ext     vendor 之后的增量日线 -> daily_ext/<code>.parquet + snapshot.parquet
build-source        vendor + 增量 拼成冻结源格式 -> daily_source/<code>.parquet + manifest.json
update              日更入口：依次跑上面四步并写 data_asof.json

用法
----
    set PYTHONPATH=src
    .venv\\Scripts\\python.exe scripts\\competition_data.py <子命令> [选项]

常用选项
    --limit N        只处理前 N 只股票（小规模试跑）
    --codes a,b,c    只处理指定代码
    --force          忽略断点续跑，强制重取/重建
    --workers N      线程数（默认 4，上限 4）
    --check-sample N build-source 的一致性检验抽样只数（默认 200）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ---------------------------------------------------------------- 路径常量

OUT_ROOT = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live"
VENDOR_DIR = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/vendor/stock-trading-data-2025-12-23N"
QLIB_ROOT = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"
QLIB_INSTRUMENTS = os.path.join(QLIB_ROOT, "instruments")
REF_SOURCE = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025"

DAILY_EXT_DIR = os.path.join(OUT_ROOT, "daily_ext")
DAILY_SOURCE_DIR = os.path.join(OUT_ROOT, "daily_source")
INSTRUMENTS_DIR = os.path.join(OUT_ROOT, "instruments")
LOG_DIR = os.path.join(OUT_ROOT, "logs")
RAW_DIR = os.path.join(OUT_ROOT, "raw")

INDEXES = ["000300", "000905", "000852"]
INDEX_TO_UNIVERSE = {"000300": "csi300", "000905": "csi500", "000852": "csi1000"}

# vendor 数据截止日（qlib instruments 的“仍在指数内”哨兵值）
QLIB_INSTRUMENTS_SENTINEL = "2026-01-29"
# 2026 年 6 月定期调整生效前最后一个交易日 / 生效首日
JUNE_REBALANCE_LAST = "2026-06-12"
JUNE_REBALANCE_FIRST = "2026-06-15"
FAR_FUTURE = "2100-01-01"

VENDOR_START = "2015-01-01"
EXT_START = "2026-06-20"       # 与 vendor 重叠 6/20-7/17，用于一致性核对
INDEX_START = "2014-01-01"

BJ_TZ = timezone(timedelta(hours=8))

# 参考目录的代码正则（沪深主板/创业板/科创板）
CODE_RE = re.compile(r"^(?:sh(?:600|601|603|605|688|689)|sz(?:000|001|002|003|300|301|302))\d{3}$")

# 冻结源列顺序（与参考目录完全一致）
SOURCE_COLUMNS = [
    "date", "code", "open", "high", "low", "close", "volume", "amount",
    "float_shares", "total_shares", "qfq_ratio", "vwap_qfq", "prev_close",
    "gu_1m", "gd_1m", "rbar_up17", "rbar_down17", "r_0931_1000", "r_1001_1030",
    "overnight_return", "raw_open", "raw_prev_close", "stock_name",
    "is_st", "is_delisting", "float_market_cap", "total_market_cap",
]
MINUTE_COLUMNS = ["gu_1m", "gd_1m", "rbar_up17", "rbar_down17",
                  "r_0931_1000", "r_1001_1030", "overnight_return"]

VENDOR_COLUMNS = ["code", "stock_name", "date", "open", "high", "low", "close",
                  "prev_close_adj", "volume", "amount", "float_market_cap", "total_market_cap"]

# ---------------------------------------------------------------- 基础工具


def _stdout_utf8():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def now_bj() -> datetime:
    return datetime.now(BJ_TZ)


def today_str() -> str:
    return now_bj().strftime("%Y%m%d")


# A 股 15:00 收盘；留 5 分钟给行情源结算尾盘集合竞价
SETTLE_HOUR, SETTLE_MINUTE = 15, 5


def session_closed(ts: datetime | None = None) -> bool:
    ts = ts or now_bj()
    return (ts.hour, ts.minute) >= (SETTLE_HOUR, SETTLE_MINUTE)


def drop_unsettled(df: pd.DataFrame, col: str = "date", include_today: bool = False) -> pd.DataFrame:
    """盘中跑管线时，当天的那根 K 线是**未收盘的半截 bar**，默认丢掉。

    行情接口在交易时段内就会返回当日 bar（开盘价真、收盘价=当前价、成交量只到当下），
    直接写进研究面板会造成当日收益率、成交量、换手率全部失真。
    """
    if include_today or df is None or len(df) == 0 or session_closed():
        return df
    today = pd.Timestamp(now_bj().date())
    return df[df[col] != today].reset_index(drop=True)


def log(msg: str):
    print(f"[{now_bj():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def ensure_dirs():
    for d in (OUT_ROOT, DAILY_EXT_DIR, DAILY_SOURCE_DIR, INSTRUMENTS_DIR, LOG_DIR, RAW_DIR):
        os.makedirs(d, exist_ok=True)


class RateLimiter:
    """全局自适应限速器（所有线程共享）。

    东方财富对本机 IP 有明显的突发配额：连续成功一段时间后会开始直接重置连接，
    停一会儿又恢复。固定 5 次/秒会长时间打在“被封”状态上，实际吞吐反而只有 0.7 次/秒。
    所以按成功/失败自适应调节间隔：成功就慢慢加速（上限 per_second），失败就退避。
    """

    def __init__(self, per_second: float, max_interval: float = 3.0):
        self.min_interval = 1.0 / per_second
        self.max_interval = max_interval
        self.interval = self.min_interval
        self._lock = threading.Lock()
        self._next = time.monotonic()
        self.n_ok = 0
        self.n_fail = 0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            if self._next < now:
                self._next = now
            wait = self._next - now
            self._next += self.interval
        if wait > 0:
            time.sleep(wait)

    def report(self, ok: bool):
        with self._lock:
            if ok:
                self.n_ok += 1
                self.interval = max(self.min_interval, self.interval * 0.97)
            else:
                self.n_fail += 1
                self.interval = min(self.max_interval, self.interval * 1.20)

    def stats(self) -> dict:
        return {"n_ok": self.n_ok, "n_fail": self.n_fail,
                "interval": round(self.interval, 3),
                "success_rate": round(self.n_ok / max(1, self.n_ok + self.n_fail), 3)}


_LIMITER = RateLimiter(5.0)
_HOST_LIMITERS: dict[str, RateLimiter] = {}
_HOST_LOCK = threading.Lock()
# 熔断：某个 host 连续失败这么多次之后，本次运行内直接跳过它，不再浪费重试预算
CIRCUIT_TRIP = 40
_HOST_FAILS: dict[str, int] = {}
_TLS = threading.local()


def _limiter_for(host: str) -> RateLimiter:
    """每个 host 一套自适应限速 —— 否则东方财富被限流会把腾讯也拖慢。"""
    with _HOST_LOCK:
        lim = _HOST_LIMITERS.get(host)
        if lim is None:
            lim = RateLimiter(_LIMITER.min_interval and 1.0 / _LIMITER.min_interval or 5.0)
            _HOST_LIMITERS[host] = lim
        return lim


def _limiter_report() -> dict:
    with _HOST_LOCK:
        return {h.split(".")[0]: l.stats() for h, l in _HOST_LIMITERS.items()}


def host_tripped(host: str) -> bool:
    with _HOST_LOCK:
        return _HOST_FAILS.get(host, 0) >= CIRCUIT_TRIP


def _note_host(host: str, ok: bool):
    with _HOST_LOCK:
        if ok:
            _HOST_FAILS[host] = 0
        else:
            _HOST_FAILS[host] = _HOST_FAILS.get(host, 0) + 1


def _session() -> requests.Session:
    """每线程一个 Session；trust_env=False —— 本机 HTTP_PROXY 指向一个已死的本地代理。"""
    s = getattr(_TLS, "session", None)
    if s is None:
        s = requests.Session()
        s.trust_env = False
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        _TLS.session = s
    return s


def http_get(host: str, path: str, params=None, headers=None, tries: int = 8,
             schemes=("http", "https"), timeout: int = 12):
    """自适应限速 + 重试 + http/https 交替。

    被限流时服务端是**立即重置连接**（0 秒失败），所以重试本身很便宜，
    真正的节流交给 RateLimiter 的自适应间隔。
    """
    if host_tripped(host):
        raise RuntimeError(f"GET {host}{path} 跳过: 该 host 已熔断（连续失败 >= {CIRCUIT_TRIP} 次）")
    lim = _limiter_for(host)
    last = None
    for i in range(tries):
        scheme = schemes[i % len(schemes)] if i else schemes[0]
        lim.acquire()
        try:
            r = _session().get(f"{scheme}://{host}{path}", params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                lim.report(True)
                _note_host(host, True)
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            last = exc
        lim.report(False)
        _note_host(host, False)
        if host_tripped(host):
            break
    raise RuntimeError(f"GET {host}{path} 失败: {type(last).__name__}: {last}")


# ---------------------------------------------------------------- 东方财富接口

KLINE_HOST = "push2his.eastmoney.com"
KLINE_PATH = "/api/qt/stock/kline/get"
QUOTE_HOST = "push2.eastmoney.com"
QUOTE_PATH = "/api/qt/stock/get"
CLIST_HOST = "push2delay.eastmoney.com"   # clist 只有这个节点稳定可达
CLIST_PATH = "/api/qt/clist/get"

KLINE_FIELDS = ["date", "open", "close", "high", "low", "volume_lots", "amount",
                "amplitude", "pct_chg", "chg", "turnover_rate"]


def secid(code: str) -> str:
    """sh600000 -> 1.600000, sz000001 -> 0.000001。沪市 market=1，深市 market=0。"""
    mkt = "1" if code[:2].lower() == "sh" else "0"
    return f"{mkt}.{code[2:]}"


def fetch_kline(sec: str, fqt: int, beg: str, end: str) -> pd.DataFrame | None:
    """东方财富日 K。fqt=0 未复权，fqt=1 前复权（锚定最新交易日）。

    返回列：date/open/close/high/low/volume_lots(手)/amount(元)/amplitude/pct_chg/chg/turnover_rate
    """
    r = http_get(KLINE_HOST, KLINE_PATH, params=dict(
        secid=sec, fields1="f1,f2,f3,f4,f5,f6",
        fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        klt=101, fqt=fqt, beg=beg, end=end))
    data = r.json().get("data")
    if not data or not data.get("klines"):
        return None
    rows = [x.split(",") for x in data["klines"]]
    df = pd.DataFrame(rows, columns=KLINE_FIELDS)
    df["date"] = pd.to_datetime(df["date"])
    for c in KLINE_FIELDS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ---- 腾讯日线（个股增量的主源）
#
# 为什么不用东方财富做个股增量：东方财富对本机 IP 有很紧的突发配额，
# 实测持续吞吐只有约 0.7 次/秒（且是连接直接被重置），全市场要 4 小时以上。
# 腾讯 newfqkline 一次请求给一只股票的完整日线（含成交额与换手率），
# 4 线程实测 27-30 只/秒、成功率 100%，且与 vendor CSV 的开高低收/成交量/成交额完全一致。

TENCENT_HOST = "web.ifzq.gtimg.cn"
TENCENT_PATH = "/appstock/app/newfqkline/get"


def fetch_kline_tencent(code: str, count: int = 140, mode: str = "nfq") -> pd.DataFrame | None:
    """腾讯日线。mode="nfq" 未复权，"qfq" 前复权（等比，锚定最新交易日）。

    返回列同 fetch_kline，另含 amount(元)/turnover_rate(%)。
    行格式：[日期, 开, 收, 高, 低, 成交量(手), {}, 换手率%, 成交额(万元), ...]
    """
    r = http_get(TENCENT_HOST, f"{TENCENT_PATH}?param={code},day,,,{count},{mode}",
                 schemes=("http", "https"))
    try:
        j = r.json()
    except ValueError:
        return None
    d = ((j or {}).get("data") or {}).get(code)
    if not isinstance(d, dict):
        return None
    rows = d.get("day") if mode == "nfq" else (d.get(f"{mode}day") or d.get("day"))
    if not rows:
        return None
    rec = []
    for x in rows:
        if len(x) < 6:
            continue
        def g(i):
            if len(x) <= i:
                return np.nan
            v = x[i]
            if isinstance(v, dict) or v in ("", "-", None):
                return np.nan
            try:
                return float(v)
            except (TypeError, ValueError):
                return np.nan
        rec.append({"date": x[0], "open": g(1), "close": g(2), "high": g(3), "low": g(4),
                    "volume_lots": g(5), "turnover_rate": g(7), "amount": g(8) * 1e4})
    if not rec:
        return None
    df = pd.DataFrame(rec)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return df


def fetch_snapshot_tencent(codes: list[str]) -> pd.DataFrame:
    """腾讯批量行情（一次 60 只）：第 1 项简称，44 流通市值（亿元），45 总市值（亿元），72 流通股本，73 总股本。
    股本与东方财富 f39 / f38 逐只一致（2026-10-11 核对）。"""
    rows = []
    for i in range(0, len(codes), 60):
        r = http_get("qt.gtimg.cn", "/q=" + ",".join(codes[i:i + 60]), schemes=("http", "https"))
        for line in r.content.decode("gbk", "ignore").split(";"):
            m = re.search(r'v_(\w+)="(.*)"', line)
            if not m:
                continue
            f = m.group(2).split("~")
            if len(f) < 74:
                continue
            rows.append({"code": m.group(1), "name": f[1],
                         "total_shares": _num(f[73]), "float_shares": _num(f[72]),
                         "total_market_cap": _num(f[45]) * 1e8, "float_market_cap": _num(f[44]) * 1e8})
    df = pd.DataFrame(rows)
    return df.drop_duplicates("code") if len(df) else df


def fetch_snapshot_one(code: str) -> dict | None:
    """单只实时快照：f57 代码 f58 简称 f84 总股本 f85 流通股本 f116 总市值 f117 流通市值。"""
    r = http_get(QUOTE_HOST, QUOTE_PATH, params=dict(secid=secid(code), fields="f57,f58,f84,f85,f116,f117"))
    d = r.json().get("data")
    if not d or not d.get("f57"):
        return None
    return {"code": code, "name": d.get("f58"),
            "total_shares": _num(d.get("f84")), "float_shares": _num(d.get("f85")),
            "total_market_cap": _num(d.get("f116")), "float_market_cap": _num(d.get("f117"))}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return np.nan
    return np.nan if v <= 0 else v


# ---- 除权除息事件表（决定调整后前收盘价，vendor 口径）

DIVIDEND_HOST = "datacenter-web.eastmoney.com"
DIVIDEND_PATH = "/api/data/v1/get"
_EVENTS: dict[tuple[str, pd.Timestamp], tuple[float, float]] = {}


def fetch_dividend_events(beg: str, end: str) -> pd.DataFrame:
    """拉取区间内所有 A 股除权除息事件。

    PRETAX_BONUS_RMB = 每 10 股税前派息(元)，BONUS_IT_RATIO = 每 10 股送转合计(股)。
    除息价（与 vendor“前收盘价”同口径）：
        prev_close_adj = (前一交易日原始收盘 - 派息/10) / (1 + 送转/10)
    """
    rows, page = [], 1
    while True:
        r = http_get(DIVIDEND_HOST, DIVIDEND_PATH, params={
            "reportName": "RPT_SHAREBONUS_DET",
            "columns": "SECURITY_CODE,SECUCODE,EX_DIVIDEND_DATE,PRETAX_BONUS_RMB,BONUS_RATIO,IT_RATIO,BONUS_IT_RATIO",
            "pageNumber": page, "pageSize": 500,
            "sortColumns": "EX_DIVIDEND_DATE", "sortTypes": -1,
            "filter": f"(EX_DIVIDEND_DATE>='{beg}')(EX_DIVIDEND_DATE<='{end}')",
            "source": "WEB", "client": "WEB"}, schemes=("https", "http"))
        res = (r.json() or {}).get("result") or {}
        data = res.get("data") or []
        if not data:
            break
        for d in data:
            sc = str(d.get("SECUCODE") or "")
            code = None
            if sc.endswith(".SH"):
                code = "sh" + sc[:6]
            elif sc.endswith(".SZ"):
                code = "sz" + sc[:6]
            elif sc.endswith(".BJ"):
                code = "bj" + sc[:6]
            if code is None:
                continue
            cash = d.get("PRETAX_BONUS_RMB") or 0.0
            bonus = d.get("BONUS_IT_RATIO")
            if bonus is None:
                bonus = (d.get("BONUS_RATIO") or 0.0) + (d.get("IT_RATIO") or 0.0)
            rows.append({"code": code,
                         "ex_date": pd.Timestamp(str(d.get("EX_DIVIDEND_DATE"))[:10]),
                         "cash_per_10": float(cash), "bonus_it_per_10": float(bonus or 0.0)})
        if page >= int(res.get("pages") or 1):
            break
        page += 1
        if page > 60:
            break
    df = pd.DataFrame(rows)
    if len(df):
        df = (df.groupby(["code", "ex_date"], as_index=False)
                .agg({"cash_per_10": "sum", "bonus_it_per_10": "sum"}))
    return df


def load_events(beg: str, end: str, force: bool = False) -> pd.DataFrame:
    path = os.path.join(OUT_ROOT, "dividend_events.parquet")
    if os.path.exists(path) and not force:
        df = pd.read_parquet(path)
        if len(df) and df["ex_date"].max() >= pd.Timestamp(end) - pd.Timedelta(days=10):
            _install_events(df)
            return df
    df = fetch_dividend_events(beg, end)
    df.to_parquet(path, index=False)
    _install_events(df)
    return df


def _install_events(df: pd.DataFrame):
    _EVENTS.clear()
    for r in df.itertuples(index=False):
        _EVENTS[(r.code, pd.Timestamp(r.ex_date))] = (float(r.cash_per_10), float(r.bonus_it_per_10))


def fetch_snapshot_all() -> pd.DataFrame:
    """批量行情列表：一次 100 只，约 60 个请求覆盖全市场。
    f12 代码 f13 市场 f14 简称 f20 总市值 f21 流通市值 f38 总股本 f39 流通股本。
    """
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    rows, page = [], 1
    while True:
        qs = (f"pn={page}&pz=100&po=0&np=1&fltt=2&invt=2&fid=f12&fs={fs}"
              f"&fields=f12,f13,f14,f20,f21,f38,f39")
        r = http_get(CLIST_HOST, f"{CLIST_PATH}?{qs}")
        data = r.json().get("data")
        if not data or not data.get("diff"):
            break
        for d in data["diff"]:
            mkt = d.get("f13")
            prefix = "sh" if mkt == 1 else ("sz" if mkt == 0 else "bj")
            rows.append({"code": f"{prefix}{d.get('f12')}", "name": d.get("f14"),
                         "total_shares": _num(d.get("f38")), "float_shares": _num(d.get("f39")),
                         "total_market_cap": _num(d.get("f20")), "float_market_cap": _num(d.get("f21"))})
        total = data.get("total") or 0
        if page * 100 >= total:
            break
        page += 1
        if page > 120:
            break
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates("code")
    return df


# ---------------------------------------------------------------- vendor / qlib 读取


def vendor_codes() -> list[str]:
    out = []
    for f in sorted(os.listdir(VENDOR_DIR)):
        if not f.endswith(".csv"):
            continue
        code = f[:-4].lower()
        if code.startswith("bj"):
            continue
        if not code.startswith(("sh", "sz")):
            continue
        out.append(code)
    return out


def read_vendor(code: str, start: str | None = VENDOR_START) -> pd.DataFrame | None:
    path = os.path.join(VENDOR_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, encoding="gbk", skiprows=1, header=0)
    except Exception:  # noqa: BLE001
        try:
            df = pd.read_csv(path, encoding="gb18030", skiprows=1, header=0)
        except Exception:  # noqa: BLE001
            return None
    if df.shape[1] != len(VENDOR_COLUMNS):
        return None
    df.columns = VENDOR_COLUMNS
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    for c in ["open", "high", "low", "close", "prev_close_adj", "volume", "amount",
              "float_market_cap", "total_market_cap"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    df["code"] = code
    return df


def read_qlib_bin(instrument: str, field: str) -> tuple[pd.DatetimeIndex, np.ndarray] | None:
    path = os.path.join(QLIB_ROOT, "features", instrument, f"{field}.day.bin")
    if not os.path.exists(path):
        return None
    arr = np.fromfile(path, dtype="<f4")
    if arr.size < 2:
        return None
    start = int(arr[0])
    vals = arr[1:].astype(np.float64)
    cal = qlib_calendar()
    return cal[start:start + len(vals)], vals


_CAL_CACHE = None


def qlib_calendar() -> pd.DatetimeIndex:
    global _CAL_CACHE
    if _CAL_CACHE is None:
        with open(os.path.join(QLIB_ROOT, "calendars", "day.txt"), encoding="utf-8") as fh:
            _CAL_CACHE = pd.DatetimeIndex(pd.to_datetime(fh.read().split()))
    return _CAL_CACHE


# ================================================================ A. fetch-index


def cmd_fetch_index(args) -> dict:
    """三大指数原始日线。

    东方财富能一次给到 2014 年，但它对本机 IP 的限流会整天反复；腾讯稳定但最多回溯约 2000 个交易日。
    所以：优先东方财富，失败退腾讯，再与已有文件合并 —— 历史段保留，新段覆盖旧段
    （这样盘中写进去的半截 bar 会被收盘后的完整 bar 覆盖）。
    """
    ensure_dirs()
    end = today_str()
    out_path = os.path.join(OUT_ROOT, "index_daily.parquet")
    frames, report, sources = [], {}, {}
    for idx in INDEXES:
        df, src = None, None
        try:
            df = fetch_kline(f"1.{idx}", 0, INDEX_START.replace("-", ""), end)
            src = "eastmoney"
        except Exception as exc:  # noqa: BLE001
            log(f"指数 {idx} 东方财富失败（{type(exc).__name__}），改用腾讯")
        if df is None or len(df) == 0:
            try:
                df = fetch_kline_tencent(f"sh{idx}", count=2000)
                src = "tencent"
            except Exception as exc:  # noqa: BLE001
                log(f"指数 {idx} 腾讯也失败: {type(exc).__name__}")
                df = None
        if df is None or len(df) == 0:
            log(f"指数 {idx} 拉取失败，保留已有历史")
            sources[idx] = "failed"
            continue
        out = pd.DataFrame({
            "date": df["date"], "index_code": idx,
            "open": df["open"], "high": df["high"], "low": df["low"], "close": df["close"],
            "volume": df["volume_lots"] * 100.0,   # 手 -> 股，与个股口径一致
            "amount": df["amount"],
        })
        out = drop_unsettled(out, include_today=getattr(args, "include_today", False))
        frames.append(out)
        sources[idx] = src
        log(f"指数 {idx}（{src}）: {len(out)} 行, {out['date'].min():%Y-%m-%d} ~ {out['date'].max():%Y-%m-%d}")

    old = pd.read_parquet(out_path) if os.path.exists(out_path) else None
    if not frames and old is None:
        raise RuntimeError("三个指数全部拉取失败，且本地没有历史文件")
    new = pd.concat(frames, ignore_index=True) if frames else None
    if old is not None and new is not None:
        old = old[~old.set_index(["index_code", "date"]).index.isin(
            new.set_index(["index_code", "date"]).index)]
        allidx = pd.concat([old, new], ignore_index=True)
    else:
        allidx = new if new is not None else old
    allidx = (allidx.sort_values(["index_code", "date"])
              .drop_duplicates(["index_code", "date"], keep="last").reset_index(drop=True))
    allidx.to_parquet(out_path, index=False)
    report["_sources"] = sources

    # 与 qlib 二进制对账（qlib close 是归一化值，close/factor 才是指数点位）
    for idx in INDEXES:
        inst = f"sh{idx}"
        c = read_qlib_bin(inst, "close")
        f = read_qlib_bin(inst, "factor")
        if c is None:
            report[idx] = {"status": "qlib 无此标的"}
            continue
        dates, close = c
        qlib_close = close / f[1] if f is not None else close
        qs = pd.Series(qlib_close, index=dates)
        es = allidx[allidx["index_code"] == idx].set_index("date")["close"]
        common = qs.index.intersection(es.index)
        rel = (es.loc[common] - qs.loc[common]).abs() / qs.loc[common]
        bad = rel[rel > 1e-3]
        report[idx] = {
            "overlap_days": int(len(common)),
            "overlap_range": [str(common.min().date()), str(common.max().date())] if len(common) else None,
            "max_rel_diff": float(rel.max()) if len(rel) else None,
            "median_rel_diff": float(rel.median()) if len(rel) else None,
            "n_diff_gt_0.1pct": int(len(bad)),
            "dates_diff_gt_0.1pct": [str(d.date()) for d in bad.index[:50]],
        }
        log(f"指数 {idx} 对账: 重叠 {len(common)} 天, 最大相对差 {rel.max():.3e}, >0.1% 共 {len(bad)} 天")
    with open(os.path.join(OUT_ROOT, "index_check.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return {"rows": int(len(allidx)), "last_date": str(allidx["date"].max().date()), "check": report}


def trading_days_from_index() -> pd.DatetimeIndex:
    p = os.path.join(OUT_ROOT, "index_daily.parquet")
    if not os.path.exists(p):
        return pd.DatetimeIndex([])
    d = pd.read_parquet(p, columns=["date", "index_code"])
    d = d[d["index_code"] == "000300"]
    return pd.DatetimeIndex(sorted(d["date"].unique()))


def market_last_date() -> pd.Timestamp | None:
    td = trading_days_from_index()
    return td.max() if len(td) else None


# ================================================================ B. fetch-constituents

CSINDEX_HOST = "oss-ch.csindex.com.cn"
CSINDEX_PATH = "/static/html/csindex/public/uploads/file/autofile/cons/{idx}cons.xls"
EXCHANGE_MAP = {"上海证券交易所": "SH", "深圳证券交易所": "SZ", "北京证券交易所": "BJ"}


def _norm_code(raw_code: str, exchange: str | None) -> str | None:
    c = str(raw_code).strip().zfill(6)
    if not c.isdigit():
        return None
    pre = EXCHANGE_MAP.get(str(exchange).strip()) if exchange else None
    if pre is None:
        if c.startswith(("6", "9")):
            pre = "SH"
        elif c.startswith(("0", "2", "3")):
            pre = "SZ"
        elif c.startswith(("4", "8", "92")):
            pre = "BJ"
        else:
            return None
    return pre + c


def cmd_fetch_constituents(args) -> dict:
    ensure_dirs()
    fetched_at = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.csindex.com.cn/"}
    rows, per_index = [], {}
    for idx in INDEXES:
        r = http_get(CSINDEX_HOST, CSINDEX_PATH.format(idx=idx), headers=headers,
                     schemes=("https", "http"))
        raw_path = os.path.join(RAW_DIR, f"{idx}cons.xls")
        with open(raw_path, "wb") as fh:
            fh.write(r.content)
        df = pd.read_excel(raw_path, engine="xlrd", dtype=str)
        cols = {c: c for c in df.columns}
        def pick(key):
            for c in cols:
                if key in str(c):
                    return c
            return None
        c_date, c_code = pick("日期"), pick("成份券代码")
        c_name, c_ex = pick("成份券名称"), pick("交易所")
        if c_name is not None and "英文" in str(c_name):
            c_name = None
        for c in df.columns:  # 中文名列（排除英文名列）
            if "成份券名称" in str(c) and "英文" not in str(c):
                c_name = c
        as_of = str(df[c_date].iloc[0]).strip() if c_date else ""
        as_of = f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:8]}" if len(as_of) == 8 and as_of.isdigit() else as_of
        codes = []
        for _, row in df.iterrows():
            code = _norm_code(row[c_code], row[c_ex] if c_ex else None)
            if code is None:
                continue
            codes.append(code)
            rows.append({"index_code": idx, "code": code,
                         "name": str(row[c_name]).strip() if c_name else "",
                         "as_of_date": as_of, "downloaded_at": fetched_at})
        per_index[idx] = {"n": len(codes), "as_of_date": as_of, "codes": codes,
                          "bytes": len(r.content)}
        log(f"成分股 {idx}: {len(codes)} 只, 官方日期 {as_of}, 文件 {len(r.content)} 字节")
    cons = pd.DataFrame(rows)
    cons.to_csv(os.path.join(OUT_ROOT, "constituents_current.csv"), index=False, encoding="utf-8-sig")

    # ---- point-in-time instruments
    report = {}
    for idx in INDEXES:
        uni = INDEX_TO_UNIVERSE[idx]
        src = os.path.join(QLIB_INSTRUMENTS, f"{uni}.txt")
        hist = pd.read_csv(src, sep="\t", header=None, names=["code", "start", "end"], dtype=str)
        hist["code"] = hist["code"].str.strip().str.upper()
        hist["start"] = hist["start"].str.strip()
        hist["end"] = hist["end"].str.strip()
        live_mask = hist["end"] == QLIB_INSTRUMENTS_SENTINEL
        old_names = set(hist.loc[live_mask, "code"])
        hist.loc[live_mask, "end"] = JUNE_REBALANCE_LAST
        cur = per_index[idx]["codes"]
        new_rows = pd.DataFrame({"code": cur, "start": JUNE_REBALANCE_FIRST, "end": FAR_FUTURE})
        out = pd.concat([hist, new_rows], ignore_index=True).sort_values(["code", "start"])
        out_path = os.path.join(INSTRUMENTS_DIR, f"{uni}.txt")
        out.to_csv(out_path, sep="\t", header=False, index=False)
        cur_set = set(cur)
        added = sorted(cur_set - old_names)
        removed = sorted(old_names - cur_set)
        report[uni] = {"index_code": idx, "as_of_date": per_index[idx]["as_of_date"],
                       "n_current": len(cur_set), "n_at_2026-01-29": len(old_names),
                       "n_added": len(added), "n_removed": len(removed),
                       "added_sample": added[:15], "removed_sample": removed[:15],
                       "segments_written": int(len(out))}
        log(f"{uni}: 当前 {len(cur_set)} 只 / 2026-01-29 名单 {len(old_names)} 只 / "
            f"新进 {len(added)} 只 / 调出 {len(removed)} 只")
    with open(os.path.join(OUT_ROOT, "constituents_check.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


# ================================================================ C. fetch-daily-ext

EXT_COLUMNS = ["date", "code", "open", "high", "low", "close", "volume", "amount",
               "pct_chg", "turnover_rate", "prev_close_raw", "pct_chg_adj",
               "prev_close_adj", "adj_event", "source"]


def _ext_path(code: str) -> str:
    return os.path.join(DAILY_EXT_DIR, f"{code}.parquet")


def _ext_is_fresh(code: str, target: pd.Timestamp | None) -> bool:
    p = _ext_path(code)
    if not os.path.exists(p):
        return False
    if target is None:
        return True
    try:
        d = pq.read_table(p, columns=["date"]).to_pandas()
    except Exception:  # noqa: BLE001
        return False
    if len(d) == 0:
        return False
    last = pd.Timestamp(d["date"].max()).normalize()
    if last < target.normalize():
        return False
    # 当日 bar 如果是收盘前写的，那是半截 bar，必须重取
    today = pd.Timestamp(now_bj().date())
    if last == today:
        written = datetime.fromtimestamp(os.path.getmtime(p), BJ_TZ)
        if not session_closed(written):
            return False
    return True


def round_half_up_cent(x):
    """四舍五入到分，逢五进一。

    不能直接 np.round（银行家舍入），也不能直接 floor(x*100+0.5)——
    4.685*100 在 float64 里是 468.49999999999994，会错误地舍成 4.68。
    先把 x*100 收敛到 6 位小数消掉表示误差，再进位。
    """
    a = np.asarray(x, dtype=float)
    scaled = np.round(np.abs(a) * 100.0, 6)
    return np.floor(scaled + 0.5) / 100.0 * np.sign(a)


def _volume_multiplier(raw: pd.DataFrame) -> float:
    """判定成交量要乘多少才是“股”：用 成交额/成交量 必须接近当日收盘价来定。"""
    m = raw[(raw["volume_lots"] > 0) & (raw["amount"] > 0) & (raw["close"] > 0)]
    if len(m) == 0:
        return 100.0
    best, best_err = 100.0, np.inf
    for cand in (100.0, 1.0):
        vwap = m["amount"] / (m["volume_lots"] * cand)
        err = float(np.nanmedian((vwap / m["close"] - 1.0).abs()))
        if np.isfinite(err) and err < best_err:
            best, best_err = cand, err
    return best if best_err < 0.3 else 100.0


def fetch_one_ext(code: str, beg: str, end: str, with_qfq: bool = False,
                  include_today: bool = False) -> tuple[str, str, int]:
    """拉一只股票的增量日线，落一个 parquet。

    主源腾讯（一次请求拿到开高低收/成交量/成交额/换手率），东方财富作兜底。

    “调整后前收盘价”（= vendor CSV 的“前收盘价”）必须另算，因为两个源给的
    涨跌幅都是对**未复权**前收盘算的，不含除息调整：

    主口径 —— 除权除息事件表（精确，无舍入噪声）：
        prev_close_adj[t] = (raw_close[t-1] - 派息/10) / (1 + 送转/10)
    非事件日：prev_close_adj[t] = raw_close[t-1]
    兜底 —— 前复权序列隐含的跳变（抓配股等事件表里没有的情形），仅 with_qfq=True 时启用：
        G[t] = (1+pct_qfq[t]/100)/(1+pct_raw[t]/100)，|G-1| > 1% 时改用 raw_close[t-1]/G[t]
        （前复权价只有两位小数，逐日用 G 会引入约 0.05% 的假收益，所以只在跳变时用）
    """
    src = "tencent"
    raw = None
    try:
        raw = fetch_kline_tencent(code)
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None or len(raw) == 0:
        src = "eastmoney"
        raw = fetch_kline(secid(code), 0, beg, end)
        if raw is None or len(raw) == 0:
            return code, "empty", 0
        raw = raw.rename(columns={})
        raw["amount"] = raw["amount"]
    # 多留一段历史用于算首日前收盘，最后再裁到 beg
    beg_ts = pd.Timestamp(f"{beg[:4]}-{beg[4:6]}-{beg[6:]}") if len(beg) == 8 else pd.Timestamp(beg)
    raw = raw.sort_values("date").reset_index(drop=True)

    # 成交量单位：主板/创业板腾讯给“手”，科创板(688/689)给“股”。
    # 不写死板块，用 成交额/成交量 必须落在当日价格区间内 来判定倍数。
    mult = _volume_multiplier(raw)
    df = pd.DataFrame({
        "date": raw["date"], "code": code,
        "open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"],
        "volume": raw["volume_lots"] * mult,       # -> 股（已与 vendor CSV 核对）
        "amount": raw["amount"],
        "turnover_rate": raw.get("turnover_rate"),
    })
    df["prev_close_raw"] = df["close"].shift(1)
    df["pct_chg"] = (df["close"] / df["prev_close_raw"] - 1.0) * 100.0

    # 前复权序列：只用来**发现**事件表里没有的除权（配股、分拆等），以及给出交易所口径的除权参考价。
    # 腾讯 qfq 是等比前复权，qfq_close/raw_close 是分段常数，跳变处就是除权日。
    ratio = None
    if with_qfq:
        try:
            q = fetch_kline_tencent(code, mode="qfq")
        except Exception:  # noqa: BLE001
            q = None
        if q is not None and len(q):
            qmap = q.set_index("date")["close"]
            qc = df["date"].map(qmap).astype(float)
            ratio = (qc / df["close"]).to_numpy(dtype=float)
    df["pct_chg_adj"] = df["pct_chg"]

    prev_raw = df["prev_close_raw"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    prev_adj = prev_raw.copy()
    events = [""] * len(df)
    dates = list(df["date"])
    for i in range(len(df)):
        pr = prev_raw[i]
        if not np.isfinite(pr) or pr <= 0:
            continue
        # 优先用前复权序列的跳变：它直接给出交易所口径的除权参考价（含交易所自己的取整），
        # 实测与 vendor 逐分一致；事件表的派息/送转比例偶尔会和实际除权比例对不上。
        # 事件表兜底：小额分红引起的跳变淹没在前复权价的两位小数舍入里，只有事件表能识别。
        used = False
        if ratio is not None and i > 0:
            r0, r1 = ratio[i - 1], ratio[i]
            if np.isfinite(r0) and np.isfinite(r1) and r0 > 0 and r1 > 0:
                jump = r1 / r0
                # 前复权价只有两位小数，非除权日的跳变全是舍入噪声；容差按价位放大
                qa, qb = abs(pr * r0), abs(close[i] * r1)
                tol = 3.0 * 0.005 * (1.0 / max(qa, 1e-6) + 1.0 / max(qb, 1e-6)) + 1e-4
                if abs(jump - 1.0) > max(tol, 2e-3):
                    # 前一日原始收盘换算到今天的价格尺度 = 交易所口径的除权参考价
                    prev_adj[i] = pr * r0 / r1
                    events[i] = "qfq_jump"
                    used = True
        if not used:
            ev = _EVENTS.get((code, pd.Timestamp(dates[i])))
            if ev is not None:
                cash, bonus = ev
                prev_adj[i] = (pr - cash / 10.0) / (1.0 + bonus / 10.0)
                events[i] = "event_table"
        if events[i]:
            df.loc[df.index[i], "pct_chg_adj"] = (close[i] / prev_adj[i] - 1.0) * 100.0 if prev_adj[i] else np.nan
    # 交易所公布的除权参考价是**两位小数**（价格最小变动单位 0.01），vendor CSV 的
    # “前收盘价”就是这个值。事件表算出来的理论价要取整到两位，否则会和 vendor 差半分钱。
    ev_mask = np.array([bool(x) for x in events])
    if ev_mask.any():
        prev_adj = np.where(ev_mask & np.isfinite(prev_adj), round_half_up_cent(prev_adj), prev_adj)
    df["prev_close_adj"] = prev_adj
    df["adj_event"] = events
    df["source"] = f"{src}:vol_x{int(mult)}"
    df = drop_unsettled(df, include_today=include_today)
    df = df[df["date"] >= beg_ts].reset_index(drop=True)
    if len(df) == 0:
        return code, "empty", 0
    df = df[EXT_COLUMNS]
    df.to_parquet(_ext_path(code), index=False)
    return code, "ok", len(df)


def cmd_fetch_daily_ext(args) -> dict:
    ensure_dirs()
    codes = _target_codes(args)
    target = market_last_date()
    beg_dash = args.beg or EXT_START
    beg = beg_dash.replace("-", "")
    end = today_str()
    ev = load_events(min(beg_dash, EXT_START), now_bj().strftime("%Y-%m-%d"), force=args.force)
    log(f"除权除息事件表: {len(ev)} 条 ({min(beg_dash, EXT_START)} ~ 今天)")
    todo = codes if args.force else [c for c in codes if not _ext_is_fresh(c, target)]
    log(f"fetch-daily-ext: 共 {len(codes)} 只, 待取 {len(todo)} 只, 区间 {beg}~{end}, "
        f"目标最新交易日 {target.date() if target is not None else '未知'}")

    ok, empty, failed = [], [], []
    workers = max(1, min(4, args.workers))
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_one_ext, c, beg, end, args.with_qfq, args.include_today): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            done += 1
            try:
                _, status, n = fut.result()
                (ok if status == "ok" else empty).append(c)
            except Exception as exc:  # noqa: BLE001
                failed.append({"code": c, "error": f"{type(exc).__name__}: {exc}"[:200]})
            if done % 100 == 0:
                el = time.time() - t0
                eta = (len(todo) - done) / max(done / el, 1e-9) / 60.0
                log(f"  进度 {done}/{len(todo)} 成功 {len(ok)} 空 {len(empty)} 失败 {len(failed)} "
                    f"| {done / el:.2f} 只/s 预计剩余 {eta:.0f} 分钟 | 限速 {_limiter_report()}")
    log(f"fetch-daily-ext 完成: 成功 {len(ok)}, 空 {len(empty)}, 失败 {len(failed)}, "
        f"限速统计 {_limiter_report()}")
    # 失败的再补一轮（配额恢复后通常能过）
    if failed:
        retry = [f["code"] for f in failed]
        log(f"对 {len(retry)} 只失败代码做第二轮补拉")
        failed = []
        with ThreadPoolExecutor(max_workers=max(1, workers // 2)) as pool:
            futs = {pool.submit(fetch_one_ext, c, beg, end, args.with_qfq, args.include_today): c for c in retry}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    _, status, n = fut.result()
                    (ok if status == "ok" else empty).append(c)
                except Exception as exc:  # noqa: BLE001
                    failed.append({"code": c, "error": f"{type(exc).__name__}: {exc}"[:200]})
        log(f"第二轮后: 成功 {len(ok)}, 空 {len(empty)}, 失败 {len(failed)}")

    # ---- 实时快照（批量列表接口，缺的再单只补；东方财富列表节点不通时整体改用腾讯批量行情）
    try:
        snap = fetch_snapshot_all()
    except RuntimeError as exc:
        log(f"东方财富列表接口失败（{str(exc)[:80]}），改用腾讯批量行情")
        snap = fetch_snapshot_tencent(codes)
    have = set(snap["code"]) if len(snap) else set()
    missing = [c for c in codes if c not in have]
    log(f"快照批量接口返回 {len(snap)} 只, 缺 {len(missing)} 只, 逐只补拉")
    extra = []
    for c in missing[:1500]:
        try:
            one = fetch_snapshot_one(c)
            if one:
                extra.append(one)
        except Exception:  # noqa: BLE001
            pass
    if extra:
        snap = pd.concat([snap, pd.DataFrame(extra)], ignore_index=True).drop_duplicates("code")
    snap = snap[snap["code"].isin(codes)].reset_index(drop=True)
    snap["fetched_at"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    snap = snap[["code", "name", "total_shares", "float_shares",
                 "total_market_cap", "float_market_cap", "fetched_at"]]
    snap.to_parquet(os.path.join(OUT_ROOT, "snapshot.parquet"), index=False)
    log(f"snapshot.parquet 写入 {len(snap)} 只")

    check = {"skipped": True} if getattr(args, "no_check", False) else _check_ext_vs_vendor(codes)
    report = {"n_codes": len(codes), "n_fetched_ok": len(ok), "n_empty": len(empty),
              "n_failed": len(failed), "failed_sample": failed[:20],
              "n_snapshot": int(len(snap)), "n_snapshot_missing": len(codes) - int(len(snap)),
              "overlap_check": check}
    with open(os.path.join(OUT_ROOT, "daily_ext_check.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def _check_ext_vs_vendor(codes: list[str]) -> dict:
    """重叠区间（7/1-7/17）逐日核对开高低收、前收盘价、成交量单位、成交额。"""
    n_checked = 0
    bad_ohlc, bad_prev, bad_vol, bad_amt, no_overlap = [], [], [], [], []
    vol_ratios, prev_rels = [], []
    n_event_rows = n_fallback_rows = 0
    src_counts: dict[str, int] = {}
    for code in codes:
        p = _ext_path(code)
        if not os.path.exists(p):
            continue
        try:
            e = pd.read_parquet(p)
            v = read_vendor(code, start=EXT_START)
        except Exception:  # noqa: BLE001
            continue
        if len(e) and "source" in e.columns:
            s0 = str(e["source"].iloc[0])
            src_counts[s0] = src_counts.get(s0, 0) + 1
        if v is None or len(v) == 0 or len(e) == 0:
            continue
        m = e.merge(v, on="date", suffixes=("_e", "_v"))
        m = m[m["close_v"].notna() & (m["close_v"] > 0)]
        if len(m) == 0:
            no_overlap.append(code)
            continue
        n_checked += 1
        if "adj_event" in e.columns:
            n_event_rows += int((e["adj_event"] == "event_table").sum())
            n_fallback_rows += int((e["adj_event"] == "qfq_jump").sum())
        rel = lambda a, b: ((a - b).abs() / b.abs().clip(lower=1e-9)).max()  # noqa: E731
        d_ohlc = max(rel(m["open_e"], m["open_v"]), rel(m["high_e"], m["high_v"]),
                     rel(m["low_e"], m["low_v"]), rel(m["close_e"], m["close_v"]))
        if d_ohlc > 1e-4:
            bad_ohlc.append(code)
        mm = m[(m["prev_close_adj_v"] > 0) & m["prev_close_adj_e"].notna()]
        if len(mm):
            pr = rel(mm["prev_close_adj_e"], mm["prev_close_adj_v"])
            prev_rels.append(float(pr))
            if pr > 1e-3:
                bad_prev.append(code)
        mv = m[(m["volume_v"] > 0) & (m["volume_e"] > 0)]
        if len(mv):
            r = (mv["volume_v"] / mv["volume_e"])
            vol_ratios.append(float(r.median()))
            if (r - 1.0).abs().max() > 1e-4:
                bad_vol.append(code)
        ma = m[(m["amount_v"] > 0) & (m["amount_e"] > 0)]
        if len(ma) and rel(ma["amount_e"], ma["amount_v"]) > 1e-4:
            bad_amt.append(code)
    return {
        "n_checked": n_checked,
        "source_breakdown": src_counts,
        "volume_ratio_vendor_over_ext": {
            "median": float(np.median(vol_ratios)) if vol_ratios else None,
            "min": float(np.min(vol_ratios)) if vol_ratios else None,
            "max": float(np.max(vol_ratios)) if vol_ratios else None,
        },
        "n_ohlc_mismatch": len(bad_ohlc), "ohlc_mismatch_sample": bad_ohlc[:20],
        "n_prev_close_mismatch": len(bad_prev), "prev_close_mismatch_sample": bad_prev[:20],
        "prev_close_rel_diff": _q(prev_rels, "prev_close_rel"),
        "n_rows_adjusted_by_event_table": n_event_rows,
        "n_rows_adjusted_by_qfq_jump": n_fallback_rows,
        "n_volume_mismatch": len(bad_vol), "volume_mismatch_sample": bad_vol[:20],
        "n_amount_mismatch": len(bad_amt), "amount_mismatch_sample": bad_amt[:20],
        "n_no_overlap": len(no_overlap), "no_overlap_sample": no_overlap[:20],
    }


# ================================================================ D. build-source


def _snapshot_map() -> dict[str, dict]:
    p = os.path.join(OUT_ROOT, "snapshot.parquet")
    if not os.path.exists(p):
        return {}
    df = pd.read_parquet(p)
    return {r["code"]: r for r in df.to_dict("records")}


def build_one_source(code: str, snap: dict | None) -> dict:
    v = read_vendor(code, start=VENDOR_START)
    ext_p = _ext_path(code)
    e = pd.read_parquet(ext_p) if os.path.exists(ext_p) else None

    parts = []
    if v is not None and len(v):
        parts.append(pd.DataFrame({
            "date": v["date"], "open": v["open"], "high": v["high"], "low": v["low"],
            "close": v["close"], "volume": v["volume"], "amount": v["amount"],
            "prev_close_adj": v["prev_close_adj"], "stock_name": v["stock_name"].astype(str),
            "float_market_cap": v["float_market_cap"], "total_market_cap": v["total_market_cap"],
            "src": "vendor",
        }))
    vendor_last = v["date"].max() if (v is not None and len(v)) else None
    if e is not None and len(e):
        e2 = e if vendor_last is None else e[e["date"] > vendor_last]
        if len(e2):
            name = (snap or {}).get("name")
            # 除权日的前收盘价取整到两位（与交易所公布的除权参考价、vendor CSV 同口径）
            if "adj_event" in e2.columns:
                _m = e2["adj_event"].fillna("").astype(str).str.len() > 0
                if _m.any():
                    e2 = e2.copy()
                    e2.loc[_m, "prev_close_adj"] = round_half_up_cent(e2.loc[_m, "prev_close_adj"])
            parts.append(pd.DataFrame({
                "date": e2["date"], "open": e2["open"], "high": e2["high"], "low": e2["low"],
                "close": e2["close"], "volume": e2["volume"], "amount": e2["amount"],
                "prev_close_adj": e2["prev_close_adj"],
                "stock_name": pd.Series([name] * len(e2), index=e2.index, dtype=object),
                "float_market_cap": np.nan, "total_market_cap": np.nan,
                "src": "ext",
            }))
    if not parts:
        return {"code": code, "status": "no_data"}
    df = pd.concat(parts, ignore_index=True).sort_values("date")
    df = df.drop_duplicates("date", keep="first").reset_index(drop=True)
    df = df[df["close"].notna() & (df["close"] > 0)]
    if len(df) == 0:
        return {"code": code, "status": "no_valid_rows"}
    df = df.reset_index(drop=True)

    # ---- 复权因子：adj[T]=1, adj[t] = adj[t+1] * prev_close_adj[t+1] / close[t]
    close = df["close"].to_numpy(dtype=float)
    prev_adj = df["prev_close_adj"].to_numpy(dtype=float)
    n = len(df)
    adj = np.ones(n, dtype=float)
    for i in range(n - 2, -1, -1):
        pa, c = prev_adj[i + 1], close[i]
        step = (pa / c) if (np.isfinite(pa) and pa > 0 and np.isfinite(c) and c > 0) else 1.0
        if not np.isfinite(step) or step <= 0:
            step = 1.0
        adj[i] = adj[i + 1] * step
    df["qfq_ratio"] = adj

    # 首日 prev_close_adj 若缺失，用当日开盘价兜底（与参考目录一致的做法：不参与收益计算）
    first_pa = df.loc[df.index[0], "prev_close_adj"]
    if not np.isfinite(first_pa) or first_pa <= 0:
        df.loc[df.index[0], "prev_close_adj"] = df.loc[df.index[0], "open"]

    # ---- 股本
    fs = df["float_market_cap"] / df["close"]
    ts = df["total_market_cap"] / df["close"]
    is_ext = (df["src"] == "ext").to_numpy()
    shares_jump = None
    if is_ext.any():
        last_v = (~is_ext).nonzero()[0]
        base_fs = fs.iloc[last_v[-1]] if len(last_v) else np.nan
        base_ts = ts.iloc[last_v[-1]] if len(last_v) else np.nan
        fs = fs.copy(); ts = ts.copy()
        # 沿用 vendor 最后一日股本，但送转（10 送 X / 10 转 X）会立即改变股本，用事件表精确还原
        mult = np.ones(len(df), dtype=float)
        run = 1.0
        for i in range(len(df)):
            if is_ext[i]:
                ev = _EVENTS.get((code, pd.Timestamp(df["date"].iloc[i])))
                if ev is not None and ev[1]:
                    run *= (1.0 + ev[1] / 10.0)
            mult[i] = run
        fs.loc[is_ext] = base_fs * mult[is_ext]
        ts.loc[is_ext] = base_ts * mult[is_ext]
        if snap:
            s_fs, s_ts = snap.get("float_shares"), snap.get("total_shares")
            last_i = df.index[-1]
            carried_fs, carried_ts = fs.loc[last_i], ts.loc[last_i]
            if np.isfinite(s_fs) and s_fs > 0:
                if np.isfinite(carried_fs) and carried_fs > 0 and abs(s_fs / carried_fs - 1) > 0.05:
                    shares_jump = {"code": code, "field": "float_shares",
                                   "carried": float(carried_fs), "snapshot": float(s_fs),
                                   "rel": float(s_fs / carried_fs - 1)}
                fs.loc[last_i] = s_fs
            if np.isfinite(s_ts) and s_ts > 0:
                if (np.isfinite(carried_ts) and carried_ts > 0
                        and abs(s_ts / carried_ts - 1) > 0.05 and shares_jump is None):
                    shares_jump = {"code": code, "field": "total_shares",
                                   "carried": float(carried_ts), "snapshot": float(s_ts),
                                   "rel": float(s_ts / carried_ts - 1)}
                ts.loc[last_i] = s_ts
    df["float_shares"] = fs.to_numpy()
    df["total_shares"] = ts.to_numpy()

    out = pd.DataFrame({
        "date": df["date"].astype("datetime64[ns]"),
        "code": code,
        "open": df["open"] * df["qfq_ratio"],
        "high": df["high"] * df["qfq_ratio"],
        "low": df["low"] * df["qfq_ratio"],
        "close": df["close"] * df["qfq_ratio"],
        "volume": df["volume"].astype(float),          # 原始股数（与参考目录一致，未复权）
        "amount": df["amount"].astype(float),
        "float_shares": df["float_shares"],
        "total_shares": df["total_shares"],
        "qfq_ratio": df["qfq_ratio"],
        "vwap_qfq": np.where(df["volume"] > 0, df["amount"] / df["volume"].replace(0, np.nan), np.nan) * df["qfq_ratio"],
        "prev_close": df["prev_close_adj"] * df["qfq_ratio"],
        "raw_open": df["open"].astype(float),
        "raw_prev_close": df["prev_close_adj"].astype(float),
        "stock_name": df["stock_name"],
        "float_market_cap": df["close"] * df["float_shares"],
        "total_market_cap": df["close"] * df["total_shares"],
    })
    for c in MINUTE_COLUMNS:
        out[c] = np.nan
    nm = out["stock_name"].fillna("").astype(str)
    out["is_st"] = nm.str.upper().str.contains("ST", regex=False)
    out["is_delisting"] = nm.str.contains("退", regex=False)
    out["stock_name"] = out["stock_name"].astype("string")
    out["is_st"] = out["is_st"].astype("boolean")
    out["is_delisting"] = out["is_delisting"].astype("boolean")
    out = out[SOURCE_COLUMNS]
    out.to_parquet(os.path.join(DAILY_SOURCE_DIR, f"{code}.parquet"), index=False)
    return {"code": code, "status": "ok", "rows": int(len(out)),
            "start": str(out["date"].min().date()), "end": str(out["date"].max().date()),
            "n_ext": int(is_ext.sum()), "shares_jump": shares_jump}


def cmd_build_source(args) -> dict:
    ensure_dirs()
    codes = _target_codes(args)
    snapmap = _snapshot_map()
    ev_path = os.path.join(OUT_ROOT, "dividend_events.parquet")
    if os.path.exists(ev_path):
        _install_events(pd.read_parquet(ev_path))
        log(f"载入除权除息事件表 {len(_EVENTS)} 条")
    results, failures = [], []
    workers = max(1, min(4, args.workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(build_one_source, c, snapmap.get(c)): c for c in codes}
        done = 0
        for fut in as_completed(futs):
            c = futs[fut]
            done += 1
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": c, "error": f"{type(exc).__name__}: {exc}"[:200]})
            if done % 500 == 0:
                log(f"  build 进度 {done}/{len(codes)}")
    ok = [r for r in results if r.get("status") == "ok"]
    jumps = [r["shares_jump"] for r in ok if r.get("shares_jump")]
    ends = pd.Series([r["end"] for r in ok])
    log(f"build-source: 成功 {len(ok)}/{len(codes)}, 失败 {len(failures)}, 股本跳变 {len(jumps)} 只")

    manifest = {
        "source_root": DAILY_SOURCE_DIR,
        "built_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range": {"start": VENDOR_START,
                       "end": str(ends.max()) if len(ends) else None},
        "stock_filter": {"exchanges": ["SH", "SZ"], "code_pattern": CODE_RE.pattern},
        "stock_count": len(ok),
        "row_count": int(sum(r["rows"] for r in ok)),
        "available_fields": [c for c in SOURCE_COLUMNS if c not in ("date", "code")],
        "inputs": {"vendor_csv": VENDOR_DIR, "daily_ext": DAILY_EXT_DIR,
                   "snapshot": os.path.join(OUT_ROOT, "snapshot.parquet")},
        "minute_columns": {c: "全部为 NaN（本管线不产出分钟数据）" for c in MINUTE_COLUMNS},
        "conventions": {
            "prices": "open/high/low/close/vwap_qfq/prev_close 为前复权（锚定各股最后一个交易日）",
            "raw": "raw_open/raw_prev_close 为未复权原始价；raw_prev_close 含除权除息调整",
            "volume": "原始股数（未复权），与参考目录一致",
            "amount": "元",
            "qfq_ratio": "前复权因子，最后一日 = 1",
        },
        "shares_jump_codes": jumps,
        "failures": failures[:50],
    }
    with open(os.path.join(DAILY_SOURCE_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    check = _check_source_vs_reference(ok, args.check_sample)
    report = {"n_codes": len(codes), "n_ok": len(ok), "n_failed": len(failures),
              "failures_sample": failures[:20],
              "end_date_mode": str(ends.mode().iloc[0]) if len(ends) else None,
              "n_shares_jump": len(jumps), "shares_jump_sample": jumps[:20],
              "consistency_check": check}
    with open(os.path.join(OUT_ROOT, "build_source_check.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def _q(a, name):
    a = np.asarray([x for x in a if np.isfinite(x)], dtype=float)
    if a.size == 0:
        return {name: None}
    return {f"{name}_median": float(np.median(a)), f"{name}_p99": float(np.percentile(a, 99)),
            f"{name}_max": float(a.max()), f"{name}_n": int(a.size)}


def _check_source_vs_reference(ok: list[dict], sample_n: int) -> dict:
    """与参考冻结源在 2024-01-01 ~ 2025-12-31 重叠区间对比。"""
    cand = [r["code"] for r in ok if os.path.exists(os.path.join(REF_SOURCE, f"{r['code']}.parquet"))]
    rng = random.Random(20260916)
    sample = rng.sample(cand, min(sample_n, len(cand)))
    ret_diff, price_ratio_cv, vol_ratio, fs_diff, st_rate, n_rows_match = [], [], [], [], [], []
    missing_days, raw_open_diff, raw_prev_diff, ret_raw_diff, del_rate = [], [], [], [], []
    lo, hi = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")
    for code in sample:
        try:
            a = pd.read_parquet(os.path.join(DAILY_SOURCE_DIR, f"{code}.parquet"))
            b = pd.read_parquet(os.path.join(REF_SOURCE, f"{code}.parquet"))
        except Exception:  # noqa: BLE001
            continue
        a = a[(a["date"] >= lo) & (a["date"] <= hi)]
        b = b[(b["date"] >= lo) & (b["date"] <= hi)]
        if len(a) == 0 or len(b) == 0:
            continue
        m = a.merge(b, on="date", suffixes=("_a", "_b"))
        if len(m) < 20:
            continue
        n_rows_match.append(len(m) / max(len(b), 1))
        missing_days.append(len(b) - len(m))
        ra = m["close_a"] / m["prev_close_a"] - 1
        rb = m["close_b"] / m["prev_close_b"] - 1
        ret_diff.append(float((ra - rb).abs().max()))
        pr = (m["close_a"] / m["close_b"]).replace([np.inf, -np.inf], np.nan).dropna()
        if len(pr) > 5:
            price_ratio_cv.append(float(pr.std() / abs(pr.mean())))
        vb = m["volume_b"].replace(0, np.nan)
        vr = (m["volume_a"] / vb).dropna()
        if len(vr):
            vol_ratio.append(float((vr - 1).abs().max()))
        fb = m["float_shares_b"].replace(0, np.nan)
        fd = ((m["float_shares_a"] - m["float_shares_b"]).abs() / fb).dropna()
        if len(fd):
            fs_diff.append(float(fd.max()))
        sa = m["is_st_a"].fillna(False).astype(bool)
        sb = m["is_st_b"].fillna(False).astype(bool)
        st_rate.append(float((sa == sb).mean()))
        da = m["is_delisting_a"].fillna(False).astype(bool)
        db = m["is_delisting_b"].fillna(False).astype(bool)
        del_rate.append(float((da == db).mean()))
        # 原始价层面的对比（不受两边前复权舍入噪声影响，是真正的口径判据）
        ob = m["raw_open_b"].replace(0, np.nan)
        raw_open_diff.append(float(((m["raw_open_a"] - m["raw_open_b"]).abs() / ob).max()))
        pb = m["raw_prev_close_b"].replace(0, np.nan)
        raw_prev_diff.append(float(((m["raw_prev_close_a"] - m["raw_prev_close_b"]).abs() / pb).max()))
        rra = m["close_a"] / m["qfq_ratio_a"] / m["raw_prev_close_a"] - 1
        rrb = m["close_b"] / m["qfq_ratio_b"] / m["raw_prev_close_b"] - 1
        ret_raw_diff.append(float((rra - rrb).abs().max()))
    out = {"n_sampled": len(sample), "n_compared": len(ret_diff), "window": "2024-01-01~2025-12-31"}
    out.update(_q(ret_diff, "daily_return_abs_diff"))
    out.update(_q(price_ratio_cv, "qfq_price_ratio_cv"))
    out.update(_q(vol_ratio, "volume_rel_diff"))
    out.update(_q(fs_diff, "float_shares_rel_diff"))
    out.update(_q(st_rate, "is_st_match_rate"))
    out.update(_q(del_rate, "is_delisting_match_rate"))
    out.update(_q(n_rows_match, "row_coverage"))
    out.update(_q(missing_days, "missing_days_vs_reference"))
    out.update(_q(raw_open_diff, "raw_open_rel_diff"))
    out.update(_q(raw_prev_diff, "raw_prev_close_rel_diff"))
    out.update(_q(ret_raw_diff, "raw_return_abs_diff"))
    return out


# ================================================================ E. update


def cmd_update(args) -> dict:
    ensure_dirs()
    started = now_bj()
    steps = {}
    log("=== update: 1/4 fetch-index ===")
    steps["fetch_index"] = cmd_fetch_index(args)
    log("=== update: 2/4 fetch-daily-ext ===")
    # 起始日固定在 EXT_START：腾讯一次请求就返回约 140 个交易日，缩短窗口并不省时间，
    # 反而会把 daily_ext 文件截断成只剩最近几天，导致 build-source 丢中间段。
    args.beg = args.beg or EXT_START
    steps["fetch_daily_ext"] = cmd_fetch_daily_ext(args)
    log("=== update: 3/4 fetch-constituents ===")
    steps["fetch_constituents"] = cmd_fetch_constituents(args)
    log("=== update: 4/4 build-source ===")
    steps["build_source"] = cmd_build_source(args)

    asof = {
        "run_started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "run_finished_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "index_daily_last_date": steps["fetch_index"].get("last_date"),
        "daily_ext_last_date": _dir_last_date(DAILY_EXT_DIR),
        "daily_source_last_date": _dir_last_date(DAILY_SOURCE_DIR),
        "vendor_csv_asof": _vendor_asof(),
        "session_closed_at_run": session_closed(started),
        "include_today": bool(getattr(args, "include_today", False)),
        "note": ("收盘后运行，含当日 bar" if session_closed(started) or getattr(args, "include_today", False)
                 else "盘中运行，已丢弃当日未收盘的半截 bar"),
        "constituents_as_of": {k: v.get("as_of_date") for k, v in steps["fetch_constituents"].items()},
        "counts": {
            "daily_ext_files": _count_parquet(DAILY_EXT_DIR),
            "daily_source_files": _count_parquet(DAILY_SOURCE_DIR),
        },
    }
    with open(os.path.join(OUT_ROOT, "data_asof.json"), "w", encoding="utf-8") as fh:
        json.dump(asof, fh, ensure_ascii=False, indent=2)
    log("update 完成，data_asof.json 已写入")
    return {"asof": asof, "steps": steps}


def _count_parquet(d: str) -> int:
    if not os.path.isdir(d):
        return 0
    return sum(1 for f in os.listdir(d) if f.endswith(".parquet"))


def _dir_last_date(d: str) -> str | None:
    if not os.path.isdir(d):
        return None
    files = [f for f in os.listdir(d) if f.endswith(".parquet")]
    if not files:
        return None
    best = None
    for f in files[:400]:
        try:
            t = pq.read_table(os.path.join(d, f), columns=["date"]).to_pandas()
        except Exception:  # noqa: BLE001
            continue
        if len(t):
            m = pd.Timestamp(t["date"].max())
            best = m if best is None or m > best else best
    return str(best.date()) if best is not None else None


def _vendor_asof() -> str | None:
    p = os.path.join(VENDOR_DIR, "timestamp.txt")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read().strip().split(",")[0]


# ---------------------------------------------------------------- CLI


def _target_codes(args) -> list[str]:
    if args.codes:
        return [c.strip().lower() for c in args.codes.split(",") if c.strip()]
    codes = vendor_codes()
    if args.limit:
        codes = codes[:args.limit]
    return codes


def main():
    _stdout_utf8()
    ap = argparse.ArgumentParser(description="A 股比赛策略日频数据补齐管线")
    ap.add_argument("command", choices=["fetch-index", "fetch-constituents", "fetch-daily-ext",
                                        "build-source", "update"])
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只股票")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔的代码列表，如 sh600000,sz000001")
    ap.add_argument("--force", action="store_true", help="忽略断点续跑")
    ap.add_argument("--workers", type=int, default=4, help="线程数（上限 4）")
    ap.add_argument("--beg", type=str, default="", help="增量起始日 YYYY-MM-DD")
    ap.add_argument("--check-sample", dest="check_sample", type=int, default=200,
                    help="build-source 一致性检验抽样只数")
    ap.add_argument("--rate", type=float, default=10.0, help="每秒最大请求数（自适应上限）")
    ap.add_argument("--no-qfq-check", dest="with_qfq", action="store_false", default=True,
                    help="关闭前复权交叉校验（省一半请求，但会漏掉除权除息事件表没覆盖的除权）")
    ap.add_argument("--no-check", dest="no_check", action="store_true",
                    help="跳过与 vendor 的重叠区间核对（日更时省 3-5 分钟）")
    ap.add_argument("--include-today", dest="include_today", action="store_true",
                    help="盘中运行时也保留当日未收盘的半截 K 线（默认丢弃）")
    args = ap.parse_args()

    global _LIMITER
    _LIMITER = RateLimiter(max(0.5, min(20.0, args.rate)))

    fn = {"fetch-index": cmd_fetch_index, "fetch-constituents": cmd_fetch_constituents,
          "fetch-daily-ext": cmd_fetch_daily_ext, "build-source": cmd_build_source,
          "update": cmd_update}[args.command]
    t0 = time.time()
    res = fn(args)
    log(f"{args.command} 用时 {time.time() - t0:.1f}s")
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

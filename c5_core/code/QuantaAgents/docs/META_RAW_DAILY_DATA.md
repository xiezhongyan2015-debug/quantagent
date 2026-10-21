# 原价日线适配器：直接 CSV 来源与固定 2019 开发窗口

已在隔离副本 revision5 实现通用的 `RawDailyData`，直接读取原始 CSV 的开、高、低、收、前收盘、成交量额，不从调整价或未验证的 `qfq_ratio` 反推价格。

固定数据试验已经完成：沿用此前 L2 的 8 只股票，加上明确选定的分红示例 `sz000001`，窗口为 **2019-06-20 至 2019-06-28**。63 个申请股票日中 **56 个通过、7 个保留拒绝**。这只是数据覆盖/字段检查；全部 `execution_valid=false`，没有策略收益计算、付费模型调用、主 `src` 修改或后续行情价格读取。

## 原价真正来自哪里

`/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/features/<code>/` 有 `open/high/low/close/volume/amount/factor/adjclose/...day.bin` 等字段。项目旧转换器 `scripts/convert_qlib_daily_to_parquet.py` 的 `load_stock_frame` 将 Qlib 连续复权价格、因子及调整量换到项目口径。该函数先通过 `np.fromfile` 读取完整特征文件，再截取日期，不能直接用于本轮严格限定行情读取边界的试验。

更关键的是，`/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025/manifest.json` 明确记录了：

```text
raw_daily.source_root =
/Users/leslie/Downloads/中证500量化60min/c5_core/data/vendor/stock-trading-data-2025-12-23N
```

转换器的 `load_raw_daily_frame` 从该目录逐股 CSV 读取原价，编码为 GB18030，跳过第一行发布方说明。第二行表头实际包含：

```text
股票代码,股票名称,交易日期,开盘价,最高价,最低价,收盘价,
前收盘价,成交量,成交额,流通市值,总市值
```

旧转换器读取了 `raw_close` 来计算股本，却没有把 `raw_close` 放入最终 `RAW_DAILY_OUTPUT_COLUMNS`。所以“现有 tradeable Parquet 没有 raw_close”是输出列裁剪导致，不能推断本地没有原始收盘价。本轮直接使用 CSV 的原始四价及前收盘列，不依赖那个被裁剪的 Parquet。

该 CSV 是转换清单明确指向的第三方历史档案，不是本次重新取得的交易所原始逐日回执。`source_origin` 保存真实路径，`source_kind=direct_unadjusted_vendor_csv` 表示字段直接来自这个原价文件，不表示供应商历史数据已经获得全面独立认证。来源的历史修订、当时可得性、股票简称/ST 标签及市场状态仍未验证。

## 公共接口与字段

实现位于：

```text
experiment_traces/meta_ashare_revision5/src/quanta_agents/raw_daily_data.py
```

```python
adapter = RawDailyData({
    "raw_root": raw_csv_directory,
    "calendar_path": qlib_calendar_file,
    "max_read_bytes": 32 * 1024 * 1024,
})
bars = adapter.load(
    codes,
    "2019-06-20",
    "2019-06-28",
    reference=existing_bounded_reference_frame,  # optional date/code/raw_open
)
```

输入代码支持 `sh600006` / `600006.SH` 两种写法，统一返回小写前缀格式。当前有证据的范围限定沪市 `sh60xxxx` 和深市 `sz00xxxx` 主板；不默默扩展到其他板块。归一化后的代码必须唯一。

| 输出 | 口径 |
|---|---|
| `date, code` | Qlib 日历中申请窗口的每个日期 × 固定代码，缺失也保留 |
| `raw_open, raw_high, raw_low, raw_close` | 原 CSV 对应四价列，绝不复权倒推 |
| `raw_prev_close` | 原 CSV 的前收盘参考，绝不简单 shift(raw_close) |
| `volume, amount` | 原 CSV 数量与金额；按股数量/人民币金额的源口径作价格范围一致性检查 |
| `stock_name` | 源当日标签；不能自动当成已验证的盘前可知名称 |
| `raw_price_text` | 保留五个原价字段的精确字符串，便于 Decimal/分tick检查及账本复核 |
| `accepted, reason_codes` | 只表示数据检查通过/拒绝；拒绝行不删除、原价数值不前向填充 |
| `available_at` | 固定 null，真实到达/可得时间未知 |
| `pit_vintage_verified, market_status_verified, execution_valid` | 全部 false |
| `is_st_name_proxy, is_delisting_name_proxy` | 仅从源名称推断的代理值，命名明确保留 proxy，不冒充独立市场状态表 |
| `source_origin, quality, reference_status` | 来源、读取前缀摘要、质量原因与旧缓存匹配状态 |

四价用 Decimal 核查正值、OHLC 高低关系和 0.01 价格步长，再提供便于计算的数值。前收盘参考可能存在小于一分的理论值，因此保留原精度，不强制把它取整为可成交价格。数量不可缺失、非有限、负数或非整数；金额不可缺失或为负。成交量额为零的关系需一致；有成交时 `amount/volume` 应落在原始最低/最高价范围内，允许 0.02 的固定数据误差容差。这个检查支持单位一致性，不证明容量。

失败行的原价/数量/金额字段为 null，`raw_price_text` 和有限的质量说明保留供排错。未上市/缺数据不会被换成另一只股票，也不会用其他日价格补上。

## 读取边界与可复现身份

读取器按原 CSV 的固定列顺序先读取身份和日期前缀。日期大于 `end_date` 时，在该行第一个价格字段之前停止；文件以无缓冲二进制流读取，价格尾部没有被提前读取或解析。早于 `start_date` 的历史行会在流式定位过程中经过，但不解析其价格字段、不进入返回数据。没有为了取得完整文件 SHA256 而读入后续行情。

这种策略依赖原文件按日期严格递增的布局。已扫描日期出现重复或倒序时整只股票拒绝；未扫描的文件后缀不被宣称已经完成排序验证。源错误或缺失保留在申请分母中。Qlib `day.txt` 的完整日期内容允许读取，未来日历条目是元数据，不是未来行情。

固定 pilot 的字节计数：

| 项 | 字节 |
|---|---:|
| 9 个原 CSV 实际流读取量 | 3,240,517 |
| Qlib 日历 | 56,320 |
| 合计 | **3,296,837** |
| 固定事故上限 | 33,554,432（32 MiB） |
| Qlib `.bin` 价格字段读取量 | **0** |

这些是适配器实际返回到 Python 的原文件字节数，不是操作系统底层物理磁盘 I/O 测量。所有有目标数据的股票只解码 7 行价格，最大日期为 2019-06-28，并在下一行 `2019-07-01,` 日期前缀结束处停止。`sh603786` 总共只读 200 字节，看到首个源日期为 2019-10-15 后就停止，没有读取其上市首日价格。

保存了原文件大小/修改时间、精确已读字节区间和已读前缀 SHA256。**没有计算或声称核验原 CSV 整个文件内容摘要。** 文件名及元数据可包含 2025 档案日期，不代表读取了 2024–2025 行情价格。

## 固定 pilot 的真实结果

冻结规则：此前 L2 样本的 SHA 选股结果完整沿用，再加明确选定的 `sz000001` 分红例。原 L2 选股计划摘要仍有绑定；本轮没有按数据完整性、收益或形态重新选股。

| 股票 | 请求日数 | 通过 | 结果 |
|---|---:|---:|---|
| sh600006 | 7 | 7 | 直接原价 CSV |
| sh603786 | 7 | 0 | 源首次出现为 2019-10-15，7 行均为 `source_begins_after_requested_date` |
| sh600653 | 7 | 7 | 直接原价 CSV |
| sh600741 | 7 | 7 | 直接原价 CSV |
| sz002296 | 7 | 7 | 直接原价 CSV |
| sz002377 | 7 | 7 | 直接原价 CSV |
| sz002576 | 7 | 7 | 直接原价 CSV |
| sz002527 | 7 | 7 | 直接原价 CSV |
| sz000001 | 7 | 7 | 其中 3 个开盘价与已冻结缓存一致 |

“源起始晚于请求日”保持数据层表述：它与该股票尚未上市的情形一致，但本模块没有把源首行日期冒充独立上市信息验收。

本轮唯一使用的参考是已冻结的 `experiment_traces/meta_cash_dividend_pilot/raw_price_projection.json`，没有重新扫描跨年的 Parquet 原价列。sz000001 的以下三个 `raw_open` 完全一致：

| 日期 | 旧缓存与本轮原 CSV |
|---|---:|
| 2019-06-24 | 13.69 |
| 2019-06-25 | 13.72 |
| 2019-06-26 | 13.27 |

其余 60 个请求行的 `reference_status=not_checked`，包括 7 个缺失行；不能将“三项一致”写成 63 项独立核验。旧缓存也来自既有原价链路，属于不同处理阶段的一致性检查，不是第二家独立供应商验证。

除息参考的具体边界也得到确认：sz000001 在 06-25 的 `raw_close=13.43`；06-26 的源 `raw_prev_close=13.29`、`raw_open=13.27`、`raw_close=13.37`。不能用前一天 13.43 简单替换 13.29，也不能把已经带当日参考调整的字段再次扣红利。现金权益与股份变动由独立公司行动账本处理。

本轮计划 SHA256：`4f4555c3d6dd36a44e761674737f03b628c2f285a12547114edd4995de07efd0`。模块 SHA256：`c006814ef5577e8ccecda705c30f3389f29f64f9ea146c687ce122c99116f7e8`。

## 可得时间及组合执行器接入

返回 DataFrame 的 `attrs['availability_contract']` 保存真实可用时间缺失，以及明确标记 `simulation_not_observed_arrival` 的经济时点模拟策略；`attrs['source_manifest']` 保存来源与已读字节证据。pilot 已将两者另行保存，避免 DataFrame 导出时丢失 attrs。

默认模拟策略将开盘价视作开盘执行时的观察值，不允许把它当成下单前已知信号；前收与名称的盘前可得性是未验证假设；高低收、量额按当日 15:05 完整日线假设供之后使用。**这些时刻不是供应商实测接收时间。** 所有真实 `available_at` 保持 null。

组合执行器可用原始开盘价进行已冻结的开盘成交模拟、用收盘价估值，并用滞后数据决定交易；当日高低价、收盘量额不能反过来决定当日开盘是否成交。前收、股票名称、ST 及涨跌停边界还需独立 PIT 和交易规则证据。数据 `accepted` 不等价于“这一日允许买卖”，更不等价于正式执行有效。

## 产物、测试与下一步

- revision5 `src/quanta_agents/raw_daily_data.py`：直接原价适配器。
- revision5 `tests/test_raw_daily_data.py`：**15 项通过，4.59 秒**。
- 根 `scripts/run_raw_daily_data_pilot.py`：固定案例运行脚本；已有完成结果时拒绝覆盖。
- `experiment_traces/meta_raw_daily_data_pilot/plan.json`：固定股票/日期、来源元数据、模块与参考身份。
- 同目录 `raw_daily_rows.json`、`raw_daily_rows.parquet`：全部 63 个申请行。
- 同目录 `source_manifest.json`、`availability_contract.json`：数据边界与经济时点假设。
- 同目录 `result.json`：通过/拒绝分母、参考状态及产物摘要。

测试覆盖精确原价/数量、结束日期后的价格字节未读、改写未来价格不影响过去值和读取前缀、晚起始/缺文件/缺日期保留、前收不等于移位收盘、价格缺失/高低矛盾/单位不符拒绝、重复日期拒绝、参考不一致拒绝、读取预算及含逗号/引号的名称解析。

这一步做得怎么样：已经补上真实原始四价的数据入口，避开旧 Parquet 丢列与复权倒推问题，并保留了原价前收、缺失股票和历史可得性的不确定性；读取实际严格止于开发窗口边界。

下一步：root 使用这些原价、已核实的公司行动和真实整数股份账完成固定组合试验，并独立检查开盘订单、现金、可卖股份与日终估值。不能用本轮 56 行数据检查通过来代替组合执行验收、策略回测或系统稳定产生高 Sharpe 的证明。

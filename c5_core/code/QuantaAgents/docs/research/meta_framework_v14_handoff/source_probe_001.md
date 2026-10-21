# v14 公司行动候选来源有界实探 001

2026-09-07，Asia/Hong_Kong；Astra/xhigh 独立工程取证。**发现一个此前本地未尝试、此次可调用的候选来源：BaoStock 对固定 `sz.000001 / 2019 / operate` 返回 1 条现金分红记录，税前每股 0.145 元及关键日期与已保存发行人公告一致。** 这只通过单事件字段可用性检查，尚未证明 2017–2021 全事件或股票池覆盖，也没有改变 `execution_valid=false`。

完整取证包位于 [meta_corporate_action_source_probe_v14](../../../experiment_traces/meta_corporate_action_source_probe_v14/)。没有修改冻结 v13、v14 产品源码或旧公司行动结果，没有查询价格、封存时期行情、回测或资金接口。

## 固定范围与先前记录

先检索 docs/scripts/src/tests 及四个既有 corporate/dividend 证据目录，未发现 Baostock 或 `query_dividend_data` 尝试。已读 [原来源可行性说明](../../META_CORPORATE_ACTION_SOURCE_FEASIBILITY.md)及原 `reviewed_event_samples.json`；没有重复原新浪等失败路线，也没有换股票或年份。

原样本为 `sz000001_2018_annual_20190626`，发行人 2019-031 实施公告。其原 PDF 重新只读核 SHA 为 `ed23cd9c216f3bfc14b808a5d1dc51de9fabc8c0d2136c7c0672dae41b62d509`，原文件未改。已冻结 [probe_plan.json](../../../experiment_traces/meta_corporate_action_source_probe_v14/probe_plan.json)，SHA `aeb08bed896b328ab8f5f9e20ba7b9f2fabbeb65aecdfaff2fda79b2d8193748`；实际冻结时刻 2026-09-06 19:17:01 UTC。早于任何 HTTP 的本地日期解析错误及旧计划草稿保存在 plan_commit/draft，错误未发出网络请求；修正 Python 3.10 的 UTC 时间解析后才开始取源。

下一网络准入截止为任务起点后 10 分钟，即 19:24:40 UTC；最后数据请求在 19:21:50 UTC 完成。本轮为 **3 次搜索、4 份外部来源获取、3 次数据 API 调用，其中分红 query 仅 1 次**。没有重试、翻页、第二股票/年份或额外参考样本。

## 官方合同与实际请求

由[官方文档入口](https://www.baostock.com/mainContent?file=pythonAPI.md)获取页面，再静读它引用的 JS，定位到该页面实际使用的 [官方 Markdown 接口](https://www.baostock.com/helpdocs/api/markdown/pythonAPI.md)。用同一公开 POST 空对象取到文档，保存原响应头、压缩 body、独立解码副本和分红合同节选。网页渲染失败的搜索摘要没有被误作 API 不可用。

官方说明与下载 SDK 的函数合同一致：`report` 指**预案公告年份**，并不等于财务报告所属年度；`operate` 指**除权除息年份**。本例虽是 2018 年度利润分配，固定查询仍为 `year="2019", yearType="operate"`。登记、实施公告、除权除息、派息和红股上市为分开的日级字段。

本地及基础 Python 原未装 BaoStock。第三次搜索取得维护者 baostock 的 [PyPI 0.9.3 发布信息](https://pypi.org/project/baostock/0.9.3/)及 wheel 内容地址、SHA；第 4 份且最后来源是该 wheel 原字节，SHA `acbd19403285bc4e254cee8297cf0e2646ae2276e5af7e549deed3988ab02293`，已核对。仅在证据包下解包，不安装或改项目环境。

静读 SDK 后使用公开默认匿名登录，未设置 API key。自有 wrapper 只记录和限制原 SDK 的 TCP 请求：连接 `public-api.baostock.com:10030` 一次，超时 10 秒、原响应最多 2 MiB，不调用会自动翻页的 `next/get_data`，不运行 SDK 示例。依次 login/query/logout 均返回 `error_code="0"`。HTTP 获取与 TCP 数据返回分别保存，不把 TCP 原包称为 HTTP 响应。

## 单事件对照结果

[原始 SDK 结果](../../../experiment_traces/meta_corporate_action_source_probe_v14/query_dividend_data_result.json)和 [cross_check.json](../../../experiment_traces/meta_corporate_action_source_probe_v14/cross_check.json)保留全部原字符串：

| 字段 | 本次返回 | 对照原公告 |
| --- | --- | --- |
| `dividCashPsBeforeTax` | `0.145` | 税前 CNY/股；与公告及每 10 股派 1.45 元叙述一致 |
| `dividPlanDate` | `2019-06-20` | 实施公告日期一致 |
| `dividRegistDate` | `2019-06-25` | 登记日期一致 |
| `dividOperateDate` | `2019-06-26` | 除息日期一致 |
| `dividPayDate` | `2019-06-26` | 公告派息安排日期一致 |
| `dividStocksPs` | `0.000000` | 每股送股数与原样本 0 一致 |
| `dividCashPsAfterTax` | `0.1305或0.145` | 保留多种账户/税项可能，不选一个数代替 |
| 转增、红股上市、预披露 | 空字符串 | 保留未知；不自动填 0、派息日或 no_event |

返回范围只有该代码、2019 除权除息年份、第 1 页 1 行；SDK 声明页大小 2000。本轮没有请求下一页，也不能从这一行证明预案/更正/撤销或全股票池事件完整。原公告的委托派发适用范围与账户盘中到账仍由原样本约束；第三方日级派息日期不能升级为特定账户实收观察。

分红 TCP 原响应 `wire_006_response.bin` 为 568 字节，SHA `9ae2ea52ecfe4484bc4f7bc2fbffef823545f2a123f80bd0e4f6661501ac879f`。官方 Markdown 原压缩 body SHA `a7f4346d1c8f2a7e04700b831bd78ed71b7dd10260e8e7f0eebf41865cad2fe4`。所有请求参数、retrieved_at、响应头、原字节及本地错误均在包内；搜索工具无法提供底层 HTTP 原字节，此限制明确记录，不能冒充已经保存了搜索引擎原响应。

## 两问自检

**这一步做得怎么样？** 已从“尚无新候选 API 实测”推进到固定旧样本的一次真实返回，六项机械字段对照通过，保存了原来源及完整调用身份；没有借漂亮样本挑股/换年。可用结论仅限现金及日期字段的本例一致性，转增空值、税后分支、完整事件覆盖和精确到账均未解决。

**下一步该做什么，如何改进？** 若后续另行授权，应先冻结一个不因结果更换的 2017–2021 小覆盖矩阵，用实施公告、更正/撤销和无事件证明逐项检查 provider 漏项，保留空结果为 unavailable/unknown；验证来源与账户时点后再谈适配器接线。本轮到此停止，不开全池下载，不把第三方字段一致当原文认证、真实执行有效或策略/架构达标。新增项目研究 Gateway、模型策略、回测和资金动作均为 0；Astra/xhigh 工程用量非零，另列且不冒称由项目研究账本计量。

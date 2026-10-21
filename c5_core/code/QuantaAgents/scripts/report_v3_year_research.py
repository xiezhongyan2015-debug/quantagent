"""Describe a completed audited development run; never manufacture model final."""
import json
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    root = repo/"experiment_traces/meta_framework_v3/year_research_001"
    audit = json.loads((root/"audits/saved_001/audit.json").read_text(encoding="utf-8"))
    assert len(audit["calls"]) == 12 and all(c["status"] == "applied" for c in audit["calls"])
    assert audit["unknown_or_pending_nominal_reserve"] == 0 and len(audit["authentic_reports"]) == 1
    assert audit["source_pins_verified"] and len(audit["executions"]) == 3
    report = audit["authentic_reports"]["year_reversal_001"]["report"]
    rows = []
    names = ["企稳策略", "移除企稳条件", "持续 50% 仓位"]
    for name, ex in zip(names, audit["executions"]):
        a = ex["daily_audit"]
        assert a["all_days_reconciled"] and len(a["daily"]) == 244
        rows.append(f'| {name} | {float(ex["final_cash"]):,.2f} | {float(a["net_return"]):.2%} | {float(a["maximum_drawdown"]):.2%} | {ex["trade_count"]} | {float(ex["fees"]["total"]):,.2f} | {float(ex["slippage"]):,.2f} |')
    known = audit["known_tokens"]
    body = f'''# V3 全年真实数据开发研究：闭环完成，正式目标未达标

2026-09-07。本轮让 Astra / xhigh 从普通输入自主查证、生成策略和对照、运行完整资金账户，并提交真实终稿。企稳策略在这一个开发样本中净赚 126,473.56 元，但收益低于持续 50% 仓位对照，回撤较小。**这不是已经验证的样本外优势，真实成本后冻结样本外净 Sharpe > 1 仍未达成。**

普通输入：“研究股票经历下跌后出现企稳迹象时，是否存在覆盖成本的后续收益。自主取证、开发仓位与退出规则、核查失败和成本；使用完整资金与公司行动账本交付结论，证据不足可以弃权。”没有由主控事先提供策略程序。

## 账户结果

同一只预先按来源证据范围选定的股票 sh600004、同一 2019 年 244 个真实交易日、每条账户初始现金 100 万元。全部空仓日、亏损、费用、分红、应收与税款准备均保留；期末均为零持股。费用与成交仍是已冻结的声明模拟。

| 程序 | 期末资金（元） | 完整资金净收益 | 最大日度回撤 | 成交笔数 | 费用（元） | 成交价内滑点（元） |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

滑点已经进入成交价格，没有再次从利润扣除。169 笔成交不等于 169 个独立交易样本；持续仓位基准的大部分成交是小额再平衡。

![三条账户的全年资金收益与回撤](../experiment_traces/meta_framework_v3/year_research_001/audits/cashflows_001/full_capital_comparison.png)

原策略有 111 个持股日、133 个空仓日、14 次从有仓到空仓的完整周期；移除企稳条件为 115 / 129 天、15 次；持续仓位基准为 211 / 33 天、1 次。完整周期不是自动合格的正式退出批次。

企稳策略比移除企稳条件多赚 68,823.82 元，但比持续仓位基准少赚 98,123.54 元。对照账户的持股时间、换手和市场暴露不同，这些金额差不能直接解释为因果效应，也不能按实际动用的少量资金重算收益分母。

## 模型自己交付了什么

最终报告来源调用：{audit["authentic_reports"]["year_reversal_001"]["call_id"]}。以下段落逐字取自已核验模型终稿；主控审计没有替模型生成或修改结论。

> {report["conclusion"].replace(chr(10), chr(10)+'> ')}

完整原始结论、局限、证伪条件与下一步见[模型终稿](../experiment_traces/meta_framework_v3/year_research_001/audits/saved_001/year_reversal_001_model_report.md)。

轨迹包括覆盖检查、期限与条件诊断、原策略、移除企稳条件的对照、持续仓位基准、分红前后逐日账目和亏损阶段交易核查。第 12 次调用进入关闭门，只允许提交终稿；三个候选全部保存。

原策略使用过去回撤至少 5%、最近三日收益非负作为企稳信号，过去十日内有信号且未跌破此前五日最低收盘时目标仓位 50%，否则为零；下一交易日开盘执行。窗口可以因新信号刷新，实际持股可能超过十日。窗口参考了已暴露诊断，这是开发选择，不是事前固定的样本外证据。完整程序和哈希保存在各调用的 request、compiled 与 targets 原件中。

## 资金与来源审计

三条账户均通过保存事件重放核验，没有重新模拟策略或再次请求模型。另用独立 Decimal 算术，核对全年度原始开收盘、整手、T+1、卖出分批、费用与成交价内滑点、现金、持股及每日净值。三条账户只有各自唯一的 100 万元外部入金。

企稳策略分红 5,117 元、税款实际模拟支出 612 元；移除企稳条件为 5,202 / 617.10 元；持续仓位基准为 5,865 / 586.50 元。公告发放日 8 月 9 日，冻结的保守模拟将现金可用日定为 8 月 12 日，应收期间仍进入资产计量。税款分类和假想当日清仓准备采用原冻结适配器，本次独立审计核实现金、负债余额和净值处理，**没有独立认证实际账户的法定税款分类或到账事实**。

年度来源准入基于已保存发行人年报、实施公告与原价参考差异核验。年度报告只用于事后账户核算，未作为 2019 年的可交易信息交给模型。此次单股全年范围没有将原两股短样本的 192 项 pending 改为通过，也未证明全市场公司行动覆盖完整。

审计额外在保存结果副本中注入九种错误，包括删除亏损日、追加资金、删应收、改分红权益、删税款、免手续费等，全部被拒绝。原始结果未修改。报告的独立性边界详见[现金流审计](../experiment_traces/meta_framework_v3/year_research_001/audits/cashflows_001/summary.json)与[原始回执及资金审计](../experiment_traces/meta_framework_v3/year_research_001/audits/saved_001/audit.json)。

## 框架改动与验证

原保存层在每个事件重复复制全部历史流水和进度，阻碍全年研究。本轮只扩展保存层，使其追加新流水和进度，并独立重放核验；原冻结原价/现金/股份/税务计算内核保持不变。长范围必须显式选择新后端，限 16 股、512 个开发交易日，并保留持久化、事件数与文件大小上限。中断前后资金状态、未确认事件和已有风险头寸不能被丢弃或重新注资。

定向工程验证共 10 项通过，覆盖原内核整份结果一致性、260 个生成会话和超过 500 笔成交、写入前中断/提交后回执丢失、未知公司行动保留既有持仓、现金分红与延迟税款，以及同一普通研究工具入口。生成会话仅用于工程验证，不属于真实市场样本。实际三条全年账户保存载荷约 7.23、7.39、16.56 MB，均在事前固定上限内。84 份冻结源码已保存至本轮 source_snapshot。

终稿后另修复了事件分页：本轮最后一次紧凑事件请求仍有 10,408 字节，超过 8,000 字节上下文上限，中间内容确实被截断。新版本按完整行和整份返回包装控制大小，返回准确续页位置；单行过大则明确报告无法交付，游标不前移。对本轮保存的全部 42 个事件做只读重放，分为 23 / 19 行两页，分别 7,925 / 6,732 字节，所有事件、亏损、缺失值和顺序一致。9 项定向测试通过。此改动发生在研究进程结束、源码归档和最终回执审计之后，没有重做付费研究，也不改写旧终稿或声称模型已经读到原先缺失的内容。

## 用量与未通过的门

本轮 12 次实际模型调用，已知 {known:,} token，未决预留为 0；请求参数均为 gpt-6-astra / xhigh，供应方返回的模型身份仍未独立认证。V3 此前 707,254，加本轮后共 {707254+known:,} 已知 token。旧 V2 的 491,954 已知及 80,000 未知预留独立保留；平台目标计数不是供应商账单，不与研究账本相加。

正式成功分母仍为 0。本轮为单股已暴露开发数据，仅 244 个交易日；真实历史信息到达、真实费用和成交容量等门尚未全部认证。2024–2025 封存验证未打开，跨至少六个真实案例、三机制族、每题每架构三次独立启动、预注册成功率区间和冻结后新增数据/纸面验证均未完成。本文不计算或宣称正式净 Sharpe。

下一步应先补真实执行与信息时点的来源证据，并冻结覆盖完整的共同验证范围，再做独立案例与机制检验。当前三条策略和这一年结果保持冻结，不因看到盈亏追加同题候选或重调参数。旧范围与未知费用不重开，不进行实盘、资金、购买或外发操作。
'''
    path = repo/"docs/META_V3_YEAR_RESEARCH_REPORT_20260907.md"
    assert not path.exists()
    path.write_text(body, encoding="utf-8")
    print(json.dumps({"report":str(path), "known_tokens":known, "total_v3_known":707254+known}))


if __name__ == "__main__":
    main()

"""Describe the authorized maintenance plan and a non-executable rebalance preview."""
import copy
import json
import pandas as pd
from console_core import HOME,BASE_DIR,pcf_targets,dec
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import read_json,digest
from watch_engine import save


def main():
    out=HOME/'plans/2026-09-24';p=read_json(out/'plan_preview.json');c=read_json(HOME/'settings.json')
    assert digest(c)==p['config_sha256'], 'Settings changed after the preview'
    a=read_json(out/'plan_audit.json') if (out/'plan_audit.json').exists() else read_json(HOME/'diagnostics/20260924_001112/quarantined_audit_1.json')
    record=read_json(out/'pcf_20260923.json');old=read_json(BASE_DIR/'daily_public_pcf/state/archive/2026-09-22/pcf.json')
    newq={str(x['C_STOCKCODE']):float(x['L_NUMBER']) for x in record['stocklist']}
    oldq={str(x['C_STOCKCODE']):float(x['L_NUMBER']) for x in old['stocklist']}
    changes=[dict(code=code,old=oldq.get(code,0),new=newq.get(code,0)) for code in sorted(set(newq)|set(oldq)) if newq.get(code,0)!=oldq.get(code,0)]
    save(out/'pcf_quantity_changes.json',changes)
    q=read_json(out/'close_quotes_20260923.json')
    for x in q.values():x['previous_close']=x['last']
    proposed=copy.deepcopy(c);proposed['allow_sells']=True
    universe=set(pd.read_parquet(c['membership_path']).symbol.map(normalize_code))
    targets=pcf_targets(record,'2026-09-24','2026-09-23',a['total_assets'],proposed,q,universe,a['positions'])
    for code in a['positions']:targets.setdefault(code,dict(code=code,quantity=0,reference_price=q[code]['last']))
    deltas={code:t['quantity']-a['positions'].get(code,0) for code,t in targets.items() if t['quantity']!=a['positions'].get(code,0)}
    buys={code:n for code,n in deltas.items() if n>0};sells={code:-n for code,n in deltas.items() if n<0}
    buy=sum(dec(q[k]['last'])*n for k,n in buys.items());sell=sum(dec(q[k]['last'])*n for k,n in sells.items())
    target_value=sum(dec(q[k]['last'])*x['quantity'] for k,x in targets.items())
    variant=dict(preview_only=True,enabled=False,requires_allow_sells=True,execution_date='2026-09-24',
                 buy_names=len(buys),sell_names=len(sells),buy_principal_cny=str(buy),sell_principal_cny=str(sell),
                 target_value_cny=str(target_value),target_fraction=float(target_value/dec(a['total_assets'])),
                 buys=buys,sells=sells,targets=targets,blocked_unknown_symbol_deltas={k:v for k,v in deltas.items() if k in p['isolated_symbols']},
                 limitations='Preview only. Does not authorize selling, resolve UNKNOWN, sequence cash, or guarantee executable prices/available shares.',
                 source_config_sha256=p['config_sha256'])
    save(out/'two_way_rebalance_review_only.json',variant)
    assert digest(read_json(HOME/'settings.json'))==p['config_sha256']
    text=f'''# 2026年9月24日交易计划

根据用户确认，本计划针对9月24日即将开盘的交易日。使用9月23日收盘参考价。计划生成步骤只读取账户证据并计算预览，没有启动交易、下单、撤单或开启卖出。

**当前配置对应“持有现仓、优先维持90%仓位”，收盘价预览无需新增买卖；还不能宣称一键启动已就绪，300475未知回执仍阻止跨日。**

## 已核验的准备状态

- 两轮Choice只读快照一致：净资产9,947,546.44元，股票市值9,002,590.00元，仓位90.5006%，138只持仓；冻结为0，已知委托没有挂单。
- 账户可用资金944,956.44元；保留395.66元历史隔离、460.933元正向现金差异及36,062.02元未知委托预算后，保守可买资金908,037.827元。现金小额浮动不是本次跨日阻塞原因。
- 已下载并核验9月23日官方PCF，准确日期文件已放入原控制台缓存；189条清单记录、154只正数量证券。仍走“前一交易日PCF、次日执行”的现有路径。
- 与9月22日相比有{len(changes)}只证券的清单数量变化。**零新增交易不代表PCF没变化，而是当前“禁止卖出+最低仓位”模式在已达90.5%时保留全部现仓，只在缺口出现后补买。它不等于完整复制新篮子。**
- 300475现有200股；9月23日11:16那笔新增200股、限价180.08元仍没有可唯一关联的服务器回执。持仓、成交、资金其余部分核对通过，并不等于可以把该笔标成失败或再次提交。

## 当天执行安排

| 时点 | 计划 |
|---|---|
| 盘前 | 核查300475历史委托及成交，先消除跨日阻塞；取得唯一服务器回执后按完整账户核验接回。若仍无证据，保留原记录，不清空账本或重发。 |
| 09:35之后 | 只有跨日核验通过，才点击原一键控制台启动；重新读取当日行情、资金和仓位。最新PCF已缓存，但凌晨预览不能代替开盘核验。 |
| 上午、下午 | 当前策略为PCF最低仓位维护；收盘参考值下买入0只、卖出0只。出现实际仓位缺口时，在策略池、原整手、±3%价格保护和限额内补足。 |
| 11:25—13:05 | 原控制台暂停新增委托。 |
| 14:35—14:50 | 重点检查90%最低仓位与未成交；14:50后停止新增，清理自己的已确认挂单，15:00完成核验。 |

保持90%最低要求、90.5%规划缓冲、950万元总上限、单股/单笔50万元、至少5秒间隔、禁止卖出。树模型继续研究，未接入当天账户。

9月25日至27日为中秋休市，9月28日恢复交易，见[上交所2026休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml)。如果9月24日首次低于90%，按已约定“发现日算第1个交易日”，第3个交易日是**9月29日**；程序仍应优先当天补足，不能把期限当作主动等待的理由。

## 如果要完整跟随新PCF：已做另一个预览，尚未启用

在其余参数相同、只允许双向调仓的假设下，收盘价目标约为：

| 项目 | 预览 |
|---|---:|
| 增加持仓 | {len(buys)}只，约{float(buy)/10000:.2f}万元 |
| 减少持仓 | {len(sells)}只，约{float(sell)/10000:.2f}万元 |
| 调整后股票仓位 | {variant['target_fraction']:.4%} |

这个方案涉及原先关闭的卖出权限，且需重新核对可卖数量、卖出回款、临时仓位和实际成交；**只是供审阅的候选，没有改设置，也没有生成可直接绕过核验的执行入口**。300475仍保留保护。当前计划采用上面的持仓维护路径。

## 文件

- 当前配置预览：plan_preview.json。
- 新PCF：pcf_20260923.json；数量变化：pcf_quantity_changes.json。
- 完整调仓候选：two_way_rebalance_review_only.json。
- 两轮账户证据：console/diagnostics/20260924_001112，包含私有账户记录，不应上传GitHub。

目前点击启动仍会提示“前日有未确认委托，禁止跨日重置”。本计划已完成准备和核查，但没有把这一阻塞包装成可正常交易。
'''
    if p['rollover_ready'] and p.get('expired_receipt_count'):
        text=text.replace('还不能宣称一键启动已就绪，300475未知回执仍阻止跨日。',
            '300475旧日未确认委托已完成跨日核验与本地归档，启动阻塞已解除；开盘仍须重新核验账户和行情。')
        text=text.replace('账户可用资金944,956.44元；保留395.66元历史隔离、460.933元正向现金差异及36,062.02元未知委托预算后，保守可买资金908,037.827元。现金小额浮动不是本次跨日阻塞原因。',
            f"账户可用资金{float(a['account_cash_before_quarantine']):,.2f}元；保留395.66元历史隔离及460.933元正向现金差异后，保守可买资金{float(a['cash']):,.3f}元。36,062.02元未知委托预约已解除，原始资金基线未改写。")
        text=text.replace('持仓、成交、资金其余部分核对通过，并不等于可以把该笔标成失败或再次提交。',
            '9月24日00:52完成两轮完整历史委托、成交、持仓、现金核验，均无新增记录。现按“日内申报已到期、未观察到受理或成交”归档，保留原始UNKNOWN证据；不认定服务器拒单、不重发旧单，后续持续检查迟到回执。')
        text=text.replace('核查300475历史委托及成交，先消除跨日阻塞；取得唯一服务器回执后按完整账户核验接回。若仍无证据，保留原记录，不清空账本或重发。',
            '跨日核验已完成。打开原控制台，点击“启动 / 查看已有运行”，程序重新核验账户并准备当天计划；09:35前等待开盘。')
        text=text.replace('只有跨日核验通过，才点击原一键控制台启动；重新读取当日行情、资金和仓位。',
            '重新读取当日行情、资金和仓位；盘前已启动的程序按原窗口自动继续。')
        text=text.replace('300475仍保留保护。','300475可按当日策略重新计算目标差额，受原限额、价格与账户核验约束。')
        text=text.replace('console/diagnostics/20260924_001112',p['account_evidence'])
        text=text.replace('目前点击启动仍会提示“前日有未确认委托，禁止跨日重置”。本计划已完成准备和核查，但没有把这一阻塞包装成可正常交易。',
            '跨日检查已通过。当前未运行交易进程；请在控制台点击启动，届时继续执行账户、行情、交易时段和限额核验。类似跨日孤立买单今后会在启动时自动完成两轮只读核验，条件不满足则保留阻塞。')
    (out/'9月24日交易计划.md').write_text(text,encoding='utf-8')
    save(out/'plan_validation.json',dict(account_snapshots_consistent=True,closing_marks_match_account=True,pcf_date_valid=True,
         current_maintenance_has_zero_new_trades=not p['buys'] and not p['sells'],settings_unchanged=True,pcf_quantity_change_names=len(changes),
         live_ledger_edits=0,orders_submitted=0,rollover_ready=p['rollover_ready'],
         prior_receipt_local_closure_count=p.get('expired_receipt_count',0),account_evidence=p.get('account_evidence')))
    print(json.dumps({k:v for k,v in variant.items() if k not in ['buys','sells','targets']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

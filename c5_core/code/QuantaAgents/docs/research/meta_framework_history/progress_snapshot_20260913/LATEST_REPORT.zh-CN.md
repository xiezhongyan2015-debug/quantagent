# V10A 因子与组合研究状态

当前阶段：reviewed；批次：0。六年历史目标达标：否。
独立样本外稳定性及交易盈利均未证明。2025数值不加载。

主要因子已评 50 / 最低300；其中新定义 5、旧库参考 45、继承重评 0。
完整且非数值重复的组合规格 40 / 最低200；控制项 6；数值重复 1；实现失败记录 0。
定义/数值重复、失败、控制、年度预测和权重更新次数不充当新增研究规模。

验收为同一实体2019—2024六年逐年日度截面Pearson IC年均严格过线：固定无监督因子 >0.05，组合 >0.10。方向仅由2016—2018确定。

## 当前因子证据

| 定义或规格 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 最差年度 |
|---|---:|---:|---:|---:|---:|---:|---:|
| calendar.standard.emv | 0.00247 | 0.01469 | 0.00061 | -0.00388 | -0.00285 | 0.00731 | -0.00388 |
| participation_main_effect | -0.00417 | 0.00307 | 0.00823 | 0.00111 | 0.02631 | -0.00153 | -0.00417 |
| calendar.standard.volume_oscillator | 0.00280 | 0.01553 | -0.00576 | 0.00027 | 0.01889 | -0.00534 | -0.00576 |
| F3 | 0.01173 | 0.00015 | -0.00046 | 0.01443 | -0.01016 | 0.01855 | -0.01016 |
| v10a_return_participation_residual | 0.01185 | 0.01403 | 0.02131 | 0.02270 | -0.01121 | -0.00112 | -0.01121 |
| F6 | 0.01687 | 0.00390 | 0.00166 | 0.01905 | -0.01183 | 0.02131 | -0.01183 |
| calendar.standard.overnight_return | 0.00924 | -0.00163 | -0.00460 | -0.01273 | 0.01163 | -0.00334 | -0.01273 |
| calendar.standard.roc | 0.01220 | -0.01077 | 0.00359 | 0.02240 | 0.02693 | -0.01293 | -0.01293 |

## 当前组合证据

| 定义或规格 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 最差年度 |
|---|---:|---:|---:|---:|---:|---:|---:|
| combo_9b2acb583eea2495703cdb58 | 0.00976 | 0.02432 | 0.01321 | 0.03377 | 0.02402 | 0.02530 | 0.00976 |
| combo_f8a583b1c8eb89417453c2de | 0.00841 | 0.01590 | 0.01470 | 0.01635 | 0.02311 | 0.01936 | 0.00841 |
| combo_6a202c4fc9c0424cfdaecc75 | 0.01938 | 0.04173 | 0.00564 | 0.01243 | 0.00627 | 0.01956 | 0.00564 |
| combo_04b17bb1e6534530e9d21467 | 0.01086 | 0.04672 | 0.01000 | 0.00527 | 0.02689 | 0.01595 | 0.00527 |
| combo_50d05c42d75ee5afb08e1975 | 0.00410 | 0.04052 | 0.01106 | 0.00902 | 0.03335 | 0.02016 | 0.00410 |
| combo_7d489643e022654078e2a55c | 0.00617 | 0.05569 | 0.00368 | 0.03379 | 0.02993 | 0.03632 | 0.00368 |
| combo_f7349ede3f87ee33ef3c53f8 | 0.00400 | 0.05908 | 0.00361 | 0.01710 | 0.00384 | 0.01430 | 0.00361 |
| combo_e0e0530276cfa23d766ffcdf | 0.00643 | 0.05551 | 0.00345 | 0.03364 | 0.02977 | 0.03624 | 0.00345 |

## 成本与恢复

累计研究网关模型调用 3 次；已知输入 171656、输出 26391 token；未知用量调用 0 次。
登记CPU 1332.12秒、墙钟 2483.55秒。货币费用未知，未推测账单。
模型缓存输入属于输入、推理输出属于输出，不重复相加。主任务及开发/审计子代理用量在团队回执中另计，不能把研究网关数字称作全部开发成本。

当前阻塞：null

每版先完成最低规模，再按50因子和40组合扩展；连续两扩展批次无足够改善且无新互补项后复盘。资源上限触发复盘或可恢复等待，均不是金融目标完成。

完整状态：F:\V10A_Factor_Research\state.json
原始模型回执：F:\V10A_Factor_Research\model_calls
逐因子、逐日和公式包：F:\V10A_Factor_Research\versions\V10A\factors
组合模型系数、更新窗口与逐日预测：F:\V10A_Factor_Research\versions\V10A\combinations
独立验收源：F:\V10A_Factor_Research\acceptance
历次批复盘：F:\V10A_Factor_Research\versions\V10A\decisions

## 最新证据复盘

**为什么尚未达标：** The frozen target remains unmet, and the study is incomplete. The best recorded factor floor is EMV's -0.0038751149 in 2022, below 0.05 by 0.0538751149. The best combination floor belongs to combo_9b2acb583eea2495703cdb58: its annual Pearson ICs for 2019–2024 are 0.009758, 0.024316, 0.013207, 0.033774, 0.024016 and 0.025304. Its weakest year misses 0.10 by 0.0902422462. The higher-mean combo_7d489643e022654078e2a55c still falls to 0.003677 in 2021. Reported coverage and valid-day counts are adequate in these examples; weak and uneven predictive information explains their target shortfall.

The participation residual, rolling_mean(rolling_residual(pct_change(close, 1), log(volume / lag(rolling_mean(volume, 20), 1)), 20), 3), illustrates instability: its frozen direction produces IC -0.011214 in 2023 and -0.001118 in 2024. These observations do not justify changing its direction or repairing its formula. Separately, dense46 control combo_c26e22b3a2890a52082bcdd5 could not fit because of insufficient common training days/cells. That is an availability outcome, not an observed low prediction IC. Neither aggregate means, rank IC nor relative-return summaries satisfy the frozen annual Pearson criterion.
证据：950d96aff4f0347a9a96438e80e4a94dfb467ec0cefe2c69d1499376f98b4870, 0b72293879319eedeb71bcd321f40f9ade2961ac1e1420eb1f182ec29c419bb4, combo_9b2acb583eea2495703cdb58, combo_7d489643e022654078e2a55c, combo_c26e22b3a2890a52082bcdd5, combination_coverage:bd09e43e9858f55b582f35a9a45590902d2418ce7d8a0bf3435deb30e16a3808

**问题来源：** No implementation exception or repair-eligible failed formula is declared: failures is empty. Dense46's unavailable fit must remain separate from that counter. Its member definitions resolve, but the report does not identify which histories or intersections prevent fitting. The registered long-reversal member has a 1260-session denominator lag, making warm-up attrition a diagnostic hypothesis, not an established cause or permission to shorten it. Calibration repeats the unavailable dense46 result outside formal study counts. Only one formal dense46 control was attempted; the other 23, including combo_927fb7c210ca7cdb4dc06b42, remain unattempted.

The declaration contains 299 complete specs: 275 ordinary and 24 controls. Actual ordinary evaluations total 40, distributed across sizes 4/8/12/24 as 10/10/9/11. They comprise four fixed equal-direction schemes and 36 ridge schemes. Ridge marginals are raw/rank training targets 20/16; fixed/quarterly-expanding/quarterly-rolling3y updates 14/11/11; and lambda 0.001/0.01/0.1/1.0 counts 5/4/23/4. These marginals do not establish complete Cartesian coverage. Of 12 declared member sets, nine ordinary sets have evaluations, dense46 has one unavailable attempt, and two ordinary 24-member sets have no attempts. There are 235 ordinary and 23 control specs pending. Eleven evaluations concentrate on diversity member set 707e43daa804ebfd982748bbee77ed599afa278c334c2722c2cb504535a87d5f while both other 24-member sets remain untouched.

Combination comparisons show tradeoffs rather than a demonstrated solution. combo_04b17bb1e6534530e9d21467 and combo_f8a583b1c8eb89417453c2de hold eight members, lambda 0.1, fixed updates and recorded training mask constant while changing the training target. Raw-target fitting has higher mean Pearson IC, 0.019284 versus 0.016292, but lower annual floor, 0.005269 versus 0.008409. Both still evaluate raw-return Pearson IC. combo_7d489643e022654078e2a55c and combo_e0e0530276cfa23d766ffcdf isolate lambda 0.001 versus 0.01 with the same 24 members, rank target and expanding updates; their mean IC difference is only 0.00008723. This does not establish prediction correlation or numerical identity. Comparing either with fixed lambda-1 combo_9b2acb583eea2495703cdb58 confounds regularization and updates. Some other matching specs were evaluated according to coverage, but their detailed paired comparisons are absent here. Twenty-four fitted update intervals constitute one complete scheme, not 24 candidates.

Complementarity evidence uses actual paired samples 832bcfe2636b99ecd8ab9730767e5e35548726027e86cc484400e115b34f993b and b5ac4b678e7fef53640e44ca7b36144a458a8fb0a885a231bd724a3c40fb1de3. The two folds have 238/237 prediction days and 55,844/61,633 common cells; their fits report 238/476 training days and 51,490/107,334 cells. Baseline-original, baseline-common and augmented models are fitted, with matched fit/prediction masks, zero coverage-selection deltas and negligible refit deltas. F3's paired increments are -0.01427216 and -0.00028958. F7's are -0.00167420 and +0.00123895. ACD, F4, TMA and BBI also have negative reported two-fold means. These are adverse results for their specified baselines, not universal rejection evidence. F3, F7 and the remaining additions use different inner baseline memberships. The exposed-history comparisons use another baseline containing F1, F2 and both new residuals. On their common mask 7f5f6ef847b2f135b5fc33f2b1e64054b53ecd36f409d732b187a807bbe059ad, F7 adds +0.00155578 despite its negative inner mean. Preserve its risk role and eligibility for further complementary or conditional tests; this exposed increment cannot retrospectively change training selection.

Redundancy accounting records 1,037 canonical duplicates and one numerical duplicate, without supplying that numerical pair. These counts do not measure independent information. Same-definition duplication, same-values duplication including masks, and measured high correlation require separate evidence. Leading records show no numerical-duplicate links. Information coverage is also uneven: generated quality and condition route counts are zero, although references and combination membership retain those roles. Selection scores marked historical cannot become fresh validation evidence. Follow-up selection remains adapted to exposed history.
证据：combo_c26e22b3a2890a52082bcdd5, combo_927fb7c210ca7cdb4dc06b42, combination_coverage:bd09e43e9858f55b582f35a9a45590902d2418ce7d8a0bf3435deb30e16a3808, combo_04b17bb1e6534530e9d21467, combo_f8a583b1c8eb89417453c2de, combo_7d489643e022654078e2a55c, combo_e0e0530276cfa23d766ffcdf, combo_9b2acb583eea2495703cdb58, 5b7e7abd418fd21cda1ca0f4a822df3b60e1e3949665d5d77eb61aaa733aa7dd, 818dc25de8e47adabef223651b86c2c45ec28e5497dbbabcdbe1151fc5b322d2, 649d61ef410470fa1c7e12736290daa57546c24f1fb9e1d87ff43678bd605f65, 77facc2de31aaccbe73af276fc1b086a8e992bcc23cf06b4229a17ea458f279f, bdb03daf9222d6c4213cc05f96c02784667ccdcb261de94d11674424f60a8eb9, da7a8b6126d547fabd8922cfd09a560094657c3dc4710b7cf40004e4ac07ab0a

**修改及反证：** Continue V10A's existing complete_minimum work. The justified framework improvement to test separately is a scheduler that prioritizes completion of matched comparisons across member sets. Its mechanism is to obtain interpretable breadth before spending further attempts on closely related regularization settings: the evidence currently has two untouched 24-member sets alongside eleven evaluations on one set.

The program should compare its frozen current queue with a deterministic queue that prioritizes the least-attempted member set, then an incomplete declared contrast changing exactly one of method, training target, update rule or lambda. Hold registered memberships, formulas, preprocessing, fitting rules and evaluator fixed. Use only the existing pending manifest, with integer attempt_slots in [24,24] and tie_break_seed in [0,0] for the isolated experiment. Retain quality, complement and condition routes and all return, risk and condition roles. Unavailable controls remain explicit outcomes.

Measure distinct member sets with completed contrasts and completed matched contrasts per measured CPU second. Verify identical outputs for specs shared by both arms. This proposal adds no market information and promises no IC increase. Deployment would constitute a scheduler framework change and requires isolated testing and independent audit; neither has occurred. If the current scheduler already implements this behavior, completing its pending batches does not justify a new framework version.
证据：combination_coverage:bd09e43e9858f55b582f35a9a45590902d2418ce7d8a0bf3435deb30e16a3808, combo_7d489643e022654078e2a55c, combo_e0e0530276cfa23d766ffcdf, combo_927fb7c210ca7cdb4dc06b42

**相较上一版：** No same-protocol prior framework-close snapshot exists. V9A has a different study scope, so added information, effective-candidate gains and efficiency improvements versus a prior framework are unestablished.

Within this snapshot, 50 primary factors were evaluated: 45 references and five new entries, plus six separately counted controls. The five new entries include two legacy window variants, so they do not represent five new mechanisms. The participation-normalized residual has mean Pearson IC 0.00960689 versus 0.00369418 for volume_level_residual, an observed difference of 0.00591271. Their matching annual coverage summaries do not substitute for a paired incremental combination test, and both fail the annual target. Forty evaluated ordinary schemes across nine member sets are known; their effective independent prediction count is unknown. Canonical duplicate counts, quarterly refits and equal-direction target/lambda metadata cannot inflate it.

The snapshot records per-version numeric work of 730.294462 wall seconds and 679.328125 CPU seconds. Its broader ledger records 2,107.377905 wall seconds, 1,330.453125 CPU seconds and 43,732 known tokens across two model calls; that ledger is cumulative across versions and has measurement gaps. These are resource records, not billing amounts or proof of efficiency. The proposed scheduler trial should be charged separately in resource accounting. No six-year historical attainment, OOS performance or profitability has been established.
证据：0b72293879319eedeb71bcd321f40f9ade2961ac1e1420eb1f182ec29c419bb4, 2a2fd28f4443fdf0e424485269e5e6dedd7c7704c6999e56fd770c80291a7962, combination_coverage:bd09e43e9858f55b582f35a9a45590902d2418ce7d8a0bf3435deb30e16a3808, combo_c26e22b3a2890a52082bcdd5

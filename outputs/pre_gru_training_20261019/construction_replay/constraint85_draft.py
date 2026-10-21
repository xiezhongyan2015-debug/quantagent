"""【草案，未接入】中证500 成分占净值硬约束（导师要求 >= 85%），供 pcf_overlay_20261012/live_targets.py 参考。

本文件只放在研究输出目录里，live_targets.py / 实盘日更/ 都没有改。回放 construction_replay.py 直接 import 这里的
enforce_csi500_floor，所以回放结果和这份草案是同一段代码。

规则（2026-10-19 回放 H85 口径）：
  1. 输入是最终层权重 w（占净值口径，sum(w) = 股票仓位，实盘约 0.949），c500 是中证500 成分集合，industry 是
     {代码: 申万一级代码}（实盘 实盘日更/config/industry_sw_l1_20260924.json）。
  2. 500 成分占净值 share = sum(w[c500])。share >= floor（0.85）时原样返回。
  3. 否则把占比补到 target（建议 0.86，留 1 个点缓冲）：需要搬动 need = min(target - share, 非500 总权重)。
     所有非 500 成分按同一比例削减（每只削 need / W_非500）。
  4. 每只非 500 股票削下来的权重，优先按现有权重比例加到"同一申万一级行业、已在目标里（w>0）的 500 成分"上；
     该行业没有 500 成分（或行业未知）时，进入全局池，按目标里全部 500 成分的现有权重比例补。
  5. 单票上限 max_single（默认 5%）：超出部分按比例分给其余未触顶的 500 成分（不会流回非 500，占比不降）；
     原本就高于上限的票保持原权重、不再加。
  6. 股票总仓位不变；不会加入目标以外的股票（c5 否决、PCF 外的票都不会被加回来）；不产生负权重。

接入建议（未实施）：
  * 在 live_targets.main() 里，overlay（beta 倾斜 delta）应用之后、整手取整之前调用一次：
        if a.csi500_floor > 0:
            ind = json.loads(Path(a.industry_map).read_text(encoding='utf-8-sig')) if a.industry_map else {}
            w, cinfo = enforce_csi500_floor(w, c500, ind, floor=a.csi500_floor, target=a.csi500_target, max_single=a.max_single)
            info['csi500_floor'] = cinfo
    未倾斜那一遍和带 --overlay 那一遍都会经过这里，所以 beta 倾斜的 delta 是在已满足约束的目标上算出来的，
    倾斜之后再保证一次。
  * 死区守卫 --floor-member 从 0.805 提到 0.85（guard_deadband 已有 max(floor_member, full_m - 0.005) 的逻辑）。
  * 整手之后再查一次：post_lot_member_fraction(qty, ref, c500, nav) < floor 时拒绝发布（或退一层），作为硬闸门。
  * 成分表要用执行日生效的名单：调样生效日的前一个信号日，members 文件应已切到新名单（回放里 H85x 臂就是这个口径），
    否则调样当天按新名单计的占比会掉到 85% 以下。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ['enforce_csi500_floor', 'post_lot_member_fraction']


def enforce_csi500_floor(w: pd.Series, c500, industry: dict | None = None, floor: float = 0.85, target: float = 0.86,
                         max_single: float = 0.05, tol: float = 1e-12):
    """草案，未接入。返回 (新权重 Series, 信息 dict)。w 不会被原地修改。"""
    if target < floor: raise ValueError('target must be >= floor')
    w = pd.Series(w, dtype=float).copy()
    if not np.isfinite(w.to_numpy()).all() or (w < -tol).any(): raise ValueError('weights must be finite and nonnegative')
    industry = industry or {}
    c500 = set(c500)
    is_m = pd.Series(w.index.isin(list(c500)) & (w.to_numpy() > 0), index=w.index)
    total = float(w.sum()); share = float(w[is_m].sum())
    info = dict(applied=False, floor=floor, target=target, share_before=share, share_after=share, moved=0.0,
                moved_same_industry=0.0, moved_global=0.0, capped_names=0, feasible=True, stock_weight=total)
    if share >= floor - tol: return w, info
    non = w[~is_m & (w > 0)]; W_non = float(non.sum())
    if not is_m.any() or W_non <= tol:
        info.update(feasible=False, reason='no CSI500 recipients' if not is_m.any() else 'no non-CSI500 weight to move')
        return w, info
    need = min(target - share, W_non)
    if target - share > W_non + tol: info.update(feasible=total >= floor - tol, reason='non-CSI500 weight smaller than the gap; all of it moved')
    cut = non * (need / W_non)
    recv = w[is_m]; add = pd.Series(0.0, index=recv.index)
    ind_recv = pd.Series([industry.get(c) for c in recv.index], index=recv.index, dtype=object)
    same_amt = 0.0; pooled = 0.0
    for code, c in cut.items():
        g = industry.get(code)
        peers = recv.index[(ind_recv == g).to_numpy()] if g is not None else recv.index[:0]
        ps = float(recv[peers].sum()) if len(peers) else 0.0
        if ps > tol: add[peers] += c * recv[peers] / ps; same_amt += c
        else: pooled += c
    if pooled > 0: add += pooled * recv / recv.sum()
    new = w.copy(); new[cut.index] = (new[cut.index] - cut).clip(lower=0.0); new[add.index] += add
    capped = set()
    cap = np.maximum(max_single, w[add.index])   # a name already above the cap keeps its weight but receives nothing
    for _ in range(100):   # single-name cap: spill the excess onto uncapped CSI500 names, pro-rata
        m = new[add.index]; over = m[m > cap + tol]
        if over.empty: break
        excess = float((over - cap[over.index]).sum()); new[over.index] = cap[over.index]; capped |= set(over.index)
        room = m.index[(new[m.index] < cap - tol).to_numpy()]
        if not len(room):   # nowhere to put it: hand the excess back to the names it was cut from
            new[cut.index] += excess * cut / cut.sum(); info.update(feasible=False, reason='every CSI500 recipient at the single-name cap'); break
        new[room] += excess * new[room] / new[room].sum()
    new = new[new > tol]
    new = new * (total / new.sum())   # rounding only: total is preserved by construction
    share_after = float(new[new.index.isin(list(c500))].sum())
    info.update(applied=True, share_after=share_after, moved=float(need), moved_same_industry=float(same_amt), moved_global=float(pooled),
                capped_names=len(capped), names_cut=int(len(cut)), names_dropped=int(len(w) - len(new)))
    if share_after < floor - 1e-9: info['feasible'] = False
    return new, info


def post_lot_member_fraction(qty: pd.Series, ref: pd.Series, c500, nav: float) -> float:
    """整手后按参考价计的 500 成分占净值（发布前硬闸门用）。"""
    v = (pd.Series(qty, dtype=float) * pd.Series(ref, dtype=float).reindex(qty.index)).fillna(0.0)
    return float(v[v.index.isin(list(set(c500)))].sum() / nav)

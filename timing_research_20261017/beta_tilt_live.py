"""实盘 beta 倾斜（2026-10-19 起按用户指令上线；规则 = reports/P3_beta倾斜影子登记协议v2 + 实盘补充）。
daily.sh 在 live_targets 放行未倾斜目标之后调用：
  beta_tilt_live.py <T> <NEXT> <目标目录> --cache DIR --out DIR [--panel DIR] [--final-max-turnover X] -- <live_targets.py 原参数...>
  --final-max-turnover：倾斜目标用的换手闸门（daily.sh 让未倾斜那一遍放宽 0.20，因为持仓本身带着倾斜；最终发布的倾斜目标按原闸门）
1 未倾斜目标（json/csv/precheck）备份到 <目标目录>/untilted/；若当前目标已带 overlay（重跑），以备份为基准，绝不在倾斜后的目标上再倾斜
2 beta_tilt_shadow.py 在未倾斜目标上算 S7 持有方向 pos 与权重差 delta（同时记 T 日倾斜盈亏、段内止损 −1.0pp、数据保护）
3 pos≠0：live_targets 原参数 + --overlay delta 重跑，整手、死区、换手、单票、中证500 等闸门全部照旧；
  被闸门拒绝或任何异常 → 恢复未倾斜目标（明天照常按未倾斜目标交易）
4 写 <out>/<NEXT>/applied_<NEXT>.json 与 delta_realized_<NEXT>.csv（整手后实际的 倾斜−未倾斜 权重差，盈亏按它记）
永远 exit 0：本步骤失败不会挡住发布。
"""
import sys, json, shutil, subprocess, hashlib, datetime as dt
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
LT = ROOT / 'pcf_overlay_20261012/live_targets.py'; BTS = HERE / 'beta_tilt_shadow.py'


def opt(a, k, d=None): return a[a.index(k) + 1] if k in a else d


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))


def console(c):
    """any of 600000 / 600000.SH / SH600000 -> SH600000 (same rule as live_targets.to_dotcode)"""
    c = str(c).strip().upper()
    if c[:2] in ('SH', 'SZ') and c[2:].isdigit(): return c
    if '.' in c:
        x, y = c.split('.'); return (y + x) if x.isdigit() else (x + y)
    c = c.zfill(6); return ('SH' if c.startswith(('5', '6', '9')) else 'SZ') + c


def book(target, nav, px):
    """NAV weights of an integer-share target at the release reference prices"""
    return pd.Series({t['code']: t['shares'] * px[t['code']] / nav for t in load(target)['targets']}, dtype=float)


def main():
    argv = sys.argv[1:]; cut = argv.index('--') if '--' in argv else len(argv); a, lt_args = argv[:cut], argv[cut + 1:]
    T, NEXT, tdir = a[0], a[1], Path(a[2]); cache, out = opt(a, '--cache'), Path(opt(a, '--out')); panel = opt(a, '--panel'); final_turn = opt(a, '--final-max-turnover')
    tgt, csv, pre = (tdir / f'{k}_{NEXT}.{e}' for k, e in (('target', 'json'), ('target', 'csv'), ('precheck', 'json')))
    ub = tdir / 'untilted'; nd = out / NEXT; nd.mkdir(parents=True, exist_ok=True)
    rec = dict(signal_date=T, exec_date=NEXT, at=dt.datetime.now().isoformat(timespec='seconds'), applied=False)

    def finish(reason):
        rec['reason'] = reason; (nd / f'applied_{NEXT}.json').write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding='utf-8')
        print('beta tilt live:', json.dumps(rec, ensure_ascii=False)); return 0

    def restore():
        for f in (tgt, csv, pre): shutil.copy2(ub / f.name, f)
        (tdir / f'rejected_{NEXT}.json').unlink(missing_ok=True)

    try:
        if not tgt.exists(): return finish('没有放行的目标文件')
        if load(tgt)['generated_from'].get('overlay'):
            if not (ub / tgt.name).exists(): return finish('当前目标已倾斜但没有未倾斜备份，不再处理')
        else:
            ub.mkdir(exist_ok=True)
            for f in (tgt, csv, pre): shutil.copy2(f, ub / f.name)
        base = ub / tgt.name; rec['untilted_sha256'] = sha(base); rec['untilted_turnover'] = round(load(ub / pre.name).get('planned_two_way_turnover') or 0, 4)
        cmd = [sys.executable, str(BTS), T, '--next', NEXT, '--target', str(base), '--out', str(out)] + (['--cache', cache] if cache else []) + (['--panel', panel] if panel else [])
        r = subprocess.run(cmd, capture_output=True, timeout=1500, cwd=ROOT)
        if r.returncode != 0:
            restore(); return finish('beta 计算失败：' + (r.stderr or r.stdout).decode('utf-8', 'replace').strip()[-300:])
        meta = load(nd / f'meta_{NEXT}.json'); rec.update({k: meta.get(k) for k in ('S7', 'S3x', 'p_x', 'driver', 'pos_S7', 'pos', 'episode_start', 'remaining', 'a', 'beta_live', 'beta_shadow', 'episode_cum_net', 'notes')})
        if meta.get('pos', 0) == 0 or not meta.get('a'):
            restore(); pd.DataFrame(columns=['code', 'delta']).to_csv(nd / f'delta_realized_{NEXT}.csv', index=False)
            return finish('pos=0，不倾斜' + (f"（{meta['notes']}）" if meta.get('notes') else ''))
        r = subprocess.run([sys.executable, str(LT)] + lt_args + ['--overlay', str(nd / f'delta_{NEXT}.csv')] + (['--max-turnover', final_turn] if final_turn else []), capture_output=True, timeout=600, cwd=ROOT)
        if r.returncode != 0 or not tgt.exists():
            fails = load(pre).get('failures') if pre.exists() else None
            restore(); return finish(f'倾斜目标被闸门拒绝，已恢复未倾斜目标：{fails or (r.stderr or r.stdout).decode("utf-8", "replace")[-300:]}')
        new = load(tgt)
        if new['generated_from'].get('overlay_sha256') != sha(nd / f'delta_{NEXT}.csv') or new['generated_from'].get('mode') != load(base)['generated_from'].get('mode'):
            restore(); return finish('倾斜目标与未倾斜目标的层或 overlay 不一致，已恢复')
        try:   # accounting only: a failure here keeps the tilt and the P&L falls back to the intended delta file
            acct = load(opt(lt_args, '--account')); nav = float(acct['nav'])
            px = pd.read_csv(opt(lt_args, '--prices'), dtype={'code': str}); px = {console(c): float(v) for c, v in zip(px.code, px.close)}
            px.update({k: float(v) for k, v in acct.get('prices_now', {}).items() if float(v) > 0})
            wt, wu = book(tgt, nav, px), book(base, nav, px); u = wt.index.union(wu.index)
            dr = (wt.reindex(u).fillna(0) - wu.reindex(u).fillna(0)); dr = dr[dr.abs() > 1e-9]
            dr.rename('delta').rename_axis('code').reset_index().to_csv(nd / f'delta_realized_{NEXT}.csv', index=False); rec['realized_l1_half'] = round(float(dr.abs().sum() / 2), 4)
        except Exception as e:
            (nd / f'delta_realized_{NEXT}.csv').unlink(missing_ok=True); rec['realized_error'] = f'{type(e).__name__}: {e}'
        p = load(pre); rec.update(applied=True, tilted_sha256=sha(tgt), intended_l1_half=meta.get('l1_half'),
                                  stock_fraction=round(p.get('post_lot_stock_fraction', 0), 4), csi500_share_of_target=round(p.get('csi500_share_of_target', 0), 4), turnover=round(p.get('planned_two_way_turnover', 0), 4), max_single=round(p.get('max_single', 0), 4))
        return finish('已倾斜')
    except Exception as e:
        try:
            if (ub / tgt.name).exists(): restore()
        except Exception as e2: rec['restore_error'] = repr(e2)
        return finish(f'异常，已恢复未倾斜目标：{type(e).__name__}: {e}')


if __name__ == '__main__':
    main(); sys.exit(0)

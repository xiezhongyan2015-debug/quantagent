"""Append one row per execution date to outputs/daily_log.csv from the precheck JSON (and the target file's SHA256 when released)."""
import sys, json, hashlib, csv, time
from pathlib import Path
pre = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); note = sys.argv[2] if len(sys.argv) > 2 else ''
root = Path(__file__).resolve().parents[1]; log = root / 'outputs/daily_log.csv'; tgt = Path(sys.argv[1]).with_name(f"target_{pre['execution_date']}.json")
row = dict(logged_at=time.strftime('%Y-%m-%d %H:%M:%S'), execution_date=pre['execution_date'], signal_date=pre['signal_date'], requested_mode=pre.get('requested_mode'), mode=pre.get('mode'), released=pre.get('released'), shadow=pre.get('shadow'),
           names=pre.get('names'), stock_weight=round(pre.get('stock_weight', 0) or 0, 4), post_lot=round(pre.get('post_lot_stock_fraction', 0) or 0, 4), turnover=round(pre.get('planned_two_way_turnover', 0) or 0, 4), sell_out=pre.get('names_to_sell_out'),
           csi500_share=round(pre.get('csi500_share_of_target', 0) or 0, 4), removed=pre.get('removed_names'), signal_cov=pre.get('signal_coverage'), fallback=json.dumps(pre.get('fallback_from', []), ensure_ascii=False), failures='; '.join(pre.get('failures', [])),
           target_sha256=hashlib.sha256(tgt.read_bytes()).hexdigest()[:16] if tgt.exists() else '', note=note)
new = not log.exists()
with log.open('a', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(row)); 
    if new: w.writeheader()
    w.writerow(row)
print(json.dumps(row, ensure_ascii=False))

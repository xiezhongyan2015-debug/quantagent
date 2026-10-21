from pathlib import Path
import json
import numpy as np
import pandas as pd
from gru_walkforward_20261010 import experiment_wf as wf

def legacy_2026(seed):
    """Inference only for the already completed original T2025 checkpoints."""
    w = wf.window_from_env()
    assert w['test_start'] == '2025-01-01'
    base = wf.patch(w)
    data = base.Data()
    run = base.OUT / f'control_seed{seed}'
    result = json.loads((run / 'result.json').read_text())
    assert result['signature'] == base.signature(data, 'control', seed)
    assert result['best_checkpoint_sha256'] == base.sha(run / 'best.pt')
    model = base.MarketGRU(data.p.groups, data.n_industries, 'control').cuda()
    model.load_state_dict(base.checkpoint_load(run / 'best.pt', 'cuda')['model'])
    years = pd.to_datetime(data.p.times, utc=True).tz_convert('Asia/Shanghai').year.to_numpy()
    ix = np.asarray([t for t in data.visible[years[data.visible] == 2026] if data.mask(int(t)).sum() >= 100])
    output = Path('outputs/gru_dualplatform_20261011/A3_legacy/T2025') / f'control_seed{seed}'
    output.mkdir(parents=True, exist_ok=True)
    summary, rows = base.evaluate(model, data, ix, 'cuda', output / 'extra2026_scores.npz')
    base.write(output / 'export.json', {'status':'complete', 'checkpoint_sha256':result['best_checkpoint_sha256'], 'selection_frozen':True, 'metrics':summary})
    pd.DataFrame(rows).to_csv(output / 'extra2026_sections.csv',index=False)
    print('LEGACY T2025 -> 2026 EXPORT COMPLETE', seed, flush=True)


if __name__ == "__main__":
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument("--seed",type=int,required=True);a=ap.parse_args();legacy_2026(a.seed)

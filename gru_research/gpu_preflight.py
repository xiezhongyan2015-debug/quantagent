"""Check full-size ordinary/BLO GPU updates on real inner/outer training data."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
import torch
from gru_research.model import PlanGRU, ic_losses, adamw_groups, unrolled_step
from gru_research.train import PanelDataset, seed_everything


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    panel = PanelDataset(args.dataset)
    inner_ix, outer_ix = [panel.split_indices(s, config) for s in ['inner', 'outer']]
    chosen = lambda ix: next(panel.batches(ix, config['batch_cross_sections'], None))
    inner = panel.batch(chosen(inner_ix), 'cuda', config)
    outer = panel.batch(chosen(outer_ix), 'cuda', config)
    seed_everything(11)
    model = PlanGRU(panel.groups, panel.n_industries).to('cuda')
    optimizer = torch.optim.AdamW(adamw_groups(model, config['weight_decay']), lr=config['learning_rate'], betas=tuple(config['betas']))
    started = time.perf_counter()
    loss, skipped = ic_losses(model(*inner[:3]), inner[3], inner[4], config['min_stocks'])
    loss[3].backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), config['gradient_clip'], error_if_nonfinite=True)
    optimizer.step()
    torch.cuda.synchronize()
    ordinary_seconds = time.perf_counter() - started
    optimizer.zero_grad(set_to_none=True)
    del optimizer, loss
    model.eval()
    torch.cuda.reset_peak_memory_stats()
    logits = torch.zeros(4, device='cuda', requires_grad=True)
    started = time.perf_counter()
    updated, gradient, diagnostics = unrolled_step(model, logits, inner, outer, min_stocks=config['min_stocks'])
    torch.cuda.synchronize()
    if not np.isfinite(diagnostics['predictive_hypergradient']).all() or np.linalg.norm(diagnostics['predictive_hypergradient']) <= 0:
        raise ValueError('Real-data BLO predictive hypergradient did not pass')
    if skipped or diagnostics['skipped_sections']:
        raise ValueError('A full training section was unexpectedly skipped')
    receipt = {'passed': True, 'real_market_data': True, 'training_checkpoint': False,
               'uses_validation_or_test_data': False, 'sequence_length': config['sequence_length'],
               'inner_section_sizes': inner[4], 'outer_section_sizes': outer[4],
               'ordinary_update_seconds': ordinary_seconds,
               'blo_update_seconds': time.perf_counter() - started,
               'blo_peak_allocated_bytes': torch.cuda.max_memory_allocated(),
               'diagnostics': diagnostics}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'gpu_preflight.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'event': 'gpu_preflight_passed', **receipt}), flush=True)


if __name__ == '__main__':
    main()

"""Independent dual-readout template for the verified original PlanGRU.

No production model, score, optimizer or dataset is changed. Stage one freezes
the exact original forward path in eval mode; only a new zero-initialized
Linear(128,1) return readout can be trained. Optional shared adaptation exposes
parameter anchoring and old-score distillation, never an automatic fit loop.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F


ROOT = Path(__file__).resolve().parent
DEFAULT_INVENTORY = ROOT.parent/'gru_cost_objective_20261009_repair1/diagnostics/calibration_inventory.json'
MODEL_SOURCE_SHA256 = 'b22e45fcb9814a0d088daa979217e34af97e2263856c62de13ee317a9c0aea49'
TARGET_UNITS = {'raw_log_label3', 'arithmetic_label3'}
SHARED_MODULES = {'hour', 'daily', 'industry', 'fund', 'slot', 'project', 'gru'}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference_class(inventory_path=DEFAULT_INVENTORY):
    """Resolve the actual model source from the audited inventory, not a guess."""
    inventory = json.loads(Path(inventory_path).read_text(encoding='utf8'))
    row = next(x for x in inventory['byte_identical_method_sources']
        if x['source_identity'] == 'gru_research/model.py')
    path = Path(row['local_path'])
    if row['expected_sha256'] != MODEL_SOURCE_SHA256 or _sha(path) != MODEL_SOURCE_SHA256:
        raise ValueError('Original PlanGRU source identity changed')
    spec = importlib.util.spec_from_file_location('verified_dualhead_plan_gru', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PlanGRU


def recorded_model(checkpoint_path, *, seed, inventory_path=DEFAULT_INVENTORY):
    """Load only an exact recorded checkpoint; not invoked by synthetic tests.

    Canonical trainer checkpoints contain optimizer/RNG state, so loading needs
    weights_only=False. Full bytes must match the audited original seed hash
    before deserialization. No alternate checkpoint or best-epoch search exists.
    """
    inventory = json.loads(Path(inventory_path).read_text(encoding='utf8'))
    row = next(x for x in inventory['seed_inventory'] if x['seed'] == seed)
    if _sha(checkpoint_path) != row['checkpoint_sha256']:
        raise ValueError('Frozen checkpoint SHA256 mismatch')
    state = torch.load(checkpoint_path, map_location='cpu', weights_only=False)
    if state['signature']['variant'] != 'full' or int(state['signature']['seed']) != seed:
        raise ValueError('Frozen full-input seed signature required')
    if sorted(i for values in state['groups'].values() for i in values) != list(range(122)):
        raise ValueError('Original 122-field partition required')
    model = reference_class(inventory_path)(state['groups'], int(state['n_industries']), 'full')
    model.load_state_dict(state['model'], strict=True)
    model.eval()
    return model


class RankReturnAdapter(nn.Module):
    def __init__(self, original_model, *, target_unit):
        super().__init__()
        if target_unit not in TARGET_UNITS:
            raise ValueError('Explicit raw_log_label3 or arithmetic_label3 target required')
        if original_model.__class__.__name__ != 'PlanGRU' or original_model.variant != 'full':
            raise ValueError('Verified original full PlanGRU required')
        if not isinstance(original_model.gru, nn.GRU) or (
            original_model.gru.input_size, original_model.gru.hidden_size,
            original_model.gru.num_layers) != (64, 128, 2):
            raise ValueError('Original GRU 64->128, two layers required')
        if not isinstance(original_model.head, nn.Sequential) or not (
            isinstance(original_model.head[0], nn.LayerNorm)
            and tuple(original_model.head[0].normalized_shape) == (128,)
            and isinstance(original_model.head[-1], nn.Linear)
            and original_model.head[-1].out_features == 1):
            raise ValueError('Original head readout structure required')
        self.target_unit = target_unit
        self.rank_model = copy.deepcopy(original_model)
        self.teacher = None
        for p in self.rank_model.parameters(): p.requires_grad_(False)
        original_parameter = next(original_model.parameters())
        self.return_head = nn.Linear(128, 1).to(
            device=original_parameter.device, dtype=original_parameter.dtype)
        nn.init.zeros_(self.return_head.weight)
        nn.init.zeros_(self.return_head.bias)
        self._shared_names = set()
        self._head_input = None
        # The hook observes original head input without replacing the encoder
        # or altering the old forward/readout arithmetic.
        self._head_hook = self.rank_model.head.register_forward_pre_hook(self._capture_features)
        self.train(False)

    def _capture_features(self, module, args):
        if len(args) != 1 or args[0].shape[-1] != 128:
            raise ValueError('Original final GRU representation must have width128')
        self._head_input = args[0]

    def train(self, mode=True):
        super().train(mode)
        # Deterministic old-score inference is preserved even while fitting the
        # new head. eval() disables original dropout; it does not disable grads.
        self.rank_model.eval()
        if self.teacher is not None: self.teacher.eval()
        self.return_head.train(mode)
        return self

    def forward(self, x, industry, slots):
        if x.ndim != 3 or x.shape[1:] != (80, 122):
            raise ValueError('Original [batch,80,122] windows required')
        self._head_input = None
        rank_score = self.rank_model(x, industry, slots)
        features = self._head_input
        self._head_input = None
        if features is None: raise RuntimeError('Original head did not receive encoded features')
        if not self._shared_names: features = features.detach()
        expected_return = self.return_head(features).squeeze(-1)
        return dict(rank_score=rank_score, expected_return=expected_return,
            return_unit=self.target_unit)

    def enable_shared_finetuning(self, modules):
        """Optional stage two only; caller must freeze its separate protocol.

        The old rank head and teacher remain frozen. LR, loss weights, acceptance
        thresholds, data splits and stopping are deliberately not chosen here.
        """
        modules = set(modules)
        if not modules or not modules <= SHARED_MODULES:
            raise ValueError('Choose explicit original shared module names; never head')
        if self.teacher is None:
            # A bound hook would deepcopy the adapter through its callback.
            # Remove only our observation hook while cloning the plain old model.
            self._head_hook.remove()
            try:
                teacher = copy.deepcopy(self.rank_model)
            finally:
                self._head_hook = self.rank_model.head.register_forward_pre_hook(self._capture_features)
            for p in teacher.parameters(): p.requires_grad_(False)
            self.teacher = teacher.eval()
        # Repeated calls can change the trainable subset, but never reset theta0.
        self._shared_names = set()
        for name,p in self.rank_model.named_parameters():
            trainable = name.split('.')[0] in modules
            p.requires_grad_(trainable)
            if trainable: self._shared_names.add(name)
        return self

    def parameter_anchor(self):
        """L2-SP-like sum of squared displacement from original theta0."""
        if self.teacher is None: return self.return_head.weight.sum()*0
        baseline = dict(self.teacher.named_parameters())
        terms = [(p-baseline[name].detach()).square().sum()
            for name,p in self.rank_model.named_parameters() if name in self._shared_names]
        return torch.stack(terms).sum() if terms else self.return_head.weight.sum()*0

    def old_rank_drift(self, x, industry, slots, rank_score=None):
        # Frozen rank path is identical; no extra GRU or teacher pass is needed.
        if self.teacher is None: return self.return_head.weight.sum()*0
        if rank_score is None: rank_score = self.forward(x, industry, slots)['rank_score']
        with torch.no_grad(): old_score = self.teacher(x, industry, slots)
        return F.mse_loss(rank_score, old_score)

    def serving_state_dict(self):
        """One rank_model plus129 return-head parameters; exclude training teacher.

        The explicit target_unit must accompany a future deployment manifest.
        This method returns a detached snapshot and never writes a checkpoint.
        """
        return {name:value.detach().clone() for name,value in self.state_dict().items()
            if not name.startswith('teacher.')}

    def export_readouts(self):
        return dict(target_unit=self.target_unit, model_source_sha256=MODEL_SOURCE_SHA256,
            state_dict=self.serving_state_dict(), teacher_included=False)

    def regression_target(self, raw_log_label3):
        # Expected exp(logR)-1 differs from exp(expected logR)-1. Arithmetic
        # training transforms each observed label, not the fitted mean afterward.
        return raw_log_label3 if self.target_unit == 'raw_log_label3' else torch.expm1(raw_log_label3)

    def loss_terms(self, x, industry, slots, raw_log_label3, *, mature_mask):
        """Loss components only; never runs an optimizer or historical fit.

        Labels/maturity affect fitting pairs, never forward universe selection.
        The caller should average per-timestamp losses under its frozen cohort
        policy; this helper describes one timestamp's equal-stock regression.
        """
        output = self.forward(x, industry, slots)
        prediction = output['expected_return']
        if raw_log_label3.shape != prediction.shape or mature_mask.shape != prediction.shape:
            raise ValueError('One raw label and maturity flag per predicted stock required')
        if mature_mask.dtype != torch.bool: raise ValueError('Explicit Boolean label maturity required')
        target = self.regression_target(raw_log_label3)
        valid = mature_mask & torch.isfinite(target) & torch.isfinite(prediction)
        if not bool(valid.any()): raise ValueError('No finite, matured fitting pairs')
        return dict(return_loss=F.mse_loss(prediction[valid], target[valid]),
            parameter_anchor=self.parameter_anchor(),
            old_rank_drift=self.old_rank_drift(x, industry, slots, output['rank_score']),
            fitting_pairs=int(valid.sum()), output=output)

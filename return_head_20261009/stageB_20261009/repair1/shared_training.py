"""One fixed conservative stage-B adaptation, with an immutable rank teacher."""
from contextlib import nullcontext
import math
import torch

SHARED_LR=1e-5
HEAD_LR=1e-4
TRUST_RADIUS=0.005
MAX_TRAIN_RANK_DRIFT=0.05
CHUNK=128

def enable_top_gru(adapter):
    adapter.enable_shared_finetuning({'gru'})
    names=set()
    for name,p in adapter.rank_model.named_parameters():
        selected=name.startswith('gru.') and name.endswith('_l1')
        p.requires_grad_(selected)
        if selected: names.add(name)
    if names!={'gru.weight_ih_l1','gru.weight_hh_l1','gru.bias_ih_l1','gru.bias_hh_l1'}:
        raise ValueError('Exact original second GRU layer required')
    adapter._shared_names=names
    adapter.train(True)
    return names

def anchor_term(adapter):
    baseline=dict(adapter.teacher.named_parameters())
    terms=[]
    for name,p in adapter.rank_model.named_parameters():
        if name in adapter._shared_names:
            b=baseline[name].detach()
            terms.append((p-b).square().sum()/b.square().sum().clamp_min(1e-12))
    return torch.stack(terms).mean()

@torch.no_grad()
def project_trust(adapter,radius=TRUST_RADIUS):
    """Per tensor relative L2 projection; never moves the frozen teacher."""
    baseline=dict(adapter.teacher.named_parameters()); result={}
    for name,p in adapter.rank_model.named_parameters():
        if name not in adapter._shared_names: continue
        b=baseline[name]; delta=p-b
        denominator=float(b.norm())
        if denominator<=0: raise ValueError('Zero-norm anchor needs an explicit policy')
        ratio=float(delta.norm())/denominator
        if ratio>radius: p.copy_(b+delta*(radius/ratio))
        actual=float((p-b).norm())/denominator
        if actual>radius+2e-7: raise ValueError('Shared-parameter trust projection failed')
        result[name]=actual
    return result

def optimizer(adapter):
    shared=[p for p in adapter.rank_model.parameters() if p.requires_grad]
    return torch.optim.Adam([{'params':shared,'lr':SHARED_LR},
        {'params':list(adapter.return_head.parameters()),'lr':HEAD_LR}],weight_decay=0)

def train_section(adapter,opt,tensors,target,fit_mask,return_scale2,rank_scale2,teacher_rank=None):
    """One equal-time/equal-stock step, chunking without overweighting tails.

    Original dropout remains disabled. cuDNN eval-GRU cannot backpropagate;
    native FP32 GRU is used only for the student's differentiable pass. Teacher
    inference and all exported predictions retain the original deterministic
    evaluation settings, verified separately before fitting.
    """
    x,industry,slots=tensors
    if target.shape!=(len(x),) or fit_mask.shape!=target.shape: raise ValueError('Pair alignment')
    if fit_mask.dtype!=torch.bool or int(fit_mask.sum())<100: raise ValueError('At least100 matured pairs')
    if not math.isfinite(return_scale2+rank_scale2) or min(return_scale2,rank_scale2)<=0:
        raise ValueError('Train-only loss scales must be finite positive')
    opt.zero_grad(set_to_none=True)
    with torch.no_grad():
        teacher=adapter.teacher(x,industry,slots).detach() if teacher_rank is None else teacher_rank.detach()
    if teacher.shape!=(len(x),): raise ValueError('Teacher cohort alignment changed')
    if not bool(torch.isfinite(teacher).all()): raise FloatingPointError('Teacher nonfinite')
    nfit=int(fit_mask.sum()); n=len(x); losses={'return_loss':0.,'rank_distillation':0.}
    for start in range(0,n,CHUNK):
        stop=min(n,start+CHUNK); sl=slice(start,stop)
        context=torch.backends.cudnn.flags(enabled=False) if x.is_cuda else nullcontext()
        with context:
            output=adapter(x[sl],industry[sl],slots[sl] if slots.ndim==2 else slots)
            fit=fit_mask[sl]; mu=output['expected_return']; rank=output['rank_score']
            if not (bool(torch.isfinite(mu).all()) and bool(torch.isfinite(rank).all())):
                raise FloatingPointError('Student produced nonfinite prediction')
            r=((mu[fit]-target[sl][fit]).square().sum()/nfit/return_scale2) if bool(fit.any()) else mu.sum()*0
            d=(rank-teacher[sl]).square().sum()/n/rank_scale2
            (r+d).backward()
            losses['return_loss']+=float(r.detach()); losses['rank_distillation']+=float(d.detach())
    anchor=anchor_term(adapter); anchor.backward(); losses['relative_parameter_anchor']=float(anchor.detach())
    parameters=[p for p in adapter.parameters() if p.requires_grad]
    if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in parameters):
        raise FloatingPointError('Missing/nonfinite training gradient')
    losses['gradient_norm_before_clip']=float(torch.nn.utils.clip_grad_norm_(parameters,1.0))
    opt.step(); losses['relative_shared_displacement']=project_trust(adapter)
    return losses

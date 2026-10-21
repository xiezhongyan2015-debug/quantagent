"""Server Torch synthetic checkpoint fixtures; no training/inference/market data.

Run explicitly in the existing CUHK Torch runtime. Local contract tests are a
different file; lack of local Torch must never be reported as passing these.
"""
import copy
import io
import json
from pathlib import Path
import unittest
import torch
from gru_research.model import PlanGRU
from gru_research.collect_completed_stage import cpu_checkpoint
from gru_research.tests.test_completed_stage_contract import fixture, blob, FP, CONFIG
from gru_research.completed_stage_contract import validate_blo_trace

GROUPS={'hour':list(range(24)),'daily':list(range(24,40)),'factors':list(range(40,104)),
        'fund':list(range(104,120)),'ages':list(range(120,122))}


def serialize(state):
    stream=io.BytesIO(); torch.save(state,stream); return stream.getvalue()


class CompletedStageCheckpointTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(20261002)

    def state(self, variant='full'):
        signature={'dataset_fingerprint':FP,'config':CONFIG,'seed':11,'label':3,'variant':variant}
        model=PlanGRU(GROUPS,1,variant)
        state={'signature':signature,'groups':GROUPS,'n_industries':1,'model':model.state_dict(),
               'epoch':0,'best_epoch':0,'best_ic':.03}
        return state,{'best_epoch':1,'best_validation_ic':.03}

    def test_all_four_variants_cpu_strict_load_finite_shape_without_inference(self):
        for variant in ('full','pv','pv_factors','pv_fund'):
            with self.subTest(variant=variant):
                state,result=self.state(variant)
                r=cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result)
                self.assertEqual(r['model_tensor_count'],26)
                self.assertEqual(r['parameter_count'],202481)
                self.assertTrue(r['strict_state_dict_load_passed'])
                self.assertFalse(r['inference_performed'])

    def test_nonfinite_model_and_wrong_shape_are_rejected(self):
        for corruption in ('nan','shape'):
            state,result=self.state();name='head.4.weight'
            if corruption=='nan':state['model'][name].fill_(float('nan'))
            else:state['model'][name]=torch.zeros((1,63))
            with self.subTest(corruption=corruption),self.assertRaises((ValueError,RuntimeError)):
                cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result)

    def test_checkpoint_signature_and_best_epoch_must_match_existing_result(self):
        state,result=self.state();expected=copy.deepcopy(state['signature']);state['signature']['seed']=22
        with self.assertRaisesRegex(ValueError,'signature'):
            cpu_checkpoint(serialize(state),expected,GROUPS,1,result)
        state,result=self.state();state['epoch']=1
        with self.assertRaisesRegex(ValueError,'epoch mismatch'):
            cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result)

    def selector(self):
        result,warm,steps=fixture();trace=validate_blo_trace(result,blob(warm),blob(steps),11,CONFIG,FP)
        state,_=self.state();state['signature']=result['signature']
        state.update(warm_epoch=3,step=2000,tail=[r['weights']for r in steps[-200:]],
            logits=torch.log(torch.tensor(steps[-1]['weights'],dtype=torch.float32)),
            lambda_optimizer={'state':{0:{'step':torch.tensor(2000.),'exp_avg':torch.zeros(4),'exp_avg_sq':torch.zeros(4)}},
                              'param_groups':[{'params':[0]}]})
        return state,result,trace

    def test_selector_actual_serialized_tail_logits_and_adam_budget_are_bound(self):
        state,result,trace=self.selector()
        r=cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)
        self.assertTrue(r['selector_checkpoint_trace']['checkpoint_tail_exact_actual_steps'])
        self.assertFalse(r['inference_performed'])

    def test_selector_nan_optimizer_or_logit_mismatch_is_rejected(self):
        for corruption in ('optimizer','logits'):
            state,result,trace=self.selector()
            if corruption=='optimizer':state['lambda_optimizer']['state'][0]['exp_avg'][0]=float('nan')
            else:state['logits'].zero_()
            with self.subTest(corruption=corruption),self.assertRaises(ValueError):
                cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)

    def test_selector_adam_state_key_must_equal_unique_parameter_group_id(self):
        state,result,trace=self.selector()
        state['lambda_optimizer']['state']={1:state['lambda_optimizer']['state'][0]}
        with self.assertRaisesRegex(ValueError,'state key differs'):
            cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)

    def test_selector_adam_moment_shapes_must_match_four_logits(self):
        for name in ('exp_avg','exp_avg_sq'):
            state,result,trace=self.selector()
            state['lambda_optimizer']['state'][0][name]=torch.zeros((1,4))
            with self.subTest(moment=name),self.assertRaisesRegex(ValueError,'moments must be finite FP32'):
                cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)

    def test_selector_adam_moment_dtypes_must_be_fp32(self):
        for name in ('exp_avg','exp_avg_sq'):
            state,result,trace=self.selector()
            state['lambda_optimizer']['state'][0][name]=torch.zeros(4,dtype=torch.float64)
            with self.subTest(moment=name),self.assertRaisesRegex(ValueError,'moments must be finite FP32'):
                cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)

    def test_selector_adam_missing_either_moment_is_rejected(self):
        for name in ('exp_avg','exp_avg_sq'):
            state,result,trace=self.selector()
            del state['lambda_optimizer']['state'][0][name]
            with self.subTest(moment=name),self.assertRaisesRegex(ValueError,'required moments'):
                cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)

    def test_selector_adam_length1_step_is_not_scalar2000(self):
        state,result,trace=self.selector()
        state['lambda_optimizer']['state'][0]['step']=torch.tensor([2000.])
        with self.assertRaisesRegex(ValueError,'step must be scalar2000'):
            cpu_checkpoint(serialize(state),state['signature'],GROUPS,1,result,trace)

    def test_selector_adam_extra_fields_are_recorded_and_original_bytes_preserved(self):
        import hashlib
        state,result,trace=self.selector()
        state['lambda_optimizer']['state'][0]['max_exp_avg_sq']=torch.ones(4)
        state['lambda_optimizer']['state'][0]['diagnostic_note']='synthetic_extra_must_be_preserved'
        data=serialize(state);digest=hashlib.sha256(data).hexdigest()
        r=cpu_checkpoint(data,state['signature'],GROUPS,1,result,trace)
        evidence=r['selector_checkpoint_trace']['lambda_optimizer_state_evidence']
        self.assertEqual(evidence['extra_keys_preserved_in_original_checkpoint'],['max_exp_avg_sq','diagnostic_note'])
        self.assertEqual(r['checkpoint_sha256'],digest)
        self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
        self.assertEqual(torch.load(io.BytesIO(data),weights_only=False)['lambda_optimizer']['state'][0]['diagnostic_note'],'synthetic_extra_must_be_preserved')


if __name__=='__main__':unittest.main()

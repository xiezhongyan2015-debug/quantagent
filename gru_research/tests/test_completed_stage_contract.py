"""Synthetic contract fixtures only; never real BLO or model completion proof."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from gru_research.completed_stage_contract import (PendingEvidence, FROZEN_SOURCE_NAMES, validate_blo_trace,
    validate_blo_checkpoint, validate_weights, validate_selection, validate_fixed_trace, validation_records)
from gru_research.collect_completed_stage import target, collect
from gru_research.experiment_acceptance import FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256

CONFIG = json.loads(Path(__file__).parents[1].joinpath("config.json").read_text())
FP = "synthetic_fixture_only"


def blob(rows):
    return b"".join(json.dumps(r, allow_nan=False).encode() + b"\n" for r in rows)


def fixture():
    warm = [{"event": "blo_warmup", "seed": 11, "epoch": i, "loss": .9} for i in (1, 2, 3)]
    steps = []
    for step in range(1, 2001):
        a = .1 + .05 * step / 2000
        steps.append({"event": "blo_step", "seed": 11, "step": step,
            "weights": [a, .2, .3, .5 - a], "elapsed_seconds": .2,
            "inner_loss": .9, "outer_y4_loss": .95, "entropy": 1.2,
            "predictive_hypergradient": [.01, -.01, .02, -.02],
            "hypergradient_norm": .03, "skipped_sections": 0})
    result = {"complete": True, "seed": 11, "signature": {"dataset_fingerprint": FP,
        "config": CONFIG, "seed": 11, "mode": "blo"}, "warmup_epochs": 3, "steps": 2000,
        "tail_steps": 200, "mean_tail_weights": np.mean([r["weights"] for r in steps[-200:]], axis=0).tolist()}
    return result, warm, steps


class CompletedStageContractTests(unittest.TestCase):
    def setUp(self):
        self.result, self.warm, self.steps = fixture()

    def trace(self, result=None, warm=None, steps=None):
        return validate_blo_trace(result or self.result, blob(warm or self.warm),
            blob(steps or self.steps), 11, CONFIG, FP)

    def state(self):
        return {"signature": copy.deepcopy(self.result["signature"]), "groups": {}, "n_industries": 1,
            "warm_epoch": 3, "step": 2000, "tail": [r["weights"] for r in self.steps[-200:]],
            "logits": np.log(self.steps[-1]["weights"]).tolist(), "lambda_optimizer_steps": [2000]}

    def test_complete_trace_checks_actual_logs_not_only_result_counters(self):
        trace = self.trace()
        self.assertEqual(trace["actual_steps"], 2000)
        self.assertEqual(trace["predictive_nonzero_steps"], 2000)
        self.assertFalse(trace["inner_outer_step_sample_times_recorded"])
        self.assertTrue(validate_blo_checkpoint(self.state(), trace, self.result["signature"], {}, 1)["checkpoint_tail_exact_actual_steps"])

    def test_duplicate_step_with_full_count_is_rejected(self):
        steps = copy.deepcopy(self.steps); steps[1250] = steps[1249]
        with self.assertRaisesRegex(ValueError, "duplicate, missing"):
            self.trace(steps=steps)

    def test_missing_middle_step_cannot_be_hidden_by_result2000(self):
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            self.trace(steps=self.steps[:700] + self.steps[701:])

    def test_warmup_duplicate_and_step_wrong_seed_rejected(self):
        warm = copy.deepcopy(self.warm); warm[2]["epoch"] = 2
        with self.assertRaisesRegex(ValueError, "duplicate, missing"):
            self.trace(warm=warm)
        steps = copy.deepcopy(self.steps); steps[33]["seed"] = 22
        with self.assertRaisesRegex(ValueError, "event/seed"):
            self.trace(steps=steps)

    def test_wrong_mode_even_with_valid_counts_is_rejected(self):
        result = copy.deepcopy(self.result); result["signature"]["mode"] = "full"
        with self.assertRaisesRegex(ValueError, "signature/mode"):
            self.trace(result=result)
        state = self.state(); state["signature"]["mode"] = "full"
        with self.assertRaisesRegex(ValueError, "checkpoint signature/mode"):
            validate_blo_checkpoint(state, self.trace(), self.result["signature"], {}, 1)

    def test_result_tail_average_must_come_from_logged_last200(self):
        result = copy.deepcopy(self.result); result["mean_tail_weights"] = [.1, .2, .3, .4]
        with self.assertRaisesRegex(ValueError, "last200 average"):
            self.trace(result=result)

    def test_checkpoint_reordered_tail_rejected_even_when_mean_is_identical(self):
        state = self.state(); state["tail"] = list(reversed(state["tail"]))
        with self.assertRaisesRegex(ValueError, "checkpoint tail disagrees"):
            validate_blo_checkpoint(state, self.trace(), self.result["signature"], {}, 1)

    def test_logit_and_lambda_optimizer_count_must_match_checkpoint_trace(self):
        trace = self.trace(); state = self.state(); state["logits"] = [0., 0., 0., 0.]
        with self.assertRaisesRegex(ValueError, "logits disagree"):
            validate_blo_checkpoint(state, trace, self.result["signature"], {}, 1)
        state = self.state(); state["lambda_optimizer_steps"] = [1999]
        with self.assertRaisesRegex(ValueError, "Adam checkpoint step"):
            validate_blo_checkpoint(state, trace, self.result["signature"], {}, 1)

    def test_entropy_gradient_is_not_predictive_learning_evidence(self):
        steps = copy.deepcopy(self.steps)
        for row in steps: row["predictive_hypergradient"] = [0.] * 4
        with self.assertRaisesRegex(ValueError, "entropy alone"):
            self.trace(steps=steps)

    def selectors(self):
        results = {}
        for seed in CONFIG["seeds"]:
            r = copy.deepcopy(self.result); r["seed"] = seed; r["signature"]["seed"] = seed; results[seed] = r
        weights = {"seeds": CONFIG["seeds"], "per_seed_tail_steps": 200,
            "mean_weights": self.result["mean_tail_weights"], "selected_label": 4}
        return weights, results

    def test_five_seed_aggregation_wrong_label_or_seed_roster_rejected(self):
        w, r = self.selectors(); validate_weights(w, r, CONFIG, FP)
        w["selected_label"] = 1
        with self.assertRaisesRegex(ValueError, "argmax"):
            validate_weights(w, r, CONFIG, FP)
        w, r = self.selectors(); del r[55]
        with self.assertRaisesRegex(ValueError, "five exact"):
            validate_weights(w, r, CONFIG, FP)

    def malformed_aggregate(self, value):
        weights, results = self.selectors()
        for row in results.values(): row['mean_tail_weights'] = [.25] * 4
        weights['selected_label'] = 1
        weights['mean_weights'] = value
        with self.assertRaisesRegex(ValueError, 'four finite nonnegative weights'):
            validate_weights(weights, results, CONFIG, FP)

    def test_scalar_mean_weights_cannot_broadcast_as_four_equal_weights(self):
        self.malformed_aggregate(.25)

    def test_length1_mean_weights_cannot_broadcast_as_four_equal_weights(self):
        self.malformed_aggregate([.25])

    def test_nested_1_by4_mean_weights_cannot_broadcast_as_four_equal_weights(self):
        self.malformed_aggregate([[.25] * 4])

    def selection_fixture(self, formal):
        weights, _ = self.selectors(); candidates, alternatives = {}, {}
        # A .0005 gain is below frozen .001; direct label4 must be retained.
        for method, mean in [("fixed_1", .0305), ("fixed_2", .02), ("fixed_3", .01), ("fixed_4", .03), ("blo", .029)]:
            label = 4 if method == "blo" else int(method[-1]); candidates[method] = {}; rows = []; values = []
            for i, seed in enumerate(CONFIG["seeds"]):
                ic = mean + (i - 2) * .00001; values.append(ic)
                r = {"complete": True, "real_data": True, "seed": seed, "label": label, "variant": "full", "best_epoch": 1,
                    "best_validation_ic": ic, "signature": {"dataset_fingerprint": FP, "config": CONFIG, "seed": seed, "label": label, "variant": "full"}}
                candidates[method][seed] = r
                path = formal / ("blo_final" if method == "blo" else "fixed") / f"label_{label}" / f"seed_{seed}"
                rows.append({"seed": seed, "ic": ic, "epoch": 1, "path": str(path)})
            alternatives[method] = {"mean_ic": float(np.mean(values)), "std_ic": float(np.std(values, ddof=1)), "seeds": rows}
        selection = {"selected_method": "fixed_4", "selected_label": 4, "blo_label": 4, "validation_year": 2024,
            "alternatives": alternatives, "minimum_gain": .001,
            "best_gain_vs_direct_y4": alternatives["fixed_1"]["mean_ic"] - alternatives["fixed_4"]["mean_ic"]}
        return selection, weights, candidates

    def test_existing_selection_source_path_epoch_and_threshold_are_verified(self):
        formal = Path('/synthetic_fixture/formal')
        original = self.selection_fixture(formal)
        self.assertFalse(validate_selection(*original, CONFIG, FP, formal)["new_selection_written"])
        for corrupt in ("path", "epoch", "winner", "seed"):
            selection, weights, candidates = copy.deepcopy(original)
            if corrupt == "path": selection["alternatives"]["fixed_1"]["seeds"][0]["path"] = '/other/source'
            elif corrupt == "epoch": selection["alternatives"]["fixed_1"]["seeds"][0]["epoch"] = 2
            elif corrupt == "winner": selection["selected_method"] = "fixed_1"; selection["selected_label"] = 1
            else: candidates["blo"][11]["signature"]["seed"] = 22
            with self.subTest(corrupt=corrupt), self.assertRaises(ValueError):
                validate_selection(selection, weights, candidates, CONFIG, FP, formal)

    def test_stage_templates_never_accept_reselected_ablation_label_or_full_variant(self):
        self.assertEqual(str(target('selector', 11)), 'blo/seed_11')
        self.assertEqual(str(target('blo_final', 11, 3)), 'blo_final/label_3/seed_11')
        self.assertEqual(str(target('ablation', 11, 3, 'pv_factors')), 'ablations/pv_factors/seed_11')
        with self.assertRaises(ValueError): target('selector', 11, 3)
        with self.assertRaises(ValueError): target('ablation', 11, 3, 'full')

    def test_completed_fixed_logs_reject_duplicate_epoch_despite_dictionary_count(self):
        ep = [{"event": "epoch", "seed": 11, "label": 3, "variant": "pv", "epoch": i,
            "train_loss": .9, "validation_y4_ic": .04 if i == 1 else .02, "validation_y4_rank_ic": .03,
            "best_ic": .04, "learning_rate": .0003, "bad_epochs": i - 1, "skipped_sections": 0, "elapsed_seconds": 1.} for i in range(1, 10)]
        result = {"complete": True, "real_data": True, "seed": 11, "label": 3, "variant": "pv",
            "signature": {"dataset_fingerprint": FP, "config": CONFIG, "seed": 11, "label": 3, "variant": "pv"},
            "epochs_completed": 10, "best_epoch": 1, "best_validation_ic": .04}
        good = validate_fixed_trace(result, blob(ep), 11, 3, 'pv', CONFIG, FP, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256)
        self.assertTrue(good["allowed_frozen_metadata_exception"])
        with self.assertRaisesRegex(ValueError, "duplicate, missing"):
            validate_fixed_trace(result, blob(ep[:4] + [ep[3]] + ep[4:]), 11, 3, 'pv', CONFIG, FP, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256)

    def test_missing_selector_stays_pending_without_loading_or_archiving(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root/'gru_research').mkdir(); formal = root/'formal'; formal.mkdir(); dataset = root/'dataset'; dataset.mkdir()
            config_blob = json.dumps(CONFIG).encode(); (root/'gru_research/config.json').write_bytes(config_blob)
            guards = {'gru_research/config.json': hashlib.sha256(config_blob).hexdigest()}
            for name in FROZEN_SOURCE_NAMES - {'gru_research/config.json'}:
                (root/name).parent.mkdir(parents=True, exist_ok=True)
                (root/name).write_bytes(b'fixture'); guards[name] = hashlib.sha256(b'fixture').hexdigest()
            guard = root/'guard.json'; guard.write_text(json.dumps({'source_guards_sha256': guards}))
            meta = {'production_ready': True, 'causality_audit': {'passed': True}, 'raw_market_data_is_synthetic': False,
                    'files_sha256': {str(i): 'fixture' for i in range(26)}}
            metablob = json.dumps(meta).encode(); (dataset/'meta.json').write_bytes(metablob)
            fp = hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            (formal/'run_config.json').write_bytes(config_blob)
            def ns(date):return int(np.datetime64(date, 'ns').astype(np.int64))
            splits = {k: {'eligible_sections': 4, 'first_time_ns': ns(a), 'last_time_ns': ns(b)} for k,a,b in [('inner','2019-01-02','2022-12-28'),('outer','2023-01-02','2023-12-28'),('train','2019-01-02','2023-12-28'),('validation','2024-01-02','2024-12-27')]}
            (formal/'dataset_audit.json').write_text(json.dumps({'dataset_fingerprint':fp,'splits':splits}))
            import gru_research.collect_completed_stage as collector
            helper = Path(collector.__file__); contract = helper.with_name('completed_stage_contract.py')
            copied = root/'gru_research/collect_completed_stage.py'; copied.write_bytes(helper.read_bytes())
            copied.with_name('completed_stage_contract.py').write_bytes(contract.read_bytes())
            args = SimpleNamespace(root=root, formal=formal, dataset=dataset, stage='selector', seed=11, variant=None,
                guard_receipt=guard, expected_guard_receipt_sha256=hashlib.sha256(guard.read_bytes()).hexdigest(),
                expected_meta_sha256=hashlib.sha256(metablob).hexdigest(), expected_collector_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
                expected_contract_sha256=hashlib.sha256(contract.read_bytes()).hexdigest(),output_dir=root)
            with patch.object(collector, '__file__', str(copied)), patch.object(collector, 'cpu_checkpoint', side_effect=AssertionError('must not load a nonexistent result')):
                with self.assertRaises(PendingEvidence): collect(args)
            self.assertEqual(list(root.glob('*.tar.gz')), [])

    def test_scored_population_change_fails_even_when_timestamp_and_pool_are_same(self):
        from gru_research.evaluate import breakdown
        rows = [{'signal_time_ns':int(np.datetime64('2024-01-02T02:30:00', 'ns').astype(np.int64)), 'date':'2024-01-02', 'slot':0, 'pool_count':500, 'scored_count':490, 'ic':.03, 'rank_ic':.02},
                {'signal_time_ns':int(np.datetime64('2024-01-03T02:30:00', 'ns').astype(np.int64)), 'date':'2024-01-03', 'slot':0, 'pool_count':500, 'scored_count':490, 'ic':.04, 'rank_ic':.03}]
        population = sorted([[r['signal_time_ns'],r['date'],r['slot'],r['pool_count']]for r in rows])
        scored = [p+[490]for p in population]
        digest = lambda v:hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        validation_records({'records':rows},breakdown(rows),.035,digest(population),digest(scored))
        rows[0]['scored_count']=489
        with self.assertRaisesRegex(ValueError, 'scored population'):
            validation_records({'records':rows},breakdown(rows),.035,digest(population),digest(scored))


if __name__ == '__main__':
    unittest.main()

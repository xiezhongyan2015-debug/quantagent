"""Check the frozen upstream scheduler with synthetic data only."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np


def main(source_path):
    source = Path(source_path)
    spec = importlib.util.spec_from_file_location("rd_bandit_review", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    metrics = module.extract_metrics_from_experiment(SimpleNamespace(result={
        "IC": 0.03,
        "1day.excess_return_with_cost.annualized_return": 0.12,
        "1day.excess_return_with_cost.information_ratio": 1.2,
        "1day.excess_return_with_cost.max_drawdown": -0.10,
    }))
    bandit = module.LinearThompsonTwoArm(dim=1, prior_var=1., noise_var=1.)
    x = np.array([1.])
    bandit.update("factor", x, 1.)
    first = float(bandit.mean["factor"][0])
    bandit.update("factor", x, 1.)
    second = float(bandit.mean["factor"][0])
    controller = module.EnvController()
    result = {
        "commit": "32b3d395e73d9db5eee3fe9063d69aec0fdc83bd",
        "scope": "original standalone functions; synthetic inputs; no LLM or market backtest",
        "standard_key_arr_input": .12,
        "extracted_arr": metrics.arr,
        "extracted_sharpe": metrics.sharpe,
        "posterior_scalar_after_one_observation": first,
        "posterior_scalar_after_two_observations": second,
        "correct_conjugate_posterior_after_two_observations": 2 / 3,
        "reward_all_other_metrics_zero_mdd_negative_005": controller.reward(module.Metrics(mdd=-.05)),
        "reward_all_other_metrics_zero_mdd_negative_030": controller.reward(module.Metrics(mdd=-.30)),
    }
    target = Path(__file__).with_name("audit_probe_results.json")
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])

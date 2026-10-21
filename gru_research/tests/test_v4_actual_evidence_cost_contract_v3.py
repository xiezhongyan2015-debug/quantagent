"""One source-defined cost-name check, without engines or actual inspection."""
import ast
from pathlib import Path
import unittest

from gru_research import v4_actual_evidence_cost_contract_v3 as inspector


class ReplayCostNames(unittest.TestCase):
    def test_actual_sealed_producer_names_and_wrong_identifiers(self):
        # Read the producer's actual expression, without importing its engine
        # dependencies or executing research_costs or any actual replay.
        path = Path(__file__).resolve().parents[1] / "extended_costs.py"
        tree = ast.parse(path.read_bytes())
        producer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "research_costs")
        returned = next(n for n in producer.body if isinstance(n, ast.Return))
        self.assertIsInstance(returned.value, ast.Call)
        self.assertEqual(returned.value.func.id, "ResearchCostScenario")
        name = returned.value.args[0]
        self.assertIsInstance(name, ast.IfExp)
        self.assertEqual(name.test.id, "stress")
        actual_names = {"primary": ast.literal_eval(name.orelse), "stress": ast.literal_eval(name.body)}
        self.assertEqual(actual_names, {"primary": "PRIMARY", "stress": "STRESS"})
        for scenario, actual_name in actual_names.items():
            with self.subTest(source=scenario):
                self.assertTrue(inspector.require_registered_replay_cost_name(actual_name, scenario))
        for actual_name, scenario in (("primary", "primary"), ("stress", "stress"),
                                     ("STRESS", "primary"), ("PRIMARY", "stress"),
                                     (None, "primary"), ("PRIMARY ", "primary"),
                                     ("PRIMARY", "PRIMARY"), ("OTHER", "other")):
            with self.subTest(value=actual_name, scenario=scenario):
                with self.assertRaises(inspector.EvidenceRejected):
                    inspector.require_registered_replay_cost_name(actual_name, scenario)


if __name__ == "__main__":
    unittest.main()

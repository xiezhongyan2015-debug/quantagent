"""One focused counterexample check; synthetic scores, no real artifacts/Torch."""
import unittest
import numpy as np
from gru_research.r2_comparison.tool import strict_common


class CommonGroupCounterexample(unittest.TestCase):
    def test_equal_time_and_stock_counts_do_not_make_different_symbol_groups_paired(self):
        shared = ["S%03d" % i for i in range(110)]
        left = {s: float(i) for i, s in enumerate(shared)}
        right = {s: -float(i) for i, s in enumerate(shared)}
        left.update({"L%03d" % i: -10000.0 + i for i in range(10)})
        right.update({"R%03d" % i: 10000.0 + i for i in range(10)})
        y = {s: float(i) for i, s in enumerate(shared)}
        y.update({s: float(i + 110) for i, s in enumerate([k for k in left if k.startswith("L")])})
        y.update({s: float(i + 110) for i, s in enumerate([k for k in right if k.startswith("R")])})
        pair = strict_common(left, right, y, y)
        self.assertTrue(pair["valid_pair"])
        self.assertEqual(pair["common_symbols"], shared)
        self.assertEqual(pair["common_stock_count"], 110)
        self.assertAlmostEqual(pair["r1_ic"], 1.0)
        self.assertAlmostEqual(pair["r2_ic"], -1.0)
        native = [np.corrcoef([score[s] for s in score], [y[s] for s in score])[0, 1] for score in (left, right)]
        self.assertGreater(abs((native[1] - native[0]) - (pair["r2_ic"] - pair["r1_ic"])), .1)
        with self.subTest(case="disjoint_100stock_groups_cannot_produce_paired_IC"):
            a = {"A%03d" % i: float(i) for i in range(100)}
            b = {"B%03d" % i: float(i) for i in range(100)}
            no_pair = strict_common(a, b, a, b)
            self.assertFalse(no_pair["valid_pair"])
            self.assertEqual(no_pair["common_stock_count"], 0)
            self.assertIsNone(no_pair["r1_ic"])
        with self.subTest(case="same_symbol_different_y4_is_refused"):
            changed = dict(y); changed[shared[0]] += .01
            with self.assertRaises(ValueError):
                strict_common(left, right, y, changed)


if __name__ == "__main__":
    unittest.main()

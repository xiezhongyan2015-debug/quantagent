"""One targeted arithmetic check, with no replay or source-price repair.

The constant 7.61 inputs reconstruct the independently observed sealed day
H=L=O=C case; they are not newly fetched or extracted individual minute rows.
The nonconstant and over-limit cases below are explicitly analytical fixtures.
"""
from decimal import Decimal, localcontext
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exact_decimal_twap_v1 import exact_five_open_twap, require_verified_bounds


class ExactFiveOpenContract(unittest.TestCase):
    def test_constant_boundary_fractional_cent_and_true_excess(self):
        exact = exact_five_open_twap(["7.6100"] * 5)
        self.assertEqual(exact, Decimal("7.61"))
        self.assertEqual(require_verified_bounds(exact, "6.23", "7.61"), exact)
        # The existing serialized binary64 value must still fail strict bounds.
        with self.assertRaisesRegex(ValueError, "outside verified bounds"):
            require_verified_bounds("7.610000000000001", "6.23", "7.61")
        with localcontext() as context:
            context.prec = 3  # The helper must not inherit caller rounding.
            fractional = exact_five_open_twap(["7.61", "7.61", "7.61", "7.61", "7.62"])
        self.assertEqual(fractional, Decimal("7.612"))
        self.assertEqual(require_verified_bounds(fractional, "6.23", "7.62"), fractional)
        with self.assertRaisesRegex(ValueError, "outside verified bounds"):
            require_verified_bounds(fractional, "6.23", "7.61")
        for malformed in ([7.61] * 5, [True] * 5, ["7.61"] * 4, ["NaN"] * 5):
            with self.assertRaises(ValueError):
                exact_five_open_twap(malformed)


if __name__ == "__main__":
    unittest.main()

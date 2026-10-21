"""Tests for the 2026-10-20 one-lot exception (run inside a patched copy of research/choice_pcf_automation)."""
import unittest
from decimal import Decimal
import workflow_rebalance as w


class LotException(unittest.TestCase):
    def test_ordinary_names_unchanged(self):
        for p in ('6.23', '170.72', '309.50', '499.40'):
            self.assertEqual(w.max_order_quantity(p, 'SZ000001'), w.max_order_quantity(p))
            self.assertLessEqual(w.reserved(p, w.max_order_quantity(p, 'SZ000001')), w.ORDER_CAP)
            self.assertEqual(w.order_value_cap('SZ000001', p, 100), w.ORDER_CAP)

    def test_high_priced_main_board_one_lot(self):
        self.assertEqual(w.max_order_quantity('660.70'), 0)                 # the old behaviour: never buyable
        self.assertEqual(w.max_order_quantity('660.70', 'SZ300308'), 100)
        self.assertEqual(w.order_value_cap('SZ300308', '660.70', 100), w.LOT_EXCEPTION_CAP)
        self.assertEqual(w.order_value_cap('SZ300308', '660.70', 200), w.ORDER_CAP)   # two lots: no exception
        self.assertFalse(w.lot_exception_slice('SZ300308', '660.70', 200))

    def test_star_slices(self):
        self.assertEqual(w.max_order_quantity('265.00', 'SH688536'), 200)
        self.assertTrue(w.lot_exception_slice('SH688536', '265.00', 200))
        self.assertTrue(w.lot_exception_slice('SH688536', '265.00', 300))
        self.assertFalse(w.lot_exception_slice('SH688536', '265.00', 400))
        self.assertFalse(w.lot_exception_slice('SH688536', '200.00', 300))  # 200 shares fit in 50,000: normal rules apply

    def test_hard_ceiling(self):
        self.assertFalse(w.lot_exception_slice('SZ000001', '2100.00', 100))  # one lot > 200,000: still not buyable
        self.assertEqual(w.max_order_quantity('2100.00', 'SZ000001'), 0)
        self.assertFalse(w.lot_exception_slice('SH688498', '1100.00', 200))
        self.assertEqual(w.max_order_quantity('1100.00', 'SH688498'), 0)

    def test_exception_respects_fee_reserve(self):
        p = '499.95'   # 100 shares + fees just above 50,000
        self.assertGreater(w.reserved(p, 100), w.ORDER_CAP)
        self.assertEqual(w.max_order_quantity(p, 'SZ000002'), 100)
        self.assertLessEqual(w.reserved(p, 100), w.LOT_EXCEPTION_CAP)


if __name__ == '__main__':
    unittest.main()

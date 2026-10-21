import unittest
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.security_identity import validate_known_name
from quanta_agents.factor_lab_a.choice_pcf import TradeError

class Tests(unittest.TestCase):
    def test_before_effective_date(self):
        self.assertEqual(validate_known_name('SZ300223','北京君正','2026-08-12')['expected_name'],'北京君正')
    def test_effective_date_inclusive(self):
        self.assertEqual(validate_known_name('SZ300223','君正股份','2026-08-13')['expected_name'],'君正股份')
    def test_old_name_rejected_after_change(self):
        with self.assertRaises(TradeError):validate_known_name('SZ300223','北京君正','2026-09-21')
    def test_future_name_not_backfilled(self):
        with self.assertRaises(TradeError):validate_known_name('SZ300223','君正股份','2026-08-12')
    def test_wrong_name_still_rejected(self):
        with self.assertRaises(TradeError):validate_known_name('SZ300223','君正集团','2026-09-21')

if __name__=='__main__':unittest.main()

"""Integrity checks using saved actual compound-action evidence (no fake prices)."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gru_research'))
from research_action_catalog import ResearchActionCatalog, verified_compound_rows
from scenario_action_catalog import IncompleteActionEvidence


class ActualCompoundActionTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'gru_research/trading_data/full_2025_2026/b84149c4b590f0e3c69e9cac.json'
        if not path.exists():
            self.skipTest('saved genuine sz301308 public query needed')
        self.data = path.read_bytes(); q = json.loads(self.data)
        self.h = hashlib.sha256(self.data).hexdigest()
        self.rows = []
        for i, values in enumerate(q['rows']):
            self.rows.append({**dict(zip(q['fields'], values)), 'source_provenance_json': json.dumps([
                {'source_sha256': self.h, 'source_row': i}])})
        self.coverage = [{'symbol': 'sz301308', 'operating_year': 2026, 'year_type': 'operate',
            'query_status': 'provider_success_nonempty', 'provider_rows': len(q['rows']),
            'source_sha256': self.h}]

    def catalog(self, rows):
        return ResearchActionCatalog(rows, self.coverage, source_identity='f'*64,
            query_bytes_by_sha256={self.h:self.data})

    def test_actual_annual_and_interim_components_retained(self):
        catalog = self.catalog(self.rows)
        self.assertEqual(len(catalog.records['sz301308']), 2)
        self.assertTrue(verified_compound_rows(('sz.301308', '2026-06-02'), self.rows))
        catalog.require_coverage('sz301308', [2026])
        with self.assertRaises(IncompleteActionEvidence):
            catalog.require_coverage('sz301308', [2025])

    def test_dropped_component_is_not_success_empty(self):
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.catalog(self.rows[:1])

    def test_changed_cash_source_field_rejected(self):
        changed = copy.deepcopy(self.rows)
        changed[0]['dividCashPsBeforeTax'] = '0'
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.catalog(changed)

    def test_unconfirmed_same_day_distribution_not_automatically_added(self):
        self.assertFalse(verified_compound_rows(('sz.301308', '2026-06-03'), self.rows))


if __name__ == '__main__':
    unittest.main()

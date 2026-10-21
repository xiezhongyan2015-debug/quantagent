"""Offline tests for exact source strings, duplicate clocks and cash conflicts."""
import hashlib
import io
import tempfile
import unittest
from . import recheck_historical_quote_candidates as r


def rows():
    return [{'k线结束时间': f'2022-01-04 {m//60:02d}:{m%60:02d}:00',
             **dict.fromkeys(r.FIELDS.values(), '1.00'), '成交量': '1', '成交额': '100'} for m in r.GRID]


class SourceSemantics(unittest.TestCase):
    def test_digest_follows_the_actual_decoded_csv_stream(self):
        raw = ('字段,值\r\n中文,123\r\n' * 10000).encode('gbk')
        with tempfile.TemporaryDirectory(dir=r.p.OUT) as directory:
            path = r.p.Path(directory) / 'sample.csv'
            path.write_bytes(raw)
            reader = r.DigestReader(path)
            with io.TextIOWrapper(io.BufferedReader(reader, 64), encoding='gbk', newline='') as stream:
                self.assertEqual(stream.read(), raw.decode('gbk'))
                self.assertEqual(reader.count, len(raw))
                self.assertEqual(reader.digest.hexdigest(), hashlib.sha256(raw).hexdigest())

    def test_real_auction_is_merged_without_reconstructing_any_quantity(self):
        native = rows()
        auction = {**native[0], 'k线结束时间': '2022-01-04 09:30:00',
                   '开盘价': '0.90', '最低价': '0.90', '成交量': '2', '成交额': '180'}
        bars = r.normalize_rows([auction] + native)
        self.assertEqual(str(bars[0]['open']), '0.90')
        self.assertEqual(bars[0]['volume'], 300)
        self.assertEqual(bars[0]['amount'], 280)
        self.assertEqual(sum(b['volume'] for b in bars), 24200)

    def test_duplicate_clock_and_fractional_shares_fail(self):
        with self.assertRaises(AssertionError):
            r.normalize_rows(rows() + rows()[:1])
        native = rows()
        native[0]['成交量'] = '0.001'
        with self.assertRaises(AssertionError):
            r.normalize_rows(native)

    def test_cash_difference_remains_a_conflict_even_if_coarse_price_gate_passes(self):
        bars = r.normalize_rows(rows())
        fields = ['date','time','code',*r.PRICE,'volume','amount','adjustflag']
        refs = []
        for i,m in enumerate(r.GRID[4::5]):
            refs.append(['2022-01-04', f'20220104{m//60:02d}{m%60:02d}00000', 'sz.000001',
                         '1','1','1','1','500','501' if i==0 else '500','3'])
        with tempfile.TemporaryDirectory(dir=r.p.OUT) as directory:
            leaf = r.p.Path(directory) / 'native.json'
            r.p.atomic_json(leaf, {'normalized_bars': [{f:str(v) for f,v in b.items()} for b in bars]})
            request = dict(symbol='000001.SZ',date='2022-01-04',slots=[0,1,2,3],old_native_amount_pass_by_slot={})
            query = dict(data=refs,fields=fields,error_code='0',wire_path='offline_test_only',sha256='offline',code='sz.000001')
            result = r.compare(request,query,leaf)
            self.assertEqual(result['strict_coarse_pass_slots'],[0,1,2,3])
            self.assertEqual(result['all_fields_exact_slots'],[1,2,3])
            self.assertEqual(result['comparisons'][0]['differences']['amount'],'-1')
            self.assertFalse(result['comparisons'][0]['exact']['amount'])


if __name__ == '__main__':
    unittest.main()

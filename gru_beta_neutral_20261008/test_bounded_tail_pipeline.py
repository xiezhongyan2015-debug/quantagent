"""Offline failure/recovery and bounded-batch regression checks."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from . import bounded_tail_pipeline as p
from . import stream_tail_minutes as m


class Response(io.BytesIO):
    url = 'https://example.invalid/pinned'
    status = 200
    headers = {}


class PipelineTest(unittest.TestCase):
    def table(self):
        n = p.BATCH_ROWS * 2 + 17
        return p.pa.table(dict(ticker=['000001', '600004'] * (n // 2) + ['000001'] * (n % 2),
                              time_s=list(range(n)), tran_id=list(range(n)),
                              price_x10000=[100000] * n, volume=[100] * n))

    def test_stream_selection_matches_source_across_row_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = self.table()
            p.pq.write_table(table, root / 'source.parquet', row_group_size=100001)
            result = p.stream_subset(p.pq.ParquetFile(root / 'source.parquet'), {'000001'}, root / 'subset.parquet')
            expected = table.filter(p.pc.equal(table['ticker'], '000001'))
            self.assertTrue(p.pq.ParquetFile(root / 'subset.parquet').read().equals(expected))
            self.assertEqual(result['all_source_rows_scanned'], len(table))
            self.assertGreater(result['batches'], 1)
            pf = p.pq.ParquetFile(root / 'subset.parquet')
            self.assertLessEqual(max(pf.metadata.row_group(i).num_rows for i in range(pf.num_row_groups)), p.BATCH_ROWS)
            self.assertFalse((root / 'subset.parquet.tmp').exists())

    def test_stat_guard_rejects_changed_certified_file(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'test.parquet'
            p.pq.write_table(self.table().slice(0, 2), file)
            r = dict(path=str(file), status=p.COMPLETE, verified_identity=p.identity(file), Parquet_footer_rows=2)
            p.guarded_source(r)
            with file.open('ab') as f:
                f.write(b'changed')
            with self.assertRaises(RuntimeError):
                p.guarded_source(r)

    def run_download(self, initial, replies):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / 'fixture.parquet'
            p.pq.write_table(self.table().slice(0, 7), payload)
            raw = payload.read_bytes()
            final = root / 'download.parquet'
            if initial:
                final.with_suffix('.parquet.part').write_bytes(raw[:initial])
            record = dict(path=str(final), filename='data/fixture.parquet', date='2026-09-23', prefix='00',
                          published_bytes=len(raw), published_LFS_SHA256=hashlib.sha256(raw).hexdigest(),
                          status='queued', attempts=[], received_bytes=initial)
            d = dict(repository='fixture/repo', pinned_revision='pinned', records=[record])
            seen = []

            def response(request, **kwargs):
                seen.append(request.headers.get('Range'))
                obj = Response(replies(raw, len(seen)))
                return obj

            with patch.object(p, 'OUT', root), patch.object(p, 'init_inventory', return_value=d), \
                 patch.object(p.urllib.request, 'urlopen', side_effect=response), patch.object(p.time, 'sleep'):
                p.download()
            return record, seen, final.read_bytes() if final.exists() else None

    def test_range_ignored_restarts_instead_of_appending(self):
        r, seen, raw = self.run_download(100, lambda data, n: data)
        self.assertEqual(r['status'], p.COMPLETE)
        self.assertTrue(r['attempts'][0]['server_ignored_range_restart'])
        self.assertEqual(seen, ['bytes=100-'])
        self.assertEqual(len(raw), r['published_bytes'])

    def test_truncated_body_cannot_be_certified(self):
        r, seen, raw = self.run_download(0, lambda data, n: data[:100])
        self.assertEqual(r['status'], 'failed_preserved')
        self.assertIsNone(raw)
        self.assertEqual(len(r['attempts']), 4)
        self.assertIn('Incomplete response body', r['attempts'][0]['traceback'])

    def test_resume_after_truncation_certifies_only_complete_payload(self):
        r, seen, raw = self.run_download(0, lambda data, n: data[:100] if n == 1 else data)
        self.assertEqual(r['status'], p.COMPLETE)
        self.assertEqual(seen, [None, 'bytes=100-'])
        self.assertEqual(len(raw), r['published_bytes'])

    def test_fixed_auction_and_minute_edges(self):
        seconds = p.np.array([33899,33900,33901,33902,33903,34200,34260,41400,41401,41402,41403,46800,54000,54001,54002,54003])
        mask, right = m.labels(seconds, 'closed_right')
        self.assertEqual(mask.tolist(), [False,True,True,True,False,True,True,True,True,True,False,True,True,True,True,False])
        self.assertEqual(right[mask].tolist(), [570,570,570,570,570,689,689,689,780,899,899,899])
        _, left = m.labels(seconds, 'floor_left')
        self.assertEqual(left[6], 571)

    def test_minute_aggregation_is_invariant_to_batch_splits_and_order(self):
        table = p.pa.table(dict(ticker=['000001']*6, time_s=[33901,34200,34260,34261,41402,54002],
            tran_id=[1,2,3,4,5,6], price_x10000=[10000,11000,9000,12000,13000,14000], volume=[100]*6))
        a, b = m.Aggregate(), m.Aggregate()
        for batch in table.to_batches():
            a.consume(batch)
        reverse = table.take(p.pa.array([5,4,3,2,1,0]))
        for batch in reverse.to_batches(max_chunksize=2):
            b.consume(batch)
        for model in m.MODELS:
            self.assertTrue(a.frame(model,'2026-09-23').equals(b.frame(model,'2026-09-23')))
        self.assertTrue(a.raw['000001']['strictly_increasing_ids'])
        self.assertFalse(b.raw['000001']['strictly_increasing_ids'])
        first = a.bars['closed_right']['000001',570]
        self.assertEqual((first['open'],first['close'],first['volume'],first['amount_x10000']), (10000,9000,300,3000000))


if __name__ == '__main__':
    unittest.main()

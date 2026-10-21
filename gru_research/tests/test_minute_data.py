"""Alignment, corruption and causality checks; fixtures never enter training."""
from pathlib import Path
import io
import sys
import unittest
import json
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minute_data as md
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def fixture(day='2020-01-02', missing=None, duplicate_conflict=False, zero_volume=False):
    midnight = pd.Timestamp(day, tz=md.TZ).tz_convert('UTC').value
    offsets = md.MINUTE_OFFSETS.copy()
    if missing is not None:
        offsets = offsets[offsets != missing]
    n = len(offsets)
    raw = 10 + np.arange(n) / 10000
    volume = np.zeros(n, np.int64) if zero_volume else np.full(n, 100, np.int64)
    nanos = midnight + offsets * 60 * 10**9
    d = pd.DataFrame({'股票代码': '000001', 'datetime': pd.to_datetime(nanos, utc=True).tz_convert(md.TZ).tz_localize(None).astype(str),
        'datetime_nano': nanos, 'open': raw, 'high': raw + .01, 'low': raw - .01, 'close': raw + .001,
        'volume': volume, 'qfq_ratio': 0.8, 'qfq_open': raw * .8, 'qfq_high': (raw + .01) * .8,
        'qfq_low': (raw - .01) * .8, 'qfq_close': (raw + .001) * .8})
    if duplicate_conflict:
        row = d.iloc[[0]].copy()
        row['open'] += .002
        d = pd.concat([d, row], ignore_index=True)
    out = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(d, preserve_index=False), out)
    return day, '000001.SZ', out.getvalue()


class MinuteDataTests(unittest.TestCase):
    def setUp(self):
        # Verified session dates exported from the local iFinD membership calendar.
        # Isolate the tests from the unversioned historical market data directory.
        sessions = json.loads((Path(__file__).parent / 'fixtures/calendar_next_sessions.json').read_text())
        def next_time(day):
            return int(pd.Timestamp(sessions[day], tz=md.TZ).tz_convert('UTC').value + md.EXEC_MINUTES[0] * 60 * 10**9)
        calendar = patch.object(md, 'next_known_open_time', side_effect=next_time)
        calendar.start()
        self.addCleanup(calendar.stop)

    def test_four_hours_and_five_execution_opens(self):
        rows, audit = md.aggregate_stock_day(fixture())
        self.assertEqual([r['minute_count'] for r in rows], [60] * 4)
        self.assertTrue(all(r['hour_complete'] for r in rows))
        self.assertEqual(audit['timestamp_mismatch_rows'], 0)
        self.assertEqual(audit['qfq_mismatch_rows'], 0)
        self.assertEqual(pd.Timestamp(rows[0]['signal_time'], tz='UTC').tz_convert(md.TZ).strftime('%H:%M'), '10:30')
        self.assertAlmostEqual(rows[1]['window_price'], np.mean(10 + np.arange(61, 66) / 10000) * .8)

    def test_missing_minute_and_conflicting_duplicates_do_not_create_prices(self):
        rows, audit = md.aggregate_stock_day(fixture(missing=631))
        self.assertFalse(rows[1]['hour_complete'])
        self.assertFalse(rows[1]['window_complete'])
        self.assertTrue(np.isnan(rows[1]['window_price']))
        rows, audit = md.aggregate_stock_day(fixture(duplicate_conflict=True))
        self.assertEqual(audit['duplicate_conflicts'], 1)
        self.assertFalse(any(r['hour_complete'] for r in rows))

    def test_suspension_has_no_executable_price(self):
        rows, audit = md.aggregate_stock_day(fixture(zero_volume=True))
        self.assertFalse(any(r['hour_complete'] for r in rows))
        self.assertFalse(any(r['window_complete'] for r in rows))
        self.assertTrue(all(np.isnan(r['window_price']) for r in rows))

    def test_lunch_overnight_entry_and_label_maturity(self):
        dates = ['2020-01-02', '2020-01-03', '2020-01-06']
        rows = sum((md.aggregate_stock_day(fixture(d))[0] for d in dates), [])
        f = md.add_features_and_labels(pd.DataFrame(rows), dates)
        def local(x):
            return pd.Timestamp(int(x), tz='UTC').tz_convert(md.TZ).strftime('%Y-%m-%d %H:%M')
        self.assertEqual(local(f.iloc[1].earliest_execution_time), '2020-01-02 13:01')
        self.assertEqual(local(f.iloc[3].earliest_execution_time), '2020-01-03 09:31')
        self.assertEqual(local(f.iloc[3].label_end_time4), '2020-01-06 09:36')
        self.assertEqual(local(f.iloc[-1].earliest_execution_time), '2020-01-07 09:31')
        self.assertTrue((f.earliest_execution_time > f.signal_time).all())
        self.assertTrue(np.all(np.diff(f.iloc[0][[f'label_end_time{n}' for n in range(1, 5)]].to_numpy(float)) > 0))
        self.assertTrue(f.iloc[-1][['y1', 'y2', 'y3', 'y4']].isna().all())

    def test_future_price_change_leaves_past_features_unchanged(self):
        dates = pd.bdate_range('2020-01-01', periods=30).strftime('%Y-%m-%d').tolist()
        rows = sum((md.aggregate_stock_day(fixture(d))[0] for d in dates), [])
        source = pd.DataFrame(rows)
        baseline = md.add_features_and_labels(source.copy(), dates)
        future = source.copy()
        affected = future.date.ge(dates[25])
        future.loc[affected, ['open', 'high', 'low', 'close']] *= 2
        future.loc[affected, 'window_price'] *= 2
        changed = md.add_features_and_labels(future, dates)
        np.testing.assert_allclose(baseline.loc[:99, md.HOUR_FEATURES], changed.loc[:99, md.HOUR_FEATURES], equal_nan=True)
        # Missing a real calendar hour breaks full windows even if neighbors exist.
        incomplete = source.copy()
        incomplete.loc[60, 'hour_complete'] = False
        q = md.add_features_and_labels(incomplete, dates)
        self.assertFalse(q.loc[79:119, 'window80_complete'].any())


if __name__ == '__main__':
    unittest.main()

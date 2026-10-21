import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from ifind_parallel_dispatch import Dispatcher, normalize_result


class DispatchTest(unittest.TestCase):
    def run_fake(self, count, fail=False, concurrency=4, spacing=0.05, latency=0.18):
        with tempfile.TemporaryDirectory(prefix='ifind_dispatch_', dir=Path(__file__).resolve().parent.parent / 'data_expansion') as folder:
            root = Path(folder)
            (root / 'jobs').mkdir()
            (root / 'results').mkdir()
            for i in range(count):
                (root / 'jobs' / f'{i:03}.json').write_text(json.dumps(dict(function='THS_HF', args=[i])))
            calls, active, peaks = [], [0], [0]
            lock = threading.Lock()

            def fake(identifier):
                start, thread = time.perf_counter(), threading.get_ident()
                with lock:
                    active[0] += 1
                    peaks[0] = max(peaks[0], active[0])
                time.sleep(0.015 if fail else latency)
                finish = time.perf_counter()
                with lock:
                    active[0] -= 1
                    calls.append((identifier, start, finish, thread))
                return SimpleNamespace(errorcode=-999 if fail else 0, errmsg='test', data=[], time=[], thscode=[], dataVol=0)

            def save(path, value):
                path.write_text(json.dumps(value))

            states = []

            def state(status, **data):
                states.append(status)
                if data.get('completed_requests') == count or status == 'blocked':
                    (root / 'STOP_WORKER').touch()

            scheduler = Dispatcher(SimpleNamespace(THS_HF=fake), root, save, state,
                                   concurrency=concurrency, spacing=spacing, cooldown=0.07, poll=0.002)
            errors = []

            def run():
                try:
                    scheduler.run()
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            thread.join(5)
            if thread.is_alive():
                (root / 'STOP_WORKER').touch()
                thread.join(2)
                self.fail('Dispatcher did not stop')
            self.assertFalse(errors)
            results = [json.loads(p.read_text()) for p in (root / 'results').glob('*.json')]
            return sorted(calls, key=lambda row: row[1]), peaks[0], results, states

    def test_bounded_overlap_spacing_cooldown_and_no_duplicates(self):
        calls, peak, results, _ = self.run_fake(12)
        self.assertEqual(len(calls), 12)
        self.assertEqual(len({row[0] for row in calls}), 12)
        self.assertEqual(len(results), 12)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)
        for previous, current in zip(calls, calls[1:]):
            self.assertGreaterEqual(current[1] - previous[1], 0.048)
        by_thread = {}
        for call in calls:
            previous = by_thread.get(call[3])
            if previous:
                self.assertGreaterEqual(call[1] - previous[2], 0.068)
            by_thread[call[3]] = call

    def test_api_error_stops_new_requests(self):
        calls, _, results, states = self.run_fake(8, fail=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results[0]['errorcode'], -999)
        self.assertIn('blocked', states)

    def test_statistics_dict_is_preserved(self):
        raw = dict(errorcode=0, tables=[{'remaining': 1234}])
        self.assertEqual(normalize_result(raw)['data'], raw)

    def test_twelve_workers_do_not_duplicate_requests(self):
        calls, peak, results, _ = self.run_fake(24, concurrency=12, spacing=0.02, latency=0.5)
        self.assertEqual(peak, 12)
        self.assertEqual(len({row[0] for row in calls}), 24)
        self.assertEqual(len(results), 24)

    def test_runtime_settings_reload_and_reject_unsafe_values(self):
        with tempfile.TemporaryDirectory(prefix='ifind_config_', dir=Path(__file__).resolve().parent.parent / 'data_expansion') as folder:
            root = Path(folder)
            scheduler = Dispatcher(None, root, None, None)
            scheduler.last_launch = time.perf_counter()
            config = root / 'runtime_config.json'
            config.write_text(json.dumps(dict(concurrency=12, global_start_spacing_seconds=0.5, per_request_cooldown_seconds=2)))
            scheduler.refresh_config()
            self.assertEqual((scheduler.concurrency, scheduler.spacing, scheduler.cooldown), (12, 0.5, 2))
            self.assertEqual(scheduler.next_launch, scheduler.last_launch + 0.5)
            for bad in [dict(concurrency=13), dict(global_start_spacing_seconds=0.1), dict(per_request_cooldown_seconds=0)]:
                config.write_text(json.dumps(bad))
                scheduler.last_config_check = 0
                with self.assertRaises(ValueError):
                    scheduler.refresh_config()

    def test_fundamentals_can_accelerate_but_remain_below_official_limit(self):
        with tempfile.TemporaryDirectory(prefix='ifind_fast_', dir=Path(__file__).resolve().parent.parent / 'data_expansion') as folder:
            root = Path(folder) / 'ifind_fundamental_control'
            root.mkdir()
            scheduler = Dispatcher(None, root, None, None)
            config = root / 'runtime_config.json'
            config.write_text(json.dumps(dict(concurrency=12, global_start_spacing_seconds=0.15, per_request_cooldown_seconds=0.5)))
            scheduler.refresh_config()
            self.assertEqual((scheduler.concurrency, scheduler.spacing, scheduler.cooldown), (12, 0.15, 0.5))
            config.write_text(json.dumps(dict(global_start_spacing_seconds=0.1)))
            scheduler.last_config_check = 0
            with self.assertRaises(ValueError):
                scheduler.refresh_config()


if __name__ == '__main__':
    unittest.main()

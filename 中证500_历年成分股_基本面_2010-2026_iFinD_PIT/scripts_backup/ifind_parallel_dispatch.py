"""Bounded API concurrency with global request spacing and per-slot cooldown."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


ALLOWED = {'THS_HF', 'THS_DP', 'THS_Date_Query', 'THS_BD', 'THS_HQ', 'THS_DataStatistics', 'THS_DS', 'THS_GetErrorInfo'}


def normalize_result(result):
    if isinstance(result, dict):
        return dict(errorcode=result.get('errorcode', 0), errmsg=result.get('errmsg', ''),
                    data=result, time=[], thscode=[], dataVol=result.get('dataVol', 0))
    return dict(errorcode=result.errorcode, errmsg=result.errmsg, data=result.data,
                time=result.time, thscode=result.thscode, dataVol=result.dataVol)


class Dispatcher:
    def __init__(self, api, root, save, state, concurrency=4, spacing=5.0, cooldown=5.0, poll=0.25):
        self.api, self.root, self.save, self.state = api, root, save, state
        self.concurrency = concurrency
        self.spacing, self.cooldown, self.poll = spacing, cooldown, poll
        self.jobs, self.results = root / 'jobs', root / 'results'
        self.log_lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.last_api_start = None
        self.next_launch = 0.0
        self.inflight = {}
        self.completed = 0
        self.blocked = None
        self.last_launch = None
        self.last_config_check = 0.0

    def refresh_config(self):
        """Apply bounded local runtime settings without logging in again."""
        now = time.perf_counter()
        if now - self.last_config_check < 1:
            return
        self.last_config_check = now
        path = self.root / 'runtime_config.json'
        if not path.exists():
            return
        try:
            config = json.loads(path.read_text(encoding='utf-8'))
        except PermissionError:
            return  # Retry on next refresh if Windows temporarily locks the file.
        concurrency = int(config.get('concurrency', self.concurrency))
        spacing = float(config.get('global_start_spacing_seconds', self.spacing))
        cooldown = float(config.get('per_request_cooldown_seconds', self.cooldown))
        accelerated = self.root.name == 'ifind_fundamental_control'
        min_spacing, min_cooldown = (0.15, 0.5) if accelerated else (0.5, 2)
        if not 1 <= concurrency <= 12 or not min_spacing <= spacing <= 60 or not min_cooldown <= cooldown <= 60:
            raise ValueError('Runtime settings outside allowed limits')
        self.concurrency, self.spacing, self.cooldown = concurrency, spacing, cooldown
        if accelerated:
            self.poll = 0.025
        if self.last_launch is not None:
            self.next_launch = self.last_launch + self.spacing

    def event(self, value):
        with self.log_lock:
            with (self.root / 'request_events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(value, ensure_ascii=False) + '\n')

    def request(self, path):
        started = time.perf_counter()
        started_at = datetime.now().isoformat()
        function = None
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
            function = job['function']
            if function not in ALLOWED:
                raise ValueError('Function not allowed')
            with self.start_lock:
                if self.last_api_start is not None:
                    delay = self.spacing - (time.perf_counter() - self.last_api_start)
                    if delay > 0:
                        time.sleep(delay)
                started = time.perf_counter()
                started_at = datetime.now().isoformat()
                self.event(dict(event='start', job=path.stem, function=function, time=started_at))
                self.last_api_start = time.perf_counter()
            response = normalize_result(getattr(self.api, function)(*job['args'], **job.get('kwargs', {})))
        except Exception as exc:
            response = dict(errorcode='LOCAL_ERROR', errmsg=str(exc))
        response['request_metrics'] = dict(started_at=started_at, finished_at=datetime.now().isoformat(),
                                           duration_seconds=round(time.perf_counter() - started, 4))
        self.save(self.results / path.name, response)
        if response['errorcode'] != 0:
            self.blocked = dict(job=path.stem, errorcode=response['errorcode'], errmsg=response.get('errmsg', ''))
        self.event(dict(event='finish', job=path.stem, function=function,
                        time=response['request_metrics']['finished_at'], errorcode=response['errorcode'],
                        duration_seconds=response['request_metrics']['duration_seconds'],
                        dataVol=response.get('dataVol')))
        # A slot cannot accept another request until this pause has elapsed.
        time.sleep(self.cooldown)
        return response['errorcode'], response.get('errmsg', '')

    def run(self):
        last_state = None
        with ThreadPoolExecutor(max_workers=12) as pool:
            while True:
                self.refresh_config()
                for name, (future, path) in list(self.inflight.items()):
                    if future.done():
                        error, message = future.result()
                        del self.inflight[name]
                        self.completed += 1
                stopping = (self.root / 'STOP_WORKER').exists()
                if stopping and not self.inflight:
                    self.state('stopped', completed_requests=self.completed)
                    return
                if (self.root / 'RESET_ERRORS').exists():
                    (self.root / 'RESET_ERRORS').unlink()
                    self.blocked = None
                if not stopping and not self.blocked and len(self.inflight) < self.concurrency and time.perf_counter() >= self.next_launch:
                    pending = []
                    for path in self.jobs.glob('*.json'):
                        if path.name in self.inflight or (self.results / path.name).exists():
                            continue
                        try:
                            pending.append((path.stat().st_mtime_ns, path.name, path))
                        except FileNotFoundError:
                            continue  # A completed response was just consumed by the writer.
                    pending.sort()
                    if pending:
                        path = pending[0][2]
                        future = pool.submit(self.request, path)
                        self.inflight[path.name] = (future, path)
                        self.last_launch = time.perf_counter()
                        self.next_launch = self.last_launch + self.spacing
                status = 'draining' if stopping else 'blocked' if self.blocked else 'requesting' if self.inflight else 'ready'
                current = (status, tuple(self.inflight), self.completed, self.concurrency, self.spacing, self.cooldown)
                if current != last_state:
                    self.state(status, concurrency=self.concurrency, active_jobs=list(self.inflight),
                               completed_requests=self.completed, global_start_spacing_seconds=self.spacing,
                               per_slot_cooldown_seconds=self.cooldown, error=self.blocked)
                    last_state = current
                time.sleep(self.poll)

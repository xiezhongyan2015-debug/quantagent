"""Operation-local immutable reads and bounded content-keyed native tax history.

No current inventory substitutes for historical inventory. Each top-level
adapter call captures the actual ledger anew; only nested reads reuse it.
Cross-call caching is restricted to the pure _tax_history result, keyed by
fresh exact trade content, symbol, record date and requested through date.
Integrity/registration/unsupported-action checks are never cached across calls.
"""
from collections import OrderedDict
from functools import wraps
import hashlib
import json
import threading

HISTORY_CACHE_ENTRIES = 8
HISTORY_CACHE_BYTES = 2 * 1024**2  # Per adapter; cached values are immutable bytes.


def _immutable(*args, **kwargs):
    raise TypeError("tax operation snapshot is immutable")


class _FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable


class _FrozenList(list):
    __setitem__ = __delitem__ = append = extend = insert = remove = pop = clear = sort = reverse = __iadd__ = __imul__ = _immutable


def _freeze(value):
    if type(value) is dict:
        return _FrozenDict((key, _freeze(item)) for key, item in value.items())
    if type(value) is list:
        return _FrozenList(_freeze(item) for item in value)
    return value


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def install(adapter_module):
    """Patch only the caller's isolated adapter class, leaving native files intact."""
    adapter_type = adapter_module.CashDividendAdapter
    ledger_type = adapter_module.RawShareLedger
    original_history = adapter_type._tax_history
    original_trades = adapter_type._trade_events
    original_integrity = adapter_type.validate_eod_integrity
    original_record_check = adapter_type._validate_record_journal
    cache_lock = threading.RLock()

    class OperationView(ledger_type):
        def __init__(self, ledger):
            self.origin = ledger
            self.commit = (id(ledger._state), ledger._previous_hash, len(ledger._journal))
            self.memo = {}

        def assert_unchanged(self):
            if self.commit != (id(self.origin._state), self.origin._previous_hash, len(self.origin._journal)):
                raise adapter_module.AdapterError("ledger advanced during tax snapshot operation")

        def _capture(self, key, function):
            if key not in self.memo:
                self.assert_unchanged()
                self.memo[key] = _freeze(function())
                self.assert_unchanged()
            return self.memo[key]

        @property
        def journal(self):
            return self._capture("journal", lambda: self.origin.journal)

        @property
        def genesis(self):
            return self._capture("genesis", lambda: self.origin.genesis)

        def snapshot(self):
            return self._capture("snapshot", self.origin.snapshot)

        def apply(self, *args, **kwargs):
            raise adapter_module.AdapterError("tax operation view cannot apply ledger events")

    def operation(function):
        @wraps(function)
        def wrapped(self, ledger, *args, **kwargs):
            if isinstance(ledger, OperationView):
                return function(self, ledger, *args, **kwargs)
            # Non-native test/protocol objects retain uncached reference behavior.
            if not isinstance(ledger, ledger_type):
                return function(self, ledger, *args, **kwargs)
            view = OperationView(ledger)
            result = function(self, view, *args, **kwargs)
            view.assert_unchanged()
            return result
        return wrapped

    def trade_events(self, ledger, through=None):
        if not isinstance(ledger, OperationView):
            return original_trades(self, ledger, through)
        key = ("trades", self._spec.symbol, through)
        if key not in ledger.memo:
            ledger.memo[key] = _freeze(original_trades(self, ledger, through))
        return ledger.memo[key]

    def check_once(original, tag):
        @wraps(original)
        def checked(self, ledger):
            if not isinstance(ledger, OperationView):
                return original(self, ledger)
            key = (tag, id(self), self._spec.action_id, self._spec.symbol, self._spec.record_date)
            if key not in ledger.memo:
                original(self, ledger)
                ledger.memo[key] = True
        return checked

    def tax_history(self, ledger, through):
        if not isinstance(ledger, OperationView):
            return original_history(self, ledger, through)
        # Every operation checks the fresh complete journal, even when the pure
        # reconstruction has a cached answer for the selected trade prefix.
        self.validate_eod_integrity(ledger)
        for action in ledger.snapshot()["actions"].values():
            if action["symbol"] == self._spec.symbol and action.get("active") and action.get("bonus_entitled", 0):
                raise adapter_module.UnsupportedCorporateAction("stock-distribution tax lineage is not implemented")
        key_input = [self._spec.symbol, self._spec.record_date, through.isoformat(), self._trade_events(ledger, through)]
        key = hashlib.sha256(_bytes(key_input)).hexdigest()
        with cache_lock:
            cache = getattr(self, "_v4_history_cache", None)
            if cache is None:
                cache = self._v4_history_cache = OrderedDict()
                self._v4_history_bytes = 0
                self._v4_history_stats = {"hits": 0, "misses": 0}
            if key in cache:
                encoded = cache.pop(key)
                cache[key] = encoded
                self._v4_history_stats["hits"] += 1
                return json.loads(encoded)
        # Original FIFO/day-netting algorithm constructs the historical answer.
        result = original_history(self, ledger, through)
        encoded = _bytes(result)
        with cache_lock:
            self._v4_history_stats["misses"] += 1
            if len(encoded) <= HISTORY_CACHE_BYTES:
                prior = cache.pop(key, None)
                if prior is not None:
                    self._v4_history_bytes -= len(prior)
                cache[key] = encoded
                self._v4_history_bytes += len(encoded)
                while len(cache) > HISTORY_CACHE_ENTRIES or self._v4_history_bytes > HISTORY_CACHE_BYTES:
                    _, removed = cache.popitem(last=False)
                    self._v4_history_bytes -= len(removed)
        return result

    adapter_type._trade_events = operation(trade_events)
    adapter_type.validate_eod_integrity = operation(check_once(original_integrity, "integrity"))
    adapter_type._validate_record_journal = operation(check_once(original_record_check, "registration"))
    adapter_type._tax_history = operation(tax_history)
    # Nested methods receive the same immutable view and cannot advance it.
    for name in ("record_event", "activation_event", "fifo_sale_allocations", "tax_evidence",
                 "tax_assessment_event", "settlement_events", "tax_payment_event", "tax_reserve",
                 "_validate_assessed_tax"):
        setattr(adapter_type, name, operation(getattr(adapter_type, name)))
    return OperationView

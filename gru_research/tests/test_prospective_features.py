"""Source adapter integrity fixtures only; no fixture is a market prediction."""
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from gru_research.minute_data import MINUTE_OFFSETS, aggregate_stock_day
from gru_research.prospective import (ExchangeCalendar, FeatureWindow, FrozenContract,
    IntegrityError, Pending, canonical, file_hash, local_ns)
from gru_research.prospective_features import (DAILY16, FUND16, AGES2, HOUR_FEATURES,
    HOUR_RAW, FrozenFeatures, IncrementalSources, FeatureAdapter, feature_only_hours,
    merge_daily_sources, slow_rows, write_window)


def minute_fixture(day, symbol, price_scale=10):
    midnight = local_ns(day, 0, 0)
    minute = np.arange(240)
    u = int(symbol[:6])
    close = (10 + u) * np.exp(.0002 * minute + .001 * np.sin(minute / 3 + u))
    open_ = close * np.exp(-.0001 * np.cos(minute / 7 + u))
    nano = midnight + MINUTE_OFFSETS.astype(np.int64) * 60_000_000_000
    data = {"股票代码": [symbol[:6]] * 240,
            "datetime": pd.to_datetime(nano, utc=True).tz_convert("Asia/Shanghai").tz_localize(None),
            "datetime_nano": nano, "open": open_, "high": np.maximum(open_, close) * 1.001,
            "low": np.minimum(open_, close) * .999, "close": close,
            "volume": (100 + minute + u).astype(float), "qfq_ratio": np.repeat(float(price_scale), 240),
            "available_at_ns": nano + 60_000_000_000}
    for field in ("open", "high", "low", "close"):
        data["qfq_" + field] = data[field] * price_scale
    return pd.DataFrame(data)


def parquet(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return {"path": str(path), "sha256": file_hash(path)}


class AdapterFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.symbols = [f"{i:06d}.SZ" for i in range(4)]
        self.dates = tuple(str(np.busday_offset("2030-01-01", i)) for i in range(40))
        self.exchange = ExchangeCalendar(self.dates, "synthetic fixture NOT official", self.dates[0], self.dates[-1], True)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.rawdir = self.root / "gru_research/artifacts/minute_hourly_raw"
        self.dailyroot = self.root / "daily"
        self.fundroot = self.root / "fund"
        self.fundroot.mkdir()
        times = self.exchange.signals[:100]
        shapes = (100, 4)
        arrays = {"times": times, "X": np.zeros(shapes + (60,), np.float32),
                  "industry": np.zeros(shapes, np.int64), "members": np.ones(shapes, bool),
                  "hour_complete": np.ones(shapes, bool), "hour_ohlcv": np.zeros(shapes + (10,), np.float32)}
        daily = []
        for di, day in enumerate(self.dates[:25]):
            stockdays = []
            for u, symbol in enumerate(self.symbols):
                f = minute_fixture(day, symbol)
                output = io.BytesIO()
                pq.write_table(pa.Table.from_pandas(f, preserve_index=False), output)
                rows, audit = aggregate_stock_day((day, symbol, output.getvalue()))
                self.assertEqual(audit["error"], "")
                frame = pd.DataFrame(rows)
                stockdays.append(frame)
                arrays["hour_ohlcv"][4 * di:4 * di + 4, u] = frame[HOUR_RAW].to_numpy()
                daily.append({"date": day, "thscode": symbol, "open": frame.open.iloc[0],
                    "high": frame.high.max(), "low": frame.low.min(), "close": frame.close.iloc[-1],
                    "volume": frame.volume.sum(), "amount": float((f.close * f.volume).sum()), "turnoverRatio": 4.0})
            parquet(pd.concat(stockdays, ignore_index=True), self.rawdir / (day + ".parquet"))
        parquet(pd.DataFrame(daily), self.dailyroot / "bars/2030/source.parquet")
        self.financial = []
        for symbol in self.symbols:
            for year in (2028, 2029):
                self.financial.append({"thscode": symbol, "asof_date": "2030-01-01", "report_period": f"{year}-12-31",
                    "total_assets": 100., "parent_equity": 50., "revenue": 30., "parent_net_profit": 3.,
                    "operating_cost": 20., "total_liabilities": 40., "current_assets": 30., "current_liabilities": 10.,
                    "cash": 5., "accounts_receivable": 4., "inventory": 3., "operating_cash_flow": 4.,
                    "capex_cash_paid": 2., "rd_expense": 1.})
        parquet(pd.DataFrame(self.financial), self.fundroot / "financial_verified_19_fields.parquet")
        self.market = pd.DataFrame([{"thscode": s, "asof_date": "2030-01-01", "total_market_cap_cny": 500., "industry_code_source": "unknown"} for s in self.symbols])
        parquet(self.market, self.fundroot / "weekly_market_and_industry.parquet")
        selected = [{"id": "fixture_range", "canonical": "high / low - 1", "required_fields": ["high", "low"]},
                    {"id": "fixture_amount", "canonical": "turnover * amount", "required_fields": ["turnover", "amount"]}]
        self.selection_path = self.root / "selection.json"
        self.selection_path.write_bytes(canonical({"selected": selected}))
        for name, value in arrays.items():
            np.save(self.dataset / (name + ".npy"), value)
        (self.dataset / "symbols.json").write_bytes(canonical(self.symbols))
        fields = HOUR_FEATURES + DAILY16 + ["factor_" + d["id"] for d in selected] + FUND16 + AGES2
        self.meta = {"features": fields, "factor_selection_sha256": file_hash(self.selection_path),
            "groups": {"hour": list(range(24)), "daily": list(range(24, 40)), "factors": [40, 41], "fund": list(range(42, 58)), "ages": [58, 59]},
            "causality_audit": {"passed": True}, "raw_market_data_is_synthetic": True,
            "n_industries": 1, "data_fingerprint": "synthetic fixture not research evidence",
            "normalization": "1/99 percentile clipping, population z-score, +-5 cap; current true members at each historical timestamp; NaNs retain runtime missing masks",
            "files_sha256": {p.name: file_hash(p) for p in self.dataset.iterdir()}}
        (self.dataset / "meta.json").write_bytes(canonical(self.meta))
        fingerprint = hashlib.sha256(json.dumps(self.meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        paths = [str(self.root / f"fixture{s}.pt") for s in (11, 22, 33, 44, 55)]
        freeze = {"dataset_fingerprint": fingerprint, "frozen_at_utc": datetime.fromtimestamp(local_ns(self.dates[24], 16, 0) / 1e9, timezone.utc).isoformat(),
            "config": {"sequence_length": 80, "dtype": "float32", "min_stocks": 3, "seeds": [11, 22, 33, 44, 55]},
            "selection": {"selected_label": 4}, "evaluation_models": {"selected_full": paths},
            "checkpoint_hashes": {p: hashlib.sha256(p.encode()).hexdigest() for p in paths}, "no_test_driven_tuning": True}
        freeze["freeze_id"] = hashlib.sha256(json.dumps({"data": fingerprint, "config": freeze["config"], "selection": freeze["selection"], "hashes": freeze["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
        self.freeze_path = self.root / "freeze.json"
        self.freeze_path.write_bytes(canonical(freeze))
        self.contract = FrozenContract.read(self.freeze_path, self.dataset / "meta.json", fixture=True)
        self.base = FrozenFeatures.read(self.dataset, self.selection_path)
        self.signal = local_ns(self.dates[25], 10, 30)
        self.patches = [patch("gru_research.prospective_features.ROOT", self.root),
                       patch("gru_research.prospective_features.DAILY_ROOT", self.dailyroot),
                       patch("gru_research.prospective_features.FUND_ROOT", self.fundroot)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def source(self, gap=False):
        audit = self.root / "fixture_audit.json"
        audit.write_bytes(canonical({"passed": True, "source_semantics_verified": True, "research_evidence": False}))
        minutes = []
        for symbol in self.symbols:
            for day in self.dates[24:26]:
                f = minute_fixture(day, symbol, price_scale=20)
                if gap and day == self.dates[25]:
                    f = f.iloc[1:]
                item = parquet(f, self.root / "minutes" / (day + "_" + symbol + ".parquet"))
                item.update(date=day, symbol=symbol, timestamp_semantics="left/start", volume_unit="shares", price_basis="qfq")
                minutes.append(item)
        membership = parquet(pd.DataFrame({"date": [self.dates[25]] * 4, "symbol": self.symbols,
            "available_at_ns": np.repeat(local_ns(self.dates[25], 0, 0), 4)}), self.root / "members.parquet")
        spec = {"schema_version": 1, "raw_market_data_is_synthetic": True,
            "audit": {"path": str(audit), "sha256": file_hash(audit)}, "minutes": minutes, "membership": membership, "daily": []}
        path = self.root / "source.json"
        path.write_bytes(canonical(spec))
        return IncrementalSources.read(path, fixture=True)

    def test_actual_source_flow_creates_features_only_snapshot_and_preserves_old_rows(self):
        adapter = FeatureAdapter(self.base, self.exchange, self.source())
        now = self.signal + 30_000_000_000
        window = adapter.build(self.signal, self.contract, now, fixture=True)
        self.assertEqual(window.X.shape, (80, 4, 60))
        self.assertTrue(window.hour_complete.all())
        self.assertEqual(window.metadata["adapter_audit"]["complete80_member_stocks"], 4)
        self.assertFalse(window.metadata["adapter_audit"]["factor_selection_rerun"])
        self.assertTrue(np.array_equal(window.X[:-1], np.zeros_like(window.X[:-1])))
        self.assertTrue(np.isfinite(window.X[-1, :, :24]).any())
        for bridge in window.metadata["adapter_audit"]["adjustment_bridges"].values():
            self.assertAlmostEqual(bridge["new_adjusted_to_frozen_scale"], .5)
        receipt = write_window(window, self.root / "output")
        loaded = FeatureWindow.read(receipt["path"], receipt["manifest_path"])
        self.assertTrue(loaded.validate(self.contract, self.exchange, now, True).all())
        with np.load(receipt["path"]) as arrays:
            self.assertNotIn("labels", arrays.files)
            self.assertNotIn("y4", arrays.files)
        with self.assertRaises(FileExistsError):
            write_window(window, self.root / "output")

    def test_missing_minute_never_becomes_complete_or_a_scored_window(self):
        adapter = FeatureAdapter(self.base, self.exchange, self.source(gap=True))
        with self.assertRaisesRegex(Pending, "real_complete80_member_count_below_minimum"):
            adapter.build(self.signal, self.contract, self.signal + 30_000_000_000, fixture=True)

    def test_pre_signal_cache_is_reused_and_changed_source_cannot_repaint_it(self):
        sources = self.source()
        adapter = FeatureAdapter(self.base, self.exchange, sources, self.root / "slow_cache")
        prime = adapter.prime_slow(self.signal, self.signal - 900_000_000_000, fixture=True)
        self.assertEqual(prime["status"], "known_slow_values_cached")
        self.assertEqual(prime["forecasts_created"], 0)
        self.assertFalse(prime["research_evidence"])
        receipt = json.loads(Path(prime["cache"]["receipt_path"]).read_text())
        self.assertLess(receipt["prepared_at_ns"], self.signal)
        self.assertEqual(receipt["identity"]["exchange_calendar_sha256"], self.exchange.fingerprint)
        self.assertEqual(len(receipt["identity"]["frozen_operator_sha256"]), 7)
        self.assertEqual(receipt["identity"]["previous_complete_sessions"][self.dates[25]], self.dates[24])
        # The actual computation ran during prime. A cache hit must not run it again.
        with patch("gru_research.prospective_features.slow_rows", side_effect=AssertionError("cache not reused")):
            window = adapter.build(self.signal, self.contract, self.signal + 30_000_000_000, fixture=True)
        self.assertTrue(window.metadata["adapter_audit"]["slow_cache"]["hit"])
        original_bytes = Path(prime["cache"]["path"]).read_bytes()
        membership_path = Path(sources.spec["membership"]["path"])
        membership = pd.read_parquet(membership_path)
        membership.available_at_ns += 1
        sources.spec["membership"].update(parquet(membership, membership_path))
        from gru_research.prospective_features import prepare_slow_values
        membership, memberships, days = adapter.target_memberships(self.signal, self.signal, True)
        with self.assertRaisesRegex(Pending, "valid_slow_cache_not_primed"):
            prepare_slow_values(self.base, self.exchange, sources, membership, memberships, days,
                self.signal, self.signal + 30_000_000_000, adapter.slow_cache, True, True)
        self.assertEqual(Path(prime["cache"]["path"]).read_bytes(), original_bytes)

    def test_cache_cannot_be_created_after_signal_or_modified_after_priming(self):
        adapter = FeatureAdapter(self.base, self.exchange, self.source(), self.root / "slow_cache")
        with self.assertRaisesRegex(Pending, "actual_pre_signal_clock"):
            adapter.prime_slow(self.signal, self.signal + 1, fixture=True)
        self.assertFalse(adapter.slow_cache.exists())
        prime = adapter.prime_slow(self.signal, self.signal - 900_000_000_000, fixture=True)
        path = Path(prime["cache"]["path"])
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(IntegrityError, "slow cache was altered"):
            adapter.build(self.signal, self.contract, self.signal + 30_000_000_000, fixture=True)

    def test_later_daily_arrival_invalidates_cache_even_with_unchanged_file_bytes(self):
        from gru_research.prospective_features import prepare_slow_values
        sources = self.source()
        row = pd.DataFrame({"date": [self.dates[24]], "symbol": [self.symbols[0]],
            "open": [10.], "high": [11.], "low": [9.], "close": [10.], "volume": [100.],
            "amount": [1000.], "turnoverRatio": [4.], "available_at_ns": [self.signal - 60_000_000_000]})
        item = parquet(row, self.root / "late_daily.parquet")
        item.update(price_basis="raw", volume_unit="shares", amount_unit="CNY", turnover_unit="percent")
        sources.spec["daily"] = [item]
        adapter = FeatureAdapter(self.base, self.exchange, sources, self.root / "slow_cache")
        prime = adapter.prime_slow(self.signal, self.signal - 900_000_000_000, fixture=True)
        old_sha = file_hash(item["path"])
        membership, memberships, days = adapter.target_memberships(self.signal, self.signal, True)
        with self.assertRaisesRegex(Pending, "valid_slow_cache_not_primed"):
            prepare_slow_values(self.base, self.exchange, sources, membership, memberships, days,
                self.signal, self.signal + 30_000_000_000, adapter.slow_cache, True, True)
        self.assertEqual(file_hash(item["path"]), old_sha)
        self.assertTrue(Path(prime["cache"]["path"]).is_file())

    def test_source_bytes_units_and_clock_are_checked(self):
        sources = self.source()
        sources.spec["minutes"][0]["volume_unit"] = "lots_unverified"
        with self.assertRaises(Pending):
            sources.minute_rows(self.signal)
        sources = self.source()
        path = Path(sources.spec["minutes"][0]["path"])
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(IntegrityError):
            sources.minute_rows(self.signal)
        times_path = self.dataset / "times.npy"
        times_path.write_bytes(times_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(IntegrityError, "historical clock/universe bytes changed"):
            FrozenFeatures.read(self.dataset, self.selection_path)

    def test_future_bars_and_unarrived_daily_versions_do_not_enter_earlier_inputs(self):
        sources = self.source()
        first, _ = sources.minute_rows(self.signal)
        item = sources.spec["minutes"][-1]
        f = pd.read_parquet(item["path"])
        future = f.datetime_nano >= self.signal
        f.loc[future, ["close", "qfq_close"]] = -999.
        item.update(parquet(f, Path(item["path"])))
        second, _ = sources.minute_rows(self.signal)
        pd.testing.assert_frame_equal(first, second)
        old = pd.DataFrame({"date": pd.to_datetime([self.dates[24]]), "symbol": [self.symbols[0]],
            "open": [10.], "high": [11.], "low": [9.], "close": [10.], "volume": [100.], "amount": [1000.], "turnover": [.04]})
        extra = pd.DataFrame({"date": pd.to_datetime([self.dates[25]]), "symbol": [self.symbols[0]],
            "open": [np.nan], "high": [np.nan], "low": [np.nan], "close": [np.nan], "volume": [200.],
            "amount": [2000.], "turnover": [.05], "available_at_ns": [self.signal + 1], "_price_basis": ["raw"]})
        early, _ = merge_daily_sources(old, extra, self.signal)
        self.assertEqual(len(early), 1)
        late, _ = merge_daily_sources(old, extra, self.signal + 1)
        self.assertEqual(len(late), 2)
        self.assertTrue(late.iloc[-1][["open", "high", "low", "close"]].isna().all())

    def test_same_old_financial_version_ages_instead_of_being_replaced(self):
        daily = pd.read_parquet(self.dailyroot / "bars/2030/source.parquet").rename(columns={"thscode": "symbol"})
        daily.date = pd.to_datetime(daily.date)
        daily["turnover"] = daily.turnoverRatio / 100
        target = pd.DataFrame([(d, s) for d in self.dates[25:27] for s in self.symbols], columns=["date", "symbol"])
        raw = slow_rows(target, daily, pd.DataFrame(self.financial), self.market,
            pd.DatetimeIndex(self.dates[:27]), self.symbols, self.base.selection["selected"])
        for _, group in raw.groupby("symbol"):
            a, b = group.sort_values("date").iloc[0], group.sort_values("date").iloc[1]
            delta = (b.date - a.date).days
            self.assertEqual(b.age_report_days - a.age_report_days, delta)
            self.assertEqual(b.age_version_days - a.age_version_days, delta)
            self.assertEqual(a.report_period, b.report_period)
            self.assertEqual(a.f_book_price, b.f_book_price)
        self.assertTrue(raw[raw.date.eq(pd.Timestamp(self.dates[26]))]["factor_fixture_range"].isna().all())

    def test_daily_raw_prices_are_not_spliced_into_hfq_and_union_does_not_repaint_history(self):
        sources = self.source()
        row = pd.DataFrame({"date": [self.dates[24]], "symbol": [self.symbols[0]],
            "open": [10.], "high": [11.], "low": [9.], "close": [10.], "volume": [100.],
            "amount": [1000.], "turnoverRatio": [4.], "available_at_ns": [self.signal - 1]})
        item = parquet(row, self.root / "raw_new_daily.parquet")
        item.update(price_basis="raw", volume_unit="shares", amount_unit="CNY", turnover_unit="percent")
        sources.spec["daily"] = [item]
        normalized, audit = sources.daily_rows(self.signal)
        self.assertTrue(normalized[["open", "high", "low", "close"]].isna().all().all())
        self.assertEqual(normalized.volume.iloc[0], 100.)
        self.assertEqual(normalized.turnover.iloc[0], .04)
        self.assertEqual(audit["raw_daily_price_rows_masked"], 1)
        base = row.drop(columns=["available_at_ns"]).copy()
        base["turnover"] = .04
        historical_nonmember = normalized.copy()
        historical_nonmember["symbol"] = "999999.SZ"
        merged, _ = merge_daily_sources(base, historical_nonmember, self.signal)
        self.assertEqual(len(merged), 1)

    def test_daily_hfq_bridge_requires_constant_real_overlap(self):
        base = pd.DataFrame({"date": pd.to_datetime([self.dates[24]]), "symbol": [self.symbols[0]],
            "open": [10.], "high": [11.], "low": [9.], "close": [10.], "volume": [100.], "amount": [1000.], "turnover": [.04]})
        extra = pd.concat([base, base], ignore_index=True)
        extra.loc[1, "date"] = pd.Timestamp(self.dates[25])
        extra[["open", "high", "low", "close"]] *= 2
        extra["available_at_ns"], extra["_price_basis"] = self.signal - 1, "hfq"
        merged, bridges = merge_daily_sources(base, extra, self.signal)
        self.assertEqual(bridges[self.symbols[0]], .5)
        self.assertEqual(merged.close.iloc[-1], 10.)
        extra.loc[0, "high"] = 99.
        with self.assertRaises(IntegrityError):
            merge_daily_sources(base, extra, self.signal)


if __name__ == "__main__":
    unittest.main()

"""Offline checks for the local archive; never uses real credentials or network."""
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("tushare_sync", Path(__file__).resolve().parents[1] / "scripts/tushare_sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class LocalArchiveTests(unittest.TestCase):
    def test_short_page_does_not_silently_truncate(self):
        client = sync.Client("fake")
        client.query = Mock(side_effect=[(["ts_code", "trade_date"], [["000001.SZ", "20240102"]]),
                                        (["ts_code", "trade_date"], [["000001.SZ", "20240103"]]),
                                        (["ts_code", "trade_date"], [])])
        _, rows = client.fetch_all("daily", {}, page_size=1000)
        self.assertEqual(len(rows), 2)
        self.assertEqual([c.args[1]["offset"] for c in client.query.call_args_list], [0, 1, 2])

    def test_ignored_offset_is_rejected(self):
        client = sync.Client("fake")
        client.query = Mock(return_value=(["ts_code", "trade_date"], [["000001.SZ", "20240102"]]))
        with self.assertRaisesRegex(sync.DataError, "repeated page"):
            client.fetch_all("daily", {})

    def test_resume_verifies_file_hash(self):
        with tempfile.TemporaryDirectory() as d:
            archive = sync.Archive(Path(d))
            client = Mock()
            client.fetch_all.return_value = (["ts_code", "trade_date"], [["000001.SZ", "20240102"]])
            params = {"trade_date": "20240102"}
            _, path = archive.partition(client, "daily", params)
            archive.partition(client, "daily", params)
            self.assertEqual(client.fetch_all.call_count, 1)
            path.write_bytes(b"corrupt")
            archive.partition(client, "daily", params)
            self.assertEqual(client.fetch_all.call_count, 2)

    def test_empty_required_partition_not_saved(self):
        with tempfile.TemporaryDirectory() as d:
            archive = sync.Archive(Path(d)); client = Mock()
            client.fetch_all.return_value = (["ts_code", "trade_date"], [])
            with self.assertRaises(sync.DataError):
                archive.partition(client, "daily", {"trade_date": "20240102"})
            self.assertEqual(list(Path(d).rglob("*.json")), [])

    def test_outside_scope_not_saved(self):
        with tempfile.TemporaryDirectory() as d:
            archive = sync.Archive(Path(d)); client = Mock()
            client.fetch_all.return_value = (["ts_code", "trade_date"], [["000001.SZ", "20240103"]])
            with self.assertRaises(sync.DataError):
                archive.partition(client, "daily", {"trade_date": "20240102"})
            self.assertEqual(list(Path(d).rglob("*.json")), [])

    def test_destination_and_redirect_guard(self):
        for url in ["http://tuaremax.top", "https://example.com"]:
            with self.assertRaises(sync.DataError):
                sync.Client("fake", url=url)
        self.assertIsNone(sync.NoRedirect().redirect_request())

    def test_month_partition_boundaries(self):
        self.assertEqual(list(sync.month_ranges(dt.date(2024, 2, 15), dt.date(2024, 3, 2))),
                         [("20240215", "20240229"), ("20240301", "20240302")])

    def test_private_credential_file_required(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env.tushare"
            p.write_text("TUSHARE_TOKEN=fake\n")
            p.chmod(0o644)
            with self.assertRaisesRegex(sync.DataError, "600"):
                sync.read_credentials(p)


if __name__ == "__main__":
    unittest.main()

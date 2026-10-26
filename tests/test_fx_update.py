import io
import json
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fx_update as fx


NOW = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
DAY = "2026-10-23"


class FxUpdateTests(unittest.TestCase):
    def test_ecb_cross_rate_and_missing_values(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("eurofxref-hist.csv", "Date, USD, CNY,\n2026-10-23, 2, 14,\n2026-10-22, N/A, 14,\n")
        self.assertEqual(fx.parse_ecb(buffer.getvalue()), {DAY: 7.0})

    def test_fred_missing_nonfinite_and_zero_are_not_observations(self):
        data = b"observation_date,DEXCHUS\n2026-10-23,7\n2026-10-22,.\n2026-10-21,NaN\n2026-10-20,0\n"
        self.assertEqual(fx.parse_fred(data), {DAY: 7.0})
        self.assertEqual(fx.parse_fred(b"DATE,DEXCHUS\n2026-10-23,7\n"), {DAY: 7.0})

    def test_invalid_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            fx.parse_fred(b"<html>maintenance</html>")
        with self.assertRaises(zipfile.BadZipFile):
            fx.parse_ecb(b"<html>maintenance</html>")

    def test_same_day_passes_only_within_threshold(self):
        result = fx.build_document({DAY: 7}, {DAY: 7.02}, now=NOW)
        row = result["records"][0]
        self.assertEqual(row["status"], "verified")
        self.assertAlmostEqual(row["deviation_pct"], abs(7 / 7.02 - 1) * 100, places=6)
        self.assertEqual(result["last_verified_date"], DAY)
        self.assertFalse(result["is_stale"])

    def test_threshold_boundary(self):
        self.assertEqual(fx.build_document({DAY: 1.005}, {DAY: 1}, now=NOW)["records"][0]["status"], "verified")
        self.assertEqual(fx.build_document({DAY: 1.0050001}, {DAY: 1}, now=NOW)["records"][0]["status"], "conflict")

    def test_conflict_is_quarantined(self):
        result = fx.build_document({DAY: 7}, {DAY: 7.1}, now=NOW)
        self.assertEqual(result["records"][0]["status"], "conflict")
        self.assertIsNone(result["last_verified_date"])

    def test_never_shift_dates_or_drop_one_source_only_dates(self):
        result = fx.build_document({DAY: 7}, {"2026-10-22": 7}, now=NOW)
        self.assertEqual(len(result["records"]), 2)
        self.assertTrue(all(row["status"] == "pending" for row in result["records"]))
        self.assertIsNone(result["records"][0]["value"])
        self.assertIsNone(result["last_verified_date"])

    def test_failure_retains_previous_verified_observation(self):
        previous = fx.build_document({DAY: 7}, {DAY: 7.01}, now=NOW)
        result = fx.build_document({DAY: 8}, {}, previous, {"fred": "timeout"}, NOW)
        row = result["records"][0]
        self.assertEqual(row["value"], 7)
        self.assertEqual(row["secondary_value"], 7.01)
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["unverified_primary_value"], 8)
        self.assertEqual(row["refresh_status"], "pending")
        self.assertTrue(row["cached"])
        self.assertTrue(result["is_stale"])
        self.assertEqual(result["fetch_status"], "degraded")

    def test_refetched_conflict_replaces_old_verified(self):
        previous = fx.build_document({DAY: 7}, {DAY: 7}, now=NOW)
        result = fx.build_document({DAY: 7}, {DAY: 8}, previous, now=NOW)
        self.assertEqual(result["records"][0]["status"], "conflict")
        self.assertIsNone(result["last_verified_date"])

    def test_both_sources_failure_preserves_history(self):
        previous = fx.build_document({DAY: 7}, {DAY: 7}, now=NOW)
        result = fx.build_document({}, {}, previous, {"ecb": "timeout", "fred": "timeout"}, NOW)
        self.assertEqual(result["records"][0]["value"], 7)
        self.assertEqual(result["fetch_status"], "failed")
        self.assertTrue(result["is_stale"])

    def test_stale_even_when_fetch_successful(self):
        result = fx.build_document({"2026-09-01": 7}, {"2026-09-01": 7}, now=NOW)
        self.assertTrue(result["is_stale"])
        self.assertEqual(result["fetch_status"], "ok")

    def test_history_window_and_leap_day(self):
        self.assertEqual(fx.history_start(date(2024, 2, 29)), date(2014, 2, 28))
        result = fx.build_document({"2016-10-25": 7, "2026-10-27": 7, DAY: 7}, {}, now=NOW)
        self.assertEqual([row["date"] for row in result["records"]], [DAY])

    def test_coverage_rejects_short_or_truncated_history(self):
        start = fx.history_start(NOW.date())
        with self.assertRaises(ValueError):
            fx.check_coverage({DAY: 7}, start, NOW.date())
        values = {(start + timedelta(days=i)).isoformat(): 7 for i in range(2000)}
        self.assertEqual(len(fx.check_coverage(values, start, NOW.date())), 2000)
        truncated = {(start + timedelta(days=i)).isoformat(): 7 for i in range(100, 2200)}
        with self.assertRaises(ValueError):
            fx.check_coverage(truncated, start, NOW.date())

    def test_fetch_failure_is_data_not_crash(self):
        with patch.object(fx, "download", side_effect=TimeoutError("timeout")):
            values, error = fx.fetch_source("fred", NOW.date())
        self.assertEqual(values, {})
        self.assertIn("TimeoutError", error)

    def test_tls_fallback_keeps_certificate_checks(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError("issuer"))
        with patch.object(fx.os, "name", "nt"), patch.object(fx.urllib.request, "urlopen", side_effect=error), patch.object(fx.subprocess, "run") as run:
            run.return_value.stdout = b"verified TLS payload"
            self.assertEqual(fx.download(fx.ECB_URL), b"verified TLS payload")
        args = run.call_args.args[0]
        self.assertNotIn("-k", args)
        self.assertNotIn("--insecure", args)
        self.assertIn("=https", args)

    def test_regular_network_failure_does_not_switch_transport(self):
        with patch.object(fx.urllib.request, "urlopen", side_effect=urllib.error.URLError("timeout")), patch.object(fx.subprocess, "run") as run:
            with self.assertRaises(urllib.error.URLError):
                fx.download(fx.FRED_URL)
        run.assert_not_called()

    def test_unit_and_invalid_number_guards(self):
        for value in (True, False, 700, 100, float("inf"), float("nan"), -1, 0):
            with self.subTest(value=value):
                self.assertIsNone(fx.positive_number(value))
                with self.assertRaises(ValueError):
                    fx.build_document({DAY: value}, {DAY: 7}, now=NOW)
                with self.assertRaises(ValueError):
                    fx.build_document({DAY: 7}, {DAY: value}, now=NOW)
        self.assertEqual(fx.positive_number("7.1"), 7.1)
        previous = {"records": [{"date": DAY, "value": float("nan"), "status": "verified"}]}
        with self.assertRaises(ValueError):
            fx.build_document({}, {}, previous, now=NOW)

    def test_duplicate_fred_observations_must_agree(self):
        with self.assertRaises(ValueError):
            fx.parse_fred(b"DATE,DEXCHUS\n2026-10-23,7\n2026-10-23,8\n")
        self.assertEqual(fx.parse_fred(b"DATE,DEXCHUS\n2026-10-23,7\n2026-10-23,7\n"), {DAY: 7})

    def test_duplicate_ecb_observations_must_agree(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("eurofxref-hist.csv", "Date,USD,CNY\n2026-10-23,1,7\n2026-10-23,1,8\n")
        with self.assertRaises(ValueError):
            fx.parse_ecb(buffer.getvalue())

    def test_fetch_errors_do_not_expose_urls_or_local_paths(self):
        secret = "https://example.invalid/?token=private C:/Users/private/secret"
        with patch.object(fx, "download", side_effect=OSError(secret)):
            values, error = fx.fetch_source("fred", NOW.date())
        self.assertEqual(values, {})
        self.assertIn("OSError", error)
        self.assertNotIn("private", error)
        self.assertNotIn("https://", error)
        self.assertNotIn("C:/", error)

    def test_atomic_json_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fx.json"
            fx.atomic_write(path, fx.build_document({DAY: 7}, {DAY: 7}, now=NOW))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["pair"], "USD/CNY")
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()

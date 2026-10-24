from __future__ import annotations

import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.finance_store import (
    CLOSE_CLEANUP_BATCH_SIZE,
    CLOSE_RETENTION_DAYS,
    FinanceStore,
)


class FinancePricesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "finance.db"
        self.store = FinanceStore(self.path)
        self.friday = datetime.date(2026, 10, 23)
        self.prior_friday = datetime.date(2026, 10, 16)

    def rows(self):
        with sqlite3.connect(self.path) as connection:
            return connection.execute(
                "SELECT symbol, date, price FROM finance_daily_closes ORDER BY symbol, date"
            ).fetchall()

    def test_current_friday_compares_against_previous_friday(self):
        self.store.record_close("NVDA", self.prior_friday, 100)
        self.store.record_close("NVDA", self.friday, 110)

        self.assertEqual(
            self.store.previous_week_close("NVDA", self.friday),
            (self.prior_friday, 100.0),
        )

    def test_every_current_week_day_excludes_current_week_and_weekend(self):
        monday = self.friday - datetime.timedelta(days=4)
        for offset in range(-7, 7):
            self.store.record_close("NVDA", monday + datetime.timedelta(days=offset), 100 + offset)
        for offset in range(7):
            with self.subTest(offset=offset):
                self.assertEqual(
                    self.store.previous_week_close("NVDA", monday + datetime.timedelta(days=offset)),
                    (self.prior_friday, 97.0),
                )

    def test_friday_holiday_uses_prior_thursday(self):
        # Good Friday 2026 has no stock-market close.
        thursday = datetime.date(2026, 4, 2)
        self.store.record_close("NVDA", thursday, 100)
        self.store.record_close("NVDA", datetime.date(2026, 4, 8), 110)
        self.assertEqual(
            self.store.previous_week_close("NVDA", datetime.date(2026, 4, 10)),
            (thursday, 100.0),
        )

    def test_prior_monday_is_inclusive(self):
        monday = datetime.date(2026, 10, 12)
        self.store.record_close("NVDA", monday, 100)
        self.assertEqual(self.store.previous_week_close("NVDA", self.friday), (monday, 100.0))

    def test_missing_baseline_is_not_fabricated(self):
        self.assertIsNone(self.store.previous_week_close("NVDA", self.friday))
        self.store.record_close("NVDA", self.friday, 110)
        self.assertIsNone(self.store.previous_week_close("NVDA", self.friday))
        self.store.record_close("NVDA", datetime.date(2026, 10, 9), 90)
        self.store.record_close("MSFT", self.prior_friday, 200)
        self.assertIsNone(self.store.previous_week_close("NVDA", self.friday))

    def test_year_boundary(self):
        prior_friday = datetime.date(2025, 12, 26)
        self.store.record_close("NVDA", prior_friday, 100)
        self.assertEqual(
            self.store.previous_week_close("NVDA", datetime.date(2026, 1, 2)),
            (prior_friday, 100.0),
        )

    def test_daily_upsert_and_symbol_isolation_persist_across_restarts(self):
        self.store.record_close(" nvda ", self.prior_friday, 100)
        self.store.record_close("NVDA", self.prior_friday, 101.25)
        self.store.record_close("MSFT", self.prior_friday, 200)
        reloaded = FinanceStore(self.path)
        self.assertEqual(reloaded.previous_week_close("nvda", self.friday), (self.prior_friday, 101.25))
        self.assertEqual(reloaded.previous_week_close("MSFT", self.friday), (self.prior_friday, 200.0))
        self.assertEqual(len(self.rows()), 2)
        with sqlite3.connect(self.path) as connection:
            columns = connection.execute("PRAGMA table_info(finance_daily_closes)").fetchall()
        self.assertEqual({column[1]: column[5] for column in columns}, {"symbol": 1, "date": 2, "price": 0})

    def test_existing_database_upgrade_preserves_watchlists_and_headlines(self):
        self.store.add_watch(123, "NVDA", "Nvidia", 456)
        self.store.mark_headlines_seen(123, ["existing-headline"])
        # Removing only the new table recreates the pre-snapshot schema.
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE finance_daily_closes")
        upgraded = FinanceStore(self.path)
        upgraded.record_close("NVDA", self.prior_friday, 100)
        self.assertEqual(upgraded.list_watchlist(123), self.store.list_watchlist(123))
        self.assertEqual(len(upgraded.list_watchlist(123)), 1)
        self.assertEqual(upgraded.unseen_fingerprints(123, ["existing-headline"]), set())
        self.assertEqual(
            upgraded.previous_week_close("NVDA", self.friday),
            (self.prior_friday, 100.0),
        )

    def test_invalid_prices_do_not_overwrite_valid_close(self):
        self.store.record_close("NVDA", self.prior_friday, 100)
        invalid = (0, -1, float("nan"), float("inf"), -float("inf"), None,
                   True, False, "bad", "NaN", "Infinity", 10 ** 400)
        for price in invalid:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    self.store.record_close("NVDA", self.prior_friday, price)
                self.assertEqual(
                    self.store.previous_week_close("NVDA", self.friday),
                    (self.prior_friday, 100.0),
                )

    def test_invalid_symbols_and_dates(self):
        for symbol in ("", "  ", None):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    self.store.record_close(symbol, self.prior_friday, 100)
                with self.assertRaises(ValueError):
                    self.store.previous_week_close(symbol, self.friday)
        for date in ("2026-10-16", None, datetime.datetime(2026, 10, 16)):
            with self.subTest(date=date):
                with self.assertRaises(ValueError):
                    self.store.record_close("NVDA", date, 100)
                with self.assertRaises(ValueError):
                    self.store.previous_week_close("NVDA", date)
        self.assertEqual(self.rows(), [])

    def test_retention_keeps_at_least_sixty_days_and_inclusive_cutoff(self):
        self.assertGreaterEqual(CLOSE_RETENTION_DAYS, 60)
        for age in range(CLOSE_RETENTION_DAYS + 5, -1, -1):
            self.store.record_close("NVDA", self.friday - datetime.timedelta(days=age), 100)
        rows = self.rows()
        self.assertEqual(len(rows), CLOSE_RETENTION_DAYS + 1)
        self.assertEqual(rows[0][1], (self.friday - datetime.timedelta(days=CLOSE_RETENTION_DAYS)).isoformat())
        # An old backfill must not reset the retention high-water date.
        self.store.record_close("MSFT", self.friday - datetime.timedelta(days=CLOSE_RETENTION_DAYS + 1), 200)
        self.assertEqual(self.rows(), rows)

    def test_cleanup_is_bounded_and_applies_to_inactive_symbols(self):
        expired = (self.friday - datetime.timedelta(days=CLOSE_RETENTION_DAYS + 1)).isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                "INSERT INTO finance_daily_closes (symbol, date, price) VALUES (?, ?, ?)",
                [(f"OLD{i}", expired, 100) for i in range(CLOSE_CLEANUP_BATCH_SIZE + 3)],
            )
        self.store.record_close("NVDA", self.friday, 110)
        self.assertEqual(len(self.rows()), 4)
        self.store.record_close("NVDA", self.friday, 111)
        self.assertEqual(self.rows(), [("NVDA", self.friday.isoformat(), 111.0)])

    def test_daily_alert_keys_persist_per_day_and_scope(self):
        key = "alert:2026-10-23:weekly:NVDA"
        tomorrow = "alert:2026-10-24:weekly:NVDA"
        self.assertEqual(self.store.unseen_fingerprints(123, [key]), {key})
        self.store.mark_headlines_seen(123, [key, key])
        reloaded = FinanceStore(self.path)
        self.assertEqual(reloaded.unseen_fingerprints(123, [key, tomorrow]), {tomorrow})
        self.assertEqual(reloaded.unseen_fingerprints(456, [key]), {key})
        self.assertEqual(reloaded.unseen_fingerprints(123, []), set())


if __name__ == "__main__":
    unittest.main()

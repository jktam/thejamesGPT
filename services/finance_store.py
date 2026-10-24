from __future__ import annotations

from dataclasses import dataclass
import datetime
import math
from pathlib import Path
import sqlite3
from typing import Iterable


FINANCE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "finance.db"
MAX_WATCHLIST_ITEMS = 25
HEADLINE_RETENTION_DAYS = 14
CLOSE_RETENTION_DAYS = 90
CLOSE_CLEANUP_BATCH_SIZE = 500


class WatchlistLimitError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WatchItem:
    symbol: str
    company_name: str | None


class FinanceStore:
    def __init__(self, path: Path = FINANCE_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_watchlist (
                    guild_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    company_name TEXT,
                    added_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, symbol)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_seen_headlines (
                    scope_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (scope_id, fingerprint)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_daily_closes (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    price REAL NOT NULL CHECK (
                        price > 0 AND price <= 1.7976931348623157e308
                    ),
                    PRIMARY KEY (symbol, date)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS finance_daily_closes_date
                ON finance_daily_closes (date)
                """
            )

    @staticmethod
    def _close_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a nonempty string")
        return symbol.strip().upper()

    @staticmethod
    def _close_date(date: datetime.date) -> datetime.date:
        if not isinstance(date, datetime.date) or isinstance(date, datetime.datetime):
            raise ValueError("date must be a datetime.date, not a timestamp")
        return date

    def record_close(self, symbol: str, date: datetime.date, price: float) -> None:
        """Upsert a Finnhub close using its market date, not the fetch date.

        Callers must supply a completed daily close, not an intraday quote.
        Retain 90 days relative to the newest stored close, with at most 500
        expired rows removed per write so catch-up cleanup stays bounded.
        """
        symbol = self._close_symbol(symbol)
        date = self._close_date(date)
        try:
            value = float(price)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("price must be finite and positive") from exc
        if isinstance(price, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError("price must be finite and positive")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO finance_daily_closes (symbol, date, price)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET price = excluded.price
                """,
                (symbol, date.isoformat(), value),
            )
            # The indexed high-water date also expires out-of-order backfills.
            newest = datetime.date.fromisoformat(connection.execute(
                "SELECT MAX(date) FROM finance_daily_closes"
            ).fetchone()[0])
            cutoff = datetime.date.fromordinal(
                max(1, newest.toordinal() - CLOSE_RETENTION_DAYS)
            )
            connection.execute(
                """
                DELETE FROM finance_daily_closes WHERE rowid IN (
                    SELECT rowid FROM finance_daily_closes
                    WHERE date < ? ORDER BY date LIMIT ?
                )
                """,
                (cutoff.isoformat(), CLOSE_CLEANUP_BATCH_SIZE),
            )

    def previous_week_close(
        self, symbol: str, today: datetime.date
    ) -> tuple[datetime.date, float] | None:
        """Return the latest stored weekday close in the previous calendar week.

        Compare a current quote against this baseline; never substitute a
        current-week close or an older week's close when the baseline is absent.
        Missing holiday sessions naturally fall back to an earlier weekday.
        """
        symbol = self._close_symbol(symbol)
        today = self._close_date(today)
        monday_ordinal = today.toordinal() - today.weekday()
        if monday_ordinal <= 7:
            return None
        prior_monday = datetime.date.fromordinal(monday_ordinal - 7)
        prior_friday = datetime.date.fromordinal(monday_ordinal - 3)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT date, price FROM finance_daily_closes
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (symbol, prior_monday.isoformat(), prior_friday.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return datetime.date.fromisoformat(row[0]), float(row[1])

    def add_watch(
        self,
        guild_id: int,
        symbol: str,
        company_name: str | None,
        added_by: int,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1
                FROM finance_watchlist
                WHERE guild_id = ? AND symbol = ?
                """,
                (guild_id, symbol),
            ).fetchone()
            if existing:
                return False

            count = connection.execute(
                "SELECT COUNT(*) FROM finance_watchlist WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            if count >= MAX_WATCHLIST_ITEMS:
                raise WatchlistLimitError(
                    f"Watchlist is limited to {MAX_WATCHLIST_ITEMS} symbols"
                )

            connection.execute(
                """
                INSERT INTO finance_watchlist
                    (guild_id, symbol, company_name, added_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    symbol,
                    company_name,
                    added_by,
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ),
            )
            return True

    def remove_watch(self, guild_id: int, symbol: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM finance_watchlist
                WHERE guild_id = ? AND symbol = ?
                """,
                (guild_id, symbol),
            )
            return cursor.rowcount > 0

    def list_watchlist(self, guild_id: int) -> list[WatchItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, company_name
                FROM finance_watchlist
                WHERE guild_id = ?
                ORDER BY symbol
                """,
                (guild_id,),
            ).fetchall()
        return [WatchItem(symbol=row[0], company_name=row[1]) for row in rows]

    def unseen_fingerprints(self, scope_id: int, fingerprints: Iterable[str]) -> set[str]:
        """Find unseen keys; daily alerts may use ``alert:<date>:<kind>:<symbol>``."""
        unique = set(fingerprints)
        if not unique:
            return set()

        placeholders = ",".join("?" for _ in unique)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT fingerprint
                FROM finance_seen_headlines
                WHERE scope_id = ? AND fingerprint IN ({placeholders})
                """,
                (scope_id, *unique),
            ).fetchall()
        seen = {row[0] for row in rows}
        return unique - seen

    def mark_headlines_seen(self, scope_id: int, fingerprints: Iterable[str]) -> None:
        """Persist headline or date-qualified alert keys after successful delivery."""
        unique = set(fingerprints)
        if not unique:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(days=HEADLINE_RETENTION_DAYS)
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO finance_seen_headlines (scope_id, fingerprint, seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scope_id, fingerprint)
                DO UPDATE SET seen_at = excluded.seen_at
                """,
                [(scope_id, fingerprint, now.isoformat()) for fingerprint in unique],
            )
            connection.execute(
                "DELETE FROM finance_seen_headlines WHERE seen_at < ?",
                (cutoff.isoformat(),),
            )

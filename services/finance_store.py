from __future__ import annotations

from dataclasses import dataclass
import datetime
from pathlib import Path
import sqlite3
from typing import Iterable


FINANCE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "finance.db"
MAX_WATCHLIST_ITEMS = 25
HEADLINE_RETENTION_DAYS = 14


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

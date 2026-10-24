from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.finance import FinanceCog, FinanceAPIError, StockQuote
from services.bounded_http import read_bounded
from services.finance_store import FinanceStore, WatchItem


class ChunkedStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, limit):
        return self.chunks.pop(0) if self.chunks else b""


class FinanceDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cog = FinanceCog.__new__(FinanceCog)
        self.cog.finance_store = FinanceStore(Path(self.temp.name) / "finance.db")
        self.cog.bot = SimpleNamespace(settings=SimpleNamespace(
            finnhub_api_key="test", alpha_vantage_api_key=None,
        ))
        self.cog._check_econ_tomorrow = AsyncMock(return_value=[])
        self.cog._check_spy_daily = AsyncMock(return_value=None)
        self.cog._check_btc_24h = AsyncMock(return_value=None)
        self.cog._fetch_all_news = AsyncMock(return_value=[])
        self.today = dt.date(2026, 10, 23)

    async def test_read_consumes_all_chunks_and_rejects_over_limit(self):
        self.assertEqual(await read_bounded(ChunkedStream([b"ab", b"cd"]), 4), b"abcd")
        with self.assertRaises(ValueError):
            await read_bounded(ChunkedStream([b"ab", b"cde"]), 4)

    async def test_split_rss_and_rewards_responses_are_complete(self):
        published = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        rss = f"<rss><channel><item><title>Costco results</title><link>https://example.com</link><pubDate>{published}</pubDate></item></channel></rss>".encode()
        response = AsyncMock()
        response.status = 200
        response.content = ChunkedStream([rss[:45], rss[45:100], rss[100:]])
        context = AsyncMock()
        context.__aenter__.return_value = response
        self.cog.bot.http_session = SimpleNamespace(get=Mock(return_value=context))
        parsed = await self.cog._fetch_news_feed("NYT Business", "https://example.com/rss")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(self.cog._source_status["NYT Business"], "OK: 1 fresh")
        response.content = ChunkedStream([b"<h1>", b"calendar", b"</h1>"])
        html = await self.cog._fetch_rewards_page("Chase", "https://example.com")
        self.assertEqual(html, "<h1>calendar</h1>")

    async def test_price_moves_trigger_in_both_directions_and_only_mark_after_send(self):
        for symbol in ("AMD", "COST", "NVDA"):
            self.cog.finance_store.add_watch(1, symbol, None, 2)
        self.cog._fetch_watch_quotes = AsyncMock(return_value={
            "AMD": StockQuote("AMD", 103, 3.0, self.today),
            "COST": StockQuote("COST", 960, -4.0, self.today),
            "NVDA": StockQuote("NVDA", 102.99, 2.99, self.today),
        })
        channel = SimpleNamespace(id=10, guild=SimpleNamespace(id=1), send=AsyncMock())
        self.cog._get_finance_channel = Mock(return_value=channel)
        channel.send.side_effect = RuntimeError("Discord unavailable")
        with self.assertRaises(RuntimeError):
            await self.cog._post_daily_alerts()
        keys = [f"price:{s}:{self.today}" for s in ("AMD", "COST")]
        self.assertEqual(self.cog.finance_store.unseen_fingerprints(10, keys), set(keys))
        channel.send.side_effect = None
        await self.cog._post_daily_alerts()
        embed = channel.send.call_args.kwargs["embed"]
        self.assertIn("+3.00%", embed.fields[0].value)
        self.assertIn("-4.00%", embed.fields[0].value)
        self.assertNotIn("NVDA", embed.fields[0].value)
        self.assertEqual(self.cog.finance_store.unseen_fingerprints(10, keys), set())
        channel.send.reset_mock()
        await self.cog._post_daily_alerts()
        channel.send.assert_not_called()

    async def test_all_quote_failures_are_visible(self):
        self.cog.finance_store.add_watch(1, "COST", None, 2)
        self.cog._fetch_watch_quotes = AsyncMock(return_value={"COST": None})
        result = await self.cog._build_alert_result(guild_id=1)
        self.assertIsNotNone(result)
        self.assertEqual(result.embed.fields[0].name, "Price Coverage")
        self.assertIn("COST", result.embed.fields[0].value)

    async def test_quote_freshness_positive_prices_and_cache(self):
        stamp = dt.datetime(2026, 10, 23, 20, tzinfo=dt.timezone.utc).timestamp()
        self.cog._request_json = AsyncMock(return_value={"c": 104, "pc": 100, "t": stamp})
        # Choose an historical close so this test is not sensitive to execution time.
        with patch("cogs.finance._market_today", return_value=self.today):
            quote = await self.cog._fetch_stock_quote("COST")
            self.assertAlmostEqual(quote.change_pct, 4)
            await self.cog._fetch_stock_quote("COST")
            self.cog._request_json.assert_awaited_once()
            self.cog._request_json.return_value = {"c": 104, "pc": 0, "t": stamp}
            with self.assertRaises(FinanceAPIError):
                await self.cog._fetch_stock_quote("AMD")
            self.cog._request_json.return_value = {"c": 104, "pc": 100, "t": stamp - 86400}
            with self.assertRaises(FinanceAPIError):
                await self.cog._fetch_stock_quote("NVDA")

    async def test_company_news_preserves_publisher_filters_unrelated_and_unsafe(self):
        now = dt.datetime.now(dt.timezone.utc)
        def row(title, url="https://example.com/costco", age=1):
            return {"headline": title, "url": url, "source": "Benzinga", "datetime": (now-dt.timedelta(days=age)).timestamp()}
        self.cog._request_json = AsyncMock(return_value=[
            row("Costco reports earnings", age=4),
            row("Oil cost rises"),
            row("Costco reports earnings", "javascript:alert(1)"),
            row("Costco old results", age=8),
        ])
        result = await self.cog._fetch_company_news(WatchItem("COST", None))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "Benzinga")
        self.assertEqual(self.cog._request_json.call_args.kwargs["params"]["symbol"], "COST")

    async def test_weekly_report_contains_prices_and_chase_when_sources_fail(self):
        self.cog.finance_store.add_watch(1, "COST", None, 2)
        self.cog.finance_store.record_close("COST", dt.date(2026, 10, 16), 100)
        self.cog._fetch_econ_calendar = AsyncMock(return_value=[])
        self.cog._fetch_card_section = AsyncMock(side_effect=ValueError("parser failed"))
        self.cog._fetch_watch_quotes = AsyncMock(return_value={"COST": StockQuote("COST", 106, 3.1, self.today)})
        with self.assertLogs("cogs.finance", level="ERROR"):
            result = await self.cog._build_digest_result_uncached(guild_id=1, scope_id=None, exclude_seen=False)
        fields = {field.name: field.value for field in result.embed.fields}
        self.assertIn("week +6.00%", fields["Watchlist Performance"])
        self.assertIn("could not be verified", fields["💳 Chase Rewards"])

    async def test_chase_always_has_two_bullets_and_fits(self):
        from services.chase_rewards import FreedomQuarter, TransferBonus
        for quarters, bonuses in [
            ((), ()),
            ((FreedomQuarter(4, ("Gas", "Target")),), ()),
            ((), (TransferBonus("Marriott", "50%", self.today),)),
            ((FreedomQuarter(4, tuple("x" * 80 for _ in range(8))),), tuple(
                TransferBonus("partner" * 10, "100%", self.today) for _ in range(8)
            )),
        ]:
            text = self.cog._format_card_section(self.today, quarters, bonuses)
            self.assertEqual(sum(line.startswith("•") for line in text.splitlines()), 2)
            self.assertLessEqual(len(text), 1024)
            if bonuses:
                self.assertIn(bonuses[0].bonus, text)
                self.assertNotIn("no current bonus verified", text)

    async def test_status_output_and_history_remain_bounded(self):
        for index in range(150):
            self.cog._set_source_status(f"Company news TEST{index}", "unavailable (FinanceAPIError)")
        self.assertLessEqual(len(self.cog._source_status), 80)
        embed = discord.Embed(title="Finance Status")
        self.cog._add_lines(embed, "Sources", [
            f"{key}: {value}" for key, value in self.cog._source_status.items()
        ])
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))

    async def test_max_watchlist_report_stays_within_discord_limits(self):
        watches = [WatchItem(f"TEST{i}", None) for i in range(25)]
        quotes = {item.symbol: StockQuote(item.symbol, 1000, 4.0, self.today) for item in watches}
        embed = discord.Embed(title="Digest")
        self.cog._add_watch_performance(embed, watches, quotes)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        self.assertLess(len(embed), 6000)
        self.assertIn("week pending", embed.fields[0].value)


if __name__ == "__main__":
    unittest.main()

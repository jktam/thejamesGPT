from __future__ import annotations

import asyncio
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.finance import (
    _EMBED_FIELD_LIMIT,
    FinanceAPIError,
    FinanceCog,
)
from services.finance_news import NewsArticle, safe_http_url


class FakeResponse:
    def __init__(self, payload, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, *, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


def build_cog(payload, *, status: int = 200) -> tuple[FinanceCog, FakeSession]:
    session = FakeSession(FakeResponse(payload, status=status))
    cog = FinanceCog.__new__(FinanceCog)
    cog.bot = SimpleNamespace(
        http_session=session,
        settings=SimpleNamespace(
            alpha_vantage_api_key="alpha-secret",
            finnhub_api_key="finnhub-secret",
        ),
    )
    return cog, session


class FinanceTests(unittest.TestCase):
    def test_finnhub_token_uses_header_not_query_string(self):
        cog, session = build_cog({"economicCalendar": []})

        with patch("cogs.finance._market_today", return_value=datetime.date(2026, 7, 29)):
            result = asyncio.run(cog._check_econ_tomorrow())

        self.assertEqual(result, [])
        _, request = session.requests[0]
        self.assertNotIn("token", request["params"])
        self.assertEqual(
            request["headers"],
            {"X-Finnhub-Token": "finnhub-secret"},
        )
        self.assertFalse(request["allow_redirects"])

    def test_http_error_does_not_expose_query_secret(self):
        cog, _ = build_cog({}, status=429)

        with self.assertRaises(FinanceAPIError) as ctx:
            asyncio.run(
                cog._request_json(
                    "Alpha Vantage",
                    "https://example.test",
                    params={"apikey": "do-not-log"},
                )
            )

        self.assertEqual(str(ctx.exception), "Alpha Vantage returned HTTP 429")
        self.assertNotIn("do-not-log", str(ctx.exception))

    def test_weekly_change_sorts_dates_and_uses_latest_closes(self):
        cog, _ = build_cog(
            {
                "Weekly Time Series": {
                    "2026-07-17": {"4. close": "100"},
                    "2026-07-24": {"4. close": "110"},
                }
            }
        )

        with patch("cogs.finance._market_today", return_value=datetime.date(2026, 7, 29)):
            result = asyncio.run(cog._av_weekly("SPY"))

        self.assertEqual(result["price"], "$110.00")
        self.assertAlmostEqual(result["pct"], 10.0)
        self.assertEqual(result["as_of"], "2026-07-24")

    def test_stale_spy_quote_does_not_trigger_alert(self):
        stale_timestamp = int(
            datetime.datetime(
                2026,
                7,
                28,
                20,
                tzinfo=datetime.timezone.utc,
            ).timestamp()
        )
        cog, _ = build_cog({"dp": 3.5, "c": 630.0, "t": stale_timestamp})

        with patch("cogs.finance._market_today", return_value=datetime.date(2026, 7, 29)):
            result = asyncio.run(cog._check_spy_daily())

        self.assertIsNone(result)

    def test_news_links_only_allow_http_protocols(self):
        self.assertEqual(
            safe_http_url("https://www.wsj.com/articles/example"),
            "https://www.wsj.com/articles/example",
        )
        self.assertIsNone(safe_http_url("javascript:alert(1)"))
        self.assertIsNone(safe_http_url("//example.com/article"))

    def test_rendered_news_respects_discord_field_limit(self):
        articles = [
            (
                NewsArticle(
                    source="Test",
                    title=f"Headline {index}",
                    url=f"https://example.com/{'x' * 500}/{index}",
                    published_at=datetime.datetime.now(datetime.timezone.utc),
                ),
                [],
            )
            for index in range(5)
        ]

        value, rendered = FinanceCog._fit_news_items(articles)

        self.assertLessEqual(len(value), _EMBED_FIELD_LIMIT)
        self.assertGreater(len(rendered), 0)


if __name__ == "__main__":
    unittest.main()

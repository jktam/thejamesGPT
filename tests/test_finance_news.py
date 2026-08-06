from __future__ import annotations

import datetime
from pathlib import Path
import tempfile
import unittest

from services.finance_news import (
    NewsArticle,
    article_tags,
    dedupe_articles,
    parse_feed,
    rank_articles,
    rank_earnings_articles,
)
from services.finance_store import FinanceStore, WatchItem


NOW = datetime.datetime(2026, 7, 29, 18, 0, tzinfo=datetime.timezone.utc)


def rss_item(title: str, url: str, published: str) -> str:
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>{url}</link>"
        f"<pubDate>{published}</pubDate>"
        "</item>"
    )


class FinanceNewsTests(unittest.TestCase):
    def test_parse_feed_rejects_stale_and_undated_articles(self):
        payload = (
            "<rss><channel>"
            + rss_item(
                "Fresh market story",
                "https://example.com/fresh",
                "Wed, 29 Jul 2026 17:00:00 GMT",
            )
            + rss_item(
                "Old market story",
                "https://example.com/old",
                "Mon, 20 Jul 2026 17:00:00 GMT",
            )
            + "<item><title>No date</title><link>https://example.com/no-date</link></item>"
            + "</channel></rss>"
        ).encode()

        articles = parse_feed(payload, source="Test Feed", now=NOW)

        self.assertEqual([article.title for article in articles], ["Fresh market story"])

    def test_dedupe_articles_removes_tracking_urls_and_duplicate_titles(self):
        articles = [
            NewsArticle(
                source="Source A",
                title="Markets rally after Fed decision",
                url="https://example.com/story?utm_source=rss",
                published_at=NOW,
            ),
            NewsArticle(
                source="Source B",
                title="Markets rally after Fed decision",
                url="https://other.example/story",
                published_at=NOW - datetime.timedelta(minutes=2),
            ),
            NewsArticle(
                source="Source C",
                title="Different title",
                url="https://example.com/story?utm_medium=feed",
                published_at=NOW - datetime.timedelta(minutes=3),
            ),
        ]

        result = dedupe_articles(articles)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "Source A")

    def test_rate_cut_and_watchlist_tags_are_detected(self):
        article = NewsArticle(
            source="CNBC Finance",
            title="Nvidia rises as Federal Reserve rate cut hopes grow",
            url="https://example.com/nvidia-fed",
            published_at=NOW,
        )
        watchlist = [WatchItem(symbol="NVDA", company_name="Nvidia")]

        self.assertEqual(article_tags(article, watchlist), ["RATES", "NVDA"])

    def test_official_fed_feed_is_always_tagged_as_rates(self):
        article = NewsArticle(
            source="Federal Reserve",
            title="Federal Reserve issues FOMC statement",
            url="https://www.federalreserve.gov/example",
            published_at=NOW,
        )

        self.assertEqual(article_tags(article, []), ["RATES"])

    def test_ranked_news_combines_priorities_with_source_variety(self):
        articles = [
            NewsArticle(
                source="Federal Reserve",
                title="Federal Reserve issues FOMC statement",
                url="https://www.federalreserve.gov/example",
                published_at=NOW,
            ),
            *(
                NewsArticle(
                    source="NYT Business",
                    title=f"Business headline {index}",
                    url=f"https://www.nytimes.com/business/{index}",
                    published_at=NOW - datetime.timedelta(minutes=index),
                )
                for index in range(1, 5)
            ),
            NewsArticle(
                source="BBC Business",
                title="Technology industry headline",
                url="https://www.bbc.co.uk/news/example",
                published_at=NOW - datetime.timedelta(minutes=5),
            ),
        ]

        ranked = rank_articles(articles, [])

        self.assertEqual(len(ranked), 5)
        self.assertEqual(ranked[0][1], ["RATES"])
        self.assertEqual(
            {article.source for article, _ in ranked},
            {"Federal Reserve", "NYT Business", "BBC Business"},
        )

    def test_ranked_news_caps_rate_and_watchlist_headlines(self):
        watchlist = [WatchItem(symbol="NVDA", company_name="Nvidia")]
        articles = [
            NewsArticle(
                source=f"Source {index % 3}",
                title=(
                    f"Nvidia expects Fed rate cut {index}"
                    if index < 3
                    else f"Interest rates headline {index}"
                    if index < 6
                    else f"Finance headline {index}"
                ),
                url=f"https://example.com/{index}",
                published_at=NOW - datetime.timedelta(minutes=index),
            )
            for index in range(9)
        ]

        ranked = rank_articles(articles, watchlist)
        tags = [article_tags for _, article_tags in ranked]

        self.assertEqual(len(ranked), 5)
        self.assertLessEqual(sum("NVDA" in item for item in tags), 2)
        self.assertLessEqual(sum("RATES" in item for item in tags), 2)
        self.assertTrue(any(not item for item in tags))

    def test_official_fed_news_outranks_newer_global_rate_news(self):
        articles = [
            NewsArticle(
                source="BBC Business",
                title="Bank of England holds interest rates",
                url="https://example.com/uk-rates",
                published_at=NOW,
            ),
            NewsArticle(
                source="Federal Reserve",
                title="Federal Reserve issues FOMC statement",
                url="https://example.com/fomc",
                published_at=NOW - datetime.timedelta(days=1),
            ),
        ]

        ranked = rank_articles(articles, [], limit=1)

        self.assertEqual(ranked[0][0].source, "Federal Reserve")

    def test_earnings_prioritizes_watchlist_then_major_companies(self):
        articles = [
            NewsArticle(
                source="CNBC Finance",
                title="Apple beats estimates and raises guidance",
                url="https://example.com/apple",
                published_at=NOW - datetime.timedelta(hours=1),
            ),
            NewsArticle(
                source="CNBC Finance",
                title="Palantir reports earnings and lifts guidance",
                url="https://example.com/palantir",
                published_at=NOW - datetime.timedelta(hours=2),
            ),
        ]
        watchlist = [WatchItem(symbol="PLTR", company_name="Palantir")]

        ranked = rank_earnings_articles(
            articles,
            watchlist,
            now=NOW,
        )

        self.assertEqual(ranked[0][1], ["PLTR", "WATCHLIST"])
        self.assertEqual(ranked[1][1], ["AAPL"])

    def test_earnings_rejects_previews_stale_and_non_major_companies(self):
        articles = [
            NewsArticle(
                source="CNBC Finance",
                title="What to expect from Nvidia quarterly earnings",
                url="https://example.com/preview",
                published_at=NOW - datetime.timedelta(hours=1),
            ),
            NewsArticle(
                source="CNBC Finance",
                title="Microsoft beats estimates",
                url="https://example.com/stale",
                published_at=NOW - datetime.timedelta(hours=25),
            ),
            NewsArticle(
                source="Unknown Blog",
                title="Small private company reports earnings",
                url="https://example.com/private",
                published_at=NOW - datetime.timedelta(hours=1),
            ),
        ]

        ranked = rank_earnings_articles(articles, [], now=NOW)

        self.assertEqual(ranked, [])

    def test_earnings_watchlist_matching_normalizes_curly_apostrophes(self):
        article = NewsArticle(
            source="NYT Business",
            title="McDonald’s sales rise after quarterly results",
            url="https://example.com/mcdonalds",
            published_at=NOW - datetime.timedelta(hours=1),
        )

        ranked = rank_earnings_articles(
            [article],
            [WatchItem(symbol="MCD", company_name="McDonald's")],
            now=NOW,
        )

        self.assertEqual(ranked[0][1], ["MCD", "WATCHLIST"])

    def test_earnings_watchlist_matches_ticker_when_company_name_is_present(self):
        article = NewsArticle(
            source="CNBC Finance",
            title="$PLTR beats expectations and raises guidance",
            url="https://example.com/palantir",
            published_at=NOW - datetime.timedelta(hours=1),
        )

        ranked = rank_earnings_articles(
            [article],
            [WatchItem(symbol="PLTR", company_name="Palantir")],
            now=NOW,
        )

        self.assertEqual(ranked[0][1], ["PLTR", "WATCHLIST"])


class FinanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "finance.db"
        self.store = FinanceStore(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_watchlist_persists_across_store_instances(self):
        added = self.store.add_watch(123, "NVDA", "Nvidia", 456)

        reloaded = FinanceStore(self.path)

        self.assertTrue(added)
        self.assertEqual(
            reloaded.list_watchlist(123),
            [WatchItem(symbol="NVDA", company_name="Nvidia")],
        )

    def test_duplicate_watch_does_not_create_second_row(self):
        self.assertTrue(self.store.add_watch(123, "NVDA", "Nvidia", 456))
        self.assertFalse(self.store.add_watch(123, "NVDA", "Nvidia", 789))
        self.assertEqual(len(self.store.list_watchlist(123)), 1)

    def test_seen_headlines_persist_per_scope(self):
        self.store.mark_headlines_seen(100, ["abc"])

        reloaded = FinanceStore(self.path)

        self.assertEqual(reloaded.unseen_fingerprints(100, ["abc", "def"]), {"def"})
        self.assertEqual(reloaded.unseen_fingerprints(200, ["abc"]), {"abc"})


if __name__ == "__main__":
    unittest.main()

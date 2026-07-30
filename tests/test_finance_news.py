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

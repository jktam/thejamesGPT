from __future__ import annotations

import datetime
from collections import Counter
from pathlib import Path
import tempfile
import unittest

from services.finance_news import (
    NewsArticle,
    article_tags,
    canonical_publisher,
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

    def test_ticker_evidence_is_strict_in_news_and_earnings(self):
        cases = [
            ("COST", "The cost of borrowing", False),
            ("COST", "COST pressures", False),
            ("NOW", "Buy now", False),
            ("NOW", "BUY NOW", False),
            ("SNOW", "SNOW disrupts deliveries", False),
            ("HOOD", "Under the hood", False),
            ("ON", "ON THE ROAD", False),
            ("META", "A meta analysis", False),
            ("META", "META analysis", False),
            ("NVDA", "nvda", False),
            ("AMD", "Amd", False),
            ("NVDA", "XNVDA NVDA2 NVDA_suffix", False),
            ("NVDA", "éNVDA NVDAé", False),
            ("COST", "$COSTLY", False),
            ("COST", "NASDAQ:COSTLY", False),
            ("COST", "XNASDAQ:COST", False),
            ("COST", "abc$COST", False),
            ("F", "$FORD", False),
            ("F", "F", False),
            ("F", "$f", True),
            ("F", "NYSE:F", True),
            ("COST", "$COST", True),
            ("COST", "$cost", True),
            ("COST", "(NASDAQ: COST)", True),
            ("NOW", "nyse:now", True),
            ("SNOW", "$SNOW", True),
            ("HOOD", "$HOOD", True),
            ("NVDA", "(NVDA)", True),
            ("AMD", "AMD-led growth", True),
            ("BRK.B", "BRK.B", True),
            ("BRK.B", "BRKxB", False),
        ]
        for symbol, text, matches in cases:
            # Ticker-only names also occur in persisted watches and are not aliases.
            for company_name in (None, symbol):
                with self.subTest(symbol=symbol, text=text, company_name=company_name):
                    article = NewsArticle(
                        "Unknown Blog", f"{text}: beats estimates",
                        "https://example.com/evidence", NOW,
                    )
                    watches = [WatchItem(symbol, company_name)]
                    self.assertEqual(article_tags(article, watches), [symbol] if matches else [])
                    ranked = rank_earnings_articles([article], watches, now=NOW)
                    self.assertEqual(
                        [tags for _, tags in ranked],
                        [[symbol, "WATCHLIST"]] if matches else [],
                    )

    def test_ticker_only_watches_have_company_aliases_in_news_and_earnings(self):
        aliases = {
            "AMD": ("Advanced Micro Devices",),
            "COST": ("Costco",),
            "CRM": ("Salesforce",),
            "GOOG": ("Google", "Alphabet"),
            "GOOGL": ("Google", "Alphabet"),
            "HOOD": ("Robinhood",),
            "META": ("Meta", "Meta Platforms", "Facebook"),
            "NOW": ("ServiceNow",),
            "NVDA": ("Nvidia",),
            "PYPL": ("PayPal",),
            "SNOW": ("Snowflake",),
            "AAPL": ("Apple",),
            "AMZN": ("Amazon",),
            "AVGO": ("Broadcom",),
            "JPM": ("JPMorgan", "JP Morgan"),
            "MSFT": ("Microsoft",),
            "TSLA": ("Tesla",),
            "WMT": ("Walmart",),
        }
        for symbol, names in aliases.items():
            for name in names:
                with self.subTest(symbol=symbol, name=name):
                    article = NewsArticle(
                        "Unknown Blog", f"{name} beats estimates",
                        "https://example.com/alias", NOW,
                    )
                    watches = [WatchItem(symbol, None)]
                    self.assertEqual(article_tags(article, watches), [symbol])
                    self.assertEqual(
                        rank_earnings_articles([article], watches, now=NOW)[0][1],
                        [symbol, "WATCHLIST"],
                    )

    def test_company_aliases_have_boundaries_and_custom_names_still_work(self):
        for title, symbol, name, expected in (
            ("CostcoWholesale rises", "COST", None, []),
            ("Salesforced rises", "CRM", None, []),
            ("Snowflakes fall", "SNOW", None, []),
            ("NotPalantir rises", "PLTR", "Palantir", []),
            ("Anything rises", "PLTR", "   ", []),
            ("palantir rises", "PLTR", "Palantir", ["PLTR"]),
            ("McDonald’s rises", "MCD", "McDonald's", ["MCD"]),
        ):
            with self.subTest(title=title):
                article = NewsArticle("Test", title, "https://example.com/name", NOW)
                self.assertEqual(article_tags(article, [WatchItem(symbol, name)]), expected)

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

        self.assertEqual(len(ranked), 4)
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

    def test_publisher_cap_is_hard_even_when_coverage_is_thin(self):
        articles = [
            NewsArticle(
                "One Publisher", f"Finance headline {index}",
                f"https://example.com/{index}", NOW - datetime.timedelta(minutes=index),
            )
            for index in range(10)
        ]
        for source_limit, expected in ((0, 0), (1, 1), (2, 2), (10, 2)):
            with self.subTest(source_limit=source_limit):
                ranked = rank_articles(articles, [], limit=10, source_limit=source_limit)
                self.assertEqual([a for a, _ in ranked], articles[:expected])

    def test_canonical_publisher_uses_exact_case_insensitive_aliases(self):
        groups = {
            "cnbc": ("CNBC", "cnbc", "CNBC Finance", " CNBC\tFINANCE "),
            "bbc": ("BBC", "BBC Business", "bbc NEWS", " bBc "),
            "new york times": (
                "NYT Business", "NYT", "New York Times", "The New York Times",
            ),
            "wall street journal": (
                "WSJ", "Wall Street Journal", "The Wall Street Journal",
            ),
            "independent daily": ("Independent Daily", " INDEPENDENT  DAILY "),
        }
        for expected, aliases in groups.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(canonical_publisher(alias), expected)
                    self.assertEqual(canonical_publisher(expected), expected)

        for source in (
            "NBC News", "CNBC Indonesia", "CNBC Finance Extra", "BBC America",
            "New York Post", "New York Times Opinion Blog", "MarketWatch",
            "Barron's", "Reuters", "Bloomberg",
        ):
            with self.subTest(independent=source):
                self.assertEqual(canonical_publisher(source), source.casefold())

    def test_publisher_alias_caps_span_priority_passes_and_earnings(self):
        for sources in (
            ("CNBC", "CNBC Finance", "cnbc", " CNBC FINANCE "),
            ("BBC", "BBC Business", "bbc news", " BBC "),
            ("NYT Business", "New York Times", "NYT", "The New York Times"),
            ("WSJ", "Wall Street Journal", "wsj", "The Wall Street Journal"),
            ("Local News", "local news", "LOCAL NEWS", " Local  News "),
        ):
            with self.subTest(sources=sources):
                articles = [
                    NewsArticle(
                        source=source,
                        title=title,
                        url=f"https://example.com/{index}",
                        published_at=NOW - datetime.timedelta(minutes=index),
                    )
                    for index, (source, title) in enumerate(zip(
                        (*sources, "Independent Publisher"),
                        (
                            "Nvidia beats estimates",
                            "Apple beats estimates as interest rates fall",
                            "Microsoft beats estimates",
                            "Amazon beats estimates",
                            "Tesla beats estimates",
                        ),
                    ))
                ]
                watches = [WatchItem("NVDA", None)]
                for ranked in (
                    rank_articles(articles, watches, limit=10),
                    rank_earnings_articles(articles, watches, now=NOW, limit=10),
                ):
                    # Original articles and display source names remain intact.
                    self.assertEqual([a for a, _ in ranked], articles[:2] + articles[4:])
                ranked = rank_articles(articles, watches, limit=10, source_limit=1)
                self.assertEqual([a for a, _ in ranked], [articles[0], articles[4]])

    def test_independent_publishers_keep_separate_caps(self):
        sources = (
            "CNBC", "NBC News", "CNBC Indonesia", "New York Times",
            "New York Post", "Wall Street Journal", "MarketWatch", "Barron's",
        )
        articles = [
            NewsArticle(
                source, f"Apple beats estimates {index}",
                f"https://example.com/{index}", NOW - datetime.timedelta(minutes=index),
            )
            for index, source in enumerate(source for source in sources for _ in range(3))
        ]
        expected = [article for index, article in enumerate(articles) if index % 3 < 2]
        for ranked in (
            rank_articles(articles, [], limit=30),
            rank_earnings_articles(articles, [], now=NOW, limit=30),
        ):
            self.assertEqual([article for article, _ in ranked], expected)

    def test_duplicate_alias_articles_do_not_consume_publisher_slots(self):
        articles = [
            NewsArticle(
                source, title, url, NOW - datetime.timedelta(minutes=index),
            )
            for index, (source, title, url) in enumerate((
                ("CNBC", "Apple beats estimates", "https://example.com/apple"),
                ("CNBC Finance", "Apple beats estimates", "https://other.example/apple"),
                ("cnbc", "Apple reports earnings", "https://example.com/apple?utm_source=rss"),
                (" CNBC Finance ", "Microsoft beats estimates", "https://example.com/msft"),
                ("CNBC", "Amazon beats estimates", "https://example.com/amzn"),
            ))
        ]
        self.assertEqual(dedupe_articles(articles), [articles[i] for i in (0, 3, 4)])
        for ranked in (
            rank_articles(articles, [], limit=10),
            rank_earnings_articles(articles, [], now=NOW, limit=10),
        ):
            self.assertEqual([article for article, _ in ranked], [articles[0], articles[3]])

    def test_earnings_source_eligibility_uses_canonical_publisher(self):
        for source in (
            "CNBC", " cnbc finance ", "BBC", "BBC News", "BBC Business",
            "NYT Business", "New York Times", "The New York Times",
            "CNBC Finance Extra", "BBC America", "New York Post",
        ):
            with self.subTest(source=source):
                article = NewsArticle(
                    source, "Small company reports earnings", "https://example.com/results", NOW,
                )
                ranked = rank_earnings_articles([article], [], now=NOW)
                expected = [] if source in {
                    "CNBC Finance Extra", "BBC America", "New York Post",
                } else [(article, [])]
                self.assertEqual(ranked, expected)

    def test_category_caps_apply_across_all_priority_passes(self):
        watches = [WatchItem("NVDA", None)]
        articles = [
            NewsArticle(
                f"Source {index % 4}", f"{title} {index}",
                f"https://example.com/{index}", NOW - datetime.timedelta(minutes=index),
            )
            for index, title in enumerate([
                "Nvidia rises", "Nvidia expects Fed rate cut", "Nvidia sees interest rates fall",
                "Federal Reserve signals rate cut", "Interest rates rise",
                "Nvidia launches chips", "General market news", "Business update",
                "Economy grows", "Markets rally", "Global finance update",
            ])
        ]
        for watch_limit in (0, 1, 2, 4):
            for rate_limit in (0, 1, 2, 4):
                with self.subTest(watch_limit=watch_limit, rate_limit=rate_limit):
                    ranked = rank_articles(
                        articles, iter(watches), limit=10,
                        watchlist_limit=watch_limit, rate_limit=rate_limit,
                    )
                    self.assertLessEqual(sum("NVDA" in tags for _, tags in ranked), watch_limit)
                    self.assertLessEqual(sum("RATES" in tags for _, tags in ranked), rate_limit)
                    self.assertTrue(all(n <= 2 for n in Counter(a.source for a, _ in ranked).values()))
                    self.assertTrue(any(not tags for _, tags in ranked))
                    self.assertEqual(len({a.fingerprint for a, _ in ranked}), len(ranked))

    def test_category_only_feed_does_not_flood_fallback_slots(self):
        for title, watches, kwargs in (
            ("Nvidia rises", [WatchItem("NVDA", None)], {"watchlist_limit": 1}),
            ("Interest rates rise", [], {"rate_limit": 1}),
        ):
            with self.subTest(title=title):
                articles = [
                    NewsArticle(f"Source {i}", f"{title} {i}", f"https://example.com/{i}", NOW)
                    for i in range(10)
                ]
                self.assertEqual(len(rank_articles(articles, watches, limit=10, **kwargs)), 1)

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

    def test_earnings_major_symbols_use_strict_matching_and_google_alias(self):
        for text, expected in (
            ("GOOG", ["GOOGL"]), ("$goog", ["GOOGL"]),
            ("NASDAQ:GOOGL", ["GOOGL"]), ("NVDA", ["NVDA"]),
            ("nvda", None), ("META", None), ("Meta", ["META"]),
        ):
            with self.subTest(text=text):
                article = NewsArticle(
                    "Unknown Blog", f"{text} beats estimates", "https://example.com/major", NOW,
                )
                ranked = rank_earnings_articles([article], [], now=NOW)
                self.assertEqual([tags for _, tags in ranked], [expected] if expected else [])

    def test_earnings_publisher_cap_is_hard_for_larger_limits(self):
        articles = [
            NewsArticle(
                "CNBC Finance" if i < 4 else "BBC Business",
                f"Apple beats estimates {i}", f"https://example.com/{i}",
                NOW - datetime.timedelta(minutes=i),
            )
            for i in range(6)
        ]
        ranked = rank_earnings_articles(articles, [], now=NOW, limit=6)
        self.assertEqual([a for a, _ in ranked], articles[:2] + articles[4:])
        for limit in (0, -1):
            self.assertEqual(rank_earnings_articles(articles, [], now=NOW, limit=limit), [])
            self.assertEqual(rank_articles(articles, [], limit=limit), [])


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

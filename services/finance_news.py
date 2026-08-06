from __future__ import annotations

from dataclasses import dataclass
import datetime
from email.utils import parsedate_to_datetime
import hashlib
import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import xml.etree.ElementTree as ET

from services.finance_store import WatchItem


NEWS_MAX_AGE_HOURS = 72
NEWS_TITLE_LIMIT = 120
EARNINGS_MAX_AGE_HOURS = 24

_RATE_NEWS_TERMS = (
    "rate cut",
    "rate cuts",
    "rate-cut",
    "cut rates",
    "cuts rates",
    "fed rate",
    "fed cuts",
    "interest rate",
    "interest rates",
    "federal reserve",
    "fomc",
    "monetary policy",
    "policy rate",
    "basis point",
    "dot plot",
)
_EARNINGS_RESULT_TERMS = (
    "beats estimates",
    "beat estimates",
    "beats expectations",
    "beat expectations",
    "tops estimates",
    "surpasses estimates",
    "misses estimates",
    "missed estimates",
    "misses expectations",
    "missed expectations",
    "falls short",
    "earnings beat",
    "earnings miss",
    "quarterly earnings",
    "quarterly results",
    "reports earnings",
    "reports profit",
    "reports revenue",
    "raises guidance",
    "raised guidance",
    "lifts guidance",
    "cuts guidance",
    "cut guidance",
    "slashes guidance",
    "lowers guidance",
    "withdraws guidance",
    "first results",
)
_EARNINGS_PREVIEW_TERMS = (
    "ahead of earnings",
    "earnings preview",
    "set to report",
    "will report",
    "reports after the bell",
    "reports before the bell",
    "what to expect",
)
_MARKET_MOVING_COMPANIES = {
    "AAPL": ("apple",),
    "AMZN": ("amazon",),
    "AVGO": ("broadcom",),
    "GOOGL": ("alphabet", "google"),
    "JPM": ("jpmorgan", "jp morgan"),
    "META": ("meta", "facebook"),
    "MSFT": ("microsoft",),
    "NVDA": ("nvidia",),
    "TSLA": ("tesla",),
    "WMT": ("walmart",),
}
_EARNINGS_NEWS_SOURCES = {
    "BBC Business",
    "CNBC Finance",
    "NYT Business",
}
_MATERIAL_RESULT_PATTERN = re.compile(
    r"(?:"
    r"\b(?:profit|revenue|sales)\b.{0,50}"
    r"\b(?:beats?|falls?|grows?|highest|jumps?|lowest|rises?|slows?|surges?)\b"
    r"|\b(?:record|higher|lower)\b.{0,30}\b(?:profit|revenue|sales)\b"
    r")",
    re.IGNORECASE,
)
_TRACKING_QUERY_KEYS = {
    "cid",
    "mod",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


@dataclass(frozen=True, slots=True)
class NewsArticle:
    source: str
    title: str
    url: str
    published_at: datetime.datetime

    @property
    def fingerprint(self) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def safe_markdown_text(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")[:limit]


def safe_http_url(value) -> str | None:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            "",
            urlencode(query),
            "",
        )
    )


def parse_published_at(value: str | None) -> datetime.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _entry_text(entry: ET.Element, names: Iterable[str]) -> str | None:
    for name in names:
        value = entry.findtext(name)
        if value:
            return value
        value = entry.findtext(f"{{*}}{name}")
        if value:
            return value
    return None


def _entry_url(entry: ET.Element) -> str | None:
    value = _entry_text(entry, ("link", "guid"))
    if value:
        return safe_http_url(value)

    for link in entry.findall("{*}link"):
        href = link.get("href")
        if href:
            return safe_http_url(href)
    return None


def parse_feed(
    payload: bytes,
    *,
    source: str,
    now: datetime.datetime | None = None,
    max_age_hours: int = NEWS_MAX_AGE_HOURS,
) -> list[NewsArticle]:
    current = now or datetime.datetime.now(datetime.timezone.utc)
    current = current.astimezone(datetime.timezone.utc)
    cutoff = current - datetime.timedelta(hours=max_age_hours)
    future_tolerance = current + datetime.timedelta(minutes=5)

    root = ET.fromstring(payload)
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{*}entry")

    articles = []
    for entry in entries:
        title = safe_markdown_text(_entry_text(entry, ("title",)), NEWS_TITLE_LIMIT)
        url = _entry_url(entry)
        published_at = parse_published_at(
            _entry_text(entry, ("pubDate", "date", "published", "updated"))
        )
        if not title or not url or published_at is None:
            continue
        if published_at < cutoff or published_at > future_tolerance:
            continue
        articles.append(
            NewsArticle(
                source=source,
                title=title,
                url=url,
                published_at=published_at,
            )
        )
    return articles


def dedupe_articles(articles: Iterable[NewsArticle]) -> list[NewsArticle]:
    seen_fingerprints = set()
    seen_urls = set()
    result = []

    for article in sorted(articles, key=lambda item: item.published_at, reverse=True):
        normalized_url = canonical_url(article.url)
        if article.fingerprint in seen_fingerprints or normalized_url in seen_urls:
            continue
        seen_fingerprints.add(article.fingerprint)
        seen_urls.add(normalized_url)
        result.append(article)
    return result


def _title_has_symbol(title: str, symbol: str) -> bool:
    normalized_symbol = symbol.lower()
    if len(normalized_symbol) == 1:
        return f"${normalized_symbol}" in title
    return bool(
        re.search(
            rf"(?<![a-z0-9])\$?{re.escape(normalized_symbol)}(?![a-z0-9])",
            title,
        )
    )


def _title_has_company(title: str, names: Iterable[str]) -> bool:
    normalized_title = title.replace("’", "'").replace("‘", "'").lower()
    for name in names:
        normalized_name = name.replace("’", "'").replace("‘", "'").lower()
        if re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_name)}(?![a-z0-9])",
            normalized_title,
        ):
            return True
    return False


def article_tags(article: NewsArticle, watchlist: Iterable[WatchItem]) -> list[str]:
    title_lower = article.title.lower()
    tags = []

    if article.source == "Federal Reserve" or any(
        term in title_lower for term in _RATE_NEWS_TERMS
    ):
        tags.append("RATES")

    for item in watchlist:
        symbol_matches = _title_has_symbol(title_lower, item.symbol)
        company_matches = bool(
            item.company_name
            and _title_has_company(title_lower, (item.company_name,))
        )
        if symbol_matches or company_matches:
            tags.append(item.symbol)

    return tags


def rank_earnings_articles(
    articles: Iterable[NewsArticle],
    watchlist: Iterable[WatchItem],
    *,
    now: datetime.datetime | None = None,
    limit: int = 2,
) -> list[tuple[NewsArticle, list[str]]]:
    current = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        datetime.timezone.utc
    )
    cutoff = current - datetime.timedelta(hours=EARNINGS_MAX_AGE_HOURS)
    watches = list(watchlist)
    candidates = []

    for article in dedupe_articles(articles):
        if not cutoff <= article.published_at <= current + datetime.timedelta(minutes=5):
            continue
        title = article.title.lower()
        if any(term in title for term in _EARNINGS_PREVIEW_TERMS):
            continue
        if not (
            any(term in title for term in _EARNINGS_RESULT_TERMS)
            or _MATERIAL_RESULT_PATTERN.search(title)
        ):
            continue

        watch_symbols = [
            item.symbol
            for item in watches
            if _title_has_symbol(title, item.symbol)
            or (
                bool(item.company_name)
                and _title_has_company(title, (item.company_name,))
            )
        ]
        major_symbols = [
            symbol
            for symbol, names in _MARKET_MOVING_COMPANIES.items()
            if _title_has_company(title, names)
        ]
        if (
            not watch_symbols
            and not major_symbols
            and article.source not in _EARNINGS_NEWS_SOURCES
        ):
            continue

        symbols = watch_symbols or major_symbols
        tags = (
            [symbols[0], "WATCHLIST"]
            if watch_symbols
            else [symbols[0]]
            if symbols
            else []
        )
        priority = 0 if watch_symbols else 1 if major_symbols else 2
        candidates.append((article, tags, priority))

    candidates.sort(key=lambda item: (item[2], -item[0].published_at.timestamp()))
    return [(article, tags) for article, tags, _ in candidates[:limit]]


def rank_articles(
    articles: Iterable[NewsArticle],
    watchlist: Iterable[WatchItem],
    *,
    limit: int = 5,
    watchlist_limit: int = 2,
    rate_limit: int = 2,
    source_limit: int = 2,
) -> list[tuple[NewsArticle, list[str]]]:
    watches = list(watchlist)
    candidates = [
        (article, article_tags(article, watches))
        for article in dedupe_articles(articles)
    ]
    selected: list[tuple[NewsArticle, list[str]]] = []
    selected_fingerprints: set[str] = set()
    source_counts: dict[str, int] = {}
    watchlist_count = 0
    rate_count = 0

    def add_candidates(kind: str, *, enforce_source_limit: bool = True) -> None:
        nonlocal watchlist_count, rate_count
        ordered_candidates = candidates
        if kind == "rates":
            ordered_candidates = sorted(
                candidates,
                key=lambda item: (
                    0
                    if item[0].source == "Federal Reserve"
                    else 1
                    if re.search(r"\b(?:fed|fomc|federal reserve)\b", item[0].title, re.I)
                    else 2
                ),
            )

        for article, tags in ordered_candidates:
            if len(selected) >= limit:
                return
            if article.fingerprint in selected_fingerprints:
                continue

            has_watchlist_tag = any(tag != "RATES" for tag in tags)
            has_rate_tag = "RATES" in tags
            if kind == "watchlist" and (
                not has_watchlist_tag or watchlist_count >= watchlist_limit
            ):
                continue
            if kind == "rates" and (not has_rate_tag or rate_count >= rate_limit):
                continue
            if kind == "general" and (
                (has_watchlist_tag and watchlist_count >= watchlist_limit)
                or (has_rate_tag and rate_count >= rate_limit)
            ):
                continue
            if (
                enforce_source_limit
                and source_counts.get(article.source, 0) >= source_limit
            ):
                continue

            selected.append((article, tags))
            selected_fingerprints.add(article.fingerprint)
            source_counts[article.source] = source_counts.get(article.source, 0) + 1
            watchlist_count += int(has_watchlist_tag)
            rate_count += int(has_rate_tag)

    add_candidates("watchlist")
    add_candidates("rates")
    add_candidates("general")
    # Prefer publisher variety, but do not leave empty slots when coverage is thin.
    add_candidates("general", enforce_source_limit=False)
    return selected


def format_article_age(
    published_at: datetime.datetime,
    *,
    now: datetime.datetime | None = None,
) -> str:
    current = now or datetime.datetime.now(datetime.timezone.utc)
    seconds = max(
        0,
        int(
            (
                current.astimezone(datetime.timezone.utc)
                - published_at.astimezone(datetime.timezone.utc)
            ).total_seconds()
        ),
    )
    if seconds < 60 * 60:
        return f"{max(1, seconds // 60)}m"
    if seconds < 24 * 60 * 60:
        return f"{seconds // (60 * 60)}h"
    return f"{seconds // (24 * 60 * 60)}d"

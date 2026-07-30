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


def article_tags(article: NewsArticle, watchlist: Iterable[WatchItem]) -> list[str]:
    title_lower = article.title.lower()
    tags = []

    if article.source == "Federal Reserve" or any(
        term in title_lower for term in _RATE_NEWS_TERMS
    ):
        tags.append("RATES")

    for item in watchlist:
        symbol_pattern = (
            rf"(?<![a-z0-9])\$?{re.escape(item.symbol.lower())}(?![a-z0-9])"
        )
        symbol_matches = bool(re.search(symbol_pattern, title_lower))
        if len(item.symbol) == 1:
            symbol_matches = f"${item.symbol.lower()}" in title_lower
        company_matches = False
        if item.company_name:
            company_pattern = (
                rf"(?<![a-z0-9]){re.escape(item.company_name.lower())}(?![a-z0-9])"
            )
            company_matches = bool(re.search(company_pattern, title_lower))
        if symbol_matches or company_matches:
            tags.append(item.symbol)

    return tags


def split_ranked_articles(
    articles: Iterable[NewsArticle],
    watchlist: Iterable[WatchItem],
    *,
    priority_limit: int = 5,
    general_limit: int = 5,
) -> tuple[list[tuple[NewsArticle, list[str]]], list[NewsArticle]]:
    watches = list(watchlist)
    priority = []
    general = []
    general_source_counts: dict[str, int] = {}

    for article in dedupe_articles(articles):
        tags = article_tags(article, watches)
        if tags and len(priority) < priority_limit:
            priority.append((article, tags))
        elif (
            len(general) < general_limit
            and general_source_counts.get(article.source, 0) < 3
        ):
            general.append(article)
            general_source_counts[article.source] = (
                general_source_counts.get(article.source, 0) + 1
            )
        if len(priority) >= priority_limit and len(general) >= general_limit:
            break

    return priority, general


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

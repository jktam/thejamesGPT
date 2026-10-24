from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime
import logging
import math
import re
from time import monotonic
from typing import Any
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from services.bounded_http import read_bounded

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services.chase_rewards import (
    CHASE_FREEDOM_CALENDAR_URL,
    CHASE_TRANSFER_BONUS_URL,
    FreedomQuarter,
    TransferBonus,
    parse_chase_freedom_quarters,
    parse_chase_transfer_bonuses,
)
from services.finance_news import (
    NEWS_MAX_AGE_HOURS,
    NewsArticle,
    format_article_age,
    parse_feed,
    safe_http_url,
    article_tags,
    rank_articles,
    rank_earnings_articles,
    safe_markdown_text,
)
from services.finance_store import (
    FinanceStore,
    WatchlistLimitError,
)

logger = logging.getLogger(__name__)

_QUARTER_MONTHS = {1: "Jan–Mar", 2: "Apr–Jun", 3: "Jul–Sep", 4: "Oct–Dec"}

_MARKET_TZ = ZoneInfo("America/New_York")
_DAILY_TIME = datetime.time(hour=17, minute=0, tzinfo=_MARKET_TZ)

_AV_URL = "https://www.alphavantage.co/query"
_COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
_COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
_FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_FINNHUB_ECON_URL = "https://finnhub.io/api/v1/calendar/economic"
_FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"
_NEWS_FEEDS = (
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
)
_RSS_HEADERS = {
    "Accept": "application/rss+xml, application/xml, text/xml",
    "User-Agent": "TheJamesGPT/1.0 (+https://github.com/jktam/thejamesGPT)",
}

_STOCK_SYMBOLS = {
    "S&P 500 ETF (SPY)": "SPY",
    "Nasdaq-100 ETF (QQQ)": "QQQ",
    "Gold ETF (GLD)": "GLD",
}

# Daily alert thresholds
_SPY_ALERT_PCT = 1.5
_BTC_ALERT_PCT = 5.0
_WATCH_ALERT_PCT = 3.0
_DIGEST_CACHE_SECONDS = 5 * 60
_MAX_CRYPTO_AGE_SECONDS = 60 * 60
_MAX_WEEKLY_DATA_AGE_DAYS = 10
_MAX_RSS_BYTES = 512 * 1024
_MAX_REWARDS_PAGE_BYTES = 1024 * 1024
_EMBED_FIELD_LIMIT = 1024
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_MAX_COMPANY_NAME_LENGTH = 80


class FinanceAPIError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DigestResult:
    embed: discord.Embed
    news_fingerprints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlertResult:
    embed: discord.Embed
    news_fingerprints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StockQuote:
    symbol: str
    price: float
    change_pct: float
    date: datetime.date


def _market_today() -> datetime.date:
    return datetime.datetime.now(_MARKET_TZ).date()


def _finite_float(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FinanceAPIError(f"Invalid numeric field: {field}") from exc
    if not math.isfinite(number):
        raise FinanceAPIError(f"Invalid numeric field: {field}")
    return number


def _is_fresh_timestamp(timestamp: Any, max_age_seconds: int) -> bool:
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    age = now - value
    return 0 <= age <= max_age_seconds


class FinanceCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.finance_store = FinanceStore()
        self._digest_cache: dict[int | None, tuple[float, DigestResult]] = {}
        self._digest_lock = asyncio.Lock()
        self._provider_cache = {}
        self._finnhub_lock = asyncio.Lock()
        self._finnhub_next_request = 0.0
        self._source_status = {}
        self._last_daily_status = "No weekday check since startup"
        self.weekly_digest.start()
        self.daily_check.start()

    def cog_unload(self) -> None:
        self.weekly_digest.cancel()
        self.daily_check.cancel()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Slow down! Try again in {error.retry_after:.0f}s."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        raise error

    # ------------------------------------------------------------------ tasks

    @tasks.loop(time=_DAILY_TIME)
    async def weekly_digest(self) -> None:
        if datetime.datetime.now(_MARKET_TZ).weekday() != 4:  # Friday
            return
        try:
            await self._post_digest()
        except Exception:
            logger.exception("Scheduled weekly finance digest failed")

    @weekly_digest.before_loop
    async def before_digest(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=_DAILY_TIME)
    async def daily_check(self) -> None:
        if datetime.datetime.now(_MARKET_TZ).weekday() >= 4:  # Mon–Thu only
            return
        try:
            await self._post_daily_alerts()
        except Exception:
            self._last_daily_status = "Weekday check failed; see service logs"
            logger.exception("Scheduled daily finance check failed")

    @daily_check.before_loop
    async def before_daily_check(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ channel helper

    def _get_finance_channel(self):
        channel_id = self.bot.settings.finance_channel_id
        if not channel_id:
            logger.warning("FINANCE_CHANNEL_ID not set")
            return None
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.error("Finance channel %d not found", channel_id)
        return channel

    async def _request_json(
        self,
        provider: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        session = self.bot.http_session
        if session is None:
            raise FinanceAPIError("HTTP session is not initialized")

        if provider == "Finnhub":
            # Keep all quote, company-news and calendar requests below 60/minute.
            if not hasattr(self, "_finnhub_lock"):
                self._finnhub_lock = asyncio.Lock()
                self._finnhub_next_request = 0.0
            async with self._finnhub_lock:
                delay = self._finnhub_next_request - monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self._finnhub_next_request = monotonic() + 1.1

        try:
            async with session.get(
                url,
                params=params,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise FinanceAPIError(f"{provider} returned HTTP {response.status}")
                try:
                    return await response.json(content_type=None)
                except (TypeError, ValueError) as exc:
                    raise FinanceAPIError(f"{provider} returned invalid JSON") from exc
        except FinanceAPIError:
            raise
        except Exception as exc:
            raise FinanceAPIError(f"{provider} request failed") from exc

    def _finnhub_headers(self) -> dict[str, str]:
        api_key = self.bot.settings.finnhub_api_key
        if not api_key:
            raise FinanceAPIError("Finnhub is not configured")
        return {"X-Finnhub-Token": api_key}

    # ------------------------------------------------------------------ weekly digest

    async def _post_digest(self) -> None:
        channel = self._get_finance_channel()
        if not channel:
            return

        guild_id = channel.guild.id if getattr(channel, "guild", None) else None
        result = await self._build_digest_result(
            guild_id=guild_id,
            scope_id=channel.id,
            exclude_seen=True,
        )
        await channel.send(embed=result.embed)
        self.finance_store.mark_headlines_seen(
            channel.id,
            result.news_fingerprints,
        )

    async def _build_digest_embed(self, guild_id: int | None = None) -> discord.Embed:
        result = await self._build_digest_result(guild_id=guild_id)
        return result.embed

    async def _build_digest_result(
        self,
        *,
        guild_id: int | None,
        scope_id: int | None = None,
        exclude_seen: bool = False,
    ) -> DigestResult:
        cached = self._digest_cache.get(guild_id)
        if (
            not exclude_seen
            and cached
            and monotonic() - cached[0] < _DIGEST_CACHE_SECONDS
        ):
            return DigestResult(
                embed=cached[1].embed.copy(),
                news_fingerprints=cached[1].news_fingerprints,
            )

        async with self._digest_lock:
            cached = self._digest_cache.get(guild_id)
            if (
                not exclude_seen
                and cached
                and monotonic() - cached[0] < _DIGEST_CACHE_SECONDS
            ):
                return DigestResult(
                    embed=cached[1].embed.copy(),
                    news_fingerprints=cached[1].news_fingerprints,
                )

            result = await self._build_digest_result_uncached(
                guild_id=guild_id,
                scope_id=scope_id,
                exclude_seen=exclude_seen,
            )
            if not exclude_seen:
                self._digest_cache[guild_id] = (
                    monotonic(),
                    DigestResult(
                        embed=result.embed.copy(),
                        news_fingerprints=result.news_fingerprints,
                    ),
                )
            return result

    async def _build_digest_result_uncached(
        self,
        *,
        guild_id: int | None,
        scope_id: int | None,
        exclude_seen: bool,
    ) -> DigestResult:
        settings = self.bot.settings
        watchlist = (
            self.finance_store.list_watchlist(guild_id)
            if guild_id is not None
            else []
        )

        market_task = self._fetch_market() if settings.alpha_vantage_api_key else asyncio.sleep(0)
        news_task = self._fetch_all_news(watchlist)
        econ_task = self._fetch_econ_calendar(days=7) if settings.finnhub_api_key else asyncio.sleep(0)
        card_task = self._fetch_card_section()
        watch_task = self._fetch_watch_quotes(watchlist)

        market, news, econ_events, card_text, watch_quotes = await asyncio.gather(
            market_task,
            news_task,
            econ_task,
            card_task,
            watch_task,
            return_exceptions=True,
        )

        if isinstance(market, Exception):
            logger.error("Market fetch failed: %s", market)
        if isinstance(news, Exception):
            logger.error("News fetch failed: %s", news)
        if isinstance(econ_events, Exception):
            logger.error("Econ calendar fetch failed: %s", econ_events)
        if isinstance(card_text, Exception):
            logger.error("Chase rewards fetch failed: %s", card_text)

        now = datetime.datetime.now(_MARKET_TZ)
        embed = discord.Embed(
            title="📊 Weekly Finance Digest",
            description=f"Week ending {now.strftime('%B %d, %Y')}",
            color=0x2ECC71,
        )

        if isinstance(market, dict) and market:
            lines = []
            for name, info in market.items():
                pct = info["pct"]
                arrow = "▲" if pct >= 0 else "▼"
                as_of = f" · {info['as_of']}" if info.get("as_of") else ""
                lines.append(
                    f"• **{name}:** {info['price']}  {arrow} {abs(pct):.2f}%{as_of}"
                )
            embed.add_field(
                name="📈 Latest Weekly / 7-Day Change",
                value="\n".join(lines),
                inline=False,
            )

        if isinstance(econ_events, list) and econ_events:
            embed.add_field(
                name="📅 Economic Calendar — Next 7 Days",
                value="\n".join(f"• {e}" for e in econ_events),
                inline=False,
            )

        shown_articles = []
        if isinstance(news, list):
            articles = news
            if exclude_seen and scope_id is not None:
                unseen = self.finance_store.unseen_fingerprints(
                    scope_id,
                    (article.fingerprint for article in articles),
                )
                articles = [
                    article for article in articles if article.fingerprint in unseen
                ]

            ranked_news = rank_articles(
                articles,
                watchlist,
            )
            if ranked_news:
                value, rendered = self._fit_news_items(ranked_news)
                if value:
                    embed.add_field(
                        name="📰 Finance News",
                        value=value,
                        inline=False,
                    )
                    shown_articles.extend(rendered)
            if not shown_articles:
                status = (
                    "No new headlines since the previous scheduled digest."
                    if exclude_seen
                    else f"No headlines published in the last {NEWS_MAX_AGE_HOURS} hours."
                )
                embed.add_field(name="📰 News Status", value=status, inline=False)

        if watchlist:
            self._add_watch_performance(embed, watchlist, watch_quotes)

        if isinstance(card_text, str) and card_text:
            embed.add_field(name="💳 Chase Rewards", value=card_text, inline=False)
        else:
            embed.add_field(name="💳 Chase Rewards", value=self._rewards_unavailable(), inline=False)

        coverage = self._news_coverage_note()
        if coverage:
            embed.add_field(name="News Coverage", value=coverage, inline=False)

        embed.set_footer(
            text="Data may be delayed · Informational only, not investment advice"
        )
        return DigestResult(
            embed=embed,
            news_fingerprints=tuple(
                article.fingerprint for article in shown_articles
            ),
        )

    @staticmethod
    def _format_news_line(
        article: NewsArticle,
        tags: list[str] | None = None,
    ) -> str:
        tag_text = f" `{' · '.join(tags)}`" if tags else ""
        age = format_article_age(article.published_at)
        return (
            f"• [{article.title}]({article.url}){tag_text}\n"
            f"  {article.source} · {age} ago"
        )

    @classmethod
    def _fit_news_items(
        cls,
        items: list[tuple[NewsArticle, list[str]]],
    ) -> tuple[str, list[NewsArticle]]:
        lines = []
        rendered = []
        for article, tags in items:
            line = cls._format_news_line(article, tags)
            candidate = "\n".join((*lines, line))
            if len(candidate) > _EMBED_FIELD_LIMIT:
                continue
            lines.append(line)
            rendered.append(article)
        return "\n".join(lines), rendered

    # ------------------------------------------------------------------ daily alert

    def _set_source_status(self, source: str, status: str) -> None:
        if not hasattr(self, "_source_status"):
            self._source_status = {}
        self._source_status.pop(source, None)
        self._source_status[source] = status
        if len(self._source_status) > 80:
            del self._source_status[next(iter(self._source_status))]
        logger.info("Finance source %s: %s", source, status)

    def _news_coverage_note(self) -> str:
        statuses = getattr(self, "_source_status", {})
        failures = [
            f"{source}: {statuses[source]}"
            for source, _ in _NEWS_FEEDS
            if source in statuses and not statuses[source].startswith("OK")
        ]
        return safe_markdown_text("; ".join(failures), 300)

    async def _fetch_stock_quote(self, symbol: str) -> StockQuote:
        cache = getattr(self, "_provider_cache", None)
        if cache is None:
            self._provider_cache = cache = {}
        key = ("quote", symbol, _market_today())
        cached = cache.get(key)
        if cached and monotonic() - cached[0] < _DIGEST_CACHE_SECONDS:
            return cached[1]
        data = await self._request_json(
            "Finnhub", _FINNHUB_QUOTE_URL,
            params={"symbol": symbol}, headers=self._finnhub_headers(),
        )
        if not isinstance(data, dict):
            raise FinanceAPIError("Invalid stock quote payload")
        price = _finite_float(data.get("c"), field="quote close")
        previous = _finite_float(data.get("pc"), field="quote previous close")
        timestamp = _finite_float(data.get("t"), field="quote timestamp")
        try:
            as_of = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        except (ValueError, OverflowError, OSError) as exc:
            raise FinanceAPIError("Invalid quote date") from exc
        if (
            price <= 0 or previous <= 0
            or as_of.astimezone(_MARKET_TZ).date() != _market_today()
            or as_of > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
        ):
            raise FinanceAPIError("Stale or invalid quote")
        quote = StockQuote(symbol, price, (price / previous - 1) * 100, as_of.astimezone(_MARKET_TZ).date())
        # Only persist prices timestamped at/after the regular close.
        if as_of.astimezone(_MARKET_TZ).hour >= 16 and hasattr(self, "finance_store"):
            self.finance_store.record_close(symbol, quote.date, quote.price)
        cache[key] = (monotonic(), quote)
        if len(cache) > 300:
            for old_key in list(cache):
                if monotonic() - cache[old_key][0] >= _DIGEST_CACHE_SECONDS:
                    del cache[old_key]
        return quote

    async def _fetch_watch_quotes(self, watchlist) -> dict[str, StockQuote | None]:
        if not watchlist:
            return {}
        if not self.bot.settings.finnhub_api_key:
            self._set_source_status("Watch prices", "FINNHUB_API_KEY missing")
            return {item.symbol: None for item in watchlist}
        semaphore = asyncio.Semaphore(3)

        async def fetch(item):
            async with semaphore:
                try:
                    return item.symbol, await self._fetch_stock_quote(item.symbol)
                except Exception as exc:
                    logger.warning("Quote unavailable for %s: %s", item.symbol, type(exc).__name__)
                    return item.symbol, None

        results = dict(await asyncio.gather(*(fetch(item) for item in watchlist)))
        success = sum(value is not None for value in results.values())
        self._set_source_status("Watch prices", f"{success}/{len(results)} current quotes")
        return results

    @staticmethod
    def _add_lines(embed: discord.Embed, title: str, lines: list[str]) -> None:
        value = ""
        for line in lines:
            if len(embed) + len(title) + len(value) + len(line) + 100 > 5900:
                if value:
                    embed.add_field(name=title, value=value, inline=False)
                return
            if len(value) + len(line) + 1 > _EMBED_FIELD_LIMIT:
                embed.add_field(name=title, value=value, inline=False)
                value = ""
            value += ("\n" if value else "") + line
        if value:
            embed.add_field(name=title, value=value, inline=False)

    def _add_watch_performance(self, embed, watchlist, quotes) -> None:
        quotes = quotes if isinstance(quotes, dict) else {}
        lines = []
        pending = False
        for item in watchlist:
            quote = quotes.get(item.symbol)
            if quote is None:
                lines.append(f"`{item.symbol}`: current quote unavailable")
                continue
            baseline = self.finance_store.previous_week_close(item.symbol, quote.date)
            week = "week pending"
            if baseline:
                week = f"week {(quote.price / baseline[1] - 1) * 100:+.2f}%"
            else:
                pending = True
            lines.append(f"`{item.symbol}` ${quote.price:,.2f} | day {quote.change_pct:+.2f}% | {week}")
        if pending:
            lines.append("Weekly change starts after a previous-week close has been saved.")
        self._add_lines(embed, "Watchlist Performance", lines)

    async def _post_daily_alerts(self) -> None:
        channel = self._get_finance_channel()
        if not channel:
            return
        guild_id = channel.guild.id if getattr(channel, "guild", None) else None
        result = await self._build_alert_result(
            guild_id=guild_id,
            scope_id=channel.id,
            exclude_seen=True,
        )
        if result:
            await channel.send(embed=result.embed)
            self.finance_store.mark_headlines_seen(
                channel.id,
                result.news_fingerprints,
            )
        self._last_daily_status = (
            f"{datetime.datetime.now(_MARKET_TZ).isoformat(timespec='minutes')}: "
            + ("alert posted" if result else "checked; no qualifying triggers")
        )
        logger.info("Weekday finance check: %s", self._last_daily_status)

    async def _build_alert_embed(
        self,
        guild_id: int | None = None,
    ) -> discord.Embed | None:
        result = await self._build_alert_result(guild_id=guild_id)
        return result.embed if result else None

    async def _build_alert_result(
        self,
        *,
        guild_id: int | None,
        scope_id: int | None = None,
        exclude_seen: bool = False,
    ) -> AlertResult | None:
        settings = self.bot.settings
        watchlist = (
            self.finance_store.list_watchlist(guild_id)
            if guild_id is not None
            else []
        )

        econ_task = self._check_econ_tomorrow() if settings.finnhub_api_key else asyncio.sleep(0)
        spy_task = self._check_spy_daily() if settings.finnhub_api_key else asyncio.sleep(0)
        btc_task = self._check_btc_24h()
        news_task = self._fetch_all_news(watchlist)
        watch_task = self._fetch_watch_quotes(watchlist)

        econ_tomorrow, spy_alert, btc_alert, news, quotes = await asyncio.gather(
            econ_task, spy_task, btc_task, news_task, watch_task, return_exceptions=True
        )

        for name, result in (("Economic alerts", econ_tomorrow), ("SPY alerts", spy_alert), ("Bitcoin alerts", btc_alert)):
            status = f"failed ({type(result).__name__})" if isinstance(result, Exception) else "OK: checked"
            if name != "Bitcoin alerts" and not settings.finnhub_api_key:
                status = "FINNHUB_API_KEY missing"
            self._set_source_status(name, status)

        if isinstance(econ_tomorrow, Exception):
            logger.error("Econ tomorrow check failed: %s", econ_tomorrow)
            econ_tomorrow = []
        if isinstance(spy_alert, Exception):
            logger.error("SPY check failed: %s", spy_alert)
            spy_alert = None
        if isinstance(btc_alert, Exception):
            logger.error("BTC check failed: %s", btc_alert)
            btc_alert = None
        if isinstance(news, Exception):
            logger.error("Earnings news check failed: %s", news)
            news = []

        articles = news if isinstance(news, list) else []
        if exclude_seen and scope_id is not None:
            unseen = self.finance_store.unseen_fingerprints(
                scope_id,
                (article.fingerprint for article in articles),
            )
            articles = [
                article for article in articles if article.fingerprint in unseen
            ]
        earnings = rank_earnings_articles(articles, watchlist)
        quote_map = quotes if isinstance(quotes, dict) else {}
        moves = [q for q in quote_map.values() if q and abs(q.change_pct) >= _WATCH_ALERT_PCT]
        moves.sort(key=lambda q: abs(q.change_pct), reverse=True)
        move_keys = {q.symbol: f"price:{q.symbol}:{q.date.isoformat()}" for q in moves}
        if exclude_seen and scope_id is not None:
            unseen_moves = self.finance_store.unseen_fingerprints(scope_id, move_keys.values())
            moves = [q for q in moves if move_keys[q.symbol] in unseen_moves]
        unavailable = [item.symbol for item in watchlist if quote_map.get(item.symbol) is None]

        lines = []
        if isinstance(econ_tomorrow, list):
            for event in econ_tomorrow:
                lines.append(f"📅 **Tomorrow:** {event}")
        if spy_alert:
            lines.append(spy_alert)
        if btc_alert:
            lines.append(btc_alert)

        earnings_value, rendered = self._fit_news_items(earnings)
        if not lines and not earnings_value and not moves and not unavailable:
            return None

        now = datetime.datetime.now(_MARKET_TZ)
        embed = discord.Embed(
            title="⚡ Finance Alert",
            description=now.strftime("%A, %B %d"),
            color=0xE67E22,
        )
        if lines:
            embed.add_field(name="​", value="\n".join(lines), inline=False)
        if moves:
            self._add_lines(embed, "Watchlist Moves (3% or more)", [
                f"`{q.symbol}` {q.change_pct:+.2f}% today | ${q.price:,.2f} | {q.date.isoformat()}"
                for q in moves
            ])
        if unavailable:
            embed.add_field(name="Price Coverage", value="Current quotes unavailable: " + ", ".join(unavailable), inline=False)
        if earnings_value:
            embed.add_field(name="💼 Earnings", value=earnings_value, inline=False)
        return AlertResult(
            embed=embed,
            news_fingerprints=tuple(article.fingerprint for article in rendered)
            + tuple(move_keys[q.symbol] for q in moves),
        )

    async def _check_econ_tomorrow(self) -> list[str]:
        tomorrow = _market_today() + datetime.timedelta(days=1)
        params = {
            "from": tomorrow.isoformat(),
            "to": tomorrow.isoformat(),
        }
        data = await self._request_json(
            "Finnhub",
            _FINNHUB_ECON_URL,
            params=params,
            headers=self._finnhub_headers(),
        )
        if not isinstance(data, dict):
            raise FinanceAPIError("Finnhub returned an unexpected calendar payload")

        events = data.get("economicCalendar", [])
        if not isinstance(events, list):
            raise FinanceAPIError("Finnhub returned an invalid economic calendar")
        high_impact = [
            e for e in events
            if isinstance(e, dict)
            and e.get("country") in ("US", "United States")
            and (e.get("impact") or "").lower() == "high"
        ]
        return [safe_markdown_text(e.get("event", "Unknown"), 180) for e in high_impact[:3]]

    async def _check_spy_daily(self) -> str | None:
        data = await self._request_json(
            "Finnhub",
            _FINNHUB_QUOTE_URL,
            params={"symbol": "SPY"},
            headers=self._finnhub_headers(),
        )
        if not isinstance(data, dict):
            raise FinanceAPIError("Finnhub returned an unexpected quote payload")

        dp = data.get("dp")
        c = data.get("c")
        timestamp = data.get("t")
        if dp is None or c is None or not timestamp:
            return None

        quote_date = datetime.datetime.fromtimestamp(
            int(timestamp),
            tz=datetime.timezone.utc,
        ).astimezone(_MARKET_TZ).date()
        if quote_date != _market_today():
            return None
        daily_change = _finite_float(dp, field="Finnhub dp")
        close = _finite_float(c, field="Finnhub c")
        if close <= 0:
            raise FinanceAPIError("Finnhub returned a non-positive SPY price")
        if abs(daily_change) >= _SPY_ALERT_PCT:
            arrow = "📈" if daily_change > 0 else "📉"
            return (
                f"{arrow} **S&P 500 ETF (SPY)** closed {daily_change:+.2f}% today "
                f"— ${close:,.2f}"
            )
        return None

    async def _check_btc_24h(self) -> str | None:
        params = {
            "ids": "bitcoin",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
        data = await self._request_json("CoinGecko", _COINGECKO_PRICE_URL, params=params)
        if not isinstance(data, dict):
            raise FinanceAPIError("CoinGecko returned an unexpected price payload")

        btc = data.get("bitcoin", {})
        if not isinstance(btc, dict):
            raise FinanceAPIError("CoinGecko returned invalid Bitcoin data")
        price = btc.get("usd")
        change = btc.get("usd_24h_change")
        if price is None or change is None:
            return None
        if not _is_fresh_timestamp(btc.get("last_updated_at"), _MAX_CRYPTO_AGE_SECONDS):
            raise FinanceAPIError("CoinGecko returned stale Bitcoin data")

        btc_price = _finite_float(price, field="CoinGecko usd")
        btc_change = _finite_float(change, field="CoinGecko usd_24h_change")
        if btc_price <= 0:
            raise FinanceAPIError("CoinGecko returned a non-positive Bitcoin price")
        if abs(btc_change) >= _BTC_ALERT_PCT:
            arrow = "🚀" if btc_change > 0 else "💥"
            return f"{arrow} **Bitcoin** {btc_change:+.2f}% in 24hrs — ${btc_price:,.0f}"
        return None

    # ------------------------------------------------------------------ market

    async def _fetch_market(self) -> dict:
        result = {}
        for name, symbol in _STOCK_SYMBOLS.items():
            try:
                result[name] = await self._av_weekly(symbol)
            except Exception as e:
                logger.error("Failed to fetch %s: %s", name, e)
        try:
            result["Bitcoin"] = await self._coingecko_btc()
        except Exception as e:
            logger.error("Failed to fetch Bitcoin: %s", e)
        return result

    async def _av_weekly(self, symbol: str) -> dict:
        params = {
            "function": "TIME_SERIES_WEEKLY",
            "symbol": symbol,
            "apikey": self.bot.settings.alpha_vantage_api_key,
        }
        data = await self._request_json("Alpha Vantage", _AV_URL, params=params)
        if not isinstance(data, dict):
            raise FinanceAPIError("Alpha Vantage returned an unexpected payload")

        if "Information" in data or "Note" in data:
            msg = data.get("Information") or data.get("Note", "")
            raise FinanceAPIError(
                f"Alpha Vantage rejected {symbol}: {safe_markdown_text(msg, 80)}"
            )
        if "Error Message" in data:
            raise FinanceAPIError(f"Alpha Vantage rejected symbol {symbol}")

        series = data.get("Weekly Time Series", {})
        if not isinstance(series, dict):
            raise FinanceAPIError(f"Invalid Alpha Vantage data for {symbol}")

        dates = sorted(series.keys(), reverse=True)[:2]
        if len(dates) < 2:
            raise FinanceAPIError(f"Insufficient Alpha Vantage data for {symbol}")

        try:
            latest_date = datetime.date.fromisoformat(dates[0])
        except ValueError as exc:
            raise FinanceAPIError(f"Invalid Alpha Vantage date for {symbol}") from exc
        age_days = (_market_today() - latest_date).days
        if age_days < 0 or age_days > _MAX_WEEKLY_DATA_AGE_DAYS:
            raise FinanceAPIError(f"Alpha Vantage data for {symbol} is stale")

        try:
            close = _finite_float(series[dates[0]]["4. close"], field=f"{symbol} close")
            prev_close = _finite_float(
                series[dates[1]]["4. close"],
                field=f"{symbol} previous close",
            )
        except (KeyError, TypeError) as exc:
            raise FinanceAPIError(f"Incomplete Alpha Vantage data for {symbol}") from exc
        if close <= 0 or prev_close <= 0:
            raise FinanceAPIError(f"Alpha Vantage returned a non-positive price for {symbol}")

        pct = (close - prev_close) / prev_close * 100
        price_str = f"{close:,.2f}" if close < 10_000 else f"{close:,.0f}"
        return {"price": f"${price_str}", "pct": pct, "as_of": dates[0]}

    async def _coingecko_btc(self) -> dict:
        params = {"vs_currency": "usd", "ids": "bitcoin", "price_change_percentage": "7d"}
        data = await self._request_json("CoinGecko", _COINGECKO_MARKETS_URL, params=params)
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise FinanceAPIError("CoinGecko returned invalid Bitcoin market data")

        coin = data[0]
        price = _finite_float(coin.get("current_price"), field="CoinGecko current_price")
        pct = _finite_float(
            coin.get("price_change_percentage_7d_in_currency"),
            field="CoinGecko 7-day change",
        )
        if price <= 0:
            raise FinanceAPIError("CoinGecko returned a non-positive Bitcoin price")

        updated_raw = coin.get("last_updated")
        try:
            updated_at = datetime.datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise FinanceAPIError("CoinGecko returned an invalid update time") from exc
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=datetime.timezone.utc)
        age = datetime.datetime.now(datetime.timezone.utc) - updated_at.astimezone(
            datetime.timezone.utc
        )
        if age.total_seconds() < 0 or age.total_seconds() > _MAX_CRYPTO_AGE_SECONDS:
            raise FinanceAPIError("CoinGecko returned stale Bitcoin market data")

        return {
            "price": f"${price:,.0f}",
            "pct": pct,
            "as_of": updated_at.astimezone(_MARKET_TZ).date().isoformat(),
        }

    # ------------------------------------------------------------------ news

    async def _fetch_news_feed(self, source: str, url: str) -> list[NewsArticle]:
        try:
            if self.bot.http_session is None:
                raise FinanceAPIError("HTTP session is not initialized")
            async with self.bot.http_session.get(
                url,
                headers=_RSS_HEADERS,
                allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    logger.warning("%s RSS returned HTTP %d", source, resp.status)
                    self._set_source_status(source, f"HTTP {resp.status}")
                    return []
                raw = await read_bounded(resp.content, _MAX_RSS_BYTES)
        except Exception as exc:
            self._set_source_status(source, f"fetch failed ({type(exc).__name__})")
            return []

        try:
            articles = parse_feed(raw, source=source)
            self._set_source_status(source, f"OK: {len(articles)} fresh" if articles else "no fresh articles")
            return articles
        except ET.ParseError:
            self._set_source_status(source, "invalid XML")
            return []

    async def _fetch_news(self) -> list[NewsArticle]:
        results = await asyncio.gather(
            *(
                self._fetch_news_feed(source, url)
                for source, url in _NEWS_FEEDS
            ),
            return_exceptions=True,
        )
        articles = []
        for (source, _), result in zip(_NEWS_FEEDS, results):
            if isinstance(result, Exception):
                logger.error("%s RSS fetch failed: %s", source, result)
                continue
            articles.extend(result)
        return articles

    async def _fetch_company_news(self, item) -> list[NewsArticle]:
        cache = getattr(self, "_provider_cache", None)
        if cache is None:
            self._provider_cache = cache = {}
        today = _market_today()
        key = ("news", item.symbol, today)
        cached = cache.get(key)
        if cached and monotonic() - cached[0] < _DIGEST_CACHE_SECONDS:
            data = cached[1]
        else:
            data = await self._request_json(
                "Finnhub", _FINNHUB_NEWS_URL,
                params={"symbol": item.symbol, "from": (today - datetime.timedelta(days=7)).isoformat(), "to": today.isoformat()},
                headers=self._finnhub_headers(),
            )
            if not isinstance(data, list):
                raise FinanceAPIError("Invalid company-news payload")
            cache[key] = (monotonic(), data)
        current = datetime.datetime.now(datetime.timezone.utc)
        articles = []
        for row in data[:250]:
            if not isinstance(row, dict):
                continue
            title = safe_markdown_text(row.get("headline"), 120)
            url = safe_http_url(row.get("url"))
            try:
                published = datetime.datetime.fromtimestamp(float(row.get("datetime")), datetime.timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            if not title or not url or not current - datetime.timedelta(days=7) <= published <= current + datetime.timedelta(minutes=5):
                continue
            # Preserve the actual publisher so aggregating via Finnhub does not hide diversity.
            source = safe_markdown_text(row.get("source") or "Finnhub", 50)
            article = NewsArticle(source, title, url, published)
            if item.symbol in article_tags(article, [item]):
                articles.append(article)
        self._set_source_status(f"Company news {item.symbol}", f"OK: {len(articles)} matched")
        return articles

    async def _fetch_all_news(self, watchlist) -> list[NewsArticle]:
        semaphore = asyncio.Semaphore(3)

        async def company(item):
            async with semaphore:
                try:
                    return await self._fetch_company_news(item)
                except Exception as exc:
                    self._set_source_status(f"Company news {item.symbol}", f"unavailable ({type(exc).__name__})")
                    return []

        jobs = [self._fetch_news()]
        if self.bot.settings.finnhub_api_key:
            jobs.extend(company(item) for item in watchlist)
        results = await asyncio.gather(*jobs, return_exceptions=True)
        return [article for result in results if isinstance(result, list) for article in result]

    # ------------------------------------------------------------------ econ

    async def _fetch_econ_calendar(self, days: int = 7) -> list[str]:
        if days < 1:
            return []
        today = _market_today()
        from_date = today + datetime.timedelta(days=1)
        to_date = today + datetime.timedelta(days=days)
        params = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        }
        data = await self._request_json(
            "Finnhub",
            _FINNHUB_ECON_URL,
            params=params,
            headers=self._finnhub_headers(),
        )
        if not isinstance(data, dict):
            raise FinanceAPIError("Finnhub returned an unexpected calendar payload")

        events = data.get("economicCalendar", [])
        if not isinstance(events, list):
            raise FinanceAPIError("Finnhub returned an invalid economic calendar")
        high_impact = [
            e for e in events
            if isinstance(e, dict)
            and e.get("country") in ("US", "United States")
            and (e.get("impact") or "").lower() == "high"
        ]
        result = []
        for e in high_impact[:5]:
            date_str = str(e.get("time", ""))[:10]
            name = safe_markdown_text(e.get("event", "Unknown event"), 180)
            result.append(f"{date_str} — {name}")
        return result

    # ------------------------------------------------------------------ cards

    async def _fetch_card_section(self) -> str | None:
        today = _market_today()
        freedom_quarters, transfer_bonuses = await asyncio.gather(
            self._fetch_chase_freedom_quarters(today),
            self._fetch_chase_transfer_bonuses(today),
        )
        return self._format_card_section(today, freedom_quarters, transfer_bonuses)

    @staticmethod
    def _rewards_unavailable() -> str:
        return (
            f"• **Freedom Flex 5%:** current categories could not be verified. [Chase calendar]({CHASE_FREEDOM_CALENDAR_URL})\n"
            f"• **Ultimate Rewards:** no current bonus verified. [Transfer tracker]({CHASE_TRANSFER_BONUS_URL})"
        )

    @staticmethod
    def _format_card_section(
        today: datetime.date,
        freedom_quarters: tuple[FreedomQuarter, ...],
        transfer_bonuses: tuple[TransferBonus, ...],
    ) -> str | None:
        quarter = (today.month - 1) // 3 + 1
        lines = []
        category_limit = 330
        transfer_limit = 300
        quarter_parts = []
        for item in freedom_quarters:
            categories = ", ".join(
                safe_markdown_text(category, 80) for category in item.categories
            )
            status = "current" if item.quarter == quarter else "upcoming"
            year = today.year + int(item.quarter < quarter)
            quarter_label = f"Q{item.quarter}" + (f" {year}" if year != today.year else "")
            part = (
                f"{quarter_label} ({_QUARTER_MONTHS[item.quarter]}, {status}): "
                + categories
            )
            category_line = (
                "• **Freedom Flex 5%:** " + "; ".join((*quarter_parts, part))
            )
            if len(category_line) <= category_limit:
                quarter_parts.append(part)
            elif not quarter_parts:
                quarter_parts.append(part[:250] + "... (full list: Chase calendar)")

        if quarter_parts:
            lines.append("• **Freedom Flex 5%:** " + "; ".join(quarter_parts))
        else:
            lines.append("• **Freedom Flex 5%:** current categories could not be verified.")

        transfer_parts = []
        for bonus in transfer_bonuses:
            partner = safe_markdown_text(bonus.partner, 80)
            bonus_text = safe_markdown_text(bonus.bonus, 80)
            displayed_bonus = (
                bonus_text
                if bonus_text.lower().startswith("up to ")
                else f"+{bonus_text}"
            )
            expiration = bonus.expires_on.strftime("%b %d")
            transfer_part = (
                f"{partner} {displayed_bonus} through {expiration}"
            )
            bonus_line = (
                "• **Ultimate Rewards transfer bonuses:** "
                + ", ".join((*transfer_parts, transfer_part))
            )
            if len(bonus_line) <= transfer_limit:
                transfer_parts.append(transfer_part)

        if transfer_parts:
            lines.append(
                "• **Ultimate Rewards transfer bonuses:** "
                + ", ".join(transfer_parts)
            )
        else:
            lines.append("• **Ultimate Rewards:** no current bonus verified; check the tracker.")

        sources = [f"[Chase calendar]({CHASE_FREEDOM_CALENDAR_URL})", f"[PointsYeah tracker]({CHASE_TRANSFER_BONUS_URL})"]

        footer = " · ".join(sources)
        footer += "\nActivate 5% categories; spending caps apply. Verify transfers in Chase."
        return "\n".join((*lines, footer))

    async def _fetch_rewards_page(self, label: str, url: str) -> str | None:
        session = self.bot.http_session
        if session is None:
            logger.warning("%s fetch skipped: HTTP session is not initialized", label)
            return None

        try:
            async with session.get(url, headers={"User-Agent": _RSS_HEADERS["User-Agent"]}, allow_redirects=False) as response:
                if response.status != 200:
                    logger.warning("%s returned HTTP %d", label, response.status)
                    self._set_source_status(label, f"HTTP {response.status}")
                    return None
                raw = await read_bounded(response.content, _MAX_REWARDS_PAGE_BYTES)
        except Exception as exc:
            self._set_source_status(label, f"fetch failed ({type(exc).__name__})")
            return None
        self._set_source_status(label, "OK: page downloaded")
        return raw.decode("utf-8", errors="replace")

    async def _fetch_chase_freedom_quarters(
        self,
        today: datetime.date,
    ) -> tuple[FreedomQuarter, ...]:
        html = await self._fetch_rewards_page(
            "Chase rewards page",
            CHASE_FREEDOM_CALENDAR_URL,
        )
        if html is None:
            return ()
        result = parse_chase_freedom_quarters(html, today)
        self._set_source_status("Chase calendar", f"OK: {len(result)} quarters" if result else "current categories not verified")
        return result

    async def _fetch_chase_transfer_bonuses(
        self,
        today: datetime.date,
    ) -> tuple[TransferBonus, ...]:
        html = await self._fetch_rewards_page(
            "Transfer bonus tracker",
            CHASE_TRANSFER_BONUS_URL,
        )
        if html is None:
            return ()
        result = parse_chase_transfer_bonuses(html, today)
        self._set_source_status("Chase transfers", f"OK: {len(result)} active bonuses" if result else "no current bonus verified")
        return result

    # ------------------------------------------------------------------ slash

    @app_commands.command(name="finance_status", description="Check finance scheduling and source coverage")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def finance_status(self, interaction: discord.Interaction) -> None:
        lines = [
            f"Weekday loop running: {self.daily_check.is_running()}",
            f"Friday loop running: {self.weekly_digest.is_running()}",
            f"Next weekday loop check: {self.daily_check.next_iteration}",
            f"Last weekday result: {getattr(self, '_last_daily_status', 'not checked')}",
            f"Finnhub configured: {bool(self.bot.settings.finnhub_api_key)}",
        ]
        lines.extend(f"{source}: {status}" for source, status in getattr(self, "_source_status", {}).items())
        embed = discord.Embed(title="Finance Status", description="Checks run at 5 PM Eastern; weekday alerts Mon-Thu, digest Fri.")
        self._add_lines(embed, "Status", lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    watch = app_commands.Group(
        name="watch",
        description="Manage the server finance-news watchlist",
    )

    @watch.command(name="add", description="Add a ticker to the server watchlist")
    @app_commands.describe(
        symbol="Ticker symbol, such as NVDA",
        company="Optional company name to improve headline matching",
    )
    @app_commands.checks.cooldown(2, 10.0, key=lambda interaction: interaction.user.id)
    async def watch_add(
        self,
        interaction: discord.Interaction,
        symbol: str,
        company: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "The shared watchlist is only available in a server.",
                ephemeral=True,
            )
            return

        normalized_symbol = symbol.strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
            await interaction.response.send_message(
                "Use a valid ticker with 1–10 letters, numbers, dots, or hyphens.",
                ephemeral=True,
            )
            return

        company_name = None
        if company:
            company_name = " ".join(company.split())[:_MAX_COMPANY_NAME_LENGTH]
            if not company_name:
                company_name = None

        try:
            added = self.finance_store.add_watch(
                interaction.guild_id,
                normalized_symbol,
                company_name,
                interaction.user.id,
            )
        except WatchlistLimitError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if not added:
            await interaction.response.send_message(
                f"`{normalized_symbol}` is already on this server's watchlist.",
                ephemeral=True,
            )
            return

        self._digest_cache.pop(interaction.guild_id, None)
        company_label = (
            f" ({safe_markdown_text(company_name, _MAX_COMPANY_NAME_LENGTH)})"
            if company_name
            else ""
        )
        await interaction.response.send_message(
            f"Added `{normalized_symbol}`{company_label} to the server watchlist.",
            ephemeral=True,
        )

    @watch.command(name="remove", description="Remove a ticker from the server watchlist")
    @app_commands.describe(symbol="Ticker symbol to remove")
    @app_commands.checks.cooldown(2, 10.0, key=lambda interaction: interaction.user.id)
    async def watch_remove(
        self,
        interaction: discord.Interaction,
        symbol: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "The shared watchlist is only available in a server.",
                ephemeral=True,
            )
            return

        normalized_symbol = symbol.strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
            await interaction.response.send_message("Invalid ticker symbol.", ephemeral=True)
            return

        removed = self.finance_store.remove_watch(
            interaction.guild_id,
            normalized_symbol,
        )
        if not removed:
            await interaction.response.send_message(
                f"`{normalized_symbol}` is not on this server's watchlist.",
                ephemeral=True,
            )
            return

        self._digest_cache.pop(interaction.guild_id, None)
        await interaction.response.send_message(
            f"Removed `{normalized_symbol}` from the server watchlist.",
            ephemeral=True,
        )

    @watch.command(name="list", description="Show the server finance-news watchlist")
    async def watch_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "The shared watchlist is only available in a server.",
                ephemeral=True,
            )
            return

        watchlist = self.finance_store.list_watchlist(interaction.guild_id)
        if not watchlist:
            await interaction.response.send_message(
                "This server's finance watchlist is empty.",
                ephemeral=True,
            )
            return

        lines = [
            (
                f"• `{item.symbol}` — {safe_markdown_text(item.company_name, 80)}"
                if item.company_name
                else f"• `{item.symbol}`"
            )
            for item in watchlist
        ]
        embed = discord.Embed(
            title="Server Finance Watchlist",
            description="\n".join(lines),
            color=0x3498DB,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="finance", description="Show the weekly finance digest")
    @app_commands.checks.cooldown(1, 60.0, key=lambda interaction: interaction.user.id)
    async def finance_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = await self._build_digest_embed(interaction.guild_id)
        await interaction.followup.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(FinanceCog(bot))

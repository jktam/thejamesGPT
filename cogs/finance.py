from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime
import json
import logging
import math
from pathlib import Path
import re
from time import monotonic
from typing import Any
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from services.chase_rewards import (
    CHASE_FREEDOM_CALENDAR_URL,
    parse_chase_freedom_categories,
)
from services.finance_news import (
    NEWS_MAX_AGE_HOURS,
    NewsArticle,
    format_article_age,
    parse_feed,
    safe_markdown_text,
    split_ranked_articles,
)
from services.finance_store import (
    FinanceStore,
    WatchlistLimitError,
)

logger = logging.getLogger(__name__)

_CARD_CATEGORIES_PATH = Path(__file__).parent.parent / "data" / "card_categories.json"

_QUARTER_MONTHS = {1: "Jan–Mar", 2: "Apr–Jun", 3: "Jul–Sep", 4: "Oct–Dec"}

_MARKET_TZ = ZoneInfo("America/New_York")
_DAILY_TIME = datetime.time(hour=17, minute=0, tzinfo=_MARKET_TZ)

_AV_URL = "https://www.alphavantage.co/query"
_COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
_COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
_FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_FINNHUB_ECON_URL = "https://finnhub.io/api/v1/calendar/economic"
_NEWS_FEEDS = (
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
)

_STOCK_SYMBOLS = {
    "S&P 500 ETF (SPY)": "SPY",
    "Nasdaq-100 ETF (QQQ)": "QQQ",
    "Gold ETF (GLD)": "GLD",
}

# Daily alert thresholds
_SPY_ALERT_PCT = 1.5
_BTC_ALERT_PCT = 5.0
_DIGEST_CACHE_SECONDS = 5 * 60
_MAX_CRYPTO_AGE_SECONDS = 60 * 60
_MAX_WEEKLY_DATA_AGE_DAYS = 10
_MAX_RSS_BYTES = 512 * 1024
_MAX_CHASE_PAGE_BYTES = 1024 * 1024
_EMBED_FIELD_LIMIT = 1024
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_MAX_COMPANY_NAME_LENGTH = 80


class FinanceAPIError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DigestResult:
    embed: discord.Embed
    news_fingerprints: tuple[str, ...]


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
        self._card_data: dict = self._load_card_categories()
        self.finance_store = FinanceStore()
        self._digest_cache: dict[int | None, tuple[float, DigestResult]] = {}
        self._digest_lock = asyncio.Lock()
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

    @staticmethod
    def _load_card_categories() -> dict:
        try:
            return json.loads(_CARD_CATEGORIES_PATH.read_text())
        except Exception:
            logger.warning("Could not load card_categories.json")
            return {}

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
        news_task = self._fetch_news()
        econ_task = self._fetch_econ_calendar(days=7) if settings.finnhub_api_key else asyncio.sleep(0)
        card_task = self._fetch_card_section()

        market, news, econ_events, card_text = await asyncio.gather(
            market_task,
            news_task,
            econ_task,
            card_task,
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

            priority_news, general_news = split_ranked_articles(
                articles,
                watchlist,
            )
            if priority_news:
                value, rendered = self._fit_news_items(priority_news)
                if value:
                    embed.add_field(
                        name="🎯 Watchlist & Rate-Cut News",
                        value=value,
                        inline=False,
                    )
                    shown_articles.extend(rendered)
            if general_news:
                value, rendered = self._fit_news_items(
                    [(article, []) for article in general_news]
                )
                if value:
                    embed.add_field(
                        name="📰 Fresh Finance Headlines",
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
            embed.add_field(
                name="👀 Server Watchlist",
                value=", ".join(f"`{item.symbol}`" for item in watchlist),
                inline=False,
            )

        if isinstance(card_text, str) and card_text:
            embed.add_field(name="💳 Chase Bonus Categories", value=card_text, inline=False)

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

    async def _post_daily_alerts(self) -> None:
        channel = self._get_finance_channel()
        if not channel:
            return
        embed = await self._build_alert_embed()
        if embed:
            await channel.send(embed=embed)

    async def _build_alert_embed(self) -> discord.Embed | None:
        settings = self.bot.settings

        econ_task = self._check_econ_tomorrow() if settings.finnhub_api_key else asyncio.sleep(0)
        spy_task = self._check_spy_daily() if settings.finnhub_api_key else asyncio.sleep(0)
        btc_task = self._check_btc_24h()

        econ_tomorrow, spy_alert, btc_alert = await asyncio.gather(
            econ_task, spy_task, btc_task, return_exceptions=True
        )

        if isinstance(econ_tomorrow, Exception):
            logger.error("Econ tomorrow check failed: %s", econ_tomorrow)
            econ_tomorrow = []
        if isinstance(spy_alert, Exception):
            logger.error("SPY check failed: %s", spy_alert)
            spy_alert = None
        if isinstance(btc_alert, Exception):
            logger.error("BTC check failed: %s", btc_alert)
            btc_alert = None

        lines = []
        if isinstance(econ_tomorrow, list):
            for event in econ_tomorrow:
                lines.append(f"📅 **Tomorrow:** {event}")
        if spy_alert:
            lines.append(spy_alert)
        if btc_alert:
            lines.append(btc_alert)

        if not lines:
            return None

        now = datetime.datetime.now(_MARKET_TZ)
        embed = discord.Embed(
            title="⚡ Finance Alert",
            description=now.strftime("%A, %B %d"),
            color=0xE67E22,
        )
        embed.add_field(name="​", value="\n".join(lines), inline=False)
        return embed

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
                allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    logger.warning("%s RSS returned HTTP %d", source, resp.status)
                    return []
                raw = await resp.content.read(_MAX_RSS_BYTES + 1)
                if len(raw) > _MAX_RSS_BYTES:
                    logger.warning("%s RSS response exceeded size limit", source)
                    return []
        except Exception:
            logger.exception("%s RSS fetch failed", source)
            return []

        try:
            return parse_feed(raw, source=source)
        except ET.ParseError:
            logger.warning("%s RSS returned invalid XML", source)
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
        quarter = (today.month - 1) // 3 + 1
        key = f"{today.year}-Q{quarter}"
        months = _QUARTER_MONTHS[quarter]

        lines = []
        official_categories = await self._fetch_chase_freedom_categories(today)

        for card, cats in self._card_data.get("quarterly_cards", {}).items():
            current = (
                official_categories
                if card == "Chase Freedom Flex" and official_categories
                else cats.get(key, [])
            )
            if current:
                lines.append(f"• **{card}:** {', '.join(current)}")

        transfer_bonuses = self._card_data.get("chase_ur_transfer_bonus") or []
        active_bonuses = []
        for bonus in transfer_bonuses:
            if not isinstance(bonus, dict):
                continue
            try:
                starts_on = datetime.date.fromisoformat(bonus["starts_on"])
                ends_on = datetime.date.fromisoformat(bonus["ends_on"])
            except (KeyError, TypeError, ValueError):
                continue
            if starts_on <= today <= ends_on:
                active_bonuses.append(safe_markdown_text(bonus.get("name"), 100))
        if active_bonuses:
            lines.append(f"• **UR Transfer Bonuses:** {', '.join(active_bonuses)}")

        if not lines:
            return None
        source = (
            f"\n[Official Chase calendar]({CHASE_FREEDOM_CALENDAR_URL})"
            if official_categories
            else "\nLocal current-quarter fallback"
        )
        return f"Q{quarter} ({months})\n" + "\n".join(lines) + source

    async def _fetch_chase_freedom_categories(
        self,
        today: datetime.date,
    ) -> tuple[str, ...] | None:
        session = self.bot.http_session
        if session is None:
            logger.warning("Chase rewards fetch skipped: HTTP session is not initialized")
            return None

        try:
            async with session.get(
                CHASE_FREEDOM_CALENDAR_URL,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Chase rewards page returned HTTP %d",
                        response.status,
                    )
                    return None
                raw = await response.content.read(_MAX_CHASE_PAGE_BYTES + 1)
                if len(raw) > _MAX_CHASE_PAGE_BYTES:
                    logger.warning("Chase rewards page exceeded size limit")
                    return None
        except Exception:
            logger.exception("Chase rewards page fetch failed")
            return None

        return parse_chase_freedom_categories(
            raw.decode("utf-8", errors="replace"),
            today,
        )

    # ------------------------------------------------------------------ slash

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

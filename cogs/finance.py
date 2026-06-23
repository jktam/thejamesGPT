from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path

from bs4 import BeautifulSoup
import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

_CARD_CATEGORIES_PATH = Path(__file__).parent.parent / "data" / "card_categories.json"

_QUARTER_MONTHS = {1: "Jan–Mar", 2: "Apr–Jun", 3: "Jul–Sep", 4: "Oct–Dec"}

_DIGEST_TIME = datetime.time(hour=14, minute=0, tzinfo=datetime.timezone.utc)

_AV_URL = "https://www.alphavantage.co/query"
_COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
_NEWS_RSS_URL = "https://www.cnbc.com/id/10000664/device/rss/rss.html"

_STOCK_SYMBOLS = {
    "S&P 500": "SPY",
    "Nasdaq": "QQQ",
    "Gold": "GLD",
}


class FinanceCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._card_data: dict = self._load_card_categories()
        self.weekly_digest.start()

    def cog_unload(self) -> None:
        self.weekly_digest.cancel()

    @staticmethod
    def _load_card_categories() -> dict:
        try:
            return json.loads(_CARD_CATEGORIES_PATH.read_text())
        except Exception:
            logger.warning("Could not load card_categories.json")
            return {}

    # ------------------------------------------------------------------ tasks

    @tasks.loop(time=_DIGEST_TIME)
    async def weekly_digest(self) -> None:
        if datetime.datetime.now(datetime.timezone.utc).weekday() != 6:
            return
        await self._post_digest()

    @weekly_digest.before_loop
    async def before_digest(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ core

    async def _post_digest(self) -> None:
        channel_id = self.bot.settings.finance_channel_id
        if not channel_id:
            logger.warning("FINANCE_CHANNEL_ID not set — skipping weekly digest")
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.error("Finance channel %d not found", channel_id)
            return
        embed = await self._build_embed()
        await channel.send(embed=embed)

    async def _build_embed(self) -> discord.Embed:
        settings = self.bot.settings

        market_task = self._fetch_market() if settings.alpha_vantage_api_key else asyncio.sleep(0)
        news_task = self._fetch_news()
        econ_task = self._fetch_econ_calendar() if settings.finnhub_api_key else asyncio.sleep(0)

        market, news, econ_events = await asyncio.gather(
            market_task, news_task, econ_task, return_exceptions=True
        )

        if isinstance(market, Exception):
            logger.error("Market fetch failed", exc_info=market)
        if isinstance(news, Exception):
            logger.error("News fetch failed", exc_info=news)
        if isinstance(econ_events, Exception):
            logger.error("Econ calendar fetch failed", exc_info=econ_events)

        now = datetime.datetime.now(datetime.timezone.utc)
        embed = discord.Embed(
            title="📊 Weekly Finance Digest",
            description=f"Week of {now.strftime('%B %d, %Y')}",
            color=0x2ECC71,
        )

        if isinstance(market, dict) and market:
            lines = []
            for name, info in market.items():
                pct = info["pct"]
                arrow = "▲" if pct >= 0 else "▼"
                lines.append(f"• **{name}:** {info['price']}  {arrow} {abs(pct):.2f}%")
            embed.add_field(name="📈 Markets This Week", value="\n".join(lines), inline=False)

        if isinstance(econ_events, list) and econ_events:
            embed.add_field(
                name="📅 Economic Calendar — Coming Up",
                value="\n".join(f"• {e}" for e in econ_events),
                inline=False,
            )

        if isinstance(news, list) and news:
            lines = [f"{i + 1}. [{h['title']}]({h['url']})" for i, h in enumerate(news)]
            embed.add_field(
                name="📰 Top Finance Headlines",
                value="\n".join(lines),
                inline=False,
            )

        card_text = self._card_section()
        if card_text:
            embed.add_field(name="💳 Quarterly Bonus Categories", value=card_text, inline=False)

        sources = ["Alpha Vantage", "CoinGecko", "CNBC", "Finnhub"]
        embed.set_footer(text="Data: " + " · ".join(sources))
        return embed

    # ------------------------------------------------------------------ market

    async def _fetch_market(self) -> dict:
        result = {}

        # Sequential calls to stay within AV's 5 req/min rate limit
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
        async with self.bot.http_session.get(_AV_URL, params=params) as resp:
            data = await resp.json()

        if "Information" in data or "Note" in data:
            msg = data.get("Information") or data.get("Note", "")
            raise ValueError(f"AV rate limit for {symbol}: {msg[:80]}")

        series = data.get("Weekly Time Series", {})
        dates = list(series.keys())[:2]
        if len(dates) < 2:
            raise ValueError(f"No AV data for {symbol}: {list(data.keys())}")

        close = float(series[dates[0]]["4. close"])
        prev_close = float(series[dates[1]]["4. close"])
        pct = (close - prev_close) / prev_close * 100
        price_str = f"{close:,.2f}" if close < 10_000 else f"{close:,.0f}"
        return {"price": price_str, "pct": pct}

    async def _coingecko_btc(self) -> dict:
        params = {
            "vs_currency": "usd",
            "ids": "bitcoin",
            "price_change_percentage": "7d",
        }
        async with self.bot.http_session.get(_COINGECKO_URL, params=params) as resp:
            data = await resp.json()

        coin = data[0]
        price = coin["current_price"]
        pct = coin.get("price_change_percentage_7d_in_currency") or 0.0
        return {"price": f"{price:,.0f}", "pct": pct}

    # ------------------------------------------------------------------ news

    async def _fetch_news(self) -> list[dict]:
        try:
            async with self.bot.http_session.get(_NEWS_RSS_URL) as resp:
                if resp.status != 200:
                    logger.warning("CNBC RSS returned HTTP %d", resp.status)
                    return []
                text = await resp.text()
        except Exception:
            logger.exception("CNBC RSS fetch failed")
            return []

        soup = BeautifulSoup(text, "html.parser")
        headlines = []
        for item in soup.find_all("item")[:5]:
            title_el = item.find("title")
            guid_el = item.find("guid")
            title = title_el.get_text(strip=True) if title_el else ""
            url = guid_el.get_text(strip=True) if guid_el else ""
            if title and url:
                headlines.append({"title": title[:120], "url": url})
        return headlines

    # ------------------------------------------------------------------ econ

    async def _fetch_econ_calendar(self) -> list[str]:
        today = datetime.date.today()
        to_date = today + datetime.timedelta(days=7)
        params = {
            "from": today.isoformat(),
            "to": to_date.isoformat(),
            "token": self.bot.settings.finnhub_api_key,
        }
        async with self.bot.http_session.get(
            "https://finnhub.io/api/v1/calendar/economic", params=params
        ) as resp:
            data = await resp.json()

        events = data.get("economicCalendar", [])
        high_impact = [
            e for e in events
            if e.get("country") in ("US", "United States")
            and (e.get("impact") or "").lower() == "high"
        ]
        result = []
        for e in high_impact[:5]:
            date_str = str(e.get("time", ""))[:10]
            name = e.get("event", "Unknown event")
            result.append(f"{date_str} — {name}")
        return result

    # ------------------------------------------------------------------ cards

    def _card_section(self) -> str | None:
        today = datetime.date.today()
        quarter = (today.month - 1) // 3 + 1
        key = f"{today.year}-Q{quarter}"
        months = _QUARTER_MONTHS[quarter]

        lines = []
        for card, quarters in self._card_data.items():
            cats = quarters.get(key, [])
            if cats:
                lines.append(f"• **{card}:** {', '.join(cats)}")

        if not lines:
            return None
        return f"Q{quarter} ({months})\n" + "\n".join(lines)

    # ------------------------------------------------------------------ slash

    @app_commands.command(name="finance", description="Show the weekly finance digest")
    async def finance_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = await self._build_embed()
        await interaction.followup.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(FinanceCog(bot))

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

_CARD_CATEGORIES_PATH = Path(__file__).parent.parent / "data" / "card_categories.json"

_QUARTER_MONTHS = {1: "Jan–Mar", 2: "Apr–Jun", 3: "Jul–Sep", 4: "Oct–Dec"}

_MARKET_TICKERS = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Bitcoin": "BTC-USD",
    "Gold": "GC=F",
    "10Y UST": "^TNX",
}

_DIGEST_TIME = datetime.time(hour=14, minute=0, tzinfo=datetime.timezone.utc)


def _fetch_market_sync() -> dict:
    import yfinance as yf

    result = {}
    for name, symbol in _MARKET_TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if hist.empty:
                continue
            close = float(hist["Close"].iloc[-1])
            open_ = float(hist["Open"].iloc[0])
            pct = (close - open_) / open_ * 100

            if symbol == "^TNX":
                price_str = f"{close:.2f}%"
            elif close >= 10_000:
                price_str = f"{close:,.0f}"
            elif close >= 100:
                price_str = f"{close:,.2f}"
            else:
                price_str = f"{close:.2f}"

            result[name] = {"price": price_str, "pct": pct}
        except Exception:
            logger.exception("Failed to fetch market data for %s", symbol)
    return result


_REDDIT_URL = "https://www.reddit.com/r/investing/top.json"
_REDDIT_HEADERS = {"User-Agent": "thejamesgpt-finance-bot/1.0"}


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

        market_task = asyncio.to_thread(_fetch_market_sync)

        reddit_task = self._fetch_reddit()

        econ_task = (
            self._fetch_econ_calendar()
            if settings.finnhub_api_key
            else asyncio.sleep(0)
        )

        market, reddit_posts, econ_events = await asyncio.gather(
            market_task, reddit_task, econ_task, return_exceptions=True
        )

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

        if isinstance(reddit_posts, list) and reddit_posts:
            lines = [
                f"{i + 1}. [{p['title']}]({p['url']}) — {p['score']:,} upvotes"
                for i, p in enumerate(reddit_posts)
            ]
            embed.add_field(
                name="💬 r/investing — Top This Week",
                value="\n".join(lines),
                inline=False,
            )

        card_text = self._card_section()
        if card_text:
            embed.add_field(name="💳 Quarterly Bonus Categories", value=card_text, inline=False)

        sources = ["Yahoo Finance", "Reddit"]
        if isinstance(econ_events, list) and econ_events:
            sources.append("Finnhub")
        embed.set_footer(text="Data: " + " · ".join(sources))
        return embed

    async def _fetch_reddit(self) -> list[dict]:
        params = {"t": "week", "limit": "5"}
        async with self.bot.http_session.get(
            _REDDIT_URL, params=params, headers=_REDDIT_HEADERS
        ) as resp:
            data = await resp.json()

        posts = []
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            posts.append({
                "title": p.get("title", "")[:120],
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "score": p.get("score", 0),
            })
        return posts

    async def _fetch_econ_calendar(self) -> list[str]:
        today = datetime.date.today()
        to_date = today + datetime.timedelta(days=7)
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {
            "from": today.isoformat(),
            "to": to_date.isoformat(),
            "token": self.bot.settings.finnhub_api_key,
        }
        async with self.bot.http_session.get(url, params=params) as resp:
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

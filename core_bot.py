from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import Settings

logger = logging.getLogger("thejamesroll-bot")


class JamesBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        # intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

        self.settings = settings
        self.http_session: aiohttp.ClientSession | None = None
        self.geocode_cache: dict[str, tuple[float, float, float]] = {}
        self.geocode_ttl_seconds = 60 * 60 * 24

    async def setup_hook(self) -> None:
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        self.http_session = aiohttp.ClientSession(timeout=timeout)

        for ext in ("cogs.general", "cogs.ai", "cogs.places"):
            await self.load_extension(ext)
            logger.info("Loaded extension %s", ext)

        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=self.settings.status_text,
            ),
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("Unhandled app command error", exc_info=error)

        msg = "⚠️ Command failed."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
import logging
import discord

from utils.text import chunk_text

logger = logging.getLogger("thejamesroll-bot")


async def run_interaction_task(interaction, *, task_name, work, ephemeral: bool, max_chunks: int = 6):
    await interaction.response.defer(ephemeral=ephemeral, thinking=True)

    try:
        result = await work()

        if isinstance(result, discord.Embed):
            await interaction.followup.send(embed=result, ephemeral=ephemeral)
            return

        for chunk in chunk_text(str(result), max_chunks=max_chunks):
            await interaction.followup.send(chunk, ephemeral=ephemeral)

    except Exception as exc:
        logger.exception("%s failed", task_name)
        await interaction.followup.send(f"⚠️ {task_name} failed: {exc}", ephemeral=True)
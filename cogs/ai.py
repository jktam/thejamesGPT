from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.google_translate import translate_text
from services.openai_service import OpenAIService
from utils.presentation import run_interaction_task
from utils.visibility import VISIBILITY_CHOICES, is_ephemeral


REWRITE_TONE_CHOICES = [
    app_commands.Choice(name="professional", value="professional"),
    app_commands.Choice(name="casual", value="casual"),
    app_commands.Choice(name="friendly", value="friendly"),
    app_commands.Choice(name="direct", value="direct"),
    app_commands.Choice(name="shorter", value="shorter"),
    app_commands.Choice(name="linkedin", value="linkedin"),
]

EXPLAIN_LEVEL_CHOICES = [
    app_commands.Choice(name="simple", value="simple"),
    app_commands.Choice(name="normal", value="normal"),
    app_commands.Choice(name="technical", value="technical"),
]


class AICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.openai_service = OpenAIService(bot.settings)

    @app_commands.command(name="ask", description="Ask the bot a question")
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def ask_slash(
        self,
        interaction: discord.Interaction,
        prompt: str,
        visibility: app_commands.Choice[str] | None = None,
    ):
        ephemeral = is_ephemeral(visibility, True)

        async def work():
            return await self.openai_service.ask(prompt)

        await run_interaction_task(
            interaction,
            task_name="Ask",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )

    @app_commands.command(name="rewrite", description="Rewrite text in a selected tone")
    @app_commands.choices(
        tone=REWRITE_TONE_CHOICES,
        visibility=VISIBILITY_CHOICES,
    )
    async def rewrite_slash(
        self,
        interaction: discord.Interaction,
        text: str,
        tone: app_commands.Choice[str],
        visibility: app_commands.Choice[str] | None = None,
    ):
        ephemeral = is_ephemeral(visibility, True)

        async def work():
            tone_value = tone.value

            if tone_value == "linkedin":
                prompt = (
                    "Rewrite the following text in an exaggerated LinkedIn post style. "
                    "Make it sound self-important, inspirational, buzzword-heavy, and slightly absurd, "
                    "like a parody of stereotypical LinkedIn thought-leadership posts. "
                    "Use short paragraphs when helpful, but keep it readable.\n\n"
                    f"Original text:\n{text}"
                )
            elif tone_value == "shorter":
                prompt = (
                    "Rewrite the following text to be shorter, clearer, and tighter while preserving meaning.\n\n"
                    f"Original text:\n{text}"
                )
            else:
                prompt = (
                    f"Rewrite the following text in a {tone_value} tone while preserving the original intent.\n\n"
                    f"Original text:\n{text}"
                )

            return await self.openai_service.ask(prompt)

        await run_interaction_task(
            interaction,
            task_name="Rewrite",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )

    @app_commands.command(name="explain", description="Explain text more clearly")
    @app_commands.choices(
        level=EXPLAIN_LEVEL_CHOICES,
        visibility=VISIBILITY_CHOICES,
    )
    async def explain_slash(
        self,
        interaction: discord.Interaction,
        text: str,
        level: app_commands.Choice[str] | None = None,
        visibility: app_commands.Choice[str] | None = None,
    ):
        ephemeral = is_ephemeral(visibility, True)
        selected_level = level.value if level else "normal"

        async def work():
            if selected_level == "simple":
                prompt = (
                    "Explain the following text in simple language for a non-expert. "
                    "Be clear, concise, and avoid jargon.\n\n"
                    f"Text:\n{text}"
                )
            elif selected_level == "technical":
                prompt = (
                    "Explain the following text in more technical depth for an informed reader. "
                    "Clarify assumptions, implied meaning, and important details.\n\n"
                    f"Text:\n{text}"
                )
            else:
                prompt = (
                    "Explain the following text clearly in plain language. "
                    "Preserve the meaning but make it easier to understand.\n\n"
                    f"Text:\n{text}"
                )

            return await self.openai_service.ask(prompt)

        await run_interaction_task(
            interaction,
            task_name="Explain",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )

    @app_commands.command(name="translate", description="Translate text into another language")
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def translate_slash(
        self,
        interaction: discord.Interaction,
        text: str,
        target_language: str,
        source_language: str | None = None,
        visibility: app_commands.Choice[str] | None = None,
    ):
        ephemeral = is_ephemeral(visibility, True)

        async def work():
            return await translate_text(
                self.bot,
                text=text,
                target_language=target_language,
                source_language=source_language,
            )

        await run_interaction_task(
            interaction,
            task_name="Translate",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )

    @app_commands.command(name="img", description="Generate an image from a prompt")
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def image_slash(
        self,
        interaction: discord.Interaction,
        prompt: str,
        visibility: app_commands.Choice[str] | None = None,
    ):
        ephemeral = is_ephemeral(visibility, False)

        async def work():
            url = await self.openai_service.generate_image(prompt)
            embed = discord.Embed(title="Generated image", description=prompt)
            embed.set_image(url=url)
            return embed

        await run_interaction_task(
            interaction,
            task_name="Image",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )


async def setup(bot):
    await bot.add_cog(AICog(bot))
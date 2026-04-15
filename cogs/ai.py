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
    app_commands.Choice(name="degen", value="degen"),
]

EXPLAIN_LEVEL_CHOICES = [
    app_commands.Choice(name="simple", value="simple"),
    app_commands.Choice(name="normal", value="normal"),
    app_commands.Choice(name="technical", value="technical"),
]


def build_rewrite_prompt(text: str, tone_value: str) -> str:
    if tone_value == "linkedin":
        return (
            "Rewrite the following text as an exaggerated LinkedIn post. "
            "Use corporate buzzwords, inspirational tone, dramatic framing, "
            "short paragraphs, and self-important style. Keep it parody-like but readable. "
            "Format the output cleanly for Discord readability.\n\n"
            f"Original text:\n{text}"
        )

    if tone_value == "degen":
        return (
            "Rewrite the following text in a chaotic, unhinged, meme-heavy degenerate internet style. "
            "Use lots of emojis, exaggerated reactions, slang, absurd phrasing, and dramatic energy. "
            "Keep it non-explicit, ridiculous, and entertaining. "
            "Format the output cleanly for Discord readability with line breaks and chaotic pacing.\n\n"
            f"Original text:\n{text}"
        )

    if tone_value == "shorter":
        return (
            "Rewrite the following text to be shorter, clearer, and more concise while preserving meaning.\n\n"
            f"Original text:\n{text}"
        )

    if tone_value in {"professional", "casual", "friendly", "direct"}:
        return (
            f"Rewrite the following text in a {tone_value} tone while preserving the original meaning.\n\n"
            f"Original text:\n{text}"
        )

    return (
        "Rewrite the following text in the requested tone while preserving the original meaning. "
        f"Requested tone: {tone_value}\n\n"
        f"Original text:\n{text}"
    )


class RewriteMessageModal(discord.ui.Modal, title="Rewrite Message"):
    tone = discord.ui.TextInput(
        label="Tone",
        placeholder="professional, casual, friendly, direct, shorter, linkedin, degen",
        required=True,
        max_length=50,
    )

    def __init__(self, openai_service: OpenAIService, target_message: discord.Message, max_chunks: int) -> None:
        super().__init__()
        self.openai_service = openai_service
        self.target_message = target_message
        self.max_chunks = max_chunks

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async def work() -> str:
            tone_value = self.tone.value.strip().lower()
            text = self.target_message.content or "(no text content)"
            prompt = build_rewrite_prompt(text, tone_value)
            return await self.openai_service.ask(prompt)

        await run_interaction_task(
            interaction,
            task_name="Rewrite Message",
            work=work,
            ephemeral=True,
            max_chunks=self.max_chunks,
        )


class AICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.openai_service = OpenAIService(bot.settings)

        self.rewrite_message_menu = app_commands.ContextMenu(
            name="Rewrite Message",
            callback=self.rewrite_message_context,
        )

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.rewrite_message_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(
            self.rewrite_message_menu.name,
            type=self.rewrite_message_menu.type,
        )

    async def rewrite_message_context(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        modal = RewriteMessageModal(
            openai_service=self.openai_service,
            target_message=message,
            max_chunks=self.bot.settings.max_text_chunks,
        )
        await interaction.response.send_modal(modal)

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
            prompt = build_rewrite_prompt(text, tone.value)
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
    @app_commands.describe(
        text="Text to translate",
        target_language="Target language code, e.g. es, fr, ja, zh-CN",
        source_language="Optional source language code, e.g. en",
        visibility="Whether the result should be public or private",
    )
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
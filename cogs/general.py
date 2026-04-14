import random
import discord
from discord import app_commands
from discord.ext import commands

from help_data import HELP_SECTIONS
from utils.text import build_choice_list
from utils.visibility import VISIBILITY_CHOICES, is_ephemeral


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show the bot command guide")
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def help_slash(
        self,
        interaction: discord.Interaction,
        visibility: app_commands.Choice[str] | None = None,
    ):
        ephemeral = is_ephemeral(visibility, True)

        embed = discord.Embed(title="James Bot Commands")
        for section, items in HELP_SECTIONS.items():
            embed.add_field(name=section, value="\n".join(items), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(name="choose", description="Pick one option from a comma-separated list")
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def choose_slash(
        self,
        interaction: discord.Interaction,
        choices: str,
        visibility: app_commands.Choice[str] | None = None,
    ):
        options = build_choice_list(choices)

        if len(options) < 2:
            await interaction.response.send_message("Provide at least 2 choices.", ephemeral=True)
            return

        ephemeral = is_ephemeral(visibility, False)
        await interaction.response.send_message(
            f"The result is: **{random.choice(options)}**",
            ephemeral=ephemeral,
        )


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
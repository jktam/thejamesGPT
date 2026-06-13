import re
import random
import discord
from discord import app_commands
from discord.ext import commands

from help_data import HELP_SECTIONS
from utils.text import build_choice_list
from utils.visibility import VISIBILITY_CHOICES, is_ephemeral


_DICE_RE = re.compile(r"^(\d+)?d(\d+)([+-]\d+)?$", re.IGNORECASE)
_MAX_DICE = 50
_MAX_SIDES = 1000


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

    @app_commands.command(name="roll", description="Roll dice using notation like 2d6 or 1d20+3")
    async def roll_slash(self, interaction: discord.Interaction, dice: str):
        m = _DICE_RE.match(dice.strip())
        if not m:
            await interaction.response.send_message(
                "Use dice notation like `2d6`, `1d20`, or `3d8+2`.", ephemeral=True
            )
            return

        count = int(m.group(1) or 1)
        sides = int(m.group(2))
        modifier = int(m.group(3) or 0)

        if not (1 <= count <= _MAX_DICE):
            await interaction.response.send_message(f"Dice count must be 1–{_MAX_DICE}.", ephemeral=True)
            return
        if not (2 <= sides <= _MAX_SIDES):
            await interaction.response.send_message(f"Sides must be 2–{_MAX_SIDES}.", ephemeral=True)
            return

        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + modifier

        if count == 1 and not modifier:
            await interaction.response.send_message(f"🎲 **{dice.strip()}** → **{total}**")
            return

        parts = " + ".join(str(r) for r in rolls)
        mod_str = f" {modifier:+d}" if modifier else ""
        await interaction.response.send_message(
            f"🎲 **{dice.strip()}** → [{parts}]{mod_str} = **{total}**"
        )


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))

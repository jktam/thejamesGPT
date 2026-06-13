import discord
from discord import app_commands
from discord.ext import commands

from services.google_places import (
    build_address_embed,
    build_restaurants_embed,
    get_restaurant_address,
    get_restaurants,
)
from utils.presentation import run_interaction_task
from utils.visibility import VISIBILITY_CHOICES, is_ephemeral

_MAX_CITY_LEN = 100
_MAX_CATEGORY_LEN = 50
_MAX_RESTAURANT_LEN = 100


class PlacesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"Slow down! Try again in {error.retry_after:.0f}s.", ephemeral=True
            )
        else:
            raise error

    @app_commands.command(name="eats", description="Find nearby restaurants")
    @app_commands.checks.cooldown(1, 20.0)
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def eats_slash(
        self,
        interaction: discord.Interaction,
        city: str,
        radius: app_commands.Range[float, 1, 50] = 3,
        category: str | None = None,
        visibility: app_commands.Choice[str] | None = None,
    ):
        if len(city) > _MAX_CITY_LEN:
            await interaction.response.send_message("City name is too long.", ephemeral=True)
            return
        if category and len(category) > _MAX_CATEGORY_LEN:
            await interaction.response.send_message("Category is too long.", ephemeral=True)
            return

        ephemeral = is_ephemeral(visibility, False)

        async def work():
            restaurants = await get_restaurants(
                self.bot,
                city=city,
                radius_miles=radius,
                category=category,
            )
            return build_restaurants_embed(city, radius, category, restaurants)

        await run_interaction_task(
            interaction,
            task_name="Restaurant search",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )

    @app_commands.command(name="addy", description="Look up a restaurant address")
    @app_commands.checks.cooldown(1, 10.0)
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def addy_slash(
        self,
        interaction: discord.Interaction,
        restaurant: str,
        city: str,
        visibility: app_commands.Choice[str] | None = None,
    ):
        if len(restaurant) > _MAX_RESTAURANT_LEN:
            await interaction.response.send_message("Restaurant name is too long.", ephemeral=True)
            return
        if len(city) > _MAX_CITY_LEN:
            await interaction.response.send_message("City name is too long.", ephemeral=True)
            return

        ephemeral = is_ephemeral(visibility, False)

        async def work():
            name, address = await get_restaurant_address(self.bot, restaurant, city)
            return build_address_embed(name, address)

        await run_interaction_task(
            interaction,
            task_name="Address lookup",
            work=work,
            ephemeral=ephemeral,
            max_chunks=self.bot.settings.max_text_chunks,
        )


async def setup(bot):
    await bot.add_cog(PlacesCog(bot))

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


class PlacesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="eats", description="Find nearby restaurants")
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def eats_slash(
        self,
        interaction: discord.Interaction,
        city: str,
        radius: app_commands.Range[float, 1, 50] = 3,
        category: str | None = None,
        visibility: app_commands.Choice[str] | None = None,
    ):
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
    @app_commands.choices(visibility=VISIBILITY_CHOICES)
    async def addy_slash(
        self,
        interaction: discord.Interaction,
        restaurant: str,
        city: str,
        visibility: app_commands.Choice[str] | None = None,
    ):
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
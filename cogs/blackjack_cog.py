# cogs/blackjack_cog.py

import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio

from money_pool import get_money_pool_singleton
from blackjack_engine import (
    BlackjackManager,
    BlackjackGame,
    PlayerState,
    MIN_BET,
    calc_value,
    display_hand,
    deal_card,
)

DEFAULT_START_BALANCE = 1000


class BlackjackButtons(discord.ui.View):
    def __init__(self, user_id: int, allow_split: bool = False, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.selection: Optional[str] = None

        if not allow_split:
            for child in self.children:
                if getattr(child, "custom_id", "") == "split":
                    child.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This button isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, custom_id="hit")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selection = "hit"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, custom_id="stand")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selection = "stand"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success, custom_id="double")
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selection = "double"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Split", style=discord.ButtonStyle.danger, custom_id="split")
    async def split(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selection = "split"
        await interaction.response.defer()
        self.stop()


class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.money_pool = get_money_pool_singleton()
        self.manager = BlackjackManager(self.money_pool)

    @app_commands.command(name="blackjack", description="Join or start a blackjack game")
    @app_commands.describe(bet="Your bet amount", multiplayer="Use 'join' to join an existing table")
    async def blackjack(
        self,
        interaction: discord.Interaction,
        bet: int,
        multiplayer: str = "n",
    ):
        if bet < MIN_BET:
            await interaction.response.send_message(
                f"Minimum bet is ${MIN_BET}.", ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        game = await self.manager.create_game_if_missing(guild_id)

        if multiplayer.lower() == "join":
            game.multiplayer = True

        user_id = str(interaction.user.id)
        ok, msg = await game.add_player(user_id, bet)
        if not ok:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"{interaction.user.mention} joined with ${bet}. {msg}"
        )

        if not game.multiplayer:
            asyncio.create_task(self._run_game(game, interaction))

    async def _run_game(self, game: BlackjackGame, interaction: discord.Interaction):
        async with game.lock:
            game.dealer_hand = [deal_card(game.deck), deal_card(game.deck)]

        await interaction.channel.send(
            f"Dealer shows: {display_hand([game.dealer_hand[0]])}"
        )

        await game.initial_natural_resolution(interaction)
        if game.finished:
            await self.manager.remove_game(interaction.guild.id)
            return

        for player in game.players:
            user = interaction.guild.get_member(int(player.user_id))
            hand_index = 0

            while hand_index < len(player.hands):
                player.current_hand_index = hand_index

                if player.naturals_resolved[hand_index]:
                    hand_index += 1
                    continue

                while True:
                    hand = player.current_hand()
                    value = calc_value(hand)
                    allow_split = len(hand) == 2 and hand[0] == hand[1]

                    embed = discord.Embed(
                        title=f"{user.display_name}'s turn",
                        color=0x00FF00,
                    )
                    embed.add_field(
                        name="Your hand",
                        value=f"{display_hand(hand)} (value: {value})",
                        inline=False,
                    )
                    embed.add_field(
                        name="Dealer shows",
                        value=display_hand([game.dealer_hand[0]]),
                        inline=False,
                    )

                    view = BlackjackButtons(
                        user_id=int(player.user_id),
                        allow_split=allow_split,
                    )

                    msg = await interaction.channel.send(embed=embed, view=view)
                    await view.wait()

                    selection = view.selection
                    try:
                        await msg.edit(view=None)
                    except Exception:
                        pass

                    if selection is None:
                        await interaction.channel.send(
                            f"{user.mention} timed out and stands."
                        )
                        break

                    if selection == "hit":
                        val, busted = await game.do_hit(player)
                        await interaction.channel.send(
                            f"{user.mention} hits: {display_hand(player.current_hand())} (value: {val})"
                        )
                        if busted:
                            await interaction.channel.send(f"{user.mention} busted!")
                            break

                    elif selection == "stand":
                        await interaction.channel.send(
                            f"{user.mention} stands with {display_hand(player.current_hand())}"
                        )
                        break

                    elif selection == "double":
                        val, busted, err = await game.do_double(player, player.user_id)
                        if err:
                            await interaction.channel.send(f"{user.mention} {err}")
                            continue
                        await interaction.channel.send(
                            f"{user.mention} doubled: {display_hand(player.current_hand())} (value: {val})"
                        )
                        break

                    elif selection == "split":
                        ok, msg = await game.do_split(player, player.user_id)
                        if not ok:
                            await interaction.channel.send(f"{user.mention} {msg}")
                            continue
                        await interaction.channel.send(
                            f"{user.mention} split into {len(player.hands)} hands."
                        )
                        continue

                hand_index += 1

        await game.resolve_dealer_and_payouts(interaction)
        await self.manager.remove_game(interaction.guild.id)

    @app_commands.command(name="blackjack_start", description="Start multiplayer blackjack")
    async def blackjack_start(self, interaction: discord.Interaction):
        game = self.manager.get_game(interaction.guild.id)
        if not game or not game.players:
            await interaction.response.send_message(
                "No players have joined yet.", ephemeral=True
            )
            return

        await interaction.response.send_message("Starting the game...")
        asyncio.create_task(self._run_game(game, interaction))

    @app_commands.command(name="balance", description="Check your gambalance")
    async def balance(self, interaction: discord.Interaction):
        bal = await self.money_pool.get_balance(str(interaction.user.id))
        await interaction.response.send_message(
            f"{interaction.user.mention}, your balance is ${bal}",
            ephemeral=True,
        )

    @app_commands.command(
        name="resetbalance",
        description="Reset your gambalance to default",
    )
    async def resetbalance(self, interaction: discord.Interaction):
        await self.money_pool.set_balance(
            str(interaction.user.id), DEFAULT_START_BALANCE
        )
        await interaction.response.send_message(
            f"{interaction.user.mention}, your balance has been reset to ${DEFAULT_START_BALANCE}",
            ephemeral=True,
        )

    @commands.command(name="leaderboard")
    async def leaderboard(self, ctx: commands.Context):
        top = await self.money_pool.get_leaderboard(10)
        lines = ["**Leaderboard (Historical Highs):**"]
        for i, (uid, data) in enumerate(top, start=1):
            try:
                user = await self.bot.fetch_user(int(uid))
                name = user.name
            except Exception:
                name = f"User {uid}"
            lines.append(
                f"{i}. {name} - ${data.get('historical_high', DEFAULT_START_BALANCE)}"
            )
        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(BlackjackCog(bot))

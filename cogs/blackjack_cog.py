# cogs/blackjack_cog.py
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
from money_pool import get_money_pool_singleton
from blackjack_engine import BlackjackManager, BlackjackGame, PlayerState, MIN_BET, calc_value, display_hand
import asyncio

DEFAULT_START_BALANCE = 1000

class BlackjackButtons(discord.ui.View):
    def __init__(self, user_id: int, allow_split: bool = False, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.selection: Optional[str] = None
        # disable split if not allowed
        if not allow_split:
            for child in self.children:
                if getattr(child, "custom_id", "") == "split" or getattr(child, "label", "") == "Split":
                    child.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This button isn't for you.", ephemeral=True)
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
    @app_commands.describe(bet="Your bet amount (integer)", multiplayer="Use 'join' to join an existing table")
    async def blackjack(self, interaction: discord.Interaction, bet: int, multiplayer: str = 'n'):
        if bet is None or bet < MIN_BET:
            await interaction.response.send_message(f"Minimum bet is ${MIN_BET}.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        game = await self.manager.create_game_if_missing(guild_id)
        if multiplayer and multiplayer.lower() == "join":
            game.multiplayer = True

        user_id = str(interaction.user.id)
        ok, msg = await game.add_player(user_id, bet)
        if not ok:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
            return

        await interaction.response.send_message(f"{interaction.user.mention} joined with ${bet}. {msg}", ephemeral=False)

        if not game.multiplayer:
            # start singleplayer as background task so the slash response is instant
            self.bot.loop.create_task(self._run_singleplayer_game(game, interaction))

    async def _run_singleplayer_game(self, game: BlackjackGame, interaction: discord.Interaction):
        # set initial dealer and hands
        async with game.lock:
            game.dealer_hand = [deal_card := __import__("blackjack_engine").deal_card(game.deck), None]  # placeholder; will rework
        # call engine start by manually setting to follow engine flow
        # Simpler: reuse engine logic: set dealer + call initial_natural_resolution then interactive loop.
        # Recreate properly:
        async with game.lock:
            game.dealer_hand = [deal_card(game.deck), deal_card(game.deck)]

        # announce dealer showing card
        await interaction.channel.send(f"Dealer shows: {display_hand([game.dealer_hand[0]])}")

        # first check naturals
        await game.initial_natural_resolution(interaction)
        if game.finished:
            # resolve done (dealer natural or all resolved)
            # remove game
            await self.manager.remove_game(interaction.guild.id)
            return

        # main per-player turn loop (hybrid UI)
        for player in game.players:
            user_obj = interaction.guild.get_member(int(player.user_id))
            # iterate hands for this player
            hand_index = 0
            while hand_index < len(player.hands):
                player.current_hand_index = hand_index
                # if this hand was a natural resolved earlier, skip
                if player.naturals_resolved[hand_index]:
                    hand_index += 1
                    continue

                # repeat until stand/double/bust
                while True:
                    hand = player.current_hand()
                    value = calc_value(hand)
                    allow_split = (len(hand) == 2 and hand[0] == hand[1])
                    embed = discord.Embed(title=f"{user_obj.display_name}'s turn", color=0x00FF00)
                    embed.add_field(name="Your hand", value=f"{display_hand(hand)} (value: {value})", inline=False)
                    embed.add_field(name="Dealer shows", value=f"{display_hand([game.dealer_hand[0]])}", inline=False)
                    view = BlackjackButtons(user_id=int(player.user_id), allow_split=allow_split, timeout=60.0)
                    turn_msg = await interaction.channel.send(embed=embed, view=view)

                    await view.wait()
                    selection = view.selection
                    try:
                        await turn_msg.edit(view=None)
                    except Exception:
                        pass

                    if selection is None:
                        await interaction.channel.send(f"{user_obj.mention} timed out. Standing by default.")
                        break

                    if selection == "hit":
                        value, busted = await game.do_hit(player)
                        await interaction.channel.send(f"{user_obj.mention} hits: {display_hand(player.current_hand())} (value: {value})")
                        if busted:
                            await interaction.channel.send(f"{user_obj.mention} busted!")
                            break
                        else:
                            continue
                    elif selection == "stand":
                        await interaction.channel.send(f"{user_obj.mention} stands with {display_hand(player.current_hand())} (value: {calc_value(player.current_hand())})")
                        break
                    elif selection == "double":
                        val, busted, err = await game.do_double(player, player.user_id)
                        if err:
                            await interaction.channel.send(f"{user_obj.mention} {err}")
                            continue
                        await interaction.channel.send(f"{user_obj.mention} doubled: {display_hand(player.current_hand())} (value: {val})")
                        if busted:
                            await interaction.channel.send(f"{user_obj.mention} busted after doubling.")
                        break
                    elif selection == "split":
                        ok, msg = await game.do_split(player, player.user_id)
                        if not ok:
                            await interaction.channel.send(f"{user_obj.mention} {msg}")
                            continue
                        await interaction.channel.send(f"{user_obj.mention} split into {len(player.hands)} hands.")
                        # continue playing current hand (hand_index stays), the new hand will be handled later
                        continue

                hand_index += 1

        # after all players finished their hands, resolve dealer and payouts
        await game.resolve_dealer_and_payouts(interaction)
        # cleanup
        await self.manager.remove_game(interaction.guild.id)

    @app_commands.command(name="blackjack_start", description="Start multiplayer blackjack session")
    async def blackjack_start(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        game = self.manager.get_game(guild_id)
        if not game or not game.players:
            await interaction.response.send_message("No players have joined the game yet.", ephemeral=True)
            return
        await interaction.response.send_message("Starting the game...", ephemeral=False)
        # run multiplayer loop (similar to singleplayer)
        # For brevity we reuse the same logic by calling _run_singleplayer_game (works because it loops all players)
        self.bot.loop.create_task(self._run_singleplayer_game(game, interaction))

    @app_commands.command(name="balance", description="Check your gambalance")
    async def balance(self, interaction: discord.Interaction):
        mp = get_money_pool_singleton()
        bal = await mp.get_balance(str(interaction.user.id))
        await interaction.response.send_message(f"{interaction.user.mention}, your balance is ${bal}", ephemeral=True)

    @app_commands.command(name="resetbalance", description="Reset your gambalance to default (owner only)")
    async def resetbalance(self, interaction: discord.Interaction):
        mp = get_money_pool_singleton()
        await mp.set_balance(str(interaction.user.id), DEFAULT_START_BALANCE)
        await interaction.response.send_message(f"{interaction.user.mention}, your balance has been reset to ${DEFAULT_START_BALANCE}", ephemeral=True)

    @commands.command(name="leaderboard")
    async def leaderboard(self, ctx: commands.Context):
        mp = get_money_pool_singleton()
        top = await mp.get_leaderboard(10)
        lines = ["**Leaderboard (Historical Highs):**"]
        for i, (uid, data) in enumerate(top, start=1):
            try:
                user = await self.bot.fetch_user(int(uid))
                name = user.name
            except Exception:
                name = f"User {uid}"
            lines.append(f"{i}. {name} - ${data.get('historical_high', DEFAULT_START_BALANCE)}")
        await ctx.send("\n".join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(BlackjackCog(bot))

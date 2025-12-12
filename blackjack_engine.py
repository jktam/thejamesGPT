# blackjack_engine.py
import random
import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

BASE_DECK = [2,3,4,5,6,7,8,9,10,10,10,10,11] * 4
MIN_BET = 20

def deal_card(deck: List[int]) -> int:
    card = random.choice(deck)
    deck.remove(card)
    return card

def calc_value(hand: List[int]) -> int:
    total = sum(hand)
    aces = hand.count(11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

def display_hand(hand: List[int]) -> str:
    return " ".join(f"{c}*" if c == 11 else str(c) for c in hand)

def is_natural(hand: List[int]) -> bool:
    if len(hand) != 2:
        return False
    values = sorted(hand)
    # Ace (11) + 10-value card
    return (11 in hand) and (10 in hand or any(v==10 for v in hand if v==10))

@dataclass
class PlayerState:
    user_id: str
    hands: List[List[int]] = field(default_factory=list)
    bets: List[int] = field(default_factory=list)
    current_hand_index: int = 0
    doubled_flags: List[bool] = field(default_factory=list)
    naturals_resolved: List[bool] = field(default_factory=list)  # track naturals per hand

    def current_hand(self) -> List[int]:
        return self.hands[self.current_hand_index]

    def current_bet(self) -> int:
        return self.bets[self.current_hand_index]

class BlackjackGame:
    def __init__(self, guild_id: int, money_pool):
        self.guild_id = guild_id
        self.money_pool = money_pool  # MoneyPool instance
        self.deck: List[int] = BASE_DECK.copy()
        random.shuffle(self.deck)
        self.dealer_hand: List[int] = []
        self.players: List[PlayerState] = []
        self.current_player_index = 0
        self.multiplayer = False
        self.lock = asyncio.Lock()
        self.finished = False

    def find_player(self, user_id: str) -> Optional[PlayerState]:
        for p in self.players:
            if p.user_id == user_id:
                return p
        return None

    async def add_player(self, user_id: str, bet: int) -> Tuple[bool, str]:
        if bet < MIN_BET:
            return False, f"Minimum bet is ${MIN_BET}."

        await self.money_pool.ensure_user(user_id)
        bal = await self.money_pool.get_balance(user_id)
        if bal < bet:
            return False, "Insufficient balance."

        ok = await self.money_pool.subtract(user_id, bet)
        if not ok:
            return False, "Insufficient balance."

        existing = self.find_player(user_id)
        if existing:
            existing.hands.append([deal_card(self.deck), deal_card(self.deck)])
            existing.bets.append(bet)
            existing.doubled_flags.append(False)
            existing.naturals_resolved.append(False)
            return True, "Added another hand to your existing entry."
        else:
            ps = PlayerState(
                user_id=user_id,
                hands=[[deal_card(self.deck), deal_card(self.deck)]],
                bets=[bet],
                current_hand_index=0,
                doubled_flags=[False],
                naturals_resolved=[False]
            )
            self.players.append(ps)
            return True, "Player added."

    async def initial_natural_resolution(self, ctx):
        """
        Check player naturals and dealer natural, resolve immediate payouts (3:2) or pushes.
        Returns list of players who still require normal play.
        """
        # reveal dealer hole card to check for dealer natural
        dealer_has_natural = is_natural(self.dealer_hand)
        # collect messages to send as events
        for p in self.players:
            for idx, hand in enumerate(p.hands):
                if is_natural(hand):
                    if dealer_has_natural:
                        # push: return bet
                        await self.money_pool.add(p.user_id, p.bets[idx])
                        p.naturals_resolved[idx] = True
                        await ctx.send(f"{ctx.guild.get_member(int(p.user_id)).mention} has a natural but dealer also has natural — push. Bet returned (${p.bets[idx]}).")
                    else:
                        # player wins 3:2 (payout = 1.5 * bet plus original is already deducted; easiest is add back 2.5x? careful)
                        # The player originally paid bet; to pay 3:2, we should add back bet + 1.5*bet = 2.5 * bet
                        payout = int(p.bets[idx] * 2.5)  # integer cents rounding; acceptable for integers
                        await self.money_pool.add(p.user_id, payout)
                        p.naturals_resolved[idx] = True
                        await self.money_pool.ensure_user(p.user_id)
                        await ctx.send(f"🎉 {ctx.guild.get_member(int(p.user_id)).mention} has a natural! Payout ${payout}.")
        # if dealer has natural and some players didn't, dealer wins those (no further play)
        if dealer_has_natural:
            await ctx.send("Dealer has a natural blackjack!")
            # players who didn't have naturals lose (bets already taken)
            # mark all non-natural hands as finished by setting naturals_resolved True to skip
            for p in self.players:
                for idx, hand in enumerate(p.hands):
                    if not is_natural(hand):
                        # they lose, no money added (bet already taken)
                        p.naturals_resolved[idx] = True
                        await ctx.send(f"{ctx.guild.get_member(int(p.user_id)).mention} loses to dealer natural.")
            # game essentially over
            self.finished = True

    # Public action methods to be called by the Cog:
    async def do_hit(self, player: PlayerState) -> Tuple[int, bool]:
        hand = player.current_hand()
        hand.append(deal_card(self.deck))
        value = calc_value(hand)
        busted = value > 21
        return value, busted

    async def do_double(self, player: PlayerState, user_id: str) -> Tuple[int, bool, str]:
        idx = player.current_hand_index
        cost = player.bets[idx]
        bal = await self.money_pool.get_balance(user_id)
        if bal < cost:
            return -1, False, "Insufficient funds to double."
        ok = await self.money_pool.subtract(user_id, cost)
        if not ok:
            return -1, False, "Insufficient funds to double."
        player.bets[idx] *= 2
        player.doubled_flags[idx] = True
        hand = player.current_hand()
        hand.append(deal_card(self.deck))
        value = calc_value(hand)
        return value, value > 21, ""

    async def do_split(self, player: PlayerState, user_id: str) -> Tuple[bool, str]:
        hand = player.current_hand()
        if len(hand) != 2 or hand[0] != hand[1]:
            return False, "You can only split a pair of identical values."
        idx = player.current_hand_index
        cost = player.bets[idx]
        bal = await self.money_pool.get_balance(user_id)
        if bal < cost:
            return False, "Insufficient funds to split."
        ok = await self.money_pool.subtract(user_id, cost)
        if not ok:
            return False, "Insufficient funds to split."
        card = hand.pop()
        new_hand = [card, deal_card(self.deck)]
        hand.append(deal_card(self.deck))
        player.hands.append(new_hand)
        player.bets.append(cost)
        player.doubled_flags.append(False)
        player.naturals_resolved.append(False)
        return True, ""

    async def resolve_dealer_and_payouts(self, ctx):
        """Run dealer draws and pay out all non-natural hands."""
        dealer_val = calc_value(self.dealer_hand)
        await ctx.send(f"Dealer hand: {display_hand(self.dealer_hand)} ({dealer_val})")
        while dealer_val < 17:
            self.dealer_hand.append(deal_card(self.deck))
            dealer_val = calc_value(self.dealer_hand)
            await ctx.send(f"Dealer draws: {display_hand(self.dealer_hand)} ({dealer_val})")

        # Evaluate payouts for hands that were not resolved by natural rules
        for player in self.players:
            user = ctx.guild.get_member(int(player.user_id))
            for idx, hand in enumerate(player.hands):
                if player.naturals_resolved[idx]:
                    continue  # already resolved
                val = calc_value(hand)
                bet = player.bets[idx]
                if val > 21:
                    await ctx.send(f"{user.mention} busted with {display_hand(hand)} and lost ${bet}.")
                elif dealer_val > 21 or val > dealer_val:
                    payout = bet * 2
                    await self.money_pool.add(player.user_id, payout)
                    await ctx.send(f"{user.mention} wins {payout} with {display_hand(hand)}.")
                elif val < dealer_val:
                    await ctx.send(f"{user.mention} loses {bet} against dealer.")
                else:
                    await self.money_pool.add(player.user_id, bet)
                    await ctx.send(f"{user.mention} pushes — bet returned (${bet}).")
        self.finished = True

class BlackjackManager:
    def __init__(self, money_pool):
        self.games: Dict[int, BlackjackGame] = {}
        self.lock = asyncio.Lock()
        self.money_pool = money_pool

    def get_game(self, guild_id: int) -> Optional[BlackjackGame]:
        return self.games.get(guild_id)

    async def create_game_if_missing(self, guild_id: int) -> BlackjackGame:
        async with self.lock:
            game = self.games.get(guild_id)
            if game and not game.finished:
                return game
            game = BlackjackGame(guild_id, self.money_pool)
            self.games[guild_id] = game
            return game

    async def remove_game(self, guild_id: int):
        async with self.lock:
            if guild_id in self.games:
                del self.games[guild_id]

import random
import json
import os
import discord
import asyncio

# Load or initialize the money pool
if os.path.exists('money_pool.json'):
    with open('money_pool.json', 'r') as f:
        money_pool = json.load(f)
else:
    money_pool = {}

deck_template = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4

# Used for multiplayer games
games = {}

def deal_card(deck):
    card = random.choice(deck)
    deck.remove(card)
    return card

def calculate_hand(hand):
    value = sum(hand)
    if value > 21 and 11 in hand:
        hand[hand.index(11)] = 1
        value = sum(hand)
    return value

def save_money_pool():
    with open('money_pool.json', 'w') as f:
        json.dump(money_pool, f)

### MULTIPLAYER TABLES (!21join and !21start) ###

async def play_turn(ctx, bot):
    game = games[ctx.guild.id]
    player = game['players'][game['current_player']]
    player_obj = ctx.guild.get_member(int(player['id']))
    player_value = calculate_hand(player['hand'])

    await ctx.send(f"{player_obj.mention}, it's your turn. Your hand: {player['hand']} (value: {player_value}). Do you want to hit, stand, or double down? (h/s/d)")

    def check(m):
        return m.author == player_obj and m.channel == ctx.channel and m.content.lower() in ['h', 's', 'd']

    try:
        response = await bot.wait_for('message', check=check, timeout=30.0)
    except asyncio.TimeoutError:
        await ctx.send(f'{player_obj.mention}, your turn timed out. Standing by default.')
        response = discord.Object(id=None)
        response.content = 's'

    if response.content.lower() == 'h':
        player['hand'].append(deal_card(game['deck']))
        player_value = calculate_hand(player['hand'])
        await ctx.send(f"Your hand: {player['hand']} (value: {player_value})")
        if player_value < 21:
            await play_turn(ctx)
        else:
            await end_turn(ctx)
    elif response.content.lower() == 's':
        await end_turn(ctx)
    elif response.content.lower() == 'd':
        if player['bet'] * 2 > money_pool[player['id']]:
            await ctx.send(f'{player_obj.mention}, you are too broke to double down.')
            await play_turn(ctx)
        else:
            money_pool[player['id']] -= player['bet']
            player['bet'] *= 2
            player['hand'].append(deal_card(game['deck']))
            player_value = calculate_hand(player['hand'])
            await ctx.send(f"Your hand after doubling down: {player['hand']} (value: {player_value})")
            player['doubled_down'] = True
            await end_turn(ctx)

async def end_turn(ctx):
    game = games[ctx.guild.id]
    player = game['players'][game['current_player']]
    player_value = calculate_hand(player['hand'])
    player_obj = ctx.guild.get_member(int(player['id']))

    if player_value > 21:
        await ctx.send(f'{player_obj.mention}, you busted! You lost ${player["bet"]}.')
    else:
        await ctx.send(f'{player_obj.mention} ends their turn with {player["hand"]} (value: {player_value}).')

    game['current_player'] += 1
    if game['current_player'] < len(game['players']):
        await play_turn(ctx)
    else:
        await finish_game(ctx)

async def finish_game(ctx):
    game = games[ctx.guild.id]
    dealer_value = calculate_hand(game['dealer_hand'])

    await ctx.send(f"Dealer's hand: {game['dealer_hand']} (value: {dealer_value})")

    while dealer_value < 17:
        game['dealer_hand'].append(deal_card(game['deck']))
        dealer_value = calculate_hand(game['dealer_hand'])
        await ctx.send(f"Dealer's hand: {game['dealer_hand']} (value: {dealer_value})")

    for player in game['players']:
        player_value = calculate_hand(player['hand'])
        player_obj = ctx.guild.get_member(int(player['id']))

        if dealer_value > 21 or player_value > dealer_value:
            winnings = player['bet'] * 2
            money_pool[player['id']] += winnings
            await ctx.send(f'{player_obj.mention}, you win! You won ${winnings}.')
        elif player_value < dealer_value:
            await ctx.send(f'{player_obj.mention}, dealer wins! You lost ${player["bet"]}.')
        else:
            money_pool[player['id']] += player['bet']  # Return bet on tie
            await ctx.send(f'{player_obj.mention}, it\'s a tie! Your bet of ${player["bet"]} has been returned.')

    save_money_pool()
    del games[ctx.guild.id]
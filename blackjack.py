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

def display_hand(hand):
    return [f'{card}*' if card == 11 else str(card) for card in hand]

### MULTIPLAYER TABLES (!21join and !21start) ###

async def play_turn(ctx, bot):
    game = games[ctx.guild.id]
    player = game['players'][game['current_player']]
    player_obj = ctx.guild.get_member(int(player['id']))
    current_hand = player['hands'][player['current_hand_index']]
    player_value = calculate_hand(current_hand)

    await ctx.send(f"{player_obj.mention}, it's your turn. Your hand: {display_hand(current_hand)} (value: {player_value}). Do you want to hit, stand, double down, or split? (h/s/d/sp)")

    def check(m):
        return m.author == player_obj and m.channel == ctx.channel and m.content.lower() in ['h', 's', 'd', 'sp']

    try:
        response = await bot.wait_for('message', check=check, timeout=30.0)
    except asyncio.TimeoutError:
        await ctx.send(f'{player_obj.mention}, your turn timed out. Standing by default.')
        await end_turn(ctx, bot)  # End turn if timeout occurs
        return

    if response.content.lower() == 'h':
        current_hand.append(deal_card(game['deck']))
        player_value = calculate_hand(current_hand)
        await ctx.send(f"Your hand: {display_hand(current_hand)} (value: {player_value})")
        if player_value < 21:
            await play_turn(ctx, bot)
        else:
            await end_turn(ctx, bot)
    elif response.content.lower() == 's':
        await end_turn(ctx, bot)
    elif response.content.lower() == 'd':
        if player['bets'][player['current_hand_index']] * 2 > money_pool[player['id']]['current']:
            await ctx.send(f'{player_obj.mention}, you are too broke to double down.')
            await play_turn(ctx, bot)
        else:
            money_pool[player['id']]['current'] -= player['bets'][player['current_hand_index']]
            player['bets'][player['current_hand_index']] *= 2
            current_hand.append(deal_card(game['deck']))
            player_value = calculate_hand(current_hand)
            await ctx.send(f"Your hand after doubling down: {display_hand(current_hand)} (value: {player_value})")
            player['doubled_down'] = True
            await end_turn(ctx, bot)
    elif response.content.lower() == 'sp':
        if len(current_hand) == 2 and current_hand[0] == current_hand[1]:
            if player['bets'][player['current_hand_index']] > money_pool[player['id']]['current']:
                await ctx.send(f'{player_obj.mention}, you do not have enough balance to split.')
                await play_turn(ctx, bot)
            else:
                money_pool[player['id']]['current'] -= player['bets'][player['current_hand_index']]
                player['hands'].append([current_hand.pop()])
                player['bets'].append(player['bets'][player['current_hand_index']])
                current_hand.append(deal_card(game['deck']))
                player['hands'][-1].append(deal_card(game['deck']))
                await ctx.send(f'{player_obj.mention}, you have split your hand into two hands: {display_hand(player["hands"][-2])} and {display_hand(player["hands"][-1])}.')
                await play_turn(ctx, bot)
        else:
            await ctx.send(f'{player_obj.mention}, you can only split when you have two cards of the same value.')
            await play_turn(ctx)

async def end_turn(ctx, bot):
    game = games[ctx.guild.id]
    player = game['players'][game['current_player']]
    current_hand = player['hands'][player['current_hand_index']]
    player_value = calculate_hand(current_hand)
    player_obj = ctx.guild.get_member(int(player['id']))

    if player_value > 21:
        await ctx.send(f'{player_obj.mention}, you busted with hand {display_hand(current_hand)}! You lost ${player["bets"][player["current_hand_index"]]}.')
    
    player['current_hand_index'] += 1
    if player['current_hand_index'] < len(player['hands']):
        await play_turn(ctx, bot)
    else:
        game['current_player'] += 1  # Move to the next player
        if game['current_player'] < len(game['players']):
            await play_turn(ctx, bot)
        else:
            await finish_game(ctx)

async def finish_game(ctx):
    game = games[ctx.guild.id]
    dealer_value = calculate_hand(game['dealer_hand'])

    await ctx.send(f"Dealer's hand: {display_hand(game['dealer_hand'])} (value: {dealer_value})")

    while dealer_value < 17:
        game['dealer_hand'].append(deal_card(game['deck']))
        dealer_value = calculate_hand(game['dealer_hand'])
        await ctx.send(f"Dealer's hand: {display_hand(game['dealer_hand'])} (value: {dealer_value})")

    for player in game['players']:
        for hand_index, hand in enumerate(player['hands']):
            player_value = calculate_hand(hand)
            player_obj = ctx.guild.get_member(int(player['id']))

            if player_value > 21:
                await ctx.send(f'{player_obj.mention}, you busted with hand {display_hand(hand)} and lost ${player["bets"][hand_index]}.')
            elif dealer_value > 21 or player_value > dealer_value:
                winnings = player['bets'][hand_index] * 2
                money_pool[player['id']]['current'] += winnings
                if money_pool[player['id']]['current'] > money_pool[player['id']]['historical_high']:
                    money_pool[player['id']]['historical_high'] = money_pool[player['id']]['current']
                await ctx.send(f'{player_obj.mention}, you win with hand {display_hand(hand)}! You won ${winnings}.')
            elif player_value < dealer_value:
                await ctx.send(f'{player_obj.mention}, dealer wins against hand {display_hand(hand)}! You lost ${player["bets"][hand_index]}.')
            else:
                money_pool[player['id']]['current'] += player['bets'][hand_index]  # Return bet on tie
                await ctx.send(f'{player_obj.mention}, it\'s a tie with hand {display_hand(hand)}! Your bet of ${player["bets"][hand_index]} has been returned.')

    save_money_pool()
    del games[ctx.guild.id]
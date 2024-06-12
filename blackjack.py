import random
import json
import os


# Load or initialize the money pool
if os.path.exists('money_pool.json'):
    with open('money_pool.json', 'r') as f:
        money_pool = json.load(f)
else:
    money_pool = {}

deck_template = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4

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
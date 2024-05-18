import random


deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4

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
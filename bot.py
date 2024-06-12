import discord
import os
import openai
from dotenv import load_dotenv
from PIL import Image
import requests
from io import BytesIO
import random
from discord.ext import commands
import asyncio
from blackjack import *

load_dotenv()
CHATGPT_TOKEN = os.getenv('CHATGPT_API_KEY')
DISCORD_TOKEN = os.getenv('DISCORD_BOT_API_KEY')
GEMINI_TOKEN = os.getenv('GOOGLE_AI_API_KEY')
openai.api_key = CHATGPT_TOKEN

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

async def query_chatgpt(prompt):
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=1024,
        n=1,
        stop=None,
        temperature=0.5
    )
    return response.choices[0].text

async def get_file(message):
    # Check if the message has an attached image
    if len(message.attachments) == 0:
        await message.channel.send("Please attach an image to edit.")
        return

    # Get the attached image file
    attached_file = message.attachments[0]

    # Download the attached image file
    response = requests.get(attached_file.url)
    file = BytesIO(response.content)
    return file

def resize_image(file):
    desired_size = 1028

    im = Image.open(file)
    old_size = im.size  # old_size[0] is in (width, height) format

    ratio = float(desired_size)/max(old_size)
    new_size = tuple([int(x*ratio) for x in old_size])
    # use thumbnail() or resize() method to resize the input image

    # thumbnail is a in-place operation

    # im.thumbnail(new_size, Image.ANTIALIAS)

    im = im.resize(new_size, Image.Resampling.LANCZOS)

    # create a new image and paste the resized on it
    new_im = Image.new("RGBA", (desired_size, desired_size))
    new_im.paste(im, ((desired_size-new_size[0])//2,
                        (desired_size-new_size[1])//2))

    byte_stream = BytesIO()
    new_im.save(byte_stream, format='PNG')
    byte_array = byte_stream.getvalue()
    return byte_array

async def query_dalle(prompt):
    response = openai.Image.create(
        prompt=prompt,
        n=1,
        size="1024x1024"
    )
    return response['data'][0]['url']

async def query_dalle_edit(prompt,file):
    byte_array = resize_image(file)
    try:
        response = openai.Image.create_edit(
            image=byte_array,
            # mask=open("mask.png", "rb"),
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        print(response['data'][0]['url'])
    except openai.error.OpenAIError as e:
        print(e.http_status)
        print(e.error)
        return e.error['message']

    return response['data'][0]['url']

async def query_dalle_variation(file):
    byte_array = resize_image(file)
    try:
        response = openai.Image.create_variation(
        image=byte_array,
        n=1,
        size="1024x1024"
        )
        print(response['data'][0]['url'])
    except openai.error.OpenAIError as e:
        print(e.http_status)
        print(e.error)
        return e.error['message']
    
    return response['data'][0]['url']

def query_gemini(prompt):
    try:
        response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_TOKEN}",
            headers={"Content-Type": "application/json"},
            json={
                "contents":[
                    {
                        "parts":[
                            {"text": prompt}
                        ]
                    }
                ],
                "safetySettings": [
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_NONE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_NONE"
                    },
                ],
                "generationConfig": {
                    "stopSequences": [
                        "Title"
                    ],
                    "temperature": 0.9,
                    "maxOutputTokens": 800#,
                    # "topP": 1,
                    # "topK": 1
                }
            }
        )
        response.raise_for_status()  # Raise exception for non-2xx status codes
        return response
    except requests.exceptions.RequestException as e:
        if isinstance(e, requests.exceptions.ConnectionError):
            error_message = f"Failed to connect to the API. Please check your internet connection\nStatus code: {response.status_code}"
        elif isinstance(e, requests.exceptions.Timeout):
            error_message = f"Request timed out. Please try again later.\nStatus code: {response.status_code}"
        else:
            error_message = f"An unexpected error occurred: {e}\nsafetyRatings: {response.json()['candidates'][0]['safetyRatings']}"
        return error_message

def miles_to_meters(miles):
    return miles * 1609.34

def geocode_city(city):
    geocode_url = f'https://maps.googleapis.com/maps/api/geocode/json?address={city}&key={GEMINI_TOKEN}'
    response = requests.get(geocode_url).json()
    if response['status'] == 'OK':
        location = response['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    else:
        return None, None

def get_restaurants(city, radius_miles=35, category=None):
    # Convert miles to meters
    radius_meters = miles_to_meters(radius_miles)
    
    # Geocode the city to get latitude and longitude
    lat, lng = geocode_city(city)
    if lat is None or lng is None:
        return None, "Geocoding error: City not found or API error."
    
    # Places API to find restaurants in the given radius and category
    places_url = (f'https://maps.googleapis.com/maps/api/place/nearbysearch/json'
                  f'?location={lat},{lng}&radius={radius_meters}&type=restaurant'
                  f'&key={GEMINI_TOKEN}')
    
    if category:
        places_url += f'&keyword={category}'

    places_url += '&rankby=prominence&limit=10'

    places_response = requests.get(places_url).json()
    
    if places_response['status'] != 'OK':
        return None, f"Error: {places_response['status']}"
    
    restaurants = places_response['results'][:10]
    # restaurant_list = [f"{restaurant['name']} - {restaurant['vicinity']}" for restaurant in restaurants]
    restaurant_list = [f"**{i + 1}. {restaurant['name']}**{restaurant['vicinity']}" for i, restaurant in enumerate(restaurants)]

    return restaurant_list, None


def get_restaurant_address(restaurant_name, city):
    # Geocode the city to get latitude and longitude
    lat, lng = geocode_city(city)
    if lat is None or lng is None:
        return None, "Geocoding error: City not found or API error."
    
    # Places API to search for the restaurant by name near the city location
    places_url = (f'https://maps.googleapis.com/maps/api/place/textsearch/json'
                  f'?query={restaurant_name} restaurant in {city}&location={lat},{lng}&key={GEMINI_TOKEN}')
    
    places_response = requests.get(places_url).json()
    
    if places_response['status'] != 'OK':
        return None, f"Error: {places_response['status']}"
    
    if not places_response['results']:
        return None, "Restaurant not found."
    
    restaurant = places_response['results'][0]
    name = restaurant['name']
    address = restaurant['formatted_address']
    
    return name, address, None

async def format_embed(response):
    embed = discord.Embed(title="The James Roll says...", description=response, color=0x00ff00)
    return embed

############### BOT COMMANDS ###############

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    activity = discord.Activity(type=discord.ActivityType.watching, name="you poop")
    await bot.change_presence(status=discord.Status.dnd, activity=activity)

@bot.command(name="jhelp")
async def on_message(message):
    with open("readme.md", "r") as f:
        text = f.read()
    await message.channel.send(text)

@bot.command(name="jhoose")
async def on_message(ctx, *, choices: str):
    # Split the input string into a list of choices
    choices_list = [choice.strip() for choice in choices.split(',')]
    
    if len(choices_list) < 2:
        await ctx.send("Please provide at least two choices separated by commas.")
        return

    result = random.choice(choices_list)
    await ctx.send(content=f"The result is: {result}")

@bot.command(name="jpt")
async def on_message(ctx, *, message: str):
    waiting_message = await ctx.send("...")
    try:
        prompt = message
        response = await query_chatgpt(prompt)
        embed = await format_embed(response)
        await ctx.send(embed=embed)
    finally:
        await waiting_message.delete()

@bot.command(name="jimg")
async def on_message(ctx, *, message: str):
    waiting_message = await ctx.send("...")
    try:
        prompt = message
        image_url = await query_dalle(prompt)
        await ctx.send(image_url)
    finally:
        await waiting_message.delete()

@bot.command(name="jedit")
async def on_message(ctx, *, message: str):
    waiting_message = await ctx.send("...")
    try:
        prompt = message
        file = await get_file(message)
        edited_image = await query_dalle_edit(prompt,file)
        await ctx.send(edited_image)
    finally:
        await waiting_message.delete()

@bot.command(name="jvari")
async def on_message(ctx, *, message: str):
    waiting_message = await ctx.send("...")
    try:
        file = await get_file(message)
        edited_image = await query_dalle_variation(file)
        await ctx.send(edited_image)
    finally:
        await waiting_message.delete()

@bot.command(name="jem")
async def on_message(ctx, *, message: str):
    waiting_message = await ctx.send("...")
    try:
        prompt = message
        # print(prompt)
        response = query_gemini(prompt)
        # print(response.json())
        text_response = response.json()['candidates'][0]['content']['parts'][0].get('text')
        embed = await format_embed(text_response)
        await ctx.send(embed=embed)
    finally:
        await waiting_message.delete()
    ### DEBUG INFO ###
    ## token_count = response.json()['candidates'][0]['tokenCount'] #doesn't exist?
    # finish_reason = response.json()['candidates'][0]['finishReason']
    # safety_ratings = response.json()['candidates'][0]['safetyRatings']
    # await message.channel.send(f"**Debug info**\nFinish Reason:```{finish_reason}```Safety Ratings:```{safety_ratings}```")

#@bot.command(name='21')
#async def blackjack(ctx):
    player_hand = [deal_card(deck), deal_card(deck)]
    dealer_hand = [deal_card(deck), deal_card(deck)]
    
    player_value = calculate_hand(player_hand)
    dealer_value = calculate_hand(dealer_hand)
    
    await ctx.send(f"Your hand: {player_hand} (value: {player_value})")
    await ctx.send(f"Dealer's showing card: {dealer_hand[0]}")
    
    while player_value < 21:
        await ctx.send('Do you want to hit or stand? (h/s)')
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['h', 's']
        
        try:
            response = await bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send('Game timed out.')
            return
        
        if response.content.lower() == 'h':
            player_hand.append(deal_card(deck))
            player_value = calculate_hand(player_hand)
            await ctx.send(f"Your hand: {player_hand} (value: {player_value})")
        elif response.content.lower() == 's':
            break
    
    if player_value > 21:
        await ctx.send('You busted! Dealer wins.')
        return

    await ctx.send(f"Dealer's hand: {dealer_hand} (value: {dealer_value})")
    
    while dealer_value < 17:
        dealer_hand.append(deal_card(deck))
        dealer_value = calculate_hand(dealer_hand)
        await ctx.send(f"Dealer's hand: {dealer_hand} (value: {dealer_value})")
    
    if dealer_value > 21 or player_value > dealer_value:
        await ctx.send('You win!')
    elif player_value < dealer_value:
        await ctx.send('Dealer wins!')
    else:
        await ctx.send('It\'s a tie!')
@bot.command(name='21test')
async def blackjack(ctx, bet: int = None):
    user_id = str(ctx.author.id)
    if user_id not in money_pool:
        money_pool[user_id] = 1000  # Initial balance
    balance = money_pool[user_id]

    if bet is None:
        await ctx.send(f'{ctx.author.mention}, you need to place a bet to play. Usage: `!21 <bet>`')
        return
    
    if bet > balance:
        await ctx.send(f'{ctx.author.mention}, you are too broke to place that bet.')
        return

    money_pool[user_id] -= bet

    deck = deck_template.copy()
    player_hand = [deal_card(deck), deal_card(deck)]
    dealer_hand = [deal_card(deck), deal_card(deck)]

    player_value = calculate_hand(player_hand)
    dealer_value = calculate_hand(dealer_hand)

    await ctx.send(f"Your hand: {player_hand} (value: {player_value})")
    await ctx.send(f"Dealer's showing card: {dealer_hand[0]}")

    while player_value < 21:
        await ctx.send('Do you want to hit or stand? (h/s)')

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['h', 's']

        try:
            response = await bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send('Game timed out.')
            money_pool[user_id] += bet  # Refund bet
            save_money_pool()
            return

        if response.content.lower() == 'h':
            player_hand.append(deal_card(deck))
            player_value = calculate_hand(player_hand)
            await ctx.send(f"Your hand: {player_hand} (value: {player_value})")
        elif response.content.lower() == 's':
            break

    if player_value > 21:
        await ctx.send(f'{ctx.author.mention}, you busted! Dealer wins. You lost ${bet}.')
        save_money_pool()
        return

    await ctx.send(f"Dealer's hand: {dealer_hand} (value: {dealer_value})")

    while dealer_value < 17:
        dealer_hand.append(deal_card(deck))
        dealer_value = calculate_hand(dealer_hand)
        await ctx.send(f"Dealer's hand: {dealer_hand} (value: {dealer_value})")

    if dealer_value > 21 or player_value > dealer_value:
        winnings = 2 * bet
        money_pool[user_id] += winnings
        await ctx.send(f'{ctx.author.mention}, you win! You won ${winnings}.')
    elif player_value < dealer_value:
        await ctx.send(f'{ctx.author.mention}, dealer wins! You lost ${bet}.')
    else:
        money_pool[user_id] += bet  # Return bet on tie
        await ctx.send(f'{ctx.author.mention}, it\'s a tie! Your bet of ${bet} has been returned.')

    save_money_pool()

@bot.command(name='bal')
async def balance(ctx):
    user_id = str(ctx.author.id)
    balance = money_pool.get(user_id, 1000)  # Default balance is 1000
    await ctx.send(f'{ctx.author.mention}, your balance is ${balance}')

@bot.command(name='resetbal')
async def reset_balance(ctx):
    user_id = str(ctx.author.id)
    money_pool[user_id] = 1000  # Reset balance to default
    save_money_pool()
    await ctx.send(f'{ctx.author.mention}, your balance has been reset to $1000.')


@bot.command(name='eats')
async def fetch_restaurants(ctx, city: str, radius: float = 3, *, category: str = None):
    restaurants, error = get_restaurants(city, radius, category)
    if error:
        await ctx.send(error)
    else:
        response = "\n\n".join(restaurants)
        # embed = await format_embed(response)
        await ctx.send(response if response else "No restaurants found.")

@bot.command(name='addy')
async def fetch_address(ctx, restaurant_name: str, city: str):
    name, address, error = get_restaurant_address(restaurant_name, city)
    if error:
        await ctx.send(error)
    else:
        await ctx.send(f"Address of {name}: {address}")

@bot.command(name="jtest")
async def on_message(message):
    await message.channel.send("Test command")


bot.run(DISCORD_TOKEN)

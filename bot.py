import discord
import os
import openai
from dotenv import load_dotenv
from PIL import Image
import requests
from io import BytesIO
import random
from discord import app_commands
from discord.ext import commands
import asyncio
from blackjack import *
from bs4 import BeautifulSoup

load_dotenv()
CHATGPT_TOKEN = os.getenv('CHATGPT_API_KEY')
DISCORD_TOKEN = os.getenv('DISCORD_BOT_API_KEY')
GOOGLE_GEO_PLACES_API_KEY=os.getenv('GOOGLE_GEO_PLACES_API_KEY')
GUILD_ID = os.getenv('GUILD_ID')
openai.api_key = CHATGPT_TOKEN

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

async def query_chatgpt(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1024
        )
        return response.choices[0].message["content"]
    except openai.error.OpenAIError as e:
        return f"⚠️ API Error: {e}"

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

async def query_dalle(prompt: str) -> str:
    try:
        response = openai.images.generate(
            model="gpt-image-1-mini",
            prompt=prompt,
            size="1024x1024",
            n=1
        )
        return response.data[0].url

    except openai.error.OpenAIError as e:
        return f"⚠️ API Error: {e}"

async def query_dalle_edit(prompt: str, file: BytesIO) -> str:
    try:
        byte_array = resize_image(file)
        response = openai.images.edit(
            model="gpt-image-1-mini",
            image=byte_array,
            prompt=prompt,
            n=1,
            size="1024x1024"
        )
        return response.data[0].url
    except openai.error.OpenAIError as e:
        return f"⚠️ API Error: {e}"

async def query_dalle_variation(file: BytesIO) -> str:
    try:
        byte_array = resize_image(file)
        response = openai.images.variations(
            model="gpt-image-1-mini",
            image=byte_array,
            n=1,
            size="1024x1024"
        )
        return response.data[0].url
    except openai.error.OpenAIError as e:
        return f"⚠️ API Error: {e}"

def miles_to_meters(miles):
    return miles * 1609.34

def geocode_city(city):
    geocode_url = f'https://maps.googleapis.com/maps/api/geocode/json?address={city}&key={GOOGLE_GEO_PLACES_API_KEY}'
    response = requests.get(geocode_url).json()
    if response['status'] == 'OK':
        location = response['results'][0]['geometry']['location']
        return location['lat'], location['lng']
    else:
        return None, None, f"Geocoding error: {response['status']}"

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
                  f'&key={GOOGLE_GEO_PLACES_API_KEY}')
    
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
                  f'?query={restaurant_name} restaurant in {city}&location={lat},{lng}&key={GOOGLE_GEO_PLACES_API_KEY}')
    
    places_response = requests.get(places_url).json()
    
    if places_response['status'] != 'OK':
        return None, f"Error: {places_response['status']}"
    
    if not places_response['results']:
        return None, "Restaurant not found."
    
    restaurant = places_response['results'][0]
    name = restaurant['name']
    address = restaurant['formatted_address']
    
    return name, address, None

async def get_rednote_info(url):
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        soup = BeautifulSoup(response.content, 'html.parser')
        # Extract relevant information (adjust selectors as needed)
        title_div = soup.find('div', id='detail-title', class_='title')
        if title_div:
            title = title_div.text.strip() 
        else:
            title = "Could not find title" 
        thumbnail_tag = soup.find('meta', property='og:image')
        if thumbnail_tag:
            thumbnail_url = thumbnail_tag['content']
        else:
            thumbnail_url = None
        return title, thumbnail_url
    except Exception as e:
        print(f"Error fetching RedNote info: {e}")
        return None, None

async def format_embed(response):
    embed = discord.Embed(title="The James Roll says...", description=response, color=0x00ff00)
    return embed

############### BOT COMMANDS ###############

async def setup():
    await bot.wait_until_ready()
    bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    activity = discord.Activity(type=discord.ActivityType.watching, name="you poop")
    await bot.change_presence(status=discord.Status.dnd, activity=activity)
    await setup()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if '://rednote.com/' in message.content or '://xhslink.com/a/' in message.content:
        url = message.content.split(' ')[0]  # Get the first URL in the message
        title, thumbnail_url = await get_rednote_info(url)
        if title and thumbnail_url:
            embed = discord.Embed(
                title=title,
                url=url,
                color=0x00ff00
            )
            embed.set_image(url=thumbnail_url)
            await message.channel.send(embed=embed)
    await bot.process_commands(message)

@bot.tree.command(name="jhelp", description="Prints The James Roll README.md")
async def help(Interaction: discord.interactions):
    with open("readme.md", "r") as f:
        text = f.read()
    await Interaction.response.send_message(text, ephemeral=True)

@bot.command(name="jhoose")
async def jhoose(ctx, *, choices: str):
    # Split the input string into a list of choices
    choices_list = [choice.strip() for choice in choices.split(',')]
    
    if len(choices_list) < 2:
        await ctx.send("Please provide at least two choices separated by commas.")
        return

    result = random.choice(choices_list)
    await ctx.send(content=f"The result is: {result}")

@bot.command(name="jpt")
async def jpt(ctx, *, prompt: str):
    async with ctx.typing():
        try:
            response = await query_chatgpt(prompt)
            embed = discord.Embed(
                title="🤖 The James rolls...",
                description=response,
                color=0x00FF00
            )
            await ctx.send(embed=embed)
        except requests.exceptions.RequestException as e:
            await ctx.send(f"⚠️ API request failed: {e}")
        except Exception as e:
            await ctx.send(f"⚠️ Unexpected error: {e}")

@bot.command(name="jimg")
async def jimg(ctx, *, prompt: str):
    waiting_message = await ctx.send("🖌 The James Roll is generating your image...")
    try:
        url = await query_dalle(prompt)
        embed = discord.Embed(title="🖼 DALL·E Image", description=prompt, color=0x00FF00)
        embed.set_image(url=url)
        await ctx.send(embed=embed)
    finally:
        await waiting_message.delete()

@bot.command(name="jedit")
async def jedit(ctx, *, prompt: str):
    waiting_message = await ctx.send("✏️ The James Roll is editing your image...")
    try:
        file = await get_file(ctx.message)
        if file is None:
            return
        url = await query_dalle_edit(prompt, file)
        embed = discord.Embed(title="✏️ Edited Image", description=prompt, color=0x00FF00)
        embed.set_image(url=url)
        await ctx.send(embed=embed)
    finally:
        await waiting_message.delete()

@bot.command(name="jvari")
async def jvari(ctx):
    waiting_message = await ctx.send("🔄 The James is rolling your image...")
    try:
        file = await get_file(ctx.message)
        if file is None:
            return
        url = await query_dalle_variation(file)
        embed = discord.Embed(title="🔄 Image Variation", color=0x00FF00)
        embed.set_image(url=url)
        await ctx.send(embed=embed)
    finally:
        await waiting_message.delete()

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

@bot.tree.command(name='blackjack', description='let\'s lose some money')
@app_commands.describe(bet='Your bet amount', multiplayer='Join a table ... y/n(default)')
async def blackjack(interaction: discord.Interaction, bet: int, multiplayer: str = 'n'):
    if interaction.guild.id not in games:
        games[interaction.guild.id] = {
            'players': [],
            'deck': deck_template.copy(),
            'dealer_hand': [],
            'current_player': 0,
            'multiplayer': False
        }

    game = games[interaction.guild.id]
    user_id = str(interaction.user.id)

    if user_id not in money_pool:
        money_pool[user_id] = {'current': 1000, 'historical_high': 1000}  # Initial balance and historical high

    if bet is None or not isinstance(bet,int):
        await interaction.response.send_message(f'{interaction.user.mention}, you need to place a valid bet to play. The minimum to play is $20. Usage: `!21 <bet>`', ephemeral=True)
        return
    
    if bet < 20:
        await interaction.response.send_message(f'{interaction.user.mention}, your bet is too broke. The minimum to play is $20.', ephemeral=True)
        return
    
    if bet > money_pool[user_id]['current']:
        await interaction.response.send_message(f'{interaction.user.mention}, you are too broke to place that bet.', ephemeral=True)
        return
    
    money_pool[user_id]['current'] -= bet

    if multiplayer is not None and multiplayer.lower() == 'join':
        game['multiplayer'] = True

    if game['multiplayer']:
        if any(player['id'] == user_id for player in game['players']):
            await interaction.response.send_message(f'{interaction.user.mention}, you have already joined the game.', ephemeral=True)
            return
        
        game['players'].append({
            'id': user_id,
            'hands': [[deal_card(game['deck']), deal_card(game['deck'])]],
            'bets': [bet],
            'current_hand_index': 0
        })

        await interaction.response.send_message(f'{interaction.user.mention} has joined the game with a bet of ${bet}.')
    else:
        game['players'] = [{
            'id': user_id,
            'hands': [[deal_card(game['deck']), deal_card(game['deck'])]],
            'bets': [bet],
            'current_hand_index': 0
        }]
        await start_blackjack(interaction)

@bot.tree.command(name='blackjack_start', description='Start blackjack session')
async def start_blackjack(interaction: discord.Interaction):
    if interaction.guild.id not in games or len(games[interaction.guild.id]['players']) == 0:
        await interaction.response.send_message('No players have joined the game yet.', ephemeral=True)
        return

    game = games[interaction.guild.id]
    game['dealer_hand'] = [deal_card(game['deck']), deal_card(game['deck'])]
    game['current_player'] = 0

    await interaction.response.send_message(f"Dealer's showing card: {display_hand([game['dealer_hand'][0]])[0]}")
    await play_turn(interaction, bot)

@bot.tree.command(name='balance', description='Check your gambalance')
async def balance(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    balance = money_pool.get(user_id, {}).get('current', 1000)  # Default balance is 1000
    await interaction.response.send_message(f'{interaction.user.mention}, your balance is ${balance}', ephemeral=True)

@bot.tree.command(name='resetbalance', description='Reset your gambalance to $1000')
async def reset_balance(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if user_id not in money_pool:
        money_pool[user_id] = {'current': 1000, 'historical_high': 1000}
    else:
        money_pool[user_id]['current'] = 1000
    save_money_pool()
    await interaction.response.send_message(f'{interaction.user.mention}, your balance has been reset to $1000.', ephemeral=True)

@bot.command(name='leaderboard')
async def leaderboard(ctx):
    sorted_balances = sorted(money_pool.items(), key=lambda item: item[1]['historical_high'], reverse=True)
    leaderboard_message = '**Leaderboard (Historical Highs):**\n'
    for i, (user_id, balances) in enumerate(sorted_balances[:10], start=1):  # Show top 10 players
        user = await bot.fetch_user(int(user_id))
        leaderboard_message += f'{i}. {user.name} - ${balances["historical_high"]}\n'
    await ctx.send(leaderboard_message)

bot.run(DISCORD_TOKEN)

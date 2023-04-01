import discord
import os
import openai
from dotenv import load_dotenv

load_dotenv()
CHATGPT_TOKEN = os.getenv('CHATGPT_API_KEY')
DISCORD_TOKEN = os.getenv('DISCORD_BOT_API_KEY')
openai.api_key = CHATGPT_TOKEN

intents = discord.Intents.all()
client = discord.Client(intents=intents)

async def query_chatgpt(prompt):
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=1024,
        n=1,
        stop=None,
        temperature=0.5
    )
    print(response)
    return response.choices[0].text

async def format_embed(response):
    embed = discord.Embed(title="The James Roll says...", description=response, color=0x00ff00)
    return embed

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!jpt'):
        prompt = message.content.replace('!jpt', '').strip()
        response = await query_chatgpt(prompt)
        embed = await format_embed(response)
        await message.channel.send(embed=embed)

client.run(DISCORD_TOKEN)

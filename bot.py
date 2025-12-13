# --- imports unchanged ---
import os
import logging
import asyncio
import time
from io import BytesIO
from typing import Optional, Tuple, List, Dict

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image
from bs4 import BeautifulSoup

from openai import OpenAI, OpenAIError
import random

# --- env ---
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_API_KEY")
OPENAI_API_KEY = os.getenv("CHATGPT_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_GEO_PLACES_API_KEY")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
ALLOWED_BLACKJACK_CHANNEL_ID = int(os.getenv("ALLOWED_BLACKJACK_CHANNEL_ID")) if os.getenv("ALLOWED_BLACKJACK_CHANNEL_ID") else None
ALLOWED_BLACKJACK_THREAD_ID = int(os.getenv("ALLOWED_BLACKJACK_THREAD_ID")) if os.getenv("ALLOWED_BLACKJACK_THREAD_ID") else None

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing CHATGPT_API_KEY")
if not GOOGLE_API_KEY:
    logging.warning("No GOOGLE_GEO_PLACES_API_KEY set — Google features will fail")

# --- logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("thejamesroll-bot")

# --- money pool ---
from money_pool import get_money_pool_singleton
mp = get_money_pool_singleton()

# --- bot ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

client = OpenAI(api_key=OPENAI_API_KEY)

aio_session: aiohttp.ClientSession | None = None

_GEOCODE_CACHE: Dict[str, Tuple[float, float, float]] = {}
GEOCODE_TTL = 60 * 60 * 24

# ===================== OPENAI FIX =====================
async def query_chatgpt_async(prompt: str, system_prompt: str | None = None, model: str = "gpt-4o-mini") -> str:
    if system_prompt is None:
        system_prompt = "You are a helpful assistant."

    loop = asyncio.get_running_loop()

    def do_call():
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=1024,
        )
        return resp.choices[0].message.content  # ✅ FIX

    try:
        return await loop.run_in_executor(None, do_call)
    except Exception as e:
        logger.exception("OpenAI error")
        return f"⚠️ OpenAI error: {e}"

# ===================== AIOHTTP SAFETY =====================
async def download_attachment_to_bytes(url: str) -> Optional[BytesIO]:
    if not aio_session:
        return None
    async with aio_session.get(url) as resp:
        if resp.status != 200:
            return None
        return BytesIO(await resp.read())

# ===================== EVENTS =====================
@bot.event
async def on_ready():
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(type=discord.ActivityType.watching, name="you poop"),
    )

@bot.event
async def setup_hook():
    global aio_session
    aio_session = aiohttp.ClientSession()

    await mp.load()
    await mp.start_autosave(asyncio.get_running_loop())

    await bot.load_extension("cogs.blackjack_cog")

    if GUILD_ID:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    else:
        await bot.tree.sync()

# ===================== CLEAN SHUTDOWN =====================
async def close_bot():
    global aio_session
    if aio_session:
        await aio_session.close()

bot.close = close_bot  # ✅ override safely

# ===================== COMMANDS =====================
@bot.command(name="jhoose")
async def jhoose(ctx: commands.Context, *, choices: str):
    choices_list = [c.strip() for c in choices.split(",") if c.strip()]
    if len(choices_list) < 2:
        await ctx.send("Please provide at least two choices separated by commas.")
        return
    await ctx.send(f"The result is: **{random.choice(choices_list)}**")

@bot.command(name="jpt")
async def jpt(ctx: commands.Context, *, prompt: str):
    wait = await ctx.send("💬 Thinking...")
    try:
        text = await query_chatgpt_async(prompt)
        await ctx.send(text[:1900])
    finally:
        await wait.delete()

# ===================== RUN =====================
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

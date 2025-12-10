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

from openai import OpenAI
from openai import error as openai_error

import random

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_API_KEY")
OPENAI_API_KEY = os.getenv("CHATGPT_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_GEO_PLACES_API_KEY")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID")) if os.getenv("BOT_OWNER_ID") else None
ALLOWED_BLACKJACK_CHANNEL_ID = int(os.getenv("ALLOWED_BLACKJACK_CHANNEL_ID")) if os.getenv("ALLOWED_BLACKJACK_CHANNEL_ID") else None
ALLOWED_BLACKJACK_THREAD_ID = int(os.getenv("ALLOWED_BLACKJACK_THREAD_ID")) if os.getenv("ALLOWED_BLACKJACK_THREAD_ID") else None

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_API_KEY in environment")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing CHATGPT_API_KEY in environment")
if not GOOGLE_API_KEY:
    logging.warning("No GOOGLE_GEO_PLACES_API_KEY set — Google features will fail")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("thejamesroll-bot")

# Blackjack - load money pool and start autosave
from money_pool import get_money_pool_singleton
mp = get_money_pool_singleton()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

client = OpenAI(api_key=OPENAI_API_KEY)

# aiohttp session (reuse)
aio_session = aiohttp.ClientSession()

# Simple in-memory cache for geocoding
_GEOCODE_CACHE: Dict[str, Tuple[float, float, float]] = {}
# { city_lower: (lat, lng, timestamp) }
GEOCODE_TTL = 60 * 60 * 24  # 24 hours

# Helpers: images
def resize_image_to_square_bytes(source: BytesIO, size: int = 1024) -> BytesIO:
    """
    Resize image to a square (padding preserved) and return BytesIO (PNG).
    """
    source.seek(0)
    im = Image.open(source).convert("RGBA")
    old_size = im.size
    ratio = float(size) / max(old_size)
    new_size = tuple(int(x * ratio) for x in old_size)
    im = im.resize(new_size, Image.Resampling.LANCZOS)

    # create square canvas and paste
    new_im = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    paste_pos = ((size - new_size[0]) // 2, (size - new_size[1]) // 2)
    new_im.paste(im, paste_pos, im if im.mode == "RGBA" else None)

    out = BytesIO()
    new_im.save(out, format="PNG")
    out.seek(0)
    return out

async def download_attachment_to_bytes(url: str) -> Optional[BytesIO]:
    """
    Download an attachment URL (Discord CDN) to BytesIO using aiohttp.
    """
    try:
        async with aio_session.get(url, timeout=30) as resp:
            if resp.status != 200:
                logger.warning("Attachment download failed %s status=%s", url, resp.status)
                return None
            data = await resp.read()
            return BytesIO(data)
    except Exception as e:
        logger.exception("Failed to download attachment: %s", e)
        return None

# Helpers: Google Geocoding & Places (async)
async def geocode_city_async(city: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Return (lat, lng, error). Uses simple in-memory cache.
    """
    if not GOOGLE_API_KEY:
        return None, None, "Google API key not configured"

    key = city.strip().lower()
    cached = _GEOCODE_CACHE.get(key)
    now = time.time()
    if cached and now - cached[2] < GEOCODE_TTL:
        return cached[0], cached[1], None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": city, "key": GOOGLE_API_KEY}
    try:
        async with aio_session.get(url, params=params, timeout=15) as resp:
            j = await resp.json()
    except Exception as e:
        logger.exception("Geocode request failed: %s", e)
        return None, None, "Network/geocode request failed"

    status = j.get("status")
    if status != "OK":
        logger.warning("Geocode API returned status=%s for city=%s", status, city)
        return None, None, status

    loc = j["results"][0]["geometry"]["location"]
    _GEOCODE_CACHE[key] = (loc["lat"], loc["lng"], now)
    return loc["lat"], loc["lng"], None

async def get_restaurants_async(city: str, radius_miles: float = 3, category: Optional[str] = None) -> Tuple[Optional[List[str]], Optional[str]]:
    lat, lng, err = await geocode_city_async(city)
    if err:
        return None, err

    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    radius_m = max(50, int(radius_miles * 1609.34))  # minimal radius guard
    params = {
        "location": f"{lat},{lng}",
        "radius": radius_m,
        "type": "restaurant",
        "key": GOOGLE_API_KEY,
    }
    if category:
        params["keyword"] = category

    try:
        async with aio_session.get(url, params=params, timeout=15) as resp:
            j = await resp.json()
    except Exception as e:
        logger.exception("Places API request failed: %s", e)
        return None, "Network/Places request failed"

    status = j.get("status")
    if status != "OK":
        if status == "ZERO_RESULTS":
            return [], None
        logger.warning("Places API status=%s", status)
        return None, status

    results = j.get("results", [])[:10]
    formatted = [f"**{i+1}. {r.get('name')}** — {r.get('vicinity','No address')}" for i, r in enumerate(results)]
    return formatted, None

async def get_restaurant_address_async(name: str, city: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    lat, lng, err = await geocode_city_async(city)
    if err:
        return None, None, err

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": f"{name} restaurant in {city}",
        "location": f"{lat},{lng}",
        "key": GOOGLE_API_KEY,
    }
    try:
        async with aio_session.get(url, params=params, timeout=15) as resp:
            j = await resp.json()
    except Exception as e:
        logger.exception("Places textsearch failed: %s", e)
        return None, None, "Network/Places request failed"

    status = j.get("status")
    if status != "OK":
        return None, None, status

    if not j.get("results"):
        return None, None, "ZERO_RESULTS"

    r = j["results"][0]
    return r.get("name"), r.get("formatted_address"), None

# Helpers: OpenAI wrappers
async def query_chatgpt_async(prompt: str, system_prompt: Optional[str] = None, model: str = "gpt-4o-mini") -> str:
    """
    Query OpenAI chat endpoint using the new OpenAI client.
    Note: client.chat.completions.create is synchronous in the SDK; we wrap in executor.
    """
    if system_prompt is None:
        system_prompt = "You are a helpful assistant."

    # The OpenAI python client might be sync; call in executor to avoid blocking event loop.
    loop = asyncio.get_event_loop()

    def do_call():
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1024
            )
            # Return the textual reply
            return resp.choices[0].message["content"]
        except openai_error.OpenAIError as e:
            logger.exception("OpenAI error: %s", e)
            raise

    try:
        result = await loop.run_in_executor(None, do_call)
        return result
    except Exception as e:
        return f"⚠️ OpenAI error: {e}"

async def dalle_generate_async(prompt: str, model: str = "dall-e-3", size: str = "1024x1024") -> Tuple[Optional[str], Optional[str]]:
    """
    Generate image URL (returns (url, error))
    Note: If the SDK's images methods are sync, run in executor.
    """
    loop = asyncio.get_event_loop()

    def do_call():
        try:
            resp = client.images.generate(model=model, prompt=prompt, size=size, n=1)
            return resp.data[0].url
        except openai_error.OpenAIError as e:
            logger.exception("OpenAI images error: %s", e)
            raise

    try:
        url = await loop.run_in_executor(None, do_call)
        return url, None
    except Exception as e:
        return None, f"OpenAI images error: {e}"

async def dalle_edit_async(image_bytes: BytesIO, prompt: str, model: str = "gpt-image-1") -> Tuple[Optional[str], Optional[str]]:
    """
    Edit an image using a model that supports editing (gpt-image-1 family).
    Resize and pass binary bytes. Returns (url, error)
    """
    loop = asyncio.get_event_loop()
    image_bytes.seek(0)
    # The new SDK expects file-like; convert to bytes for direct call in executor.
    image_content = image_bytes.read()

    def do_call():
        try:
            resp = client.images.edit(model=model, image=image_content, prompt=prompt, n=1, size="1024x1024")
            return resp.data[0].url
        except openai_error.OpenAIError as e:
            logger.exception("OpenAI images.edit error: %s", e)
            raise

    try:
        url = await loop.run_in_executor(None, do_call)
        return url, None
    except Exception as e:
        return None, f"OpenAI images edit error: {e}"

async def dalle_variation_async(image_bytes: BytesIO, model: str = "gpt-image-1") -> Tuple[Optional[str], Optional[str]]:
    loop = asyncio.get_event_loop()
    image_bytes.seek(0)
    bc = image_bytes.read()

    def do_call():
        try:
            resp = client.images.variations(model=model, image=bc, n=1, size="1024x1024")
            return resp.data[0].url
        except openai_error.OpenAIError as e:
            logger.exception("OpenAI images.variations error: %s", e)
            raise

    try:
        url = await loop.run_in_executor(None, do_call)
        return url, None
    except Exception as e:
        return None, f"OpenAI images variations error: {e}"

# Misc helpers (rednote scraping)
async def get_rednote_info_async(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        # Basic safety: only http/https
        if not (url.startswith("http://") or url.startswith("https://")):
            return None, None

        async with aio_session.get(url, timeout=15) as resp:
            if resp.status != 200:
                return None, None
            text = await resp.text()

        soup = BeautifulSoup(text, "html.parser")
        title_div = soup.find("div", id="detail-title", class_="title")
        title = title_div.text.strip() if title_div else None
        thumb = None
        m = soup.find("meta", property="og:image")
        if m and m.get("content"):
            thumb = m["content"]
        return title, thumb
    except Exception as e:
        logger.exception("Failed to fetch rednote info: %s", e)
        return None, None

async def format_embed_async(text: str) -> discord.Embed:
    emb = discord.Embed(title="The James Roll says...", description=text, color=0x00FF00)
    return emb

@bot.event
async def on_ready():
    logger.info("Logged in as: %s (id=%s)", bot.user, bot.user.id)
    # presence
    activity = discord.Activity(type=discord.ActivityType.watching, name="you poop")
    await bot.change_presence(status=discord.Status.dnd, activity=activity)

    # sync slash commands for a single guild (faster) if GUILD_ID provided
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            logger.info("Syncing commands to guild %s", GUILD_ID)
            await bot.tree.sync(guild=guild_obj)
        else:
            logger.info("Syncing global commands (may take up to 1 hour to propagate)")
            await bot.tree.sync()
    except Exception as e:
        logger.exception("Failed to sync commands: %s", e)

@bot.event
async def on_message(message: discord.Message):
    # keep it light and safe
    if message.author == bot.user:
        return

    # rednote quick preview
    try:
        txt = message.content.strip()
        if "://rednote.com/" in txt or "://xhslink.com/a/" in txt:
            url = txt.split()[0]
            title, thumb = await get_rednote_info_async(url)
            if title:
                emb = discord.Embed(title=title, url=url, color=0x00FF00)
                if thumb:
                    emb.set_image(url=thumb)
                await message.channel.send(embed=emb)
    except Exception:
        logger.exception("Error handling rednote in on_message")

    # allow commands to process
    await bot.process_commands(message)

@bot.command(name="jhoose")
async def jhoose(ctx: commands.Context, *, choices: str):
    choices_list = [c.strip() for c in choices.split(",") if c.strip()]
    if len(choices_list) < 2:
        await ctx.send("Please provide at least two choices separated by commas.")
        return
    await ctx.send(f"The result is: **{random.choice(choices_list)}**")

@bot.command(name="jpt")
async def jpt(ctx: commands.Context, *, prompt: str):
    wait = await ctx.send("💬 The James Roll is thinking...")
    try:
        text = await query_chatgpt_async(prompt, model="gpt-4o-mini")
        # split long messages to respect discord limit
        if len(text) > 1900:
            for chunk in [text[i:i+1900] for i in range(0, len(text), 1900)]:
                await ctx.send(chunk)
        else:
            await ctx.send(text)
    finally:
        await wait.delete()

@bot.command(name="jimg")
async def jimg(ctx: commands.Context, *, prompt: str):
    wait = await ctx.send("🎨 The James Roll is cooking...")
    try:
        url, err = await dalle_generate_async(prompt, model="dall-e-3")
        if err:
            await ctx.send(f"⚠️ {err}")
            return
        emb = discord.Embed(title="🎨 Image", description=prompt)
        emb.set_image(url=url)
        await ctx.send(embed=emb)
    finally:
        await wait.delete()

@bot.command(name="jedit")
async def jedit(ctx: commands.Context, *, prompt: str):
    wait = await ctx.send("🖌 The James Roll is cooking...")
    try:
        if not ctx.message.attachments:
            await ctx.send("Please attach an image to edit.")
            return
        file_url = ctx.message.attachments[0].url
        src = await download_attachment_to_bytes(file_url)
        if not src:
            await ctx.send("Failed to download attachment.")
            return
        resized = resize_image_to_square_bytes(src, size=1024)
        url, err = await dalle_edit_async(resized, prompt, model="gpt-image-1")
        if err:
            await ctx.send(f"⚠️ {err}")
            return
        emb = discord.Embed(title="🖌 Edited Image", description=prompt)
        emb.set_image(url=url)
        await ctx.send(embed=emb)
    finally:
        await wait.delete()

@bot.command(name="jvari")
async def jvari(ctx: commands.Context):
    wait = await ctx.send("🔄 The James Roll is rolling...")
    try:
        if not ctx.message.attachments:
            await ctx.send("Please attach an image to vary.")
            return
        file_url = ctx.message.attachments[0].url
        src = await download_attachment_to_bytes(file_url)
        if not src:
            await ctx.send("Failed to download attachment.")
            return
        resized = resize_image_to_square_bytes(src, size=1024)
        url, err = await dalle_variation_async(resized, model="gpt-image-1")
        if err:
            await ctx.send(f"⚠️ {err}")
            return
        emb = discord.Embed(title="🔄 Variation")
        emb.set_image(url=url)
        await ctx.send(embed=emb)
    finally:
        await wait.delete()

@bot.command(name="eats")
async def eats(ctx: commands.Context, city: str, radius: float = 3, *, category: Optional[str] = None):
    wait = await ctx.send("🍽 Finding restaurants...")
    try:
        results, err = await get_restaurants_async(city, radius_miles=radius, category=category)
        if err:
            await ctx.send(f"⚠️ {err}")
            return
        if not results:
            await ctx.send(f"No restaurants found near {city}.")
            return
        emb = discord.Embed(title=f"Top Restaurants Near {city}", description="\n".join(results))
        await ctx.send(embed=emb)
    finally:
        await wait.delete()

@bot.command(name="addy")
async def addy(ctx: commands.Context, restaurant: str, *, city: str):
    wait = await ctx.send("📍 Looking up address...")
    try:
        name, addr, err = await get_restaurant_address_async(restaurant, city)
        if err:
            await ctx.send(f"⚠️ {err}")
            return
        emb = discord.Embed(title=name, description=addr)
        await ctx.send(embed=emb)
    finally:
        await wait.delete()

# Blackjack
def is_valid_channel(interaction: discord.Interaction) -> bool:
    # Check if thread restriction exists
    if ALLOWED_BLACKJACK_THREAD_ID:
        return interaction.channel.id == ALLOWED_BLACKJACK_THREAD_ID
    else:
        # fallback: only allow channel
        return interaction.channel.id == ALLOWED_BLACKJACK_CHANNEL_ID
    
@bot.event
async def setup_hook():
    await mp.load()
    await mp.start_autosave(asyncio.get_event_loop())
    # load blackjack cog
    await bot.load_extension("cogs.blackjack_cog")
    # sync tree
    if GUILD_ID:
        await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    else:
        await bot.tree.sync()

# Graceful shutdown
async def _cleanup():
    logger.info("Shutting down: closing aiohttp session")
    await aio_session.close()

def run_bot():
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        # synchronous cleanup not ideal — but ensure aiohttp closed
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            loop.run_until_complete(_cleanup())


if __name__ == "__main__":
    run_bot()

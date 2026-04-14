from __future__ import annotations

import time
from urllib.parse import quote_plus

import discord

from services.http_service import get_json
from utils.text import miles_to_meters


async def geocode_city(bot, city: str) -> tuple[float, float]:
    if not bot.settings.google_api_key:
        raise RuntimeError("GOOGLE_GEO_PLACES_API_KEY is not configured")

    key = city.strip().lower()
    cached = bot.geocode_cache.get(key)
    if cached and (time.time() - cached[2]) < bot.geocode_ttl_seconds:
        return cached[0], cached[1]

    payload = await get_json(
        bot,
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={
            "address": city,
            "key": bot.settings.google_api_key,
        },
    )

    status = payload.get("status")
    if status != "OK":
        raise RuntimeError(f"Geocoding failed: {status}")

    location = payload["results"][0]["geometry"]["location"]
    lat = float(location["lat"])
    lng = float(location["lng"])

    bot.geocode_cache[key] = (lat, lng, time.time())
    return lat, lng


async def get_restaurants(
    bot,
    city: str,
    radius_miles: float = 3,
    category: str | None = None,
) -> list[str]:
    if not bot.settings.google_api_key:
        raise RuntimeError("GOOGLE_GEO_PLACES_API_KEY is not configured")

    lat, lng = await geocode_city(bot, city)

    params: dict[str, object] = {
        "location": f"{lat},{lng}",
        "radius": miles_to_meters(radius_miles),
        "type": "restaurant",
        "key": bot.settings.google_api_key,
    }
    if category:
        params["keyword"] = category

    payload = await get_json(
        bot,
        "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
        params=params,
    )

    status = payload.get("status")
    if status != "OK":
        raise RuntimeError(f"Places search failed: {status}")

    results = payload.get("results", [])[:10]
    if not results:
        return ["No restaurants found."]

    return [
        f"**{index}. {item['name']}** — {item.get('vicinity', 'No address available')}"
        for index, item in enumerate(results, start=1)
    ]


async def get_restaurant_address(bot, name: str, city: str) -> tuple[str, str]:
    if not bot.settings.google_api_key:
        raise RuntimeError("GOOGLE_GEO_PLACES_API_KEY is not configured")

    lat, lng = await geocode_city(bot, city)

    payload = await get_json(
        bot,
        "https://maps.googleapis.com/maps/api/place/textsearch/json",
        params={
            "query": f"{name} restaurant in {city}",
            "location": f"{lat},{lng}",
            "key": bot.settings.google_api_key,
        },
    )

    status = payload.get("status")
    if status != "OK":
        raise RuntimeError(f"Address lookup failed: {status}")

    results = payload.get("results", [])
    if not results:
        raise RuntimeError("No restaurant match found")

    result = results[0]
    return result["name"], result["formatted_address"]


def build_restaurants_embed(
    city: str,
    radius: float,
    category: str | None,
    restaurants: list[str],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Top restaurants near {city}",
        description="\n".join(restaurants),
    )

    if category:
        embed.set_footer(text=f"Category filter: {category} • Radius: {radius} miles")
    else:
        embed.set_footer(text=f"Radius: {radius} miles")

    return embed


def build_address_embed(name: str, address: str) -> discord.Embed:
    embed = discord.Embed(title=name, description=address)
    maps_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"
    embed.add_field(name="Maps", value=maps_url, inline=False)
    return embed
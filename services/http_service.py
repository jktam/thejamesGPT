from __future__ import annotations

from typing import Any


async def get_json(bot, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not bot.http_session:
        raise RuntimeError("HTTP session is not initialized")

    async with bot.http_session.get(url, params=params) as response:
        response.raise_for_status()
        return await response.json()
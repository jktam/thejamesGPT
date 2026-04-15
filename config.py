from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class Settings:
    discord_token: str
    openai_api_key: str
    google_api_key: str | None
    guild_id: int | None
    default_chat_model: str
    default_image_model: str
    status_text: str
    http_timeout_seconds: int
    openai_timeout_seconds: int
    max_text_chunks: int
    image_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        discord_token = os.getenv("DISCORD_BOT_API_KEY")
        openai_api_key = os.getenv("CHATGPT_API_KEY")
        google_api_key = os.getenv("GOOGLE_GEO_PLACES_API_KEY")
        guild_id_raw = os.getenv("GUILD_ID")

        if not discord_token:
            raise RuntimeError("Missing DISCORD_BOT_API_KEY")
        if not openai_api_key:
            raise RuntimeError("Missing CHATGPT_API_KEY")

        return cls(
            discord_token=discord_token,
            openai_api_key=openai_api_key,
            google_api_key=google_api_key,
            guild_id=int(guild_id_raw) if guild_id_raw else None,
            default_chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini"),
            default_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            status_text=os.getenv("BOT_STATUS_TEXT", "your messages"),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            openai_timeout_seconds=int(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
            max_text_chunks=int(os.getenv("MAX_TEXT_CHUNKS", "6")),
            image_timeout_seconds=int(os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "90")),
        )


SETTINGS = Settings.from_env()
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI

_executor = ThreadPoolExecutor(max_workers=4)


class OpenAIService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _build_instructions(self, system_prompt: str) -> str:
        # You can change this timezone if you want the bot anchored elsewhere.
        current_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")

        return (
            f"{system_prompt}\n\n"
            f"Current date: {current_date}.\n"
            f"If the user asks about current, upcoming, recent, latest, or time-sensitive real-world information, "
            f"do not guess or invent facts. State uncertainty when needed."
        )

    async def ask(
        self,
        prompt: str,
        *,
        system_prompt: str = "You are a helpful assistant.",
        model: str | None = None,
    ) -> str:
        selected_model = model or self.settings.default_chat_model
        loop = asyncio.get_running_loop()
        instructions = self._build_instructions(system_prompt)

        def do_call() -> str:
            response = self.client.responses.create(
                model=selected_model,
                instructions=instructions,
                input=prompt,
            )
            return response.output_text or ""

        return await asyncio.wait_for(
            loop.run_in_executor(_executor, do_call),
            timeout=self.settings.openai_timeout_seconds,
        )

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        selected_model = model or self.settings.default_image_model
        loop = asyncio.get_running_loop()

        def do_call() -> str:
            response = self.client.images.generate(
                model=selected_model,
                prompt=prompt,
                size="1024x1024",
            )
            image_url = response.data[0].url
            if not image_url:
                raise RuntimeError("Image API returned no URL")
            return image_url

        return await asyncio.wait_for(
            loop.run_in_executor(_executor, do_call),
            timeout=self.settings.openai_timeout_seconds,
        )
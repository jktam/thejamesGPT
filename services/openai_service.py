from __future__ import annotations

import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI


class OpenAIService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    def _build_system(self, system_prompt: str) -> str:
        current_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
        return (
            f"{system_prompt}\n\n"
            f"Current date: {current_date}.\n"
            "If the user asks about current, upcoming, recent, latest, or time-sensitive "
            "real-world information, do not guess or invent facts. State uncertainty when needed."
        )

    async def ask(
        self,
        prompt: str,
        *,
        system_prompt: str = "You are a helpful assistant.",
        model: str | None = None,
    ) -> str:
        selected_model = model or self.settings.default_chat_model
        response = await self._client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": self._build_system(system_prompt)},
                {"role": "user", "content": prompt},
            ],
            timeout=self.settings.openai_timeout_seconds,
        )
        return response.choices[0].message.content or ""

    async def generate_image(self, prompt: str, *, model: str | None = None) -> dict[str, str | bytes]:
        selected_model = model or self.settings.default_image_model
        response = await self._client.images.generate(
            model=selected_model,
            prompt=prompt,
            size="1024x1024",
            timeout=self.settings.image_timeout_seconds,
        )
        item = response.data[0]
        if item.b64_json:
            return {"kind": "bytes", "value": base64.b64decode(item.b64_json), "mime_type": "image/png"}
        if item.url:
            return {"kind": "url", "value": item.url}
        raise RuntimeError(f"Image API returned no image data for model {selected_model}")

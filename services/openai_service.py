from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import openai

_executor = ThreadPoolExecutor(max_workers=4)


class OpenAIService:
    def __init__(self, settings) -> None:
        self.settings = settings
        openai.api_key = settings.openai_api_key

    def _build_instructions(self, system_prompt: str) -> str:
        # You can change this timezone if you want the bot anchored elsewhere.
        current_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")

        return (
            f"{system_prompt}\n\n"
            f"Current date: {current_date}.\n"
            f"If the user asks about current, upcoming, recent, latest, or time-sensitive real-world information, "
            f"do not guess or invent facts. State uncertainty when needed."
        )

    def _extract_chat_text(self, response) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""

        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if content:
                return content

        text = getattr(choice, "text", None)
        return text or ""

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
            response = openai.ChatCompletion.create(
                model=selected_model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
            )
            return self._extract_chat_text(response)

        return await asyncio.wait_for(
            loop.run_in_executor(_executor, do_call),
            timeout=self.settings.openai_timeout_seconds,
        )

    async def generate_image(self, prompt: str, *, model: str | None = None) -> dict[str, str | bytes]:
        selected_model = model or self.settings.default_image_model
        loop = asyncio.get_running_loop()

        def do_call() -> dict[str, str | bytes]:
            response = openai.Image.create(
                model=selected_model,
                prompt=prompt,
                size="1024x1024",
                response_format="b64_json",
            )
            data = (response.get("data") or []) if isinstance(response, dict) else getattr(response, "data", [])
            if not data:
                raise RuntimeError(f"Image API returned no image data for model {selected_model}")

            first = data[0]
            if isinstance(first, dict):
                image_url = first.get("url")
                if image_url:
                    return {"kind": "url", "value": image_url}

                b64_json = first.get("b64_json")
                if b64_json:
                    return {
                        "kind": "bytes",
                        "value": base64.b64decode(b64_json),
                        "mime_type": first.get("mime_type", "image/png"),
                    }
            else:
                image_url = getattr(first, "url", None)
                if image_url:
                    return {"kind": "url", "value": image_url}

                b64_json = getattr(first, "b64_json", None)
                if b64_json:
                    return {
                        "kind": "bytes",
                        "value": base64.b64decode(b64_json),
                        "mime_type": getattr(first, "mime_type", "image/png"),
                    }

            raise RuntimeError(f"Image API returned no URL or image payload for model {selected_model}")

        return await asyncio.wait_for(
            loop.run_in_executor(_executor, do_call),
            timeout=self.settings.image_timeout_seconds,
        )

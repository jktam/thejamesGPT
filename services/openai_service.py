import asyncio
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

_executor = ThreadPoolExecutor(max_workers=4)


class OpenAIService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key)

    async def ask(self, prompt: str, *, system_prompt: str = "You are a helpful assistant.", model: str | None = None) -> str:
        selected_model = model or self.settings.default_chat_model
        loop = asyncio.get_running_loop()

        def do_call() -> str:
            response = self.client.responses.create(
                model=selected_model,
                instructions=system_prompt,
                input=prompt,
            )
            return response.output_text or ""

        return await asyncio.wait_for(
            loop.run_in_executor(_executor, do_call),
            timeout=self.settings.openai_timeout_seconds,
        )

    async def generate_image(self, prompt: str, *, model: str | None = None) -> str:
        selected_model = model or self.settings.default_image_model
        loop = asyncio.get_running_loop()

        def do_call() -> str:
            response = self.client.images.generate(
                model=selected_model,
                prompt=prompt,
                size="1024x1024",
            )
            return response.data[0].url

        return await asyncio.wait_for(
            loop.run_in_executor(_executor, do_call),
            timeout=self.settings.openai_timeout_seconds,
        )
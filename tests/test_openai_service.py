from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.openai_service import OpenAIService


class DummySettings:
    openai_api_key = "test"
    default_chat_model = "gpt-4.1-mini"
    default_image_model = "gpt-image-1"
    openai_timeout_seconds = 1
    image_timeout_seconds = 1


class OpenAIServiceTests(unittest.TestCase):
    def test_generate_image_prefers_url(self):
        service = OpenAIService(DummySettings())

        with patch(
            "services.openai_service.openai.Image.create",
            return_value={"data": [{"url": "https://example.com/image.png"}]},
        ):
            result = asyncio.run(service.generate_image("a cat"))

        self.assertEqual(result, {"kind": "url", "value": "https://example.com/image.png"})

    def test_generate_image_decodes_b64_json(self):
        service = OpenAIService(DummySettings())

        with patch(
            "services.openai_service.openai.Image.create",
            return_value={"data": [{"b64_json": "aGVsbG8="}]},
        ):
            result = asyncio.run(service.generate_image("a cat"))

        self.assertEqual(result["kind"], "bytes")
        self.assertEqual(result["value"], b"hello")

    def test_generate_image_raises_without_payload(self):
        service = OpenAIService(DummySettings())

        with patch(
            "services.openai_service.openai.Image.create",
            return_value={"data": [{}]},
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(service.generate_image("a cat"))

        self.assertIn("no URL or image payload", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

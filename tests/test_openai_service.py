from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.openai_service import OpenAIService


class DummySettings:
    openai_api_key = "test"
    default_chat_model = "gpt-4.1-mini"
    default_image_model = "gpt-image-1"
    openai_timeout_seconds = 1
    image_timeout_seconds = 1


def _image_response(url=None, b64_json=None):
    return SimpleNamespace(data=[SimpleNamespace(url=url, b64_json=b64_json)])


def _chat_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class OpenAIServiceTests(unittest.TestCase):
    def test_generate_image_returns_bytes_for_b64_json(self):
        service = OpenAIService(DummySettings())
        with patch.object(service._client.images, "generate", AsyncMock(return_value=_image_response(b64_json="aGVsbG8="))):
            result = asyncio.run(service.generate_image("a cat"))
        self.assertEqual(result["kind"], "bytes")
        self.assertEqual(result["value"], b"hello")

    def test_generate_image_returns_url(self):
        service = OpenAIService(DummySettings())
        with patch.object(service._client.images, "generate", AsyncMock(return_value=_image_response(url="https://example.com/img.png"))):
            result = asyncio.run(service.generate_image("a cat"))
        self.assertEqual(result, {"kind": "url", "value": "https://example.com/img.png"})

    def test_generate_image_raises_without_payload(self):
        service = OpenAIService(DummySettings())
        with patch.object(service._client.images, "generate", AsyncMock(return_value=_image_response())):
            with self.assertRaises(RuntimeError):
                asyncio.run(service.generate_image("a cat"))

    def test_ask_returns_message_content(self):
        service = OpenAIService(DummySettings())
        with patch.object(service._client.chat.completions, "create", AsyncMock(return_value=_chat_response("hello there"))):
            result = asyncio.run(service.ask("hi"))
        self.assertEqual(result, "hello there")


if __name__ == "__main__":
    unittest.main()

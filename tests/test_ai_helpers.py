from __future__ import annotations

import unittest
from types import SimpleNamespace

from cogs.ai import (
    ASK_MEMORY_LIMIT,
    ASK_MEMORY_TTL_SECONDS,
    MemoryTurn,
    build_discord_ask_prompt,
    build_rewrite_prompt,
)
from services.openai_service import OpenAIService


class AiHelperTests(unittest.TestCase):
    def test_build_rewrite_prompt_known_tone_keeps_target_style(self):
        prompt = build_rewrite_prompt("hello world", "linkedin")

        self.assertIn("LinkedIn post", prompt)
        self.assertIn("Original text:", prompt)
        self.assertIn("hello world", prompt)

    def test_build_discord_ask_prompt_includes_recent_context(self):
        memory = [
            MemoryTurn(role="user", content="what time is it?", created_at=1.0),
            MemoryTurn(role="assistant", content="it's 3pm", created_at=2.0),
        ]

        prompt = build_discord_ask_prompt("and tomorrow?", memory)

        self.assertIn("Recent conversation context:", prompt)
        self.assertIn("User: what time is it?", prompt)
        self.assertIn("Assistant: it's 3pm", prompt)
        self.assertIn("New user message:", prompt)
        self.assertIn("and tomorrow?", prompt)

    def test_memory_settings_are_bounded(self):
        self.assertEqual(ASK_MEMORY_LIMIT, 8)
        self.assertEqual(ASK_MEMORY_TTL_SECONDS, 60 * 60)

    def test_extract_chat_text_prefers_message_content(self):
        service = OpenAIService(SimpleNamespace(openai_api_key="test"))
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hello from message"),
                    text="fallback text",
                )
            ]
        )

        self.assertEqual(service._extract_chat_text(response), "hello from message")

    def test_extract_chat_text_falls_back_to_text(self):
        service = OpenAIService(SimpleNamespace(openai_api_key="test"))
        response = SimpleNamespace(choices=[SimpleNamespace(message=None, text="fallback text")])

        self.assertEqual(service._extract_chat_text(response), "fallback text")

    def test_extract_chat_text_handles_empty_response(self):
        service = OpenAIService(SimpleNamespace(openai_api_key="test"))

        self.assertEqual(service._extract_chat_text(SimpleNamespace(choices=[])), "")


if __name__ == "__main__":
    unittest.main()

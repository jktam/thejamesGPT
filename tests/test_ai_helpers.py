from __future__ import annotations

import unittest

from cogs.ai import build_discord_ask_prompt, build_rewrite_prompt


class AiHelperTests(unittest.TestCase):
    def test_build_rewrite_prompt_linkedin(self):
        prompt = build_rewrite_prompt("hello world", "linkedin")
        self.assertIn("LinkedIn post", prompt)
        self.assertIn("hello world", prompt)

    def test_build_rewrite_prompt_known_tone(self):
        prompt = build_rewrite_prompt("hello world", "professional")
        self.assertIn("professional", prompt)
        self.assertIn("hello world", prompt)

    def test_build_discord_ask_prompt_includes_user_message(self):
        prompt = build_discord_ask_prompt("what's the weather?")
        self.assertIn("what's the weather?", prompt)

    def test_build_discord_ask_prompt_no_followup_instruction(self):
        prompt = build_discord_ask_prompt("anything")
        self.assertIn("Never ask follow-up", prompt)


if __name__ == "__main__":
    unittest.main()

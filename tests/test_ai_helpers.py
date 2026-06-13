from __future__ import annotations

import unittest

from cogs.ai import build_discord_ask_prompt, build_rewrite_prompt, _ASK_SYSTEM


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
        # The no-follow-up rule now lives in the system prompt constant, not the user prompt
        self.assertIn("Never ask follow-up", _ASK_SYSTEM)

    def test_build_discord_ask_prompt_wraps_input_in_xml(self):
        prompt = build_discord_ask_prompt("anything")
        self.assertIn("<user_message>", prompt)
        self.assertIn("anything", prompt)

    def test_build_discord_ask_prompt_injection_guard_in_system(self):
        self.assertIn("untrusted", _ASK_SYSTEM)


if __name__ == "__main__":
    unittest.main()

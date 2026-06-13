from __future__ import annotations

import re

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_input(text: str, max_length: int = 2000) -> str:
    """Strip control characters and truncate to prevent oversized payloads."""
    text = _CONTROL_CHARS.sub("", text)
    return text[:max_length].strip()


def prompt_wrap(text: str, tag: str = "user_input") -> str:
    """Wrap text in XML tags so the model treats it as data, not instructions."""
    return f"<{tag}>\n{text}\n</{tag}>"

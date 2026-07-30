from __future__ import annotations

import datetime
import re

from bs4 import BeautifulSoup


CHASE_FREEDOM_CALENDAR_URL = (
    "https://creditcards.chase.com/cash-back-credit-cards/freedom/flex"
)

_QUARTER_LABELS = {
    1: "January - March",
    2: "April - June",
    3: "July - September",
    4: "October - December",
}
_BOILERPLATE_PREFIXES = (
    "activate ",
    "activation ",
    "explore ",
    "your 5%",
)


def _clean_text(value: str) -> str:
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = re.sub(r"[\u00ae\u2120\u2122]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_chase_freedom_categories(
    html: str,
    today: datetime.date,
) -> tuple[str, ...] | None:
    """Extract the current quarter from Chase's official Freedom calendar."""
    soup = BeautifulSoup(html, "html.parser")
    lines = [
        cleaned
        for raw in soup.stripped_strings
        if (cleaned := _clean_text(raw))
    ]

    year_marker = f"quarterly cash back {today.year} calendar"
    if not any(year_marker in line.lower() for line in lines):
        return None

    quarter = (today.month - 1) // 3 + 1
    target = _QUARTER_LABELS[quarter]
    try:
        start = lines.index(target)
    except ValueError:
        return None

    other_labels = set(_QUARTER_LABELS.values())
    candidates: list[str] = []
    for line in lines[start + 1 :]:
        if line in other_labels:
            break
        lowered = line.lower()
        if lowered == "coming soon":
            return None
        if "|" not in line or lowered.startswith(_BOILERPLATE_PREFIXES):
            continue
        candidates = [_clean_text(item) for item in line.split("|")]
        break

    categories = tuple(
        category
        for category in candidates
        if 2 <= len(category) <= 80
        and category.lower() != "coming soon"
    )
    if not 1 <= len(categories) <= 8:
        return None
    return categories

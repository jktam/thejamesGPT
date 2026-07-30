from __future__ import annotations

from dataclasses import dataclass
import datetime
import re

from bs4 import BeautifulSoup


CHASE_FREEDOM_CALENDAR_URL = (
    "https://creditcards.chase.com/cash-back-credit-cards/freedom/flex"
)
CHASE_TRANSFER_BONUS_URL = (
    "https://www.pointsyeah.com/blog/posts/current-credit-card-transfer-bonuses"
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
_BONUS_PATTERN = re.compile(r"^(?:up to )?\d{1,3}%(?:\s+.+)?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TransferBonus:
    partner: str
    bonus: str
    expires_on: datetime.date


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


def _parse_expiration(value: str) -> datetime.date | None:
    for date_format in ("%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def parse_chase_transfer_bonuses(
    html: str,
    today: datetime.date,
) -> tuple[TransferBonus, ...]:
    """Extract active Chase rows from a current-month transfer-bonus tracker."""
    page_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    current_marker = (
        f"current credit card transfer bonuses: {today.strftime('%B %Y')}"
    ).lower()
    if current_marker not in page_text.lower():
        return ()

    # The article body is embedded as escaped HTML in the page's Next.js payload.
    decoded = (
        html.replace("\\u003c", "<")
        .replace("\\u003e", ">")
        .replace("\\u0026", "&")
        .replace('\\"', '"')
    )
    table_fragments = re.findall(
        r"<table(?:\s[^>]*)?>.*?</table>",
        decoded,
        flags=re.IGNORECASE | re.DOTALL,
    )

    bonuses: list[TransferBonus] = []
    seen: set[tuple[str, str, datetime.date]] = set()
    latest_reasonable_expiration = today + datetime.timedelta(days=120)
    for fragment in table_fragments:
        table = BeautifulSoup(fragment, "html.parser")
        for row in table.find_all("tr"):
            cells = [
                _clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"])
            ]
            if len(cells) != 4 or cells[0].lower() != "chase ultimate rewards":
                continue

            partner, bonus_text = cells[1], cells[2]
            expires_on = _parse_expiration(cells[3])
            if (
                not 2 <= len(partner) <= 80
                or not _BONUS_PATTERN.fullmatch(bonus_text)
                or expires_on is None
                or not today <= expires_on <= latest_reasonable_expiration
            ):
                continue

            identity = (partner.casefold(), bonus_text.casefold(), expires_on)
            if identity in seen:
                continue
            seen.add(identity)
            bonuses.append(
                TransferBonus(
                    partner=partner,
                    bonus=bonus_text,
                    expires_on=expires_on,
                )
            )

    ordered = sorted(bonuses, key=lambda item: (item.expires_on, item.partner))
    return tuple(ordered[:8])

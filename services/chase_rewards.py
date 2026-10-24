from __future__ import annotations

from dataclasses import dataclass
import datetime
import re

from bs4 import BeautifulSoup, Tag


CHASE_FREEDOM_CALENDAR_URL = (
    "https://creditcards.chase.com/cash-back-credit-cards/freedom/flex"
)
CHASE_TRANSFER_BONUS_URL = (
    "https://www.pointsyeah.com/insider/deals/current-credit-card-transfer-bonuses"
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
_HEADINGS = re.compile(r"^h[1-6]$")
_YEAR_PATTERN = re.compile(r"\b20\d{2}\b")


@dataclass(frozen=True, slots=True)
class TransferBonus:
    partner: str
    bonus: str
    expires_on: datetime.date


@dataclass(frozen=True, slots=True)
class FreedomQuarter:
    quarter: int
    categories: tuple[str, ...]


def _clean_text(value: str) -> str:
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = re.sub(r"[\u00ae\u2120\u2122]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\s+\)", ")", value)


def _quarter_label(value: str) -> tuple[int, int | None] | None:
    value = _clean_text(value).lower()
    year_match = _YEAR_PATTERN.search(value)
    year = int(year_match[0]) if year_match else None
    if year_match:
        if value[year_match.end() :].strip():
            return None
        value = value[: year_match.start()].rstrip(" ,")
    value = re.sub(r"\s+", "", value)
    for quarter, label in _QUARTER_LABELS.items():
        start, end = label.lower().split(" - ")
        if value in (f"{start}-{end}", f"{start[:3]}-{end[:3]}"):
            return quarter, year
    return None


def _calendar_year(heading: Tag) -> int | None:
    # Look backward only within the nearest enclosing calendar, not across
    # unrelated sibling sections or at the first matching month on the page.
    for parent in heading.parents:
        year = None
        found_calendar = False
        for previous in parent.find_all(_HEADINGS):
            if previous is heading:
                break
            text = _clean_text(previous.get_text(" ", strip=True)).lower()
            if "calendar" in text:
                # A marker belongs to its smallest container with quarter
                # headings. A calendar in a sibling section cannot date this one.
                scope = next(
                    (
                        ancestor for ancestor in previous.parents
                        if any(
                            _quarter_label(item.get_text(" ", strip=True))
                            for item in ancestor.find_all(_HEADINGS)
                        )
                    ),
                    None,
                )
                if scope is not parent:
                    continue
                found_calendar = True
                years = _YEAR_PATTERN.findall(text)
                year = int(years[0]) if len(years) == 1 else None
        if found_calendar:
            return year
    return None


def _quarter_content(heading: Tag) -> tuple[int | None, tuple[str, ...]]:
    year = None
    candidates: list[str] = []
    for node in heading.next_elements:
        if not any(parent is heading.parent for parent in node.parents):
            break
        if not isinstance(node, Tag):
            continue
        if _HEADINGS.fullmatch(node.name):
            break
        if node.name not in ("p", "li"):
            continue
        if node.find_parent("li") is not None:
            continue
        text = _clean_text(node.get_text(" ", strip=True))
        if _YEAR_PATTERN.fullmatch(text):
            year = int(text)
            continue
        lowered = text.lower()
        if lowered.startswith("coming soon"):
            return year, ()
        if (
            not text
            or lowered.startswith(_BOILERPLATE_PREFIXES)
            or lowered in ("previous quarter", "current quarter", "next quarter")
        ):
            continue
        candidates.extend(_clean_text(part) for part in text.split("|"))
        # A paragraph is one complete legacy category block; list items form
        # the newer card layout. Never scan unrelated copy for a later pipe.
        if node.name == "p":
            break

    categories = tuple(dict.fromkeys(candidates))
    if not 1 <= len(categories) <= 8 or any(
        not 2 <= len(category) <= 80 for category in categories
    ):
        return year, ()
    return year, categories


def parse_chase_freedom_quarters(
    html: str,
    today: datetime.date,
) -> tuple[FreedomQuarter, ...]:
    """Extract this year's remaining quarters and next year's Q1 at year-end."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(
        "script, style, noscript, sup, .visually-hidden, .sr-only, "
        ".od-links--no-color, [aria-hidden='true']"
    ):
        node.decompose()

    current_quarter = (today.month - 1) // 3 + 1
    disclosed: dict[tuple[int, int], tuple[int, tuple[str, ...]]] = {}
    for heading in soup.find_all(_HEADINGS):
        label = _quarter_label(heading.get_text(" ", strip=True))
        if label is None:
            continue
        quarter, heading_year = label
        local_year, categories = _quarter_content(heading)
        if heading_year and local_year and heading_year != local_year:
            continue
        explicit_year = heading_year or local_year
        year = explicit_year or _calendar_year(heading)
        eligible = (
            year == today.year and quarter >= current_quarter
        ) or (
            current_quarter == 4 and year == today.year + 1 and quarter == 1
        )
        if not eligible or year is None:
            continue
        key = (year, quarter)
        priority = int(explicit_year is not None)
        # Explicitly dated cards supersede the legacy calendar, even when the
        # card says Coming Soon. The live page can contain both layouts.
        if key not in disclosed or priority > disclosed[key][0]:
            disclosed[key] = (priority, categories)
    return tuple(
        FreedomQuarter(quarter, categories)
        for (_, quarter), (_, categories) in sorted(disclosed.items())
        if categories
    )


def parse_chase_freedom_categories(
    html: str,
    today: datetime.date,
) -> tuple[str, ...] | None:
    """Extract current-quarter categories for callers needing the legacy shape."""
    current_quarter = (today.month - 1) // 3 + 1
    for item in parse_chase_freedom_quarters(html, today):
        if item.quarter == current_quarter:
            return item.categories
    return None


def _parse_expiration(value: str) -> datetime.date | None:
    for date_format in ("%m/%d/%y", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def parse_chase_transfer_bonuses(
    html: str,
    today: datetime.date,
) -> tuple[TransferBonus, ...]:
    """Extract active Chase rows from the current offers, not historical offers."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[list[str]] = []
    found_current_widget = False
    for title in soup.find_all(
        string=re.compile(r"^\s*Current Credit Card Transfer Bonuses\s*$", re.I)
    ):
        for container in title.parents:
            if container.name in ("body", "html", "[document]"):
                break
            children = container.find_all(recursive=False)
            has_header = any(
                tuple(
                    _clean_text(cell.get_text(" ", strip=True)).lower()
                    for cell in child.find_all(recursive=False)
                )
                == ("transfer from", "transfer to", "bonus", "offer ends")
                for child in children
            )
            if not has_header:
                continue
            found_current_widget = True
            rows.extend(
                [
                    _clean_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(recursive=False)
                ]
                for row in container.find_all(
                    attrs={"data-bonus-row": "true"}, recursive=False
                )
            )
            break

    page_text = _clean_text(soup.get_text(" ", strip=True))
    current_marker = (
        f"current credit card transfer bonuses: {today.strftime('%B %Y')}"
    ).lower()
    if not found_current_widget and current_marker not in page_text.lower():
        return ()

    if not found_current_widget:
        # Older articles embed their HTML table in the escaped Next.js payload.
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
        for fragment in table_fragments:
            table = BeautifulSoup(fragment, "html.parser")
            rows.extend(
                [
                    _clean_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"], recursive=False)
                ]
                for row in table.find_all("tr")
            )

    bonuses: list[TransferBonus] = []
    seen: set[tuple[str, str, datetime.date]] = set()
    latest_reasonable_expiration = today + datetime.timedelta(days=120)
    for cells in rows:
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

from __future__ import annotations

import datetime
import unittest

from services.chase_rewards import (
    FreedomQuarter,
    TransferBonus,
    parse_chase_freedom_categories,
    parse_chase_freedom_quarters,
    parse_chase_transfer_bonuses,
)


CALENDAR_HTML = """
<main>
  <h3>Your 5% quarterly cash back 2026 calendar</h3>
  <h4>January &ndash; March</h4>
  <p>Norwegian Cruise Line® | Dining | the American Heart Association®</p>
  <p>Activation period closed</p>
  <h4>April &ndash; June</h4>
  <p>Amazon | Chase Travel℠ | Feeding America®</p>
  <h4>July &ndash; September</h4>
  <p>Gas Stations | Public Transit | EV Charging | Select Live Entertainment | United Way®</p>
  <p>Activate starting June 15, 2026</p>
  <h4>October &ndash; December</h4>
  <p>Coming Soon</p>
</main>
"""

TRANSFER_HTML = r"""
<html>
  <h1>Current credit card transfer bonuses: July 2026</h1>
  <script>
    self.__next_f.push([1,"content \u003ctable\u003e
    \u003ctr\u003e\u003ctd\u003eTransferable rewards\u003c/td\u003e
    \u003ctd\u003eTransfer partner\u003c/td\u003e
    \u003ctd\u003eTransfer bonus\u003c/td\u003e
    \u003ctd\u003eExpires\u003c/td\u003e\u003c/tr\u003e
    \u003ctr\u003e\u003ctd\u003eChase Ultimate Rewards\u003c/td\u003e
    \u003ctd\u003eIHG\u003c/td\u003e\u003ctd\u003e100%\u003c/td\u003e
    \u003ctd\u003e07/31/26\u003c/td\u003e\u003c/tr\u003e
    \u003ctr\u003e\u003ctd\u003eAmex Membership Rewards\u003c/td\u003e
    \u003ctd\u003eHilton\u003c/td\u003e\u003ctd\u003e20%\u003c/td\u003e
    \u003ctd\u003e07/31/26\u003c/td\u003e\u003c/tr\u003e
    \u003c/table\u003e"])
  </script>
</html>
"""


# Reduced from the live Chase card markup observed on October 23, 2026.
QUARTER_CARDS_HTML = """
<section>
  <div>
    <h2>JUL-SEP</h2><p>2026</p><p>prEVIOUS Quarter</p>
    <ul><li>Gas Stations</li><li>Public Transit</li></ul>
  </div>
  <div>
    <h2>OCT-DEC</h2><p>2026</p><p>CURRENT Quarter</p>
    <ul class="current-quarter">
      <li>Grocery Stores<br><span class="bracket">(excluding Walmart
        <sup>®</sup> and Target<sup>®</sup>)
        <span class="od-links--no-color"><a href="#offerpop">*
          <span class="visually-hidden">Opens offer details overlay</span>
        </a></span></span></li>
      <li>Dining</li>
      <li>American Red Cross<sup>®</sup>
        <span class="od-links--no-color"><a href="#offerpop">*</a></span></li>
      <li><span class="activate-state">Activate starting<br>
        September 15, 2026</span></li>
    </ul>
    <p>Activate starting September 15, 2026</p>
  </div>
  <div>
    <h2>JAN-MAR</h2><p>2027</p><p>NEXT Quarter</p>
    <ul><li>Grocery Stores</li><li>Top Streaming Services</li></ul>
    <p>Activate starting December 15, 2026</p>
  </div>
  <div><h2>APR-JUN</h2><p>2027</p><p>Coming Soon</p></div>
</section>
"""


def transfer_row(
    partner="IHG",
    bonus="100%",
    expires="October 31, 2026",
    program="Chase Ultimate Rewards",
):
    return (
        '<div data-bonus-row="true" data-testid="bonus-row">'
        f"<div>{program}</div><div>{partner}</div>"
        f"<div><div><span>{bonus}</span></div></div>"
        f"<div>{expires}</div></div>"
    )


def current_transfer_widget(rows):
    # The live tracker now uses div rows and an undated title, not a table.
    return (
        '<div id="post-content"><h2>Current Transfer Bonuses</h2><div>'
        "<div><div>Current Credit Card Transfer Bonuses</div></div>"
        "<div><div>Transfer From</div><div>Transfer To</div>"
        "<div>Bonus</div><div>Offer Ends</div></div>"
        f"{rows}</div></div>"
    )


class ChaseRewardsTests(unittest.TestCase):
    def test_live_cards_override_stale_legacy_calendar_in_either_order(self):
        expected = (
            FreedomQuarter(
                4,
                (
                    "Grocery Stores (excluding Walmart and Target)",
                    "Dining",
                    "American Red Cross",
                ),
            ),
            FreedomQuarter(1, ("Grocery Stores", "Top Streaming Services")),
        )
        for html in (
            CALENDAR_HTML + QUARTER_CARDS_HTML,
            QUARTER_CARDS_HTML + CALENDAR_HTML,
        ):
            with self.subTest(html=html[:30]):
                self.assertEqual(
                    parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)),
                    expected,
                )
                self.assertEqual(
                    parse_chase_freedom_categories(html, datetime.date(2026, 10, 23)),
                    expected[0].categories,
                )

    def test_explicit_card_year_does_not_need_global_calendar_marker(self):
        html = """
        <section><h2>October &mdash; December 2026</h2>
          <ul><li>Dining</li></ul></section>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)),
            (FreedomQuarter(4, ("Dining",)),),
        )

    def test_legacy_calendar_scopes_repeated_months_to_their_year(self):
        html = """
        <main>
          <section><h3>2025 Freedom rewards calendar</h3>
            <h4>October - December</h4><p>Stale | Categories</p></section>
          <section><h3>2026 Freedom rewards <span>calendar</span></h3>
            <h4>October - December</h4><p>Dining</p></section>
          <section><h3>2027 Freedom rewards calendar</h3>
            <h4>October - December</h4><p>Future | Categories</p></section>
        </main>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)),
            (FreedomQuarter(4, ("Dining",)),),
        )

    def test_stale_card_cannot_borrow_year_from_current_calendar(self):
        html = """
        <main><h3>Your 2026 calendar</h3>
          <div><h2>OCT-DEC</h2><p>2025</p><ul><li>Stale</li></ul></div>
          <div><h2>OCT-DEC</h2><p>2026</p><ul><li>Dining</li></ul></div>
        </main>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)),
            (FreedomQuarter(4, ("Dining",)),),
        )

    def test_undated_section_cannot_borrow_year_from_sibling_calendar(self):
        html = """
        <main>
          <section><h3>2026 calendar</h3>
            <h4>January - March</h4><p>Past | Categories</p></section>
          <section><h4>October - December</h4><p>Undated | Categories</p></section>
        </main>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)), (),
        )

    def test_flat_calendars_do_not_reuse_first_matching_month(self):
        html = """
        <h3>2025 quarterly calendar</h3>
        <h4>October - December</h4><p>Stale | Categories</p>
        <h3>2026 quarterly calendar</h3>
        <h4>October - December</h4><p>Dining | Grocery Stores</p>
        <h3>2027 quarterly calendar</h3>
        <h4>October - December</h4><p>Future | Categories</p>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)),
            (FreedomQuarter(4, ("Dining", "Grocery Stores")),),
        )

    def test_conflicting_explicit_years_fail_closed(self):
        html = """
        <div><h2>October - December 2026</h2><p>2025</p>
          <ul><li>Stale</li></ul></div>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)), (),
        )

    def test_activation_year_is_not_the_quarter_year(self):
        html = """
        <div><h2>JAN-MAR</h2><ul><li>Dining</li></ul>
          <p>Activate starting December 15, 2026</p></div>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 1, 1)), (),
        )
        self.assertEqual(
            parse_chase_freedom_quarters(
                QUARTER_CARDS_HTML, datetime.date(2027, 1, 1)
            ),
            (FreedomQuarter(1, ("Grocery Stores", "Top Streaming Services")),),
        )

    def test_split_inline_markup_preserves_entire_pipe_categories(self):
        html = """
        <main><h3>2026 quarterly calendar</h3><h4>April - June</h4>
          <p><b>Amazon | Chase Travel<sup>℠</sup> | Feeding America<sup>®</sup></b></p>
          <p>Activate now</p></main>
        """
        self.assertEqual(
            parse_chase_freedom_categories(html, datetime.date(2026, 4, 1)),
            ("Amazon", "Chase Travel", "Feeding America"),
        )

    def test_explicit_coming_soon_overrides_stale_disclosure(self):
        legacy = CALENDAR_HTML.replace("<p>Coming Soon</p>", "<p>Stale | Categories</p>")
        card = "<div><h2>OCT-DEC</h2><p>2026</p><p>Coming Soon</p></div>"
        for html in (legacy + card, card + legacy):
            with self.subTest(html=html[:30]):
                self.assertIsNone(
                    parse_chase_freedom_categories(html, datetime.date(2026, 10, 23)),
                )

    def test_missing_categories_do_not_leak_outside_card(self):
        html = """
        <main><div><h2>OCT-DEC</h2><p>2026</p><p>Activate now</p></div>
          <footer><p>Privacy | Terms</p></footer></main>
        """
        self.assertEqual(
            parse_chase_freedom_quarters(html, datetime.date(2026, 10, 23)), (),
        )

    def test_card_deduplicates_categories_and_rejects_invalid_sizes(self):
        for categories, expected in (
            ("<li>Dining</li><li>Dining</li>", ("Dining",)),
            ("<li>" + "x" * 81 + "</li>", None),
            ("".join(f"<li>Category {i}</li>" for i in range(9)), None),
        ):
            with self.subTest(categories=categories):
                html = f"<div><h2>OCT-DEC</h2><p>2026</p><ul>{categories}</ul></div>"
                self.assertEqual(
                    parse_chase_freedom_categories(html, datetime.date(2026, 10, 23)),
                    expected,
                )

    def test_extracts_only_current_quarter_categories(self):
        categories = parse_chase_freedom_categories(
            CALENDAR_HTML,
            datetime.date(2026, 7, 29),
        )

        self.assertEqual(
            categories,
            (
                "Gas Stations",
                "Public Transit",
                "EV Charging",
                "Select Live Entertainment",
                "United Way",
            ),
        )

    def test_coming_soon_is_not_treated_as_a_category(self):
        categories = parse_chase_freedom_categories(
            CALENDAR_HTML,
            datetime.date(2026, 10, 1),
        )

        self.assertIsNone(categories)

    def test_disclosed_upcoming_quarter_is_included(self):
        html = CALENDAR_HTML.replace(
            "<p>Coming Soon</p>",
            "<p>PayPal | Department Stores</p>",
        )

        quarters = parse_chase_freedom_quarters(
            html,
            datetime.date(2026, 7, 29),
        )

        self.assertEqual(
            quarters,
            (
                FreedomQuarter(
                    3,
                    (
                        "Gas Stations",
                        "Public Transit",
                        "EV Charging",
                        "Select Live Entertainment",
                        "United Way",
                    ),
                ),
                FreedomQuarter(4, ("PayPal", "Department Stores")),
            ),
        )

    def test_year_end_does_not_repeat_past_quarters(self):
        quarters = parse_chase_freedom_quarters(
            CALENDAR_HTML,
            datetime.date(2026, 10, 1),
        )

        self.assertEqual(quarters, ())

    def test_rejects_calendar_for_the_wrong_year(self):
        categories = parse_chase_freedom_categories(
            CALENDAR_HTML,
            datetime.date(2027, 1, 1),
        )

        self.assertIsNone(categories)

    def test_extracts_only_active_chase_transfer_bonuses(self):
        bonuses = parse_chase_transfer_bonuses(
            TRANSFER_HTML,
            datetime.date(2026, 7, 29),
        )

        self.assertEqual(
            bonuses,
            (
                TransferBonus(
                    partner="IHG",
                    bonus="100%",
                    expires_on=datetime.date(2026, 7, 31),
                ),
            ),
        )

    def test_modern_transfer_widget_filters_deduplicates_and_sorts(self):
        html = current_transfer_widget(
            transfer_row()
            + transfer_row(partner="ihg", bonus="100%")
            + transfer_row(
                partner="Virgin Atlantic", bonus="Up to 30%", expires="10/23/2026"
            )
            + transfer_row(program="Citi ThankYou Rewards")
            + transfer_row(expires="October 22, 2026")
            + transfer_row(expires="October 31, 2027")
            + transfer_row(expires="Not announced")
            + transfer_row(bonus="Ask Chase")
            + transfer_row(partner="X")
            + '<div data-bonus-row="true"><div>Chase Ultimate Rewards</div></div>'
        )
        self.assertEqual(
            parse_chase_transfer_bonuses(html, datetime.date(2026, 10, 23)),
            (
                TransferBonus(
                    "Virgin Atlantic", "Up to 30%", datetime.date(2026, 10, 23)
                ),
                TransferBonus("IHG", "100%", datetime.date(2026, 10, 31)),
            ),
        )

    def test_modern_widget_does_not_require_current_month_title(self):
        html = "<h1>Current Credit Card Transfer Bonuses: Amex, Chase &amp; More</h1>"
        html += "<p>Last updated September 30, 2026</p>"
        html += current_transfer_widget(transfer_row())
        self.assertEqual(
            parse_chase_transfer_bonuses(html, datetime.date(2026, 10, 23)),
            (TransferBonus("IHG", "100%", datetime.date(2026, 10, 31)),),
        )

    def test_modern_widget_excludes_history_even_with_future_expiration(self):
        html = current_transfer_widget(transfer_row(program="Capital One Miles"))
        html += "<h2>Historical Transfer Bonuses</h2><div>" + transfer_row() + "</div>"
        # A legacy hydration table must not override an authoritative live widget.
        html += TRANSFER_HTML.replace("July 2026", "October 2026").replace(
            "07/31/26", "10/31/26"
        )
        self.assertEqual(
            parse_chase_transfer_bonuses(html, datetime.date(2026, 10, 23)), (),
        )

    def test_unlabeled_or_historical_widget_is_not_a_current_offer(self):
        for html in (
            transfer_row(),
            current_transfer_widget(transfer_row()).replace(
                "Current Credit Card Transfer Bonuses", "Historical Transfer Bonuses"
            ),
            current_transfer_widget(transfer_row()).replace("Offer Ends", "Offer Started"),
        ):
            with self.subTest(html=html):
                self.assertEqual(
                    parse_chase_transfer_bonuses(html, datetime.date(2026, 10, 23)), (),
                )

    def test_empty_current_widget_does_not_fall_back_to_hydration_table(self):
        html = current_transfer_widget("") + TRANSFER_HTML
        self.assertEqual(
            parse_chase_transfer_bonuses(html, datetime.date(2026, 7, 29)), (),
        )

    def test_transfer_expiration_window_and_result_limit(self):
        today = datetime.date(2026, 10, 23)
        boundary = today + datetime.timedelta(days=120)
        html = current_transfer_widget(
            transfer_row(partner="Boundary", expires=boundary.strftime("%B %d, %Y"))
            + transfer_row(
                partner="Too far",
                expires=(boundary + datetime.timedelta(days=1)).strftime("%B %d, %Y"),
            )
        )
        self.assertEqual(
            parse_chase_transfer_bonuses(html, today),
            (TransferBonus("Boundary", "100%", boundary),),
        )
        html = current_transfer_widget(
            "".join(transfer_row(partner=f"Partner {i}") for i in reversed(range(10)))
        )
        self.assertEqual(
            [bonus.partner for bonus in parse_chase_transfer_bonuses(html, today)],
            [f"Partner {i}" for i in range(8)],
        )

    def test_rejects_expired_transfer_bonuses(self):
        # Keep the current-month marker valid so this exercises date filtering.
        bonuses = parse_chase_transfer_bonuses(
            TRANSFER_HTML.replace("July 2026", "August 2026"),
            datetime.date(2026, 8, 1),
        )

        self.assertEqual(bonuses, ())

    def test_rejects_stale_month_transfer_page(self):
        bonuses = parse_chase_transfer_bonuses(
            TRANSFER_HTML,
            datetime.date(2026, 6, 30),
        )

        self.assertEqual(bonuses, ())


if __name__ == "__main__":
    unittest.main()

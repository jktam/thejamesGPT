from __future__ import annotations

import datetime
import unittest

from services.chase_rewards import (
    TransferBonus,
    parse_chase_freedom_categories,
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


class ChaseRewardsTests(unittest.TestCase):
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

    def test_rejects_expired_transfer_bonuses(self):
        bonuses = parse_chase_transfer_bonuses(
            TRANSFER_HTML,
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

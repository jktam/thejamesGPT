from __future__ import annotations

import datetime
import unittest

from services.chase_rewards import parse_chase_freedom_categories


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


if __name__ == "__main__":
    unittest.main()

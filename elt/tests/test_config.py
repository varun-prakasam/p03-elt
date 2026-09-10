"""Configuration defaults and their environment overrides.

Every value here is read once at import, so the ConfigMap is the only thing standing between a
default and production. These tests exist to make the defaults deliberate: changing one should
require changing a test that says what it means, not just a number.
"""

import importlib
import unittest
from unittest import mock

from elt import config


class DefaultsTest(unittest.TestCase):
    def reload_with(self, **env):
        with mock.patch.dict("os.environ", env, clear=True):
            return importlib.reload(config)

    def tearDown(self):
        # Other test modules import these values at their own import time; restore the real ones.
        importlib.reload(config)

    def test_defaults_match_the_deployed_configmap(self):
        fresh = self.reload_with()
        self.assertEqual(fresh.PROJECT_ID, "varun-data-engineering")
        self.assertEqual(fresh.BQ_LOCATION, "us-central1")
        self.assertEqual(fresh.RAW_DATASET, "p03_raw")

    def test_backfill_window_is_two_years(self):
        """Long enough for two heat seasons, which is what makes the seasonal story visible."""
        self.assertEqual(self.reload_with().BACKFILL_MONTHS, 24)

    def test_update_lookback_outlives_a_typical_open_request(self):
        """Requests closing months later are caught by the updates walk, not this window."""
        self.assertEqual(self.reload_with().UPDATE_LOOKBACK_DAYS, 30)

    def test_weather_lookback_covers_the_archive_lag(self):
        """Open-Meteo's archive trails by about five days; a fortnight covers it comfortably."""
        fresh = self.reload_with()
        self.assertGreaterEqual(fresh.WEATHER_LOOKBACK_DAYS, 7)

    def test_page_cap_is_off_by_default(self):
        """Daily runs move a few thousand rows; only the backfill ever sets this."""
        self.assertEqual(self.reload_with().MAX_PAGES_PER_RUN, 0)

    def test_coordinates_are_central_park(self):
        fresh = self.reload_with()
        self.assertAlmostEqual(fresh.NYC_LATITUDE, 40.7823, places=3)
        self.assertAlmostEqual(fresh.NYC_LONGITUDE, -73.9654, places=3)
        self.assertEqual(fresh.NYC_TIMEZONE, "America/New_York")


class OverrideTest(unittest.TestCase):
    def reload_with(self, **env):
        with mock.patch.dict("os.environ", env, clear=True):
            return importlib.reload(config)

    def tearDown(self):
        importlib.reload(config)

    def test_strings_are_overridable(self):
        fresh = self.reload_with(
            GCP_PROJECT_ID="other-project", BQ_LOCATION="europe-west1", P03_RAW_DATASET="scratch"
        )
        self.assertEqual(fresh.PROJECT_ID, "other-project")
        self.assertEqual(fresh.BQ_LOCATION, "europe-west1")
        self.assertEqual(fresh.RAW_DATASET, "scratch")

    def test_numbers_are_parsed_as_integers(self):
        """The ConfigMap can only hold strings, so these must not arrive as text."""
        fresh = self.reload_with(
            P03_BACKFILL_MONTHS="6",
            P03_UPDATE_LOOKBACK_DAYS="3",
            P03_WEATHER_LOOKBACK_DAYS="2",
            P03_MAX_PAGES_PER_RUN="12",
        )
        self.assertEqual(fresh.BACKFILL_MONTHS, 6)
        self.assertEqual(fresh.UPDATE_LOOKBACK_DAYS, 3)
        self.assertEqual(fresh.WEATHER_LOOKBACK_DAYS, 2)
        self.assertEqual(fresh.MAX_PAGES_PER_RUN, 12)

    def test_a_non_numeric_override_fails_loudly_at_import(self):
        """Better to crash on startup than to run a backfill with a silently defaulted window."""
        with self.assertRaises(ValueError):
            self.reload_with(P03_MAX_PAGES_PER_RUN="lots")


if __name__ == "__main__":
    unittest.main()

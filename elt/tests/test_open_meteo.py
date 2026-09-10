"""Daily NYC weather from Open-Meteo's archive endpoint.

Two things here are worth pinning down. The date window, because the archive trails real time and
asking for today returns a short array that would land a NULL row. And the timeout, because
Open-Meteo throttles by holding the socket open rather than returning 429 — a single-number timeout
applies per socket operation, so a server that accepts the connection and then dribbles never trips
it. That cost half an hour of a backfill before the timeout was split.
"""

import unittest
from datetime import date, timedelta
from unittest import mock

from elt import config
from elt.sources import open_meteo


def fake_payload(days, start=date(2026, 1, 1)):
    times = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    daily = {"time": times}
    for i, variable in enumerate(open_meteo.DAILY_VARIABLES):
        daily[variable] = [float(i * 100 + n) for n in range(days)]
    return {"daily": daily}


class WeatherResourceTest(unittest.TestCase):
    def setUp(self):
        self.response = mock.Mock()
        self.response.raise_for_status.return_value = None
        self.response.json.return_value = fake_payload(3)
        patcher = mock.patch.object(open_meteo.requests, "get", return_value=self.response)
        self.get = patcher.start()
        self.addCleanup(patcher.stop)

    def params(self):
        return self.get.call_args.kwargs["params"]

    # -- the request ---------------------------------------------------------------------------

    def test_window_ends_yesterday(self):
        """The archive has no entry for today; asking for it yields a row of nulls."""
        list(open_meteo.weather_daily())
        self.assertEqual(self.params()["end_date"], (date.today() - timedelta(days=1)).isoformat())

    def test_daily_run_uses_the_short_lookback(self):
        list(open_meteo.weather_daily())
        expected = date.today() - timedelta(days=1 + config.WEATHER_LOOKBACK_DAYS)
        self.assertEqual(self.params()["start_date"], expected.isoformat())

    def test_backfill_reaches_the_full_history(self):
        list(open_meteo.weather_daily(backfill=True))
        expected = date.today() - timedelta(days=1 + 30 * config.BACKFILL_MONTHS)
        self.assertEqual(self.params()["start_date"], expected.isoformat())

    def test_backfill_window_is_longer_than_the_daily_one(self):
        list(open_meteo.weather_daily(backfill=True))
        backfill_start = self.params()["start_date"]
        list(open_meteo.weather_daily())
        self.assertLess(backfill_start, self.params()["start_date"])

    def test_asks_for_central_park(self):
        list(open_meteo.weather_daily())
        self.assertEqual(self.params()["latitude"], config.NYC_LATITUDE)
        self.assertEqual(self.params()["longitude"], config.NYC_LONGITUDE)

    def test_local_timezone_is_requested(self):
        """Days must be NYC calendar days, or the join to dim_date is off by one at the edges."""
        list(open_meteo.weather_daily())
        self.assertEqual(self.params()["timezone"], config.NYC_TIMEZONE)

    def test_every_declared_variable_is_requested(self):
        list(open_meteo.weather_daily())
        self.assertEqual(set(self.params()["daily"].split(",")), set(open_meteo.DAILY_VARIABLES))

    def test_connect_and_read_timeouts_are_separate(self):
        """A single number is applied per socket operation, so a stalling server never trips it."""
        list(open_meteo.weather_daily())
        timeout = self.get.call_args.kwargs["timeout"]
        self.assertIsInstance(timeout, tuple)
        self.assertEqual(len(timeout), 2)
        connect, read = timeout
        self.assertGreater(connect, 0)
        self.assertGreater(read, 0)

    # -- the rows ------------------------------------------------------------------------------

    def test_parallel_arrays_become_one_row_per_day(self):
        """The API returns arrays keyed by variable, not a list of records."""
        rows = list(open_meteo.weather_daily())
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["observed_on"] for r in rows], ["2026-01-01", "2026-01-02", "2026-01-03"])

    def test_each_row_carries_every_variable_at_its_own_index(self):
        rows = list(open_meteo.weather_daily())
        payload = self.response.json.return_value["daily"]
        for index, row in enumerate(rows):
            for variable in open_meteo.DAILY_VARIABLES:
                self.assertEqual(row[variable], payload[variable][index])

    def test_rows_are_stamped_with_an_extraction_time(self):
        rows = list(open_meteo.weather_daily())
        for row in rows:
            self.assertIn("_extracted_at", row)
            self.assertRegex(row["_extracted_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_nulls_in_the_archive_are_carried_through(self):
        """Recent days settle over time; a provisional gap must land as NULL, not crash."""
        payload = fake_payload(2)
        payload["daily"]["snowfall_sum"] = [None, 1.0]
        self.response.json.return_value = payload
        rows = list(open_meteo.weather_daily())
        self.assertIsNone(rows[0]["snowfall_sum"])

    def test_an_empty_archive_window_yields_nothing(self):
        self.response.json.return_value = {"daily": {v: [] for v in ["time", *open_meteo.DAILY_VARIABLES]}}
        self.assertEqual(list(open_meteo.weather_daily()), [])

    def test_http_failure_is_not_swallowed(self):
        """Silently loading zero rows of weather would leave the marts joining to nothing.

        dlt wraps anything raised inside a resource generator, so what the pipeline actually sees
        is ResourceExtractionError with the transport error as its cause. Either way the run fails
        and the cursor is not committed, which is the behaviour that matters.
        """
        import requests
        from dlt.extract.exceptions import ResourceExtractionError

        self.response.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        with self.assertRaises(ResourceExtractionError) as caught:
            list(open_meteo.weather_daily())
        self.assertIsInstance(caught.exception.__cause__, requests.exceptions.HTTPError)


class ResourceConfigTest(unittest.TestCase):
    def test_merges_on_the_observation_date(self):
        """Re-reading a trailing window is only safe because the merge key is the date."""
        resource = open_meteo.weather_daily()
        self.assertEqual(resource.write_disposition, "merge")
        self.assertEqual(resource.name, "weather_daily")

    def test_observation_date_is_hinted_as_a_date(self):
        """Open-Meteo sends a string; without the hint the marts have to cast to join."""
        columns = open_meteo.weather_daily().columns
        self.assertEqual(columns["observed_on"]["data_type"], "date")

    def test_source_exposes_the_resource(self):
        names = {r.name for r in open_meteo.open_meteo_source().resources.values()}
        self.assertEqual(names, {"weather_daily"})

    def test_source_passes_backfill_through(self):
        with mock.patch.object(open_meteo.requests, "get") as get:
            get.return_value.raise_for_status.return_value = None
            get.return_value.json.return_value = fake_payload(1)
            list(open_meteo.open_meteo_source(backfill=True))
            start = get.call_args.kwargs["params"]["start_date"]
        expected = date.today() - timedelta(days=1 + 30 * config.BACKFILL_MONTHS)
        self.assertEqual(start, expected.isoformat())


if __name__ == "__main__":
    unittest.main()

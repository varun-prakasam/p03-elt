"""The two dlt resources and the source that assembles them.

Both resources land in one table, which only works because their dlt names differ and `table_name`
routes them. That is easy to break by renaming one and easy to miss, because the pipeline still runs
— it just quietly loads half the data. Hence the assertions on names.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from elt import config
from elt.sources import nyc311


class InitialCursorTest(unittest.TestCase):
    """Only consulted on the very first run, but a wrong value here means a wrong backfill."""

    def test_created_cursor_reaches_back_the_configured_window(self):
        value = nyc311._initial_created_date()
        parsed = datetime.strptime(value, nyc311.SOCRATA_TS)
        expected = datetime.now(timezone.utc) - timedelta(days=30 * config.BACKFILL_MONTHS)
        self.assertLess(abs((parsed - expected.replace(tzinfo=None)).total_seconds()), 120)

    def test_updated_cursor_uses_the_shorter_lookback(self):
        value = nyc311._initial_updated_date()
        parsed = datetime.strptime(value, nyc311.SOCRATA_TS)
        expected = datetime.now(timezone.utc) - timedelta(days=config.UPDATE_LOOKBACK_DAYS)
        self.assertLess(abs((parsed - expected.replace(tzinfo=None)).total_seconds()), 120)

    def test_updated_cursor_is_more_recent_than_created_cursor(self):
        self.assertGreater(nyc311._initial_updated_date(), nyc311._initial_created_date())

    def test_format_matches_what_socrata_compares_as_a_literal(self):
        """A zone suffix or missing milliseconds makes $where silently match nothing."""
        for value in (nyc311._initial_created_date(), nyc311._initial_updated_date()):
            self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000$")


class ResourceTest(unittest.TestCase):
    def setUp(self):
        self.seen = []

        def fake_paginate(field, value):
            self.seen.append((field, value))
            yield [{"unique_key": "1", field: value}]

        patcher = mock.patch.object(nyc311, "_paginate", side_effect=fake_paginate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_created_resource_walks_created_date(self):
        rows = list(nyc311.service_requests())
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.seen[0][0], "created_date")

    def test_updates_resource_walks_the_resolution_timestamp(self):
        rows = list(nyc311.service_request_updates())
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.seen[0][0], "resolution_action_updated_date")

    def test_resources_have_distinct_names_but_one_destination_table(self):
        """A dlt source is keyed by resource name; reusing one silently drops a resource."""
        created = nyc311.service_requests()
        updated = nyc311.service_request_updates()
        self.assertNotEqual(created.name, updated.name)
        self.assertEqual(created.table_name, "service_requests")
        self.assertEqual(updated.table_name, "service_requests")

    def test_both_resources_merge_on_the_natural_key(self):
        """Merge on unique_key is what makes the inclusive page boundary safe to re-read."""
        for resource in (nyc311.service_requests(), nyc311.service_request_updates()):
            self.assertEqual(resource.write_disposition, "merge")


class SourceTest(unittest.TestCase):
    def test_backfill_runs_only_the_created_walk(self):
        """Socrata returns current state, so walking created_date already lands every closure."""
        names = {r.name for r in nyc311.nyc311_source(backfill=True).resources.values()}
        self.assertEqual(names, {"requests_by_created"})

    def test_daily_runs_both_walks(self):
        """Without the updates walk, a request opened in January and closed in March stays open."""
        names = {r.name for r in nyc311.nyc311_source(backfill=False).resources.values()}
        self.assertEqual(names, {"requests_by_created", "requests_by_updated"})

    def test_daily_is_the_default(self):
        names = {r.name for r in nyc311.nyc311_source().resources.values()}
        self.assertEqual(len(names), 2)


class PaginationCapTest(unittest.TestCase):
    """MAX_PAGES_PER_RUN is what makes an all-or-nothing backfill resumable."""

    def setUp(self):
        self._page_size = nyc311.PAGE_SIZE
        self._max_pages = nyc311.MAX_PAGES_PER_RUN
        self.addCleanup(setattr, nyc311, "PAGE_SIZE", self._page_size)
        self.addCleanup(setattr, nyc311, "MAX_PAGES_PER_RUN", self._max_pages)

    def test_walk_stops_after_the_configured_number_of_pages(self):
        rows = [{"created_date": f"2026-01-{d:02d}T00:00:00.000"} for d in range(1, 21)]

        def fake_request(params):
            bound = params["$where"].split("'")[1]
            inclusive = ">=" in params["$where"]
            hits = [r for r in rows if (r["created_date"] >= bound if inclusive else r["created_date"] > bound)]
            return hits[: int(params["$limit"])]

        nyc311.PAGE_SIZE = 3
        nyc311.MAX_PAGES_PER_RUN = 2
        with mock.patch.object(nyc311, "_request", side_effect=fake_request):
            pages = list(nyc311._paginate("created_date", rows[0]["created_date"]))
        self.assertEqual(len(pages), 2, "cap did not stop the walk")

    def test_zero_means_run_to_exhaustion(self):
        rows = [{"created_date": f"2026-01-{d:02d}T00:00:00.000"} for d in range(1, 8)]

        def fake_request(params):
            bound = params["$where"].split("'")[1]
            inclusive = ">=" in params["$where"]
            hits = [r for r in rows if (r["created_date"] >= bound if inclusive else r["created_date"] > bound)]
            return hits[: int(params["$limit"])]

        nyc311.PAGE_SIZE = 3
        nyc311.MAX_PAGES_PER_RUN = 0
        with mock.patch.object(nyc311, "_request", side_effect=fake_request):
            seen = [r["created_date"] for page in nyc311._paginate("created_date", rows[0]["created_date"]) for r in page]
        self.assertEqual(set(seen), {r["created_date"] for r in rows})

    def test_an_empty_first_page_ends_the_walk(self):
        """Nothing at or beyond the cursor at all — the source has no rows in range."""
        with mock.patch.object(nyc311, "_request", return_value=[]) as req:
            pages = list(nyc311._paginate("created_date", "2030-01-01T00:00:00.000"))
        self.assertEqual(pages, [])
        self.assertEqual(req.call_count, 1, "must not probe after an empty page")


if __name__ == "__main__":
    unittest.main()

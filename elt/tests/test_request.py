"""Transport retry behaviour for Socrata reads.

The retry exists because of a specific failure that took out a backfill pass: Socrata accepts the
request, starts streaming a 20 MB page, and drops the connection partway through the body. That
surfaces from `response.json()` as a ChunkedEncodingError rather than a status code, so it is
invisible to urllib3's own retry layer, which has already handed the response back by then.

These tests pin the two properties that matter — a transient failure is survived, and a persistent
one is not swallowed — plus the backoff, so nobody "simplifies" the sleep away and turns a throttled
source into a hot loop.
"""

import unittest
from unittest import mock

import requests

from elt.sources import nyc311


class RequestRetryTest(unittest.TestCase):
    def setUp(self):
        # Real sleeps would make this suite take a minute for no benefit.
        patcher = mock.patch.object(nyc311.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def response(self, payload):
        r = mock.Mock()
        r.raise_for_status.return_value = None
        r.json.return_value = payload
        return r

    def test_returns_payload_on_first_attempt(self):
        with mock.patch.object(nyc311.requests, "get", return_value=self.response([{"a": 1}])) as get:
            self.assertEqual(nyc311._request({"$where": "x > 1", "$limit": 1}), [{"a": 1}])
        self.assertEqual(get.call_count, 1)
        self.sleep.assert_not_called()

    def test_survives_a_truncated_body(self):
        """The exact shape that killed backfill pass 4."""
        broken = requests.exceptions.ChunkedEncodingError(
            "Connection broken: IncompleteRead(5979 bytes read, 165 more expected)"
        )
        with mock.patch.object(
            nyc311.requests, "get", side_effect=[broken, self.response([{"a": 1}])]
        ) as get:
            self.assertEqual(nyc311._request({"$where": "x", "$limit": 1}), [{"a": 1}])
        self.assertEqual(get.call_count, 2)
        self.sleep.assert_called_once_with(nyc311.RETRY_BACKOFF_SECONDS)

    def test_survives_an_unparseable_body(self):
        """A truncated body can also arrive as invalid JSON rather than a transport error."""
        with mock.patch.object(
            nyc311.requests,
            "get",
            side_effect=[ValueError("Expecting value"), self.response([])],
        ) as get:
            self.assertEqual(nyc311._request({"$where": "x", "$limit": 1}), [])
        self.assertEqual(get.call_count, 2)

    def test_raises_after_exhausting_attempts(self):
        """A persistent failure must surface. Swallowing it would silently load nothing."""
        boom = requests.exceptions.ConnectionError("refused")
        with mock.patch.object(nyc311.requests, "get", side_effect=boom) as get:
            with self.assertRaises(requests.exceptions.ConnectionError):
                nyc311._request({"$where": "x", "$limit": 1})
        self.assertEqual(get.call_count, nyc311.PAGE_ATTEMPTS)
        self.assertEqual(self.sleep.call_count, nyc311.PAGE_ATTEMPTS - 1)

    def test_never_returns_none_when_attempts_run_out(self):
        """The landmine this function is shaped to avoid.

        _paginate treats a falsy page as "no rows left" and ends the walk, so a None returned from
        here would truncate an extract silently instead of failing it. Exhaustion must leave by the
        raise, never by falling off the end.
        """
        with mock.patch.object(
            nyc311.requests, "get", side_effect=requests.exceptions.ConnectionError("refused")
        ):
            with self.assertRaises(requests.exceptions.ConnectionError):
                result = nyc311._request({"$where": "x", "$limit": 1})
                self.fail(f"returned {result!r} instead of raising")

    def test_backoff_lengthens_between_attempts(self):
        with mock.patch.object(
            nyc311.requests, "get", side_effect=requests.exceptions.ConnectionError("refused")
        ):
            with self.assertRaises(requests.exceptions.ConnectionError):
                nyc311._request({"$where": "x", "$limit": 1})
        delays = [c.args[0] for c in self.sleep.call_args_list]
        self.assertEqual(delays, [10, 20, 30, 40])
        self.assertEqual(delays, sorted(delays), "backoff must not shrink")

    def test_http_error_status_is_retried(self):
        r = mock.Mock()
        r.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
        with mock.patch.object(
            nyc311.requests, "get", side_effect=[r, self.response([{"a": 1}])]
        ) as get:
            self.assertEqual(nyc311._request({"$where": "x", "$limit": 1}), [{"a": 1}])
        self.assertEqual(get.call_count, 2)

    def test_params_are_passed_through_untouched(self):
        params = {"$select": "a,b", "$where": "c >= 'd'", "$order": "c ASC", "$limit": 50000}
        with mock.patch.object(nyc311.requests, "get", return_value=self.response([])) as get:
            nyc311._request(params)
        self.assertEqual(get.call_args.kwargs["params"], params)
        self.assertEqual(get.call_args.args[0], nyc311.ENDPOINT)

    def test_a_read_timeout_is_set(self):
        """An unbounded read is how a throttling source turns into a hung pod."""
        with mock.patch.object(nyc311.requests, "get", return_value=self.response([])) as get:
            nyc311._request({"$where": "x", "$limit": 1})
        self.assertIsNotNone(get.call_args.kwargs.get("timeout"))


class QueryShapeTest(unittest.TestCase):
    """The two callers build different queries; both are load-bearing."""

    def test_page_query_is_inclusive_and_ordered(self):
        with mock.patch.object(nyc311, "_request", return_value=[]) as req:
            nyc311._fetch_page("created_date", "2026-01-01T00:00:00.000")
        params = req.call_args.args[0]
        self.assertEqual(params["$where"], "created_date >= '2026-01-01T00:00:00.000'")
        self.assertEqual(params["$order"], "created_date ASC")
        self.assertEqual(params["$limit"], nyc311.PAGE_SIZE)
        # Selecting explicitly keeps the page small and the schema stable.
        self.assertEqual(params["$select"], ",".join(nyc311.FIELDS))

    def test_probe_query_is_strictly_greater_and_tiny(self):
        """The probe must exclude the boundary, or it can never report the end of the data."""
        with mock.patch.object(nyc311, "_request", return_value=[]) as req:
            nyc311._has_rows_beyond("created_date", "2026-01-01T00:00:00.000")
        params = req.call_args.args[0]
        self.assertEqual(params["$where"], "created_date > '2026-01-01T00:00:00.000'")
        self.assertEqual(params["$limit"], 1)
        self.assertEqual(params["$select"], "created_date")

    def test_probe_reports_presence_as_a_bool(self):
        with mock.patch.object(nyc311, "_request", return_value=[{"created_date": "x"}]):
            self.assertIs(nyc311._has_rows_beyond("created_date", "v"), True)
        with mock.patch.object(nyc311, "_request", return_value=[]):
            self.assertIs(nyc311._has_rows_beyond("created_date", "v"), False)


if __name__ == "__main__":
    unittest.main()

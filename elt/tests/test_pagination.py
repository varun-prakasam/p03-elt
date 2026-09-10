"""Keyset pagination against a fake Socrata.

The walk is the one piece of this pipeline where a wrong answer is silent. A crash gets noticed the
next morning; a page loop that stops one page early just loads less data, passes every dbt test,
and shows a slightly wrong number on the dashboard forever.

Two shapes are easy to get wrong and are both covered below:

  * The boundary is inclusive, so the final page always returns the rows sitting on the cursor and
    never comes back empty. Treating "empty page" as the only terminator hangs the walk; treating
    "did not advance" as the only terminator truncates it whenever rows share a timestamp.
  * Socrata truncates pages under load, so page length says nothing about whether more data exists.

Stdlib unittest on purpose — the loader image installs only dlt and requests, and a test that
forces a test-runner into a production image is a bad trade.

    .venv-dlt/bin/python -m unittest discover -s elt/tests -v
"""

import unittest
from unittest import mock

from elt.sources import nyc311

FIELD = "created_date"


class FakeSocrata:
    """Serves whatever the walk asks for, rather than a scripted sequence of replies.

    Patched in at `_request`, the single point where both the page fetch and the beyond-probe reach
    the network, so the code under test builds its own queries and the fake answers them honestly.

    `truncate_to` cuts every page short. `truncate_first` cuts only the opening pages, which is what
    a load spike actually looks like: a couple of stunted responses, then normal service.
    """

    def __init__(self, rows, truncate_to=None, truncate_first=0):
        self.rows = sorted(rows)
        self.truncate_to = truncate_to
        self.truncate_first = truncate_first
        self.requests = 0
        self.pages_served = 0

    def __call__(self, params):
        self.requests += 1
        where = params["$where"]
        limit = int(params["$limit"])
        inclusive = ">=" in where
        bound = where.split("'")[1]

        hits = [r for r in self.rows if (r[0] >= bound if inclusive else r[0] > bound)]
        hits = hits[:limit]

        # A truncated page is still a prefix of the answer; the server just stops writing early.
        # The one-row probe is never truncated — that is the point of asking for one row.
        if inclusive:
            self.pages_served += 1
            if self.truncate_to is not None:
                hits = hits[: self.truncate_to]
            elif self.pages_served <= self.truncate_first:
                hits = hits[:1]

        return [{FIELD: cursor, "unique_key": key} for cursor, key in hits]


class PaginationTest(unittest.TestCase):
    def setUp(self):
        self._request = nyc311._request
        self._page_size = nyc311.PAGE_SIZE
        self._max_pages = nyc311.MAX_PAGES_PER_RUN

        # A stalled page backs off before retrying. Real sleeps would put a minute into the suite
        # for a delay nothing here is measuring.
        patcher = mock.patch.object(nyc311.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        nyc311._request = self._request
        nyc311.PAGE_SIZE = self._page_size
        nyc311.MAX_PAGES_PER_RUN = self._max_pages

    def walk(self, rows, page_size, truncate_to=None, truncate_first=0, max_pages=0):
        fake = FakeSocrata(rows, truncate_to, truncate_first)
        nyc311._request = fake
        nyc311.PAGE_SIZE = page_size
        nyc311.MAX_PAGES_PER_RUN = max_pages

        start = min(r[0] for r in rows)
        seen = []
        for page in nyc311._paginate(FIELD, start):
            seen.extend((r[FIELD], r["unique_key"]) for r in page)
        return seen, fake

    def assertYieldsAll(self, rows, seen):
        self.assertEqual(set(rows), set(seen), "walk did not yield every row")

    # -- ordinary walks ----------------------------------------------------------------------

    def test_multi_page_walk_with_distinct_timestamps(self):
        rows = [
            (f"2026-01-{day:02d}T00:00:{sec:02d}.000", f"k{day}-{sec}")
            for day in range(1, 11)
            for sec in range(10)
        ]
        seen, _ = self.walk(rows, page_size=7)
        self.assertYieldsAll(rows, seen)

    def test_single_row(self):
        rows = [("2026-01-01T00:00:00.000", "only")]
        seen, _ = self.walk(rows, page_size=5)
        self.assertYieldsAll(rows, seen)

    def test_data_ends_exactly_on_a_page_boundary(self):
        """The shape that broke the nightly run: the walk catches up and the last page is full."""
        rows = [(f"2026-01-01T00:00:{sec:02d}.000", f"k{sec}") for sec in range(5)]
        seen, _ = self.walk(rows, page_size=5)
        self.assertYieldsAll(rows, seen)

    def test_walk_ends_on_tied_timestamps(self):
        """Trailing rows share the final timestamp, so the last page cannot advance the cursor."""
        rows = [(f"2026-01-01T00:00:{sec:02d}.000", f"k{sec}") for sec in range(4)]
        rows += [("2026-01-01T00:00:09.000", f"tie{i}") for i in range(3)]
        seen, _ = self.walk(rows, page_size=5)
        self.assertYieldsAll(rows, seen)

    def test_short_pages_are_not_mistaken_for_the_end(self):
        rows = [(f"2026-01-{day:02d}T00:00:00.000", f"k{day}") for day in range(1, 21)]
        seen, _ = self.walk(rows, page_size=8, truncate_to=3)
        self.assertYieldsAll(rows, seen)

    # -- the wedge ---------------------------------------------------------------------------

    def wedged_rows(self):
        """More rows on one timestamp than a page holds, with real data beyond them."""
        rows = [("2026-01-01T00:00:00.000", f"tie{i}") for i in range(12)]
        rows.append(("2026-06-01T00:00:00.000", "later"))
        return rows

    def test_a_stalled_page_is_retried_rather_than_treated_as_a_wedge(self):
        """The case that took the nightly run down.

        Socrata truncated a page so that it stopped on the boundary row, while a full day of data
        sat behind it. "Did not advance" and "rows exist beyond" were both true, and reading that
        pair as a wedge failed the extract on a page that a retry sails straight through. Only one
        row shared the boundary timestamp — a real wedge needs fifty thousand.
        """
        rows = [(f"2026-01-{day:02d}T00:00:00.000", f"k{day}") for day in range(1, 13)]
        seen, fake = self.walk(rows, page_size=5, truncate_first=2)
        self.assertYieldsAll(rows, seen)

    def test_wedge_raises_when_page_is_full(self):
        with self.assertRaises(RuntimeError):
            self.walk(self.wedged_rows(), page_size=5)

    def test_wedge_raises_when_page_is_truncated(self):
        """The case page length cannot see.

        A truncated page from a wedged walk is short, exactly as an exhausted walk's page is short,
        so a length test reads the wedge as the end of the data and drops the rest of the tie plus
        everything after it without a word. Only asking the source resolves it.
        """
        with self.assertRaises(RuntimeError):
            self.walk(self.wedged_rows(), page_size=5, truncate_to=3)

    # -- resumption --------------------------------------------------------------------------

    def test_chunked_backfill_loses_nothing_across_passes(self):
        """MAX_PAGES_PER_RUN stops a run early so dlt can commit; the next run resumes inclusively."""
        rows = [
            (f"2026-01-{day:02d}T00:00:{sec:02d}.000", f"k{day}-{sec}")
            for day in range(1, 16)
            for sec in range(4)
        ]
        fake = FakeSocrata(rows)
        nyc311._request = fake
        nyc311.PAGE_SIZE = 7
        nyc311.MAX_PAGES_PER_RUN = 2

        cursor = min(r[0] for r in rows)
        seen = []
        for _ in range(50):
            batch = []
            for page in nyc311._paginate(FIELD, cursor):
                batch.extend((r[FIELD], r["unique_key"]) for r in page)
            seen.extend(batch)
            advanced = max(c for c, _ in batch)
            if advanced == cursor:
                break
            cursor = advanced

        self.assertYieldsAll(rows, seen)

    def test_probe_is_asked_once_per_walk(self):
        """The extra request is a tail cost, not a per-page one."""
        rows = [
            (f"2026-01-{day:02d}T00:00:00.000", f"k{day}") for day in range(1, 21)
        ]
        _, fake = self.walk(rows, page_size=5)
        pages = -(-len(rows) // 4)  # inclusive boundary re-reads one row, so 4 new rows per page
        self.assertLessEqual(fake.requests, pages + 2)


if __name__ == "__main__":
    unittest.main()

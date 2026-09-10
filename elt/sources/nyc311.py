"""NYC 311 Service Requests, from the Socrata Open Data API.

Dataset `erm2-nwe9`. No API key is required; an app token only raises the rate limit, and the
volumes here stay well inside the anonymous allowance.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import dlt
import requests

from elt.config import BACKFILL_MONTHS, MAX_PAGES_PER_RUN, UPDATE_LOOKBACK_DAYS

log = logging.getLogger("p03.elt.nyc311")

ENDPOINT = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

# Socrata caps a single response at 50,000 rows.
PAGE_SIZE = 50_000

FIELDS = [
    "unique_key",
    "created_date",
    "closed_date",
    "resolution_action_updated_date",
    "agency",
    "agency_name",
    "complaint_type",
    "descriptor",
    "location_type",
    "incident_zip",
    "borough",
    "status",
    "open_data_channel_type",
    "latitude",
    "longitude",
]

# Floating timestamps, no zone. Socrata compares them as literals in $where.
SOCRATA_TS = "%Y-%m-%dT%H:%M:%S.000"

PAGE_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 10

# How many consecutive pages may fail to advance the cursor before the walk gives up. A truncated
# page that stops on the boundary is transient; a tie block larger than a page is permanent. Only
# repetition tells them apart.
PAGE_STALL_LIMIT = 4


def _request(params: dict) -> list:
    """One Socrata read, retried on transport failure.

    Socrata drops connections mid-body under load — a 50,000-row page is ~20 MB and a truncated
    read surfaces as ChunkedEncodingError, not a status code, so nothing in urllib3's own retry
    layer covers it. Without this the nightly run dies on a blip that a second attempt would sail
    through, and a chunked backfill loses its whole pass.

    Retrying is safe because every request here is a pure keyset read: same $where, same rows,
    no server-side state.
    """
    last_error = None
    for attempt in range(1, PAGE_ATTEMPTS + 1):
        try:
            response = requests.get(ENDPOINT, params=params, timeout=180)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < PAGE_ATTEMPTS:
                delay = RETRY_BACKOFF_SECONDS * attempt
                log.warning(
                    "socrata read %r failed (attempt %d/%d): %s; retrying in %ds",
                    params.get("$where"),
                    attempt,
                    PAGE_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)

    # Exhaustion leaves the loop rather than raising inside it, so this function has no path that
    # falls off the end returning None. That matters more than it looks: _paginate reads a falsy
    # result as "no rows left" and stops the walk, so a None here would end an extract early and
    # silently — the same shape of bug as a page loop that terminates one page too soon.
    raise last_error


def _fetch_page(cursor_field: str, cursor_value: str) -> list:
    return _request(
        {
            "$select": ",".join(FIELDS),
            "$where": f"{cursor_field} >= '{cursor_value}'",
            "$order": f"{cursor_field} ASC",
            "$limit": PAGE_SIZE,
        }
    )


def _has_rows_beyond(cursor_field: str, cursor_value: str) -> bool:
    """Whether any row sorts strictly after the boundary.

    This is the only reliable way to tell "the walk is finished" from "the walk is wedged". Both
    look identical from the page alone — a page whose last cursor equals its first — and page
    length cannot break the tie, because Socrata truncates pages under load, so a wedged walk can
    return a short page just as an exhausted one does.

    One row, one column, asked once at the end of a walk.
    """
    return bool(
        _request(
            {
                "$select": cursor_field,
                "$where": f"{cursor_field} > '{cursor_value}'",
                "$order": f"{cursor_field} ASC",
                "$limit": 1,
            }
        )
    )


def _initial_created_date() -> str:
    start = datetime.now(timezone.utc) - timedelta(days=30 * BACKFILL_MONTHS)
    return start.strftime(SOCRATA_TS)


def _initial_updated_date() -> str:
    start = datetime.now(timezone.utc) - timedelta(days=UPDATE_LOOKBACK_DAYS)
    return start.strftime(SOCRATA_TS)


def _paginate(cursor_field: str, cursor_value: str):
    """Walk the dataset in cursor order, one page at a time.

    Keyset pagination, not $offset. A 24-month backfill is roughly 5.8M rows; paging that with an
    offset means Socrata re-sorts and skips millions of rows on every later page, which gets slow
    enough to time out. Advancing the $where boundary instead keeps every request the same cost.

    The boundary is inclusive so a page ending mid-second cannot drop the rows that share its
    timestamp. That re-reads a handful of rows per page; dlt's incremental dedupes them at the
    boundary and the merge on unique_key makes it idempotent regardless.

    A short page does not end the walk. Socrata returns one whenever a query runs long — during the
    first backfill one came back with 42,461 of the 50,000 asked for — so treating "short" as
    "finished" silently truncates the extract. The walk ends only when a page fails to advance the
    cursor *and* the source confirms nothing lies beyond it, which costs one extra request at the
    very end and cannot lose rows.

    A page that fails to advance while rows do lie beyond it is retried, not trusted. Socrata can
    truncate a response so severely that it stops on the boundary row itself, which is
    indistinguishable in a single observation from a tie block too large to page over. The first
    resolves on the next request and the second never does, so the walk retries a bounded number of
    times and then fails loudly rather than deciding on the strength of one page.
    """
    pages = 0
    stalls = 0
    while True:
        page = _fetch_page(cursor_field, cursor_value)
        if not page:
            return

        yield page
        pages += 1

        next_cursor = page[-1][cursor_field]
        if next_cursor == cursor_value:
            # The page did not move the boundary. Because the boundary is inclusive, this is what
            # the end of the data looks like — the last page always returns the rows sitting exactly
            # on the cursor and never comes back empty — so ask the source whether anything lies
            # past it. Nothing does: the walk is finished.
            if not _has_rows_beyond(cursor_field, cursor_value):
                return

            # Rows do exist beyond, so the walk is not finished; this page simply failed to reach
            # them. Almost always that is Socrata truncating a response under load and cutting it
            # off at the boundary — transient, and the same request a moment later gets a full page.
            #
            # The alternative is a genuine wedge: more rows sharing this one timestamp than a page
            # can hold, which an inclusive boundary can never step over. That needs 50,000 requests
            # in the same second, against a service that takes about 8,000 a day.
            #
            # Retrying covers both. The transient case resolves, and the wedge exhausts the retries
            # and fails loudly. Treating a non-advancing page as a wedge on sight does not: it took
            # the nightly run down on a page that had one row on the boundary and a day of data
            # waiting behind it.
            stalls += 1
            if stalls >= PAGE_STALL_LIMIT:
                raise RuntimeError(
                    f"{cursor_field} failed to advance past {cursor_value!r} across "
                    f"{PAGE_STALL_LIMIT} attempts while rows exist beyond it "
                    f"(last page held {len(page)} rows). Either the source is persistently "
                    f"truncating pages, or more than {PAGE_SIZE} rows share this timestamp and "
                    f"keyset pagination cannot step over them."
                )
            delay = RETRY_BACKOFF_SECONDS * stalls
            log.warning(
                "%s did not advance past %s (page held %d rows) but rows exist beyond it; "
                "retrying in %ds (attempt %d/%d)",
                cursor_field,
                cursor_value,
                len(page),
                delay,
                stalls,
                PAGE_STALL_LIMIT,
            )
            time.sleep(delay)
            continue

        stalls = 0
        cursor_value = next_cursor

        # dlt commits its cursor only when a whole run succeeds, so an uncapped 3.6M-row backfill
        # is all-or-nothing across roughly ninety minutes of HTTP. Stopping cleanly after a set
        # number of pages lets the run commit and the next one resume where it left off.
        if MAX_PAGES_PER_RUN and pages >= MAX_PAGES_PER_RUN:
            log.info(
                "stopping %s walk after %d pages at %s; rerun to continue",
                cursor_field,
                pages,
                cursor_value,
            )
            return


@dlt.resource(
    name="requests_by_created",
    table_name="service_requests",
    write_disposition="merge",
    primary_key="unique_key",
    columns={"created_date": {"partition": True}},
)
def service_requests(
    created_date=dlt.sources.incremental(
        "created_date",
        initial_value=_initial_created_date(),
    ),
):
    """Newly created requests, walking forward on created_date."""
    yield from _paginate("created_date", created_date.last_value)


@dlt.resource(
    name="requests_by_updated",
    table_name="service_requests",
    write_disposition="merge",
    primary_key="unique_key",
)
def service_request_updates(
    resolution_action_updated_date=dlt.sources.incremental(
        "resolution_action_updated_date",
        initial_value=_initial_updated_date(),
    ),
):
    """Requests whose resolution moved, regardless of when they were created.

    This is what makes time-to-close correct. A request opened in January and closed in March is
    invisible to a created_date cursor, so it would otherwise sit in the warehouse permanently
    marked open. Merging on unique_key means these rows update the same table in place.
    """
    yield from _paginate(
        "resolution_action_updated_date",
        resolution_action_updated_date.last_value,
    )


@dlt.source(name="nyc311")
def nyc311_source(backfill: bool = False):
    """Both resources land in the same `service_requests` table.

    They need distinct resource names — a dlt source is keyed by resource name, so reusing one
    would silently drop a resource, and each cursor is stored under its resource name too.
    `table_name` is what routes them to a single destination table.

    The updates walk is skipped during backfill. Socrata returns each request's *current* state,
    not its state at creation, so walking created_date across 24 months already lands every
    closure that has happened so far. Running both would double a 75-minute extract to no effect.
    """
    if backfill:
        return [service_requests]
    return [service_requests, service_request_updates]

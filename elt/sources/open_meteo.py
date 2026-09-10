"""Daily NYC weather observations from Open-Meteo's archive endpoint.

Free, no API key, no attribution requirement beyond crediting the source. The archive trails real
time by roughly five days, which is why the model treats weather as a driver dimension rather than
something to join on for the current day.
"""

from datetime import date, datetime, timedelta, timezone

import dlt
import requests

from elt.config import (
    BACKFILL_MONTHS,
    NYC_LATITUDE,
    NYC_LONGITUDE,
    NYC_TIMEZONE,
    WEATHER_LOOKBACK_DAYS,
)

ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
]


@dlt.resource(
    name="weather_daily",
    write_disposition="merge",
    primary_key="observed_on",
    # Open-Meteo returns the date as a string, which dlt would land as STRING. Two years of daily
    # weather is ~730 rows, so there is nothing here worth partitioning — the hint is only about
    # getting a DATE the marts can join on without casting.
    columns={"observed_on": {"data_type": "date"}},
)
def weather_daily(backfill: bool = False):
    """One row per calendar day.

    Always re-reads a short trailing window rather than tracking a cursor. The archive backfills
    recent days as observations settle, so a strictly forward cursor would lock in provisional
    values. Merging on the date makes the re-read idempotent.
    """
    end = date.today() - timedelta(days=1)
    if backfill:
        start = end - timedelta(days=30 * BACKFILL_MONTHS)
    else:
        start = end - timedelta(days=WEATHER_LOOKBACK_DAYS)

    response = requests.get(
        ENDPOINT,
        params={
            "latitude": NYC_LATITUDE,
            "longitude": NYC_LONGITUDE,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(DAILY_VARIABLES),
            "timezone": NYC_TIMEZONE,
        },
        # Separate connect and read budgets. A single number applies to both, so a server that
        # accepts the connection and then stalls gets the full allowance twice over. Open-Meteo
        # throttles by holding the socket open rather than returning 429, which is exactly the
        # shape of failure a generous read timeout turns into a hang.
        timeout=(10, 60),
    )
    response.raise_for_status()
    daily = response.json()["daily"]

    # The API returns parallel arrays keyed by variable name, not a list of records.
    for index, observed_on in enumerate(daily["time"]):
        row = {"observed_on": observed_on}
        for variable in DAILY_VARIABLES:
            row[variable] = daily[variable][index]
        row["_extracted_at"] = datetime.now(timezone.utc).isoformat()
        yield row


@dlt.source(name="open_meteo")
def open_meteo_source(backfill: bool = False):
    return weather_daily(backfill=backfill)

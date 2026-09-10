"""Runtime configuration, all overridable by environment variable."""

import os

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "varun-data-engineering")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "us-central1")
RAW_DATASET = os.environ.get("P03_RAW_DATASET", "p03_raw")

# How far back the very first run reaches. Later runs resume from the stored cursor and ignore this.
BACKFILL_MONTHS = int(os.environ.get("P03_BACKFILL_MONTHS", "24"))

# A request created months ago can close today. Incrementing on created_date alone would never see
# that closure, which would silently break every time-to-close measure. Each run therefore also
# re-reads requests whose resolution timestamp moved inside this window.
UPDATE_LOOKBACK_DAYS = int(os.environ.get("P03_UPDATE_LOOKBACK_DAYS", "30"))

# Open-Meteo's archive endpoint trails real time by roughly five days, and NYC 311 by about two.
# Re-reading a fortnight of weather each run costs one request and removes the edge case entirely.
WEATHER_LOOKBACK_DAYS = int(os.environ.get("P03_WEATHER_LOOKBACK_DAYS", "14"))

# Pages of 50,000 rows to pull per resource per run; 0 means run to exhaustion. Only the initial
# backfill needs this — dlt commits its cursor once per successful run, so an uncapped 3.6M-row
# extract is all-or-nothing over ninety minutes. Daily runs move a few thousand rows and never
# reach the limit.
MAX_PAGES_PER_RUN = int(os.environ.get("P03_MAX_PAGES_PER_RUN", "0"))

# Central Park, the reference station for NYC weather.
NYC_LATITUDE = 40.7823
NYC_LONGITUDE = -73.9654
NYC_TIMEZONE = "America/New_York"

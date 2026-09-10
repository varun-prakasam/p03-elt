"""Entry point for the extract-load half of the pipeline.

Run with `python -m elt.pipeline`. Authentication is Application Default Credentials, which under
Workload Identity resolves to `wl-p03-elt@…` with no key file involved.
"""

import logging
import os
import sys

import dlt
from google.cloud import bigquery

from elt.config import BQ_LOCATION, PROJECT_ID, RAW_DATASET
from elt.sources.nyc311 import nyc311_source
from elt.sources.open_meteo import open_meteo_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p03.elt")

# dlt creates this alongside the raw dataset to stage merge loads. It is not in the platform's
# Terraform, so nothing else manages its lifecycle.
STAGING_DATASET = f"{RAW_DATASET}_staging"
STAGING_TABLE_EXPIRY_MS = 7 * 24 * 60 * 60 * 1000


def build_pipeline() -> dlt.Pipeline:
    # Loads go out as Parquet rather than dlt's default JSONL. Normalising 600,000 rows to JSONL is
    # a Python loop and took seven minutes on the backfill; pyarrow does the same work in well
    # under one, and BigQuery ingests Parquet faster at the other end. The daily run is small
    # enough that it would not have mattered, but the backfill made the difference visible.
    return dlt.pipeline(
        pipeline_name="p03_elt",
        destination=dlt.destinations.bigquery(location=BQ_LOCATION),
        dataset_name=RAW_DATASET,
        progress="log",
    )


def reap_staging_tables() -> None:
    """Give dlt's staging dataset a table expiry.

    A load killed midway leaves staging tables behind. The raw dataset has no default expiry, so
    without this they accumulate indefinitely and nothing ever reaps them.
    """
    client = bigquery.Client(project=PROJECT_ID)
    dataset_id = f"{PROJECT_ID}.{STAGING_DATASET}"
    try:
        dataset = client.get_dataset(dataset_id)
    except Exception:
        log.info("staging dataset %s does not exist yet, skipping", dataset_id)
        return

    if dataset.default_table_expiration_ms == STAGING_TABLE_EXPIRY_MS:
        return

    dataset.default_table_expiration_ms = STAGING_TABLE_EXPIRY_MS
    client.update_dataset(dataset, ["default_table_expiration_ms"])
    log.info("set 7-day default table expiry on %s", dataset_id)


def main() -> int:
    backfill = os.environ.get("P03_BACKFILL", "").lower() in {"1", "true", "yes"}

    # Which sources to run. The daily job runs both; this exists for the backfill, which walks 311
    # in chunks over many passes and has no reason to re-pull two years of weather each time.
    # Doing so got the run throttled by Open-Meteo into a connection that hung for half an hour —
    # 730 rows of weather are the same 730 rows on every pass.
    selected = {
        s.strip() for s in os.environ.get("P03_SOURCES", "nyc311,open_meteo").split(",") if s.strip()
    }
    pipeline = build_pipeline()

    if "nyc311" in selected:
        log.info("loading NYC 311 service requests (backfill=%s)", backfill)
        info = pipeline.run(nyc311_source(backfill=backfill), loader_file_format="parquet")
        log.info("311 load: %s", info)

    if "open_meteo" in selected:
        log.info("loading NYC daily weather (backfill=%s)", backfill)
        info = pipeline.run(open_meteo_source(backfill=backfill), loader_file_format="parquet")
        log.info("weather load: %s", info)

    reap_staging_tables()
    return 0


if __name__ == "__main__":
    sys.exit(main())

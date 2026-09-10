"""Confirm Workload Identity resolves and BigQuery is reachable.

Run inside the cluster:
    kubectl -n p03-elt exec deploy/p03-dev -- /opt/dlt-venv/bin/python -m elt.healthcheck
"""

import google.auth
from google.cloud import bigquery

from elt.config import BQ_LOCATION, PROJECT_ID, RAW_DATASET


def main() -> int:
    credentials, detected_project = google.auth.default()
    print(f"detected project : {detected_project}")
    print(f"identity         : {getattr(credentials, 'service_account_email', 'unknown')}")

    client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
    row = next(iter(client.query("select session_user() as who").result()))
    print(f"bigquery sees    : {row['who']}")

    dataset = client.get_dataset(f"{PROJECT_ID}.{RAW_DATASET}")
    tables = list(client.list_tables(dataset))
    print(f"{RAW_DATASET} location : {dataset.location}")
    print(f"{RAW_DATASET} tables   : {[t.table_id for t in tables] or 'none yet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

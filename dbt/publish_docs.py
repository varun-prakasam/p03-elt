"""Upload the generated dbt docs site to the artifacts bucket.

Run after `dbt docs generate`, from the dbt virtualenv. Uses google-cloud-storage rather than
gsutil because the container image is python:3.11-slim and does not carry the gcloud SDK.

The lineage graph is the single most legible artifact this project produces — it shows the path
from the Socrata API through the star schema to the published dashboard — so it is worth having
somewhere permanent rather than only on whoever last ran dbt locally.
"""

import logging
import os
import sys
from pathlib import Path

from google.cloud import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("p03.docs")

BUCKET = os.environ.get("P03_DOCS_BUCKET", "varun-data-engineering-artifacts")
PREFIX = os.environ.get("P03_DOCS_PREFIX", "p03/docs")
TARGET_DIR = Path(os.environ.get("DBT_TARGET_DIR", "target"))

# dbt writes far more than the site into target/. Only these four make up the docs.
SITE_FILES = ["index.html", "manifest.json", "catalog.json", "run_results.json"]

CONTENT_TYPES = {".html": "text/html", ".json": "application/json"}


def main() -> int:
    missing = [name for name in SITE_FILES[:3] if not (TARGET_DIR / name).exists()]
    if missing:
        log.error("missing %s in %s — run `dbt docs generate` first", missing, TARGET_DIR)
        return 1

    client = storage.Client()
    bucket = client.bucket(BUCKET)

    for name in SITE_FILES:
        path = TARGET_DIR / name
        if not path.exists():
            continue
        blob = bucket.blob(f"{PREFIX}/{name}")
        blob.upload_from_filename(
            str(path), content_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        )
        log.info("uploaded gs://%s/%s/%s (%d bytes)", BUCKET, PREFIX, name, path.stat().st_size)

    return 0


if __name__ == "__main__":
    sys.exit(main())

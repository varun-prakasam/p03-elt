#!/bin/sh
# Extract-load then transform, in one container. An initContainer would give the same ordering
# guarantee but re-run the whole extract on every dbt retry, because a failed Job creates a new pod.
set -eu

echo "=== dlt: extract and load into p03_raw ==="
/opt/dlt-venv/bin/python -m elt.pipeline

echo "=== dbt: build p03_staging and p03_marts ==="
cd /app/dbt
/opt/dbt-venv/bin/dbt build --target prod

# Freshness is reported, not enforced. The build has already succeeded by this point, and NYC 311
# being two days behind is the city's problem rather than a reason to fail the run and page anyone.
echo "=== dbt: source freshness ==="
/opt/dbt-venv/bin/dbt source freshness --target prod || echo "freshness check reported an issue"

echo "=== dbt: publish docs ==="
/opt/dbt-venv/bin/dbt docs generate --target prod
/opt/dbt-venv/bin/python publish_docs.py

echo "=== done ==="

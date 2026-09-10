# p03-elt — ELT and dimensional modelling

Project 3 of the data platform portfolio. Discipline: **analytics engineering**.

Two live public APIs land in BigQuery through [dlt](https://dlthub.com), are modelled into a star
schema with [dbt](https://docs.getdbt.com), and are published as a dashboard built from SQL and
markdown with [Evidence](https://evidence.dev). One Kubernetes CronJob runs the whole thing daily.

| | |
|---|---|
| Sources | NYC 311 Service Requests (Socrata `erm2-nwe9`), Open-Meteo daily NYC weather |
| Warehouse | `p03_raw` → `p03_staging` → `p03_marts` |
| Schedule | CronJob in namespace `p03-elt`, 09:00 UTC |
| Identity | Workload Identity as `wl-p03-elt@…` — no key files anywhere |

## The question this answers

How long does each city agency take to resolve each kind of complaint, in each borough — and does
that degrade when demand surges or the weather turns?

Time-to-close, rather than the more obvious complaint-volume-versus-weather angle. Volume-versus-
weather is the canonical NYC open-data exercise and is partly an artifact: the legally mandated
Oct 1 – May 31 heat season is a step function that swamps any real temperature effect on
HEAT/HOT WATER complaints. Resolution time is a genuine service-level measure, and it forces the
model to handle the hard part — a request opened in January and closed in March.

## Local development

The pipeline runs in a container in-cluster, but iterating that way costs minutes per edit. Both
tools run locally instead, against the same BigQuery datasets.

**Python.** dbt-core 1.9 supports Python 3.9–3.12. This machine's `python3` resolves to 3.8 only
because the 3.8 framework directory precedes `/usr/local/bin` on `PATH`; 3.13 and 3.14 are also
installed. Neither is right, so `uv` manages a 3.12:

```
uv python install 3.12
uv venv --python 3.12 .venv-dlt
uv venv --python 3.12 .venv-dbt
```

**Installing dependencies.** `--only-binary=:all:` is required, not optional:

```
uv pip install --python .venv-dlt --only-binary=:all: -r elt/requirements.txt
uv pip install --python .venv-dbt --only-binary=:all: -r dbt/requirements.txt
```

Without it the resolver picks `cryptography` 50.x, which publishes no x86_64 macOS wheel and falls
back to compiling from Rust source. There is no Xcode CLT on this machine, so the build fails.
Forbidding source builds makes the resolver backtrack to a version that ships a wheel. The
container image is unaffected — manylinux wheels exist — so `requirements.txt` stays clean.

**Running things:**

```
.venv-dlt/bin/python -m elt.healthcheck    # confirm credentials and dataset access
.venv-dlt/bin/python -m elt.pipeline       # extract and load
cd dbt && ../.venv-dbt/bin/dbt build       # transform
```

**Tests.**

```
uv pip install --python .venv-dlt --only-binary=:all: -r elt/requirements-dev.txt
.venv-dlt/bin/python -m coverage run -m unittest discover -s elt/tests -t .
.venv-dlt/bin/python -m coverage report
```

The suite needs no network and no credentials — a fake Socrata answers the queries the code builds
rather than replaying a scripted sequence of responses, so the pagination logic is exercised for
real. `.coveragerc` enforces **branch** coverage at 100%, which is the setting that matters here:
statement coverage called the retry loop covered while its exhaustion path fell through returning
`None`, and a `None` page reads as "no rows left" and ends an extract early and silently.

That is the failure mode this package has. It does not crash when it goes wrong; it loads slightly
less data than it should, passes every dbt test on what it did load, and shows a plausible number on
the dashboard. `elt/tests/test_pagination.py` is aimed squarely at it.

The walk ends only when a page fails to advance the cursor *and* the source confirms nothing lies
beyond it. Both halves are load-bearing, and three separate situations produce a page that fails to
advance: the walk is genuinely finished, Socrata truncated the response so badly it stopped on the
boundary row, or more rows share one timestamp than a page can hold. Page length does not separate
them, and neither does a single observation of "is there anything beyond" — the last two look
identical until you ask twice. So the walk asks the source, then retries a bounded number of times,
and only then fails. Deciding on one page's evidence cost a nightly run: it declared a wedge on a
boundary holding exactly one row, with a full day of data waiting behind it.

**dbt tests.** 148 data tests and 7 unit tests, run by `dbt build` — which means the nightly CronJob
runs them too, not just a developer. Every model has a grain test, every foreign key a relationships
test, every source a freshness threshold.

The unit tests matter more than the count suggests. A data test asks "is today's data sane" and
cannot tell you whether a `<` should have been `<=`, because real observations rarely land exactly
on a threshold and the totals reconcile either way. The weather bands, the implausible-resolution
rule and the New York timezone conversion are all pinned with fixed inputs instead, at every
boundary and one step either side.

Coverage is deliberately not 100% at the column level, and chasing that number would make the suite
worse. `avg_resolution_hours` is *supposed* to be null on a day with no closures, and the UNKNOWN
borough is *supposed* to have no county — blanket `not_null` assertions on either would fail against
correct data. What is covered is chosen by risk: grains, foreign keys, categorical domains, every
boolean that gates a `where` clause downstream, and every measure's plausible range.

Unit tests execute SQL, so they need a warehouse and cannot run in CI alongside `dbt parse`. They
run in the pipeline, where there is already an identity.

**Backfilling.** The first load is about 7.2M rows over 24 months, which is a different problem from
the daily run's few thousand. dlt commits its incremental cursor once per successful run, so an
uncapped backfill is all-or-nothing across an hour of HTTP — and the first attempt was killed
partway through and lost everything. `P03_MAX_PAGES_PER_RUN` caps a run at that many 50,000-row
pages so it commits and the next run resumes:

```
for i in $(seq 1 16); do
  P03_BACKFILL=true P03_MAX_PAGES_PER_RUN=12 .venv-dlt/bin/python -m elt.pipeline || break
done
```

`P03_BACKFILL=true` also skips the late-closure walk. Socrata returns each request's *current*
state rather than its state at creation, so walking `created_date` across 24 months already lands
every closure that has happened; running both walks would double the time for nothing. Daily runs
need both, and leave `P03_MAX_PAGES_PER_RUN` at its default of 0, meaning no cap.

Local runs authenticate as `terraform-deployer` through impersonated ADC, which is broader than the
`wl-p03-elt` identity the CronJob uses. Something can therefore pass locally and still fail in the
cluster on a missing role. Verify in-cluster before calling anything done.

## Deploying

No GitHub remote yet, so images build from a local tarball and manifests are applied by hand:

```
gcloud builds submit --config=cloudbuild/elt.yaml --substitutions=_TAG=dev .
kubectl apply --server-side --field-manager=argocd-controller -k k8s/overlays/prod
```

The `--field-manager=argocd-controller` flag matters. When this repo is eventually pushed, the
platform's ArgoCD Application takes over `k8s/overlays/prod` with `selfHeal` and `prune` enabled.
Applying under ArgoCD's field manager now means field ownership already matches and the handover
is silent instead of a conflict.

`k8s/dev/` is deliberately outside the overlay so ArgoCD never manages it.

### The dashboard

```
gcloud builds submit --config=cloudbuild/evidence.yaml --substitutions=_TAG=dev .
```

That one config compiles the site, bakes it into nginx and deploys to Cloud Run. Three things about
it are not obvious:

**The Evidence compile is a build step, not a Docker stage.** `evidence sources` runs every source
query against BigQuery and writes the results to parquet, so it needs credentials. A Cloud Build
step reaches the metadata server and picks up Application Default Credentials; a `docker build` does
not, and the only way to give it any would be to hand a container a key file.

**Nothing queries BigQuery at request time.** Evidence ships the materialised parquet to the browser
and runs the page queries in DuckDB-WASM, so the deployed service is static files behind nginx. The
Cloud Run identity needs no data access, the service scales to zero, and a traffic spike costs
nothing in BigQuery.

**The dbt lineage graph is mirrored into the site.** The pipeline publishes `dbt docs` to
`gs://varun-data-engineering-artifacts/p03/docs/`, but that bucket has public access prevention on.
Rather than open it, the build copies the docs into the static output and serves them from
`/dbt-docs/` on the same origin.

The site is only as fresh as its last build, which is why the page header reads `_dlt_loads` rather
than a build timestamp — a frozen pipeline shows up as a warning banner instead of stale numbers
nobody questions.

**There is deliberately no nightly rebuild yet.** The Cloud Build REST API takes inline steps; the
"run this config file from this source" indirection lives in build *triggers*, which need a
connected repository, and in the `gcloud` client. With no GitHub remote the only ways to schedule
this are a build that shells out to another build, or a copy of these eight steps transcribed into
Terraform in the platform repo — one adds a layer to debug through, the other guarantees the two
definitions drift. Both get deleted at the GitHub cutover, when a repo-connected trigger plus
`Scheduler → triggers.run` is three resources and no nesting. Until then the rebuild is the manual
command above, and the freshness banner is what keeps a stale dashboard honest.

## Layout

```
elt/        dlt sources and pipeline entry point
dbt/        dbt project — models, seeds, snapshots, tests, macros
evidence/   Evidence dashboard
docker/     container images
cloudbuild/ Cloud Build configs
k8s/        base + overlays/prod (ArgoCD-managed), dev/ (not)
```

## Status

- [x] Repo, image, namespace, service account, CronJob, local dev environment
- [x] dlt extract and load — 7.4M requests over 24 months, plus daily NYC weather
- [x] dbt dimensional model — 76 models and tests green on a full refresh
- [x] CronJob unsuspended and running daily
- [x] Evidence dashboard on Cloud Run

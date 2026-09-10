-- The periodic snapshot mart, verbatim. Roughly 50,000 rows for two years of
-- date x borough x category, which is small enough to ship to the browser whole and lets every
-- page-level filter run client-side against DuckDB instead of costing another BigQuery scan.
select
    service_date,
    borough_name,
    service_category,
    requests_created,
    requests_closed,
    avg_resolution_hours,
    median_resolution_hours,
    p90_resolution_hours,
    closed_within_1_day,
    closed_within_3_days,
    closures_measured,
    pct_closed_within_3_days,
    temp_mean_f,
    precipitation_in,
    snowfall_in,
    temp_band,
    precip_band,
    snow_band
from `varun-data-engineering.p03_marts.fct_daily_service_levels`
order by service_date

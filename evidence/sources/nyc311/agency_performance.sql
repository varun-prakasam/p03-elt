-- Aggregated in BigQuery, not in the browser. The fact table is millions of rows; this collapses
-- it to a few thousand before anything crosses the wire.
--
-- Medians come from the fact rather than from averaging the daily mart's medians, because a median
-- of medians is not a median.
select
    agency.agency_code,
    agency.agency_name,
    requests.borough_name,
    requests.service_category,

    count(*)                                                        as requests_total,
    countif(requests.is_closed)                                     as requests_closed,
    countif(not requests.is_closed)                                 as requests_open,

    avg(requests.reportable_resolution_hours)                       as avg_resolution_hours,
    approx_quantiles(requests.reportable_resolution_hours, 100)[offset(50)]
                                                                    as median_resolution_hours,
    approx_quantiles(requests.reportable_resolution_hours, 100)[offset(90)]
                                                                    as p90_resolution_hours,

    countif(requests.reportable_resolution_hours is not null)       as closures_measured,
    countif(requests.reportable_resolution_hours <= 72)             as closed_within_3_days,
    safe_divide(
        countif(requests.reportable_resolution_hours <= 72),
        countif(requests.reportable_resolution_hours is not null)
    )                                                               as pct_closed_within_3_days

from `varun-data-engineering.p03_marts.fct_service_requests` as requests
join `varun-data-engineering.p03_marts.dim_agency` as agency
    on requests.agency_key = agency.agency_key
group by 1, 2, 3, 4

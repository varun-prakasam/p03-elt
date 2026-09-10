---
title: NYC 311 Service Levels
description: How long the city takes to close a service request, by agency, borough and category.
---

```sql freshness
select
    last_load_at,
    latest_request_at,
    requests_total,
    date_diff('day', latest_request_at::date, today()::date) as days_behind
from nyc311.pipeline_freshness
```

<Alert status={freshness[0].days_behind > 4 ? 'warning' : 'info'}>

Loaded <Value data={freshness} column=requests_total fmt='#,##0'/> service requests.
Most recent request: <Value data={freshness} column=latest_request_at fmt='mmm d, yyyy'/>
(<Value data={freshness} column=days_behind/> days behind).

</Alert>

NYC 311 publishes every service request the city receives. This dashboard is about the part that is
harder to see than volume: **how long each agency takes to close one**, and whether that degrades
when demand spikes.

Everything below is built from a dimensional model in BigQuery, refreshed nightly from the Socrata
and Open-Meteo APIs. The [lineage graph and test results](/dbt-docs/index.html) are published
alongside it — source API through staging and marts to this page.

## Demand and service, city-wide

```sql citywide_daily
select
    service_date,
    sum(requests_created)   as requests_created,
    sum(requests_closed)    as requests_closed
from nyc311.daily_service_levels
group by service_date
order by service_date
```

<LineChart
    data={citywide_daily}
    x=service_date
    y={['requests_created', 'requests_closed']}
    title="Requests opened and closed per day"
    yAxisTitle="requests"
/>

The two lines tracking each other is the healthy state: the city closes roughly what it opens. Gaps
that persist rather than close are backlog.

## Time to close, by agency

```sql agency_rollup
select
    agency_code,
    agency_name,
    sum(requests_total)                                             as requests_total,
    sum(closures_measured)                                          as closures_measured,
    sum(closed_within_3_days) / nullif(sum(closures_measured), 0)   as pct_closed_within_3_days,
    sum(avg_resolution_hours * closures_measured)
        / nullif(sum(closures_measured), 0)                         as avg_resolution_hours
from nyc311.agency_performance
group by agency_code, agency_name
having sum(requests_total) > 1000
order by requests_total desc
```

<DataTable data={agency_rollup} rows=15>
    <Column id=agency_code title="Agency"/>
    <Column id=agency_name title="Name"/>
    <Column id=requests_total title="Requests" fmt='#,##0'/>
    <Column id=avg_resolution_hours title="Avg hours" fmt='#,##0.0'/>
    <Column id=pct_closed_within_3_days title="Closed ≤ 3 days" fmt=pct1 contentType=bar/>
</DataTable>

Read the averages with the definition in mind. NYPD marks noise and parking complaints closed when a
car is dispatched, so its median is about an hour and measures dispatch, not resolution. TLC and
Parks run to weeks because their work is an actual investigation or a site visit. The comparison
that means something is an agency against its own past, not against another agency.

## Where the work is

```sql by_borough_category
select
    borough_name,
    service_category,
    sum(requests_created) as requests_created
from nyc311.daily_service_levels
where borough_name != 'UNKNOWN'
group by borough_name, service_category
```

<BarChart
    data={by_borough_category}
    x=borough_name
    y=requests_created
    series=service_category
    title="Requests by borough and category"
    yAxisTitle="requests"
/>

## Service level over time

```sql service_level_trend
select
    date_trunc('month', service_date)                               as month,
    service_category,
    sum(closed_within_3_days) / nullif(sum(closures_measured), 0)   as pct_closed_within_3_days
from nyc311.daily_service_levels
group by 1, 2
having sum(closures_measured) > 200
order by 1
```

<LineChart
    data={service_level_trend}
    x=month
    y=pct_closed_within_3_days
    series=service_category
    title="Share of closures within three days, by month"
    yFmt=pct0
/>

## More

- [Agency detail](agencies) — resolution time by agency, borough and category
- [Weather and the heat season](weather) — why the obvious reading of this dataset is wrong

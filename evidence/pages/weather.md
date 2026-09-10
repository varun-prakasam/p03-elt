---
title: Weather and the Heat Season
description: Why the obvious reading of heat complaints against temperature is wrong.
---

The standard NYC open-data exercise correlates 311 complaints with the weather and reports that
heat complaints rise when it gets cold. They do. But the causal story is mostly statutory, not
meteorological.

**New York City law requires residential heat between October 1 and May 31.** Outside that window a
landlord owes no heat, so there is nothing to complain about and the complaint count collapses —
regardless of temperature. Modelling this as a temperature effect attributes a step function to a
curve.

```sql heat_daily
select
    service_date,
    is_heat_season,
    temp_mean_f,
    heat_requests
from nyc311.heat_season
order by service_date
```

<LineChart
    data={heat_daily}
    x=service_date
    y=heat_requests
    title="Heat and hot water complaints per day"
    yAxisTitle="complaints"
/>

The cliff edges land on October 1 and May 31, not on the first cold or warm day of the year.

## Inside the season, temperature does matter

```sql in_season
select
    round(temp_mean_f / 5) * 5      as temp_bucket,
    avg(heat_requests)              as avg_heat_requests,
    count(*)                        as days
from nyc311.heat_season
where is_heat_season
  and temp_mean_f is not null
group by 1
having count(*) >= 5
order by 1
```

```sql out_of_season
select
    round(temp_mean_f / 5) * 5      as temp_bucket,
    avg(heat_requests)              as avg_heat_requests,
    count(*)                        as days
from nyc311.heat_season
where not is_heat_season
  and temp_mean_f is not null
group by 1
having count(*) >= 5
order by 1
```

<BarChart
    data={in_season}
    x=temp_bucket
    y=avg_heat_requests
    title="Heat season (Oct 1 – May 31): complaints by mean temperature"
    xAxisTitle="mean temperature (°F)"
    yAxisTitle="avg complaints per day"
/>

<BarChart
    data={out_of_season}
    x=temp_bucket
    y=avg_heat_requests
    title="Out of season (Jun 1 – Sep 30): complaints by mean temperature"
    xAxisTitle="mean temperature (°F)"
    yAxisTitle="avg complaints per day"
/>

Once the season is held constant the temperature effect is real but far smaller than the raw
correlation implies — and the out-of-season panel shows cold days that produce almost no complaints,
which a temperature-only model cannot explain.

## Weather as a driver of resolution time

Weather earns its place in this model as a *driver dimension*, not as the subject. The question it
answers is whether the city gets slower in bad conditions.

```sql by_weather
select
    temp_band,
    precip_band,
    sum(requests_created)                                           as requests_created,
    sum(closed_within_3_days) / nullif(sum(closures_measured), 0)   as pct_closed_within_3_days
from nyc311.daily_service_levels
where temp_band != 'Unknown'
group by temp_band, precip_band
having sum(closures_measured) > 500
```

<BarChart
    data={by_weather}
    x=temp_band
    y=pct_closed_within_3_days
    series=precip_band
    title="Share of closures within three days, by weather band"
    yFmt=pct0
    type=grouped
/>

```sql snow_days
select
    snow_band,
    sum(requests_created)                                           as requests_created,
    sum(avg_resolution_hours * closures_measured)
        / nullif(sum(closures_measured), 0)                         as avg_resolution_hours
from nyc311.daily_service_levels
where snow_band != 'Unknown'
group by snow_band
```

<DataTable data={snow_days}>
    <Column id=snow_band title="Snowfall"/>
    <Column id=requests_created title="Requests" fmt='#,##0'/>
    <Column id=avg_resolution_hours title="Avg hours to close" fmt='#,##0.0'/>
</DataTable>

{{
    config(
        materialized='table',
        partition_by={'field': 'service_date', 'data_type': 'date', 'granularity': 'month'},
        cluster_by=['borough_name', 'service_category']
    )
}}

-- A periodic snapshot fact at date x borough x category. This is what the dashboard reads: it is
-- roughly 50,000 rows against the fact table's millions, which matters because Evidence executes
-- every query on every page at build time.
--
-- Rebuilt in full rather than incrementally. The whole table costs about one gigabyte to scan and
-- takes seconds; an incremental version would need its own late-arrival window and would be a
-- second place for the same subtle bug to live.

with requests as (

    select * from {{ ref('fct_service_requests') }}

),

-- Demand: what arrived on this day.
created as (

    select
        created_date_local              as service_date,
        borough_name,
        service_category,
        count(*)                        as requests_created
    from requests
    group by 1, 2, 3

),

-- Service: what was resolved on this day. A closure belongs to the day it happened, not the day
-- the request arrived, so this cannot be derived from the block above.
closed as (

    select
        closed_date_local               as service_date,
        borough_name,
        service_category,

        count(*)                                                as requests_closed,
        avg(reportable_resolution_hours)                         as avg_resolution_hours,
        approx_quantiles(reportable_resolution_hours, 100)[offset(50)]
                                                                 as median_resolution_hours,
        approx_quantiles(reportable_resolution_hours, 100)[offset(90)]
                                                                 as p90_resolution_hours,

        countif(reportable_resolution_hours <= 24)               as closed_within_1_day,
        countif(reportable_resolution_hours <= 72)               as closed_within_3_days,
        countif(reportable_resolution_hours is not null)         as closures_measured

    from requests
    where is_closed
      and closed_date_local is not null
    group by 1, 2, 3

),

-- A dense spine. Without it a borough that logged no complaints in a category on a given day has
-- no row at all, and the dashboard's time series silently skips the day rather than plotting zero.
spine as (

    select
        dates.date_key          as service_date,
        locations.borough_name,
        categories.service_category
    from {{ ref('dim_date') }} as dates
    cross join (select distinct borough_name from {{ ref('dim_location') }}) as locations
    cross join (select distinct service_category from {{ ref('dim_complaint_category') }}) as categories
    where dates.date_key between
        (select min(created_date_local) from requests)
        and (select max(created_date_local) from requests)

),

weather as (

    select * from {{ ref('int_weather_banded') }}

),

final as (

    select
        spine.service_date,
        spine.borough_name,
        spine.service_category,

        {{ dbt_utils.generate_surrogate_key(['spine.service_date', 'spine.borough_name', 'spine.service_category']) }}
                                                                as daily_service_level_key,
        {{ dbt_utils.generate_surrogate_key(['spine.borough_name']) }}
                                                                as location_key,
        coalesce(weather.weather_band_key,
                 {{ dbt_utils.generate_surrogate_key(["'Unknown'", "'Unknown'", "'Unknown'"]) }})
                                                                as weather_band_key,

        coalesce(created.requests_created, 0)                   as requests_created,
        coalesce(closed.requests_closed, 0)                     as requests_closed,

        closed.avg_resolution_hours,
        closed.median_resolution_hours,
        closed.p90_resolution_hours,

        coalesce(closed.closed_within_1_day, 0)                 as closed_within_1_day,
        coalesce(closed.closed_within_3_days, 0)                as closed_within_3_days,
        coalesce(closed.closures_measured, 0)                   as closures_measured,

        -- Left null rather than zero when nothing closed. A day with no closures has no service
        -- level; reporting it as 0% compliance would drag every average down with absences.
        safe_divide(closed.closed_within_3_days, closed.closures_measured)
                                                                as pct_closed_within_3_days,

        weather.temp_mean_f,
        weather.precipitation_in,
        weather.snowfall_in,
        weather.temp_band,
        weather.precip_band,
        weather.snow_band

    from spine
    left join created
        on  spine.service_date      = created.service_date
        and spine.borough_name      = created.borough_name
        and spine.service_category  = created.service_category
    left join closed
        on  spine.service_date      = closed.service_date
        and spine.borough_name      = closed.borough_name
        and spine.service_category  = closed.service_category
    left join weather
        on spine.service_date = weather.weather_date

)

select * from final

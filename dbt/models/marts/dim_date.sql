{{ config(materialized='table') }}

-- Generated, not derived from the facts. A date dimension built from observed data has holes on
-- days nothing happened, which turns "zero complaints closed" into "no row" and quietly breaks
-- every time series in the dashboard.

with spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2023-01-01' as date)",
        end_date="date_add(current_date('America/New_York'), interval 1 year)"
    ) }}

),

dates as (

    select cast(date_day as date) as date_day
    from spine

),

enriched as (

    select
        date_day                                                as date_key,

        extract(year    from date_day)                          as calendar_year,
        extract(quarter from date_day)                          as calendar_quarter,
        extract(month   from date_day)                          as calendar_month,
        format_date('%B', date_day)                             as month_name,
        format_date('%Y-%m', date_day)                          as year_month,

        extract(dayofweek from date_day)                        as day_of_week,
        format_date('%A', date_day)                             as day_name,
        extract(dayofweek from date_day) in (1, 7)              as is_weekend,

        date_trunc(date_day, WEEK(MONDAY))                      as week_start_date,
        date_trunc(date_day, MONTH)                             as month_start_date,

        -- New York City law requires residential heat between October 1 and May 31. This is the
        -- single most important flag in the model: it is a step function that drives an enormous
        -- seasonal swing in HEAT/HOT WATER complaints, and any weather analysis that ignores it
        -- will attribute the jump to temperature rather than to the statute.
        case
            when extract(month from date_day) >= 10 then true
            when extract(month from date_day) <= 5  then true
            else false
        end                                                     as is_heat_season,

        case
            when extract(month from date_day) in (12, 1, 2)  then 'Winter'
            when extract(month from date_day) in (3, 4, 5)   then 'Spring'
            when extract(month from date_day) in (6, 7, 8)   then 'Summer'
            else 'Autumn'
        end                                                     as season

    from dates

)

select * from enriched

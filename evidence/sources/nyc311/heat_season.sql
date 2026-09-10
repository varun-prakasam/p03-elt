-- The one chart that argues with the obvious reading of this dataset.
--
-- Heat complaints track temperature, so the tempting conclusion is that cold weather causes them.
-- It mostly does not: New York City law requires landlords to heat residential buildings between
-- October 1 and May 31, and outside that window there is nothing to complain about. The volume is
-- a step function on a statute, not a curve on a thermometer. Joining dim_date's is_heat_season
-- alongside the observed temperature is what lets the dashboard show both at once.
select
    dates.date_key                                                  as service_date,
    dates.is_heat_season,
    weather.temp_mean_f,

    countif(requests.service_subcategory = 'Heat & Hot Water')      as heat_requests,
    countif(requests.service_subcategory != 'Heat & Hot Water')     as other_requests,

    avg(case
            when requests.service_subcategory = 'Heat & Hot Water'
            then requests.reportable_resolution_hours
        end)                                                        as heat_avg_resolution_hours

from `varun-data-engineering.p03_marts.dim_date` as dates
join `varun-data-engineering.p03_marts.fct_service_requests` as requests
    on requests.created_date_local = dates.date_key
left join (
    -- The daily mart repeats each day's weather across every borough and category, so one value
    -- per date is all this needs.
    select
        service_date,
        any_value(temp_mean_f) as temp_mean_f
    from `varun-data-engineering.p03_marts.fct_daily_service_levels`
    group by 1
) as weather
    on weather.service_date = dates.date_key
group by 1, 2, 3
order by 1

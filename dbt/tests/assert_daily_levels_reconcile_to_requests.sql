-- The two fact tables must agree. fct_daily_service_levels is an aggregate of
-- fct_service_requests, so if their totals diverge the aggregate has either double-counted through
-- a bad join or dropped rows through the dense spine's date filter.
--
-- This is the test that would have caught a spine built on the wrong date column, which is the
-- easiest mistake to make in this model and the hardest to see in a chart.

with from_requests as (

    select count(*) as n
    from {{ ref('fct_service_requests') }}
    where created_date_local is not null

),

from_daily as (

    select sum(requests_created) as n
    from {{ ref('fct_daily_service_levels') }}

)

select
    from_requests.n as request_count,
    from_daily.n    as daily_count
from from_requests
cross join from_daily
where from_requests.n != from_daily.n

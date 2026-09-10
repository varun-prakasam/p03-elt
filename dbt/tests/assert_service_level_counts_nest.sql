-- The service-level counters must nest: anything closed within a day is also closed within three,
-- and both are subsets of the closures actually measured.
--
-- Column-level tests cannot express this. Each counter is individually non-negative and individually
-- plausible; the bug this catches is one counter drifting out of step with another — a changed
-- threshold, a filter applied to one countif and not its neighbour — which produces a service-level
-- percentage above 100% or a nonsensical improvement, with every other test still green.
--
-- closures_measured counts rows with a reportable duration, so the two thresholds can only ever
-- count a subset of it.

select
    service_date,
    borough_name,
    service_category,
    closed_within_1_day,
    closed_within_3_days,
    closures_measured,
    requests_closed
from {{ ref('fct_daily_service_levels') }}
where closed_within_1_day > closed_within_3_days
   or closed_within_3_days > closures_measured
   or closures_measured > requests_closed

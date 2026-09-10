-- No milestone timestamp may lie in the future.
--
-- This is a data-quality check with teeth, because these rows do more than look odd. The model is
-- incremental on `max(coalesce(resolution_updated_at, created_at))`, so the newest timestamp in the
-- table sets the lookback window for every subsequent run. One row stamped ahead of the present
-- drags that window into the future, and every genuine update then falls below it and is never
-- merged. The fact stops growing, silently, while every other test stays green.
--
-- That is not hypothetical: exactly one row in the first 7.4 million carried a resolution timestamp
-- three months ahead and froze the model until the watermark was taught to ignore it. The watermark
-- is defended in fct_service_requests.sql; this test is how the bad rows stay visible rather than
-- being quietly tolerated.
--
-- Warns rather than errors. NYC keys these dates by hand and a typo is not a reason to take the
-- nightly build down — but a sudden jump in the count means something upstream changed shape.

{{ config(severity='warn', warn_if='>10') }}

select
    request_id,
    created_at,
    closed_at,
    resolution_updated_at
from {{ ref('fct_service_requests') }}
where created_at > current_timestamp()
   or closed_at > current_timestamp()
   or resolution_updated_at > current_timestamp()

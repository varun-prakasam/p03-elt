{{
    config(
        severity='warn',
        warn_if='>0',
        error_if='>10000'
    )
}}

-- The seed covers every complaint type present in the 24-month backfill, but New York adds
-- programmes and retires them, so new values will appear. That is expected and must not take the
-- nightly build down — the requests still land, just under 'Unmapped'.
--
-- The thresholds are the point. Any unmapped row warns, so the seed gets updated. More than ten
-- thousand means something structural changed at the source, and a dashboard silently reporting a
-- large slice of the city's complaints as 'Unmapped' is worse than a failed build.

select
    complaint_type,
    count(*) as unmapped_requests
from {{ ref('fct_service_requests') }}
where is_unmapped_complaint_type
group by complaint_type

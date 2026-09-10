-- Internal consistency of the accumulating snapshot: a request flagged closed must carry both a
-- closing timestamp and a resolution measure, and an open one must carry neither.
--
-- These are derived in the same model from the same column, so a failure here means the derivation
-- itself broke — a timezone cast changed, or a coalesce swallowed a null. Cheap to check, and it
-- fails loudly rather than producing a dashboard of plausible-looking wrong numbers.

select
    request_id,
    is_closed,
    closed_at,
    resolution_hours
from {{ ref('fct_service_requests') }}
where (is_closed and (closed_at is null or resolution_hours is null))
   or (not is_closed and (closed_at is not null or resolution_hours is not null))

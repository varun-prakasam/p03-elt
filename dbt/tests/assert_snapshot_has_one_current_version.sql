-- Exactly one open version per request in the SCD2 history.
--
-- The defining invariant of a Type 2 table, and the one that breaks silently. A second row with a
-- null dbt_valid_to means an earlier version was never closed off, so any point-in-time join
-- returns two rows where it should return one — every count downstream doubles for that request,
-- and nothing about the table looks wrong.
--
-- Uniqueness on dbt_scd_id cannot catch this: both rows are legitimately distinct versions. The
-- error is in which of them is still considered current.

select
    request_id,
    count(*) as open_versions
from {{ ref('snp_request_status') }}
where dbt_valid_to is null
group by request_id
having count(*) > 1

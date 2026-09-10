{% snapshot snp_request_status %}

{{
    config(
        unique_key='request_id',
        strategy='timestamp',
        updated_at='status_changed_at',
        invalidate_hard_deletes=false
    )
}}

-- Type 2 history of a request's status. This is what turns a warehouse of current state into a
-- record of how the city actually behaved: the fact table knows a request took 40 hours to close,
-- the snapshot knows it sat in 'Assigned' for 38 of them.
--
-- Scoped deliberately. Snapshotting all 3.6M requests would copy millions of rows that reached
-- 'Closed' two years ago and will never change again. Only requests that are still open, or that
-- closed recently enough to still be moving, are tracked. When a row falls out of this window it
-- simply stops being updated — invalidate_hard_deletes is off, so its final version stays valid
-- rather than being marked deleted.

select
    request_id,
    status,
    agency_code,
    complaint_type,
    borough_name,
    created_at,
    closed_at,
    resolution_updated_at,

    -- dbt's timestamp strategy needs a non-null column to compare. A request that has never been
    -- actioned has no resolution timestamp, so its creation time is the last moment it changed.
    coalesce(resolution_updated_at, created_at) as status_changed_at

from {{ ref('stg_311_requests') }}

where closed_at is null
   or closed_at >= timestamp_sub(current_timestamp(), interval {{ var('snapshot_window_days', 45) }} day)

{% endsnapshot %}

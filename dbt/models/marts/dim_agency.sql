{{ config(materialized='table') }}

-- 311 sends the agency acronym on every row and the full name on most, but not all. Taking the
-- most recent non-null name per acronym gives a stable label without inventing one.

with requests as (

    select
        agency_code,
        agency_name,
        created_at
    from {{ ref('stg_311_requests') }}
    where agency_code is not null

),

ranked as (

    select
        agency_code,
        agency_name,
        row_number() over (
            partition by agency_code
            order by case when agency_name is null then 1 else 0 end, created_at desc
        ) as rn
    from requests

),

observed as (

    select
        agency_code,
        coalesce(agency_name, agency_code) as agency_name
    from ranked
    where rn = 1

),

-- 311 occasionally records no agency at all. Those requests still need somewhere to point, or the
-- fact table's foreign key would be unenforceable — the same reason dim_location carries UNKNOWN.
with_unknown as (

    select agency_code, agency_name, true as is_known_agency from observed
    union all
    select 'UNKNOWN', 'Unknown', false

),

final as (

    select
        {{ dbt_utils.generate_surrogate_key(['agency_code']) }}      as agency_key,
        agency_code,
        agency_name,
        is_known_agency
    from with_unknown

)

select * from final

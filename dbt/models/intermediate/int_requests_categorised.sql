{{ config(materialized='ephemeral') }}

-- Attaches the seed hierarchy and derives the resolution measures. Ephemeral because it is a
-- staging step for exactly one downstream model and there is no reason to persist it.

with requests as (

    select * from {{ ref('stg_311_requests') }}

),

categories as (

    select * from {{ ref('complaint_category_map') }}

),

joined as (

    select
        requests.*,

        -- A left join, not an inner one. 311 adds complaint types without notice, and an inner
        -- join would silently drop those requests from every count in the warehouse. Routing them
        -- to 'Unmapped' keeps the totals right and makes the gap visible to a test instead.
        coalesce(categories.service_category, 'Unmapped')       as service_category,
        coalesce(categories.service_subcategory, 'Unmapped')    as service_subcategory,
        coalesce(categories.is_weather_sensitive, false)        as is_weather_sensitive,
        categories.complaint_type is null                       as is_unmapped_complaint_type

    from requests
    left join categories
        on requests.complaint_type = categories.complaint_type

),

measured as (

    select
        *,

        date(created_at, 'America/New_York')                    as created_date_local,
        date(closed_at, 'America/New_York')                     as closed_date_local,

        closed_at is not null                                   as is_closed,

        -- Null for open requests rather than measured against now(). An open request has no
        -- resolution time; substituting the current age would make every unresolved backlog item
        -- look like a fast close the moment it was measured.
        case
            when closed_at is not null
            then timestamp_diff(closed_at, created_at, HOUR)
        end                                                     as resolution_hours,

        case
            when closed_at is not null
            then timestamp_diff(closed_at, created_at, HOUR) / 24.0
        end                                                     as resolution_days,

        -- 311 contains rows closed before they were created and rows open since 2010. Both are
        -- data-entry artifacts. They stay in the fact table for completeness but are flagged so
        -- averages can exclude them, rather than being silently deleted.
        -- Measured in hours, not days, because the reported measure is in hours. timestamp_diff
        -- with DAY truncates, so a request open for 365.9 days reads as 365 days and passes the
        -- flag while its 8,781 hours fail the downstream range test.
        case
            when closed_at is not null and closed_at < created_at then true
            when closed_at is not null
                 and timestamp_diff(closed_at, created_at, HOUR) > 8760 then true
            else false
        end                                                     as is_implausible_resolution

    from joined

)

select * from measured

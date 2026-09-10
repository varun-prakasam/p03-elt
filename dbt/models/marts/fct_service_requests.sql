{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='request_id',
        partition_by={'field': 'created_date_local', 'data_type': 'date', 'granularity': 'month'},
        cluster_by=['borough_name', 'service_category'],
        on_schema_change='append_new_columns'
    )
}}

-- An accumulating snapshot fact. One row per service request, updated in place as the request
-- moves through its lifecycle, with the milestone timestamps and the lag measures between them.
-- The grain never changes; the row does.
--
-- Partitioned by month rather than by day. Daily partitions over 24 months would be 730 of them
-- averaging 5,000 rows each, which is well under the size where BigQuery partitioning pays for
-- itself, and the merge would touch far more partition metadata than it saves in scan.

with requests as (

    select * from {{ ref('int_requests_categorised') }}

    -- Bounded to the analytical window. The updates walk returns any request whose resolution moved
    -- recently regardless of when it was opened, so a few thousand requests going back to 2020
    -- arrive alongside this year's. Two things go wrong if they stay:
    --
    --   * Anything older than the date spine has no dim_date member to join to, so the daily mart
    --     drops it and the two facts stop reconciling — which is what
    --     assert_daily_levels_reconcile_to_requests caught.
    --   * The daily mart's spine is dense across the fact's whole date range, so even a handful of
    --     old rows stretch it over years of empty days and every chart opens with a flatline.
    --
    -- Clamped to the spine as well as the window, because the two are configured independently and
    -- a spine narrower than the window would reintroduce the first problem silently.
    where created_at >= greatest(
              timestamp(date_sub(current_date(), interval {{ var('analysis_window_months') }} month)),
              (select timestamp(min(date_key)) from {{ ref('dim_date') }})
          )

    {% if is_incremental() %}

        -- Not simply "created since the high watermark". A request opened in January can close in
        -- March, and a created_date filter would never see that closure — the row would sit in the
        -- warehouse permanently marked open and every resolution measure would be wrong. The
        -- window is on when the request last *moved*, not when it arrived.
        and coalesce(resolution_updated_at, created_at)
              >= timestamp_sub(
                     (
                         select max(coalesce(resolution_updated_at, created_at))
                         from {{ this }}

                         -- Ignore timestamps that have not happened yet. 311 contains a handful of
                         -- typo'd dates, and exactly one of them — a single row out of 7.4 million,
                         -- stamped three months into the future — is enough to push this watermark
                         -- past the present. The lookback window then starts in the future too, so
                         -- every real update falls below it and is never merged again.
                         --
                         -- That failure is invisible: the model still builds, every test still
                         -- passes, and the fact simply stops growing. It was found by comparing raw
                         -- row counts against the fact, not by anything in the suite.
                         where coalesce(resolution_updated_at, created_at) <= current_timestamp()
                     ),
                     interval {{ var('incremental_lookback_days') }} day
                 )

    {% endif %}

),

weather as (

    select * from {{ ref('int_weather_banded') }}

),

final as (

    select
        requests.request_id,

        -- Foreign keys. Generated the same way as in each dimension, so a join that misses is a
        -- modelling error rather than a type mismatch.
        -- The dimension is keyed on the complaint type it actually holds. A request whose type is
        -- not in the seed points at the 'Unmapped' member rather than at a key that exists nowhere,
        -- which is what keeps the foreign key enforceable instead of decorative.
        {{ dbt_utils.generate_surrogate_key([
            "case when requests.is_unmapped_complaint_type then 'Unmapped' else requests.complaint_type end"
        ]) }}                                                       as complaint_category_key,
        {{ dbt_utils.generate_surrogate_key(["coalesce(requests.agency_code, 'UNKNOWN')"]) }}
                                                                    as agency_key,
        {{ dbt_utils.generate_surrogate_key(["coalesce(requests.borough_name, 'UNKNOWN')"]) }}
                                                                    as location_key,
        coalesce(weather.weather_band_key,
                 {{ dbt_utils.generate_surrogate_key(["'Unknown'", "'Unknown'", "'Unknown'"]) }})
                                                                    as weather_band_key,

        requests.created_date_local                                 as created_date_local,
        requests.closed_date_local                                  as closed_date_local,

        -- Milestones.
        requests.created_at,
        requests.resolution_updated_at,
        requests.closed_at,

        -- Degenerate and descriptive attributes kept on the fact for query convenience. They
        -- duplicate the dimensions on purpose: Evidence runs every dashboard query at build time,
        -- and avoiding a join on 3.6M rows is worth the redundancy.
        requests.complaint_type,
        requests.descriptor,
        requests.service_category,
        requests.service_subcategory,
        coalesce(requests.agency_code, 'UNKNOWN')                   as agency_code,
        coalesce(requests.borough_name, 'UNKNOWN')                  as borough_name,
        requests.incident_zip,
        requests.status,
        requests.channel_type,
        requests.latitude,
        requests.longitude,

        -- Measures.
        requests.resolution_hours,
        requests.resolution_days,

        requests.is_closed,
        requests.is_weather_sensitive,
        requests.is_unmapped_complaint_type,
        requests.is_implausible_resolution,

        -- The measure the dashboard actually reports. Implausible rows — closed before opened, or
        -- open for over a year — are real rows in 311 and stay in the table, but they are excluded
        -- here so a handful of data-entry errors cannot move a borough's average by days.
        case
            when requests.is_closed and not requests.is_implausible_resolution
            then requests.resolution_hours
        end                                                         as reportable_resolution_hours,

        requests.dlt_load_id

    from requests
    left join weather
        on requests.created_date_local = weather.weather_date

)

select * from final

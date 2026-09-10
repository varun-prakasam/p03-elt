{{ config(materialized='table') }}

-- Driven by the seed rather than by the observed data, so a category keeps its row even in a
-- period where nobody complained about it. The 'Unmapped' member is added explicitly for the same
-- reason the left join upstream exists: unmapped requests need somewhere to land.

with mapped as (

    select
        complaint_type,
        service_category,
        service_subcategory,
        is_weather_sensitive
    from {{ ref('complaint_category_map') }}

),

with_catchall as (

    select * from mapped

    union all

    select
        'Unmapped'          as complaint_type,
        'Unmapped'          as service_category,
        'Unmapped'          as service_subcategory,
        false               as is_weather_sensitive

),

final as (

    select
        {{ dbt_utils.generate_surrogate_key(['complaint_type']) }}   as complaint_category_key,
        complaint_type,
        service_category,
        service_subcategory,
        is_weather_sensitive
    from with_catchall

)

select * from final

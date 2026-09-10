{{ config(materialized='ephemeral') }}

-- Assigns each observed day to a weather band. The band logic itself lives in macros/weather_bands
-- so this and dim_weather_band are guaranteed to use the same thresholds.

with weather as (

    select * from {{ ref('stg_weather_daily') }}

),

banded as (

    select
        weather_date,

        temp_max_f,
        temp_min_f,
        temp_mean_f,
        precipitation_in,
        snowfall_in,
        wind_speed_max_kmh,

        {{ temp_band('temp_mean_f') }}      as temp_band,
        {{ precip_band('precipitation_in') }} as precip_band,
        {{ snow_band('snowfall_in') }}      as snow_band

    from weather

),

keyed as (

    select
        *,
        {{ dbt_utils.generate_surrogate_key(['temp_band', 'precip_band', 'snow_band']) }}
            as weather_band_key
    from banded

)

select * from keyed

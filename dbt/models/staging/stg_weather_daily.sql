{{ config(materialized='view') }}

-- Open-Meteo reports metric. The dashboard is about New York, so the conversions to Fahrenheit and
-- inches happen once here rather than in every downstream model.

with source as (

    select * from {{ source('p03_raw', 'weather_daily') }}

),

converted as (

    select
        observed_on                                             as weather_date,

        temperature_2m_max                                      as temp_max_c,
        temperature_2m_min                                      as temp_min_c,
        temperature_2m_mean                                     as temp_mean_c,

        round(temperature_2m_max * 9 / 5 + 32, 1)               as temp_max_f,
        round(temperature_2m_min * 9 / 5 + 32, 1)               as temp_min_f,
        round(temperature_2m_mean * 9 / 5 + 32, 1)              as temp_mean_f,

        precipitation_sum                                       as precipitation_mm,
        rain_sum                                                as rain_mm,
        snowfall_sum                                            as snowfall_cm,
        wind_speed_10m_max                                      as wind_speed_max_kmh,

        round(precipitation_sum / 25.4, 2)                      as precipitation_in,
        round(snowfall_sum / 2.54, 2)                           as snowfall_in,

        _dlt_load_id                                            as dlt_load_id

    from source

)

select * from converted

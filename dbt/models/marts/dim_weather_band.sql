{{ config(materialized='table') }}

-- A junk dimension: the cross product of every temperature, precipitation and snowfall band,
-- generated rather than observed. Six by five by four is 120 rows, which is cheaper to store than
-- it is to reason about, and it means a band that has never occurred still joins cleanly.
--
-- Weather is a driver here, not the subject. The headline measure is how long an agency takes to
-- close a request; these bands exist to ask whether that degrades when the weather turns.

with combinations as (

    select
        temp_band,
        precip_band,
        snow_band
    from unnest({{ temp_band_values() }})    as temp_band,
         unnest({{ precip_band_values() }})  as precip_band,
         unnest({{ snow_band_values() }})    as snow_band

),

final as (

    select
        {{ dbt_utils.generate_surrogate_key(['temp_band', 'precip_band', 'snow_band']) }}
                                                                as weather_band_key,
        temp_band,
        precip_band,
        snow_band,

        concat(temp_band, ', ', precip_band, ' precip')          as weather_band_label,

        temp_band in ('Freezing', 'Hot')                         as is_temperature_extreme,
        precip_band = 'Heavy' or snow_band = 'Significant'       as is_severe_precipitation

    from combinations

)

select * from final

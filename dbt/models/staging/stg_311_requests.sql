{{ config(materialized='view') }}

-- Rename, recast, trim. No business logic and no joins: anything that needs another table belongs
-- in intermediate. Socrata sends every value as a JSON string, so the casts here are the whole
-- reason this layer exists.

with source as (

    select * from {{ source('p03_raw', 'service_requests') }}

),

renamed as (

    select
        unique_key                                          as request_id,

        created_date                                        as created_at,
        closed_date                                         as closed_at,
        resolution_action_updated_date                      as resolution_updated_at,

        nullif(trim(agency), '')                            as agency_code,
        nullif(trim(agency_name), '')                       as agency_name,

        nullif(trim(complaint_type), '')                    as complaint_type,
        nullif(trim(descriptor), '')                        as descriptor,
        nullif(trim(location_type), '')                     as location_type,

        nullif(trim(incident_zip), '')                      as incident_zip,

        -- 311 spells the absence of a borough as the literal string 'Unspecified'. Left alone it
        -- would become a sixth borough in every group-by.
        nullif(nullif(trim(borough), ''), 'Unspecified')    as borough_name,

        nullif(trim(status), '')                            as status,
        nullif(trim(open_data_channel_type), '')            as channel_type,

        safe_cast(latitude as float64)                      as latitude,
        safe_cast(longitude as float64)                     as longitude,

        _dlt_load_id                                        as dlt_load_id

    from source

)

select * from renamed

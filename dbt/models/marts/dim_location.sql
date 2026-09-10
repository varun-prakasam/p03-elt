{{ config(materialized='table') }}

-- Fixed at five boroughs plus an explicit unknown member, rather than selected from the data. The
-- boroughs of New York do not change, and hard-coding them means a borough with no requests in a
-- filtered period still appears in the dashboard rather than vanishing from the axis.

with boroughs as (

    select * from unnest([
        struct('BRONX'         as borough_name, 'Bronx'         as borough_label, 36005 as county_fips, 'Bronx'     as county_name),
        struct('BROOKLYN',           'Brooklyn',                     36047,             'Kings'),
        struct('MANHATTAN',          'Manhattan',                    36061,             'New York'),
        struct('QUEENS',             'Queens',                       36081,             'Queens'),
        struct('STATEN ISLAND',      'Staten Island',                36085,             'Richmond')
    ])

),

with_unknown as (

    select
        borough_name,
        borough_label,
        county_fips,
        county_name,
        true    as is_known_borough
    from boroughs

    union all

    -- stg_311_requests turns both empty strings and the literal 'Unspecified' into null. Those
    -- requests still need a dimension row, or the fact table's foreign key would be unenforceable.
    select
        'UNKNOWN', 'Unknown', null, null, false

),

final as (

    select
        {{ dbt_utils.generate_surrogate_key(['borough_name']) }}     as location_key,
        borough_name,
        borough_label,
        county_fips,
        county_name,
        is_known_borough
    from with_unknown

)

select * from final

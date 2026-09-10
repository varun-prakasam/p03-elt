{# Band definitions live here so the junk dimension and the daily assignment cannot drift apart.
   Both dim_weather_band and int_weather_banded call these macros; a relationships test from the
   fact back to the dimension catches it if they ever do. #}

{% macro temp_band(column) %}
    case
        when {{ column }} is null    then 'Unknown'
        when {{ column }} <  32      then 'Freezing'
        when {{ column }} <  50      then 'Cold'
        when {{ column }} <  70      then 'Mild'
        when {{ column }} <  85      then 'Warm'
        else                              'Hot'
    end
{% endmacro %}


{% macro precip_band(column) %}
    case
        when {{ column }} is null    then 'Unknown'
        when {{ column }} =  0       then 'None'
        when {{ column }} <  0.1     then 'Light'
        when {{ column }} <  0.5     then 'Moderate'
        else                              'Heavy'
    end
{% endmacro %}


{% macro snow_band(column) %}
    case
        when {{ column }} is null    then 'Unknown'
        when {{ column }} =  0       then 'None'
        when {{ column }} <  2       then 'Light'
        else                              'Significant'
    end
{% endmacro %}


{# Emitted as text for `unnest(...)` in dim_weather_band. These cannot be reused as the `values:` of
   an accepted_values test: dbt parses a schema file as YAML first and renders Jinja afterwards, so
   `{{ ... }}` there is read as a nested mapping and fails to parse, while quoting it yields a
   string rather than a list. The intermediate model's band columns are covered by unit tests on the
   thresholds instead, which is a sharper check than an enumeration anyway. #}

{% macro temp_band_values() %}
    ['Freezing', 'Cold', 'Mild', 'Warm', 'Hot', 'Unknown']
{% endmacro %}

{% macro precip_band_values() %}
    ['None', 'Light', 'Moderate', 'Heavy', 'Unknown']
{% endmacro %}

{% macro snow_band_values() %}
    ['None', 'Light', 'Significant', 'Unknown']
{% endmacro %}

{#
    dbt's default behaviour concatenates the target dataset and the custom schema, which would
    produce `p03_staging_marts`. The platform pre-creates `p03_staging` and `p03_marts` in
    Terraform, so the custom schema has to be used verbatim instead.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        p03_{{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}

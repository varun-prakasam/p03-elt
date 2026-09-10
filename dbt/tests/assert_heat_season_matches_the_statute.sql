-- The heat-season flag must be exactly October 1 through May 31.
--
-- This flag carries more analytical weight than anything else in the model. The project's stated
-- thesis is that HEAT/HOT WATER volume tracks a statute rather than a thermometer, and every
-- weather page on the dashboard is written around that claim. If the boundaries drift by a month
-- the charts still render, the totals still reconcile, and the conclusion quietly becomes wrong.
--
-- Asserted against the rule itself rather than against the model's own CASE expression, so this
-- fails if someone edits the CASE — restating the implementation would test nothing.

select
    date_key,
    is_heat_season,
    extract(month from date_key) as month_number
from {{ ref('dim_date') }}
where is_heat_season
    != (extract(month from date_key) >= 10 or extract(month from date_key) <= 5)

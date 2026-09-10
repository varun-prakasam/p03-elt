---
title: Agency Detail
description: Resolution time by agency, borough and complaint category.
---

```sql agencies
select distinct agency_code, agency_name
from nyc311.agency_performance
order by agency_code
```

<Dropdown data={agencies} name=agency value=agency_code label=agency_name defaultValue="HPD"/>

```sql selected
select
    borough_name,
    service_category,
    sum(requests_total)                                             as requests_total,
    sum(requests_open)                                              as requests_open,
    sum(closures_measured)                                          as closures_measured,
    sum(avg_resolution_hours * closures_measured)
        / nullif(sum(closures_measured), 0)                         as avg_resolution_hours,
    sum(closed_within_3_days) / nullif(sum(closures_measured), 0)   as pct_closed_within_3_days
from nyc311.agency_performance
where agency_code = '${inputs.agency.value}'
group by borough_name, service_category
having sum(requests_total) > 50
order by requests_total desc
```

```sql selected_totals
select
    sum(requests_total)                                             as requests_total,
    sum(requests_open)                                              as requests_open,
    sum(avg_resolution_hours * closures_measured)
        / nullif(sum(closures_measured), 0)                         as avg_resolution_hours
from nyc311.agency_performance
where agency_code = '${inputs.agency.value}'
```

<BigValue data={selected_totals} value=requests_total fmt='#,##0' title="Requests"/>
<BigValue data={selected_totals} value=requests_open fmt='#,##0' title="Still open"/>
<BigValue data={selected_totals} value=avg_resolution_hours fmt='#,##0.0' title="Avg hours to close"/>

## By borough and category

<DataTable data={selected} rows=20>
    <Column id=borough_name title="Borough"/>
    <Column id=service_category title="Category"/>
    <Column id=requests_total title="Requests" fmt='#,##0'/>
    <Column id=requests_open title="Open" fmt='#,##0'/>
    <Column id=avg_resolution_hours title="Avg hours" fmt='#,##0.0'/>
    <Column id=pct_closed_within_3_days title="Closed ≤ 3 days" fmt=pct1 contentType=bar/>
</DataTable>

## Does the borough matter?

```sql by_borough
select
    borough_name,
    sum(avg_resolution_hours * closures_measured)
        / nullif(sum(closures_measured), 0)                         as avg_resolution_hours
from nyc311.agency_performance
where agency_code = '${inputs.agency.value}'
  and borough_name != 'UNKNOWN'
group by borough_name
order by avg_resolution_hours desc
```

<BarChart
    data={by_borough}
    x=borough_name
    y=avg_resolution_hours
    title="Average hours to close, by borough"
    yAxisTitle="hours"
    swapXY=true
/>

A spread across boroughs for the same agency and the same complaint type is the interesting result:
the work is identical, so the difference is capacity, not caseload mix.

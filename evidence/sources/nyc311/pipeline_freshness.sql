-- Powers the banner at the top of the dashboard.
--
-- A dashboard that silently freezes is worse than one that is visibly down: the numbers still look
-- plausible, so nobody checks. Reading dlt's own load ledger means the banner reports when data
-- last *arrived*, not when the site was last built — the two diverge exactly when the pipeline has
-- failed and the nightly rebuild has not.
select
    (select max(inserted_at)
     from `varun-data-engineering.p03_raw._dlt_loads`
     where status = 0)                                              as last_load_at,

    (select count(*)
     from `varun-data-engineering.p03_raw._dlt_loads`
     where status = 0)                                              as loads_total,

    (select max(created_at)
     from `varun-data-engineering.p03_marts.fct_service_requests`)  as latest_request_at,

    (select count(*)
     from `varun-data-engineering.p03_marts.fct_service_requests`)  as requests_total,

    current_timestamp()                                             as built_at

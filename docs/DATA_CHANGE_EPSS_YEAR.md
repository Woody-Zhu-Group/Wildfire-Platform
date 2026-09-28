# EPSS calendar-year counts

EPSS statistics use the calendar year of `start_date` across records, circuit
rankings, map layers and their embedded outages, time series, circuit details,
comparisons and measured dataset coverage. An outage starting December 31, 2022
and ending January 1, 2023 counts in 2022 even when its source `year` is 2023.
The returned record's `year` remains the unmodified source attribute; map
summary `years` lists the event-start years.

`services/shared/time_adapter.py` translates an optional calendar year into
inclusive date bounds. When explicit dates are also supplied, it intersects
the constraints. Disjoint filters return no rows; neither constraint is silently
dropped. Queries filter the indexed `start_date` column directly. No loader
change, source rewrite, migration, or model fit is required. Comparison queries
and the coverage loader already use this convention, so coverage JSON is not
rewritten by hand.

Regression checks use `AGGREGATE_TEST_DSN` pointing to disposable PostgreSQL:
`python -m pytest tests/test_time_adapter.py tests/test_epss_time_window.py`.
The fixture includes a source-year mismatch, both year boundaries, and a leap
day. All query populations are compared with an independent SQL predicate.
Geometry serialization is stubbed in this fixture; spatial calculations and
production warehouse totals require separate live verification.

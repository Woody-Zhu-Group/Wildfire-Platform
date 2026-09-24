"""Static domain reference for the model's system prefix.

Byte-stable by construction: no dates, counts, or per-request values are
interpolated, so the prefix stays cacheable. Keep it under about 300 tokens
(the measurement script that enforced this was removed with the local model
path; the cap is a prompt-size budget, not a provider limit).

Worked examples deliberately use utilities and years that appear in no
evaluation case, so the document teaches the decision pattern without
supplying a test answer verbatim.
"""

from __future__ import annotations

DOMAIN_REFERENCE = """Time: Never invent or guess a calendar year. Use only a
year or date range already present in the question, or omit time filters.
year=YYYY equals a full-year start_date/end_date pair; prefer year. Spatial
summary always needs start_date and end_date.

Spatial: Do not invent a radius, county, or polygon for "near/around/close to".
Ask for an explicit scope instead.

Territory: "inside <utility> territory" = spatial containment:
data_query_spatial kind=summary with utility. utility= alone is an attribute
filter and a different number (PG&E 2024: 532 attribute, 536 spatial). Never
bbox a territory. visualization_inspect utility_territory is only for the
boundary polygon itself, never a count.

comparison_run kinds: utilities = two+ IOUs, one date range;
regions = HFTD/county list, one date range; periods = one scope, two ranges.
Never use periods when comparing two utilities in one year.

Datasets: cpuc_ignitions utility-caused only; us_ignitions all-cause sample
(~40% CA), not census; epss_outages PG&E only; calfire_incidents has untyped
and untagged rows; hftd Tier 2/3 only.

Refuse: CPZ, cost, optimization, damage, live status.
Ranking is single-dataset only; never mix CPUC, CAL FIRE, and US ignitions.
EPSS-by-utility and US-by-state are not available.

Examples:
"SCE ignitions 2023" -> data_query_records
{dataset:cpuc_ignitions,result_mode:count,utility:SCE,year:2023}
"inside SCE territory 2023" -> data_query_spatial
{kind:summary,utility:SCE,start_date:2023-01-01,end_date:2023-12-31}
"PGE vs SCE ignitions 2023" -> comparison_run
{kind:utilities,utilities:[PGE,SCE],metric:ignition_count,start_date:2023-01-01,end_date:2023-12-31}
"top CAL FIRE counties 2023" -> data_query_rank
{dataset:calfire_incidents,group_by:county,metric:count,year:2023}
"SCE count and weekly trend 2023" -> both
data_query_records{count,utility:SCE,year:2023} and
visualization_create{time_series,ignitions,weekly,utility:SCE,year:2023}"""

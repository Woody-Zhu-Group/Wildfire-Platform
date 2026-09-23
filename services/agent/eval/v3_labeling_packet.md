# Holdout v3 labeling packet

Label each question from the house policies and tool limits below. Do not use a router trace or a model answer.

For every question return:

- disposition: `answer`, `clarify`, or `unsupported`
- if clarify or unsupported, the reason id from the policy list
- if the question is answerable and asks for more than one result, the set of tool calls a correct answer needs

A comparison of named entities or periods and one count per entity return the same numbers. Either set of calls is acceptable for that case.

## House policies

- ambiguous_risk_metric: Riskiest, most risky, or highest risk without a named metric (fitted cell risk, ignition count, CAL FIRE incidents, or EPSS outages) must be clarified.
- missing_location: Near me with no latitude, longitude, or bounding box must be clarified.
- undefined_spatial_scope: Near, around, or close to a place with no radius, coordinates, or county or utility polygon must be clarified.
- undefined_region: Northern or southern California is not a warehouse region and must be clarified.
- ambiguous_relative_time: Recent, lately, and currently must be clarified; a named year, an exact date, or a simple relative year such as last year is enough.
- time_out_of_coverage: A period outside the years stored for that dataset must be clarified rather than guessed.
- ambiguous_risk_place: Fitted risk accepts one place, so a county and a utility named together must be clarified.
- forecast_missing_date: A risk score needs one historical calendar day through 2025-12-31; if none is given, ask for one.
- risk_future_date: Fitted risk stops on 2025-12-31, so tomorrow, a forward phrase, or any later date must be clarified.
- risk_missing_place: Fitted risk needs a grid cell, county, PGE/SCE/SDGE territory, or coordinates; otherwise ask which place.
- map_plus_trend_missing_year: A map plus a trend with no year or date range must be clarified.
- map_missing_year: A map of events with no year or date range must be clarified, except a timeless HFTD layer.
- trend_missing_year: A time series with no year or date range must be clarified.
- spatial_missing_year: A count inside a territory with no year or date range must be clarified.
- records_missing_year: A count or list that names a dataset but no year or date range must be clarified.
- ranking_missing_slots: A ranking needs one dataset and one grouping: CPUC county or utility, CAL FIRE county, or EPSS circuit.
- ranking_missing_year: A ranking with no year or date range must be clarified.
- ranking_county_contradiction: Do not rank counties and also filter to one named county; ask which was meant.
- unexpressed_filter_constraints: If a named county or month cannot be applied by the matched read, ask instead of dropping it.
- unsupported_cpz: Circuit protection zones (CPZ) are not in this warehouse, so refuse.
- unsupported_cost: Cost, price, budget, dollars, and economic impact are out of scope, so refuse.
- unsupported_optimization: Requests to optimize, schedule, or allocate resources are out of scope, so refuse.
- unsupported_damage: Property damage, insured or expected loss, and fatalities are out of scope, so refuse.
- unsupported_live_web: Fires burning right now, live status, and web search are out of scope, so refuse.
- unsupported_rank_cross_dataset: A ranking that mixes datasets such as CAL FIRE and CPUC must be refused.
- unsupported_rank_us_state: US ignitions have no state column, so ranking by state must be refused.
- unsupported_rank_epss_utility: EPSS is PG&E only, so ranking it by utility must be refused.
- unsupported_ranking: Any ranking other than CPUC by county or utility, CAL FIRE by county (count or acres), or EPSS by circuit must be refused.
- unexpressable_county_filter: County filters exist only on calfire_incidents, cpuc_ignitions, epss_outages, psps_events, and circuits. Never infer a utility from a place name, and do not answer a statewide count that drops a named county.

## Tools and limits

Warehouse years used by the time resolver run from 2014 through the current year. Fitted risk covariates end on 2025-12-31. A named city is not a place argument. Fitted risk accepts a grid cell, a county, coordinates, or a PGE, SCE, or SDGE territory. Sacramento and Fresno are also county names. Chico, Modesto, and Stockton are not counties. Do not treat a city as a county, and do not infer a utility from a city.

- data_query_records: One filtered count, or a short list of records, for one dataset. One utility and one county. Datasets: cpuc_ignitions, us_ignitions, epss_outages, psps_events, calfire_incidents, circuits, hftd, iou_territories. County is rejected on us_ignitions. An HFTD tier filter is valid only on hftd. A circuit id is valid only on circuits or epss_outages. EPSS rows are PG&E only.
- data_query_rank: One ranking inside one dataset. Allowed: CPUC by county or utility (count), CAL FIRE by county (count or acres burned), EPSS by circuit (count). Not EPSS by utility. Not US ignitions by state. Not a mix of datasets. Not county rank plus a county filter.
- data_query_spatial: Either a point, which requires latitude and longitude, or a summary count inside exactly one utility territory or exactly one HFTD tier, which also requires a start and end date. It does not take a city name. It does not return which circuits intersect a tier.
- visualization_create: A map or a time series. Map and series datasets: ignitions, us_ignitions, epss, psps, calfire, hftd. HFTD has no time series. Series interval is daily, weekly, or monthly, not yearly. A map of events needs a year, except the timeless HFTD layer. Circuits are not a map dataset here.
- visualization_inspect: One utility territory, which requires a utility, or one event or circuit, which requires a dataset and a record id. It does not answer which territory contains a city.
- risk_forecast: Historical fitted ignition risk for one place on one past day through 2025-12-31. The place must be exactly one of cell id, lat and lon, county, or PGE, SCE, or SDGE. Not a city. Not PacifiCorp, Liberty, or Bear Valley. Not a future date. Not a county and a utility together.
- comparison_run: One metric for several utilities in one date range, for HFTD or county regions in one date range, or for two periods of one scope. Metrics: ignition_count, epss_outage_count, epss_to_ignition_ratio, calfire_incident_count, acres_burned, psps_event_count, customers_deenergized. acres_burned is CAL FIRE fire acres, not the area of an HFTD polygon. Periods hold two ranges, not three or more. Not two warehouse datasets in one call.

## Questions

1. Which utility had more reported ignitions in 2021, SCE or SDG&E?
2. Show me a yearly chart of utility-caused ignitions for PG&E, SCE, and SDG&E from 2017 to 2023.
3. how many wildfire incidents were recorded in Shasta County in each year from 2019-2023
4. For Kern, Tulare, and Kings counties, give the number of CAL FIRE incidents in 2018, 2019, and 2020 and chart the yearly totals.
5. What percentage of the sample US wildfire ignitions in California occurred in 2022?
6. Compare utility-caused ignition counts in Riverside County for 2019, 2020, 2021, and 2022.
7. Show the monthly number of PG&E EPSS fast-trip events in 2022 and 2023.
8. which utility had the most PSPS events in 2020
9. How many PSPS events affected each utility service territory in 2019 through 2022?
10. Map Tier 2 and Tier 3 High Fire Threat District areas around Bakersfield.
11. Is the city of Chico inside a Tier 2 or Tier 3 High Fire Threat District?
12. What utility service territory contains Modesto?
13. For Sacramento, Stockton, and Fresno, identify the utility territory and HFTD tier, if any.
14. Give me the distribution circuits in SCE territory that intersect Tier 3 HFTD areas.
15. rank utilities by total EPSS fast-trip events from 2021 to 2023
16. Compare Liberty and Bear Valley utility-caused ignition totals from 2019 through 2023.
17. What was the monthly trend in SCE PSPS events during 2021?
18. List CAL FIRE wildfire incidents within 10 miles of Redding between 2017 and 2020.
19. How many utility-caused ignitions occurred in Sonoma County in 2018 and 2021, and can you chart the comparison?
20. show ignition counts by county for 2022 for the 15 counties with the most incidents
21. Which counties had the largest increase in CAL FIRE wildfire incidents between 2019 and 2020?
22. For 2017-2022, compare CPUC utility ignitions with the sample of all-cause wildfire ignitions by year.
23. How many EPSS events occurred on PG&E circuits in Tier 3 HFTD areas in 2023?
24. map PG&E distribution circuits in Nevada County that are inside HFTD tier 2 or 3
25. What was the average historical ignition risk for grid cells in San Luis Obispo County in July 2020?
26. Give the historical ignition-risk values for a location near Visalia on June 15, 2019 and June 15, 2021.
27. Which of these counties had more utility ignitions in 2020: Merced, Stanislaus, or Madera?
28. Count PSPS events by month for PG&E in 2019, 2020, and 2021.
29. compare 2022 and 2023 EPSS events for each of the six utilities and make a chart
30. What are the five California cities with the most recorded wildfire incidents in 2019?
31. For Monterey, Ventura, and Santa Barbara counties, list CAL FIRE incident counts for each year from 2018 through 2022.
32. How many CPUC utility-caused ignitions were in SCE territory versus SDG&E territory in 2020?
33. Show me a map of 2021 utility-caused ignitions in San Bernardino County.
34. Which distribution circuits had the highest number of EPSS fast trips in 2022?
35. Compare HFTD Tier 2 and Tier 3 acreage within PG&E and SCE service territories.
36. How many sample wildfire ignitions occurred in California from all causes in 2018, 2019, 2020, 2021, and 2022?
37. Rank counties by utility-caused ignition rate per county for 2021.
38. How many fires were caused by faulty transformers in 2020?
39. Which utility had the most dangerous fires last year?
40. Give me the number of ignitions per 100,000 customers for each utility in 2022.
41. how many acres did each utility's fires burn in 2021
42. What was the average response time to each wildfire incident for PG&E in 2020?
43. Compare the severity of PSPS events in 2019 and 2020 for all utilities.
44. Which circuits had the most equipment failures before an ignition in 2022?
45. What was the cause of each CAL FIRE incident in Yolo County during 2021?
46. how many people were evacuated from utility-caused fires in 2019
47. Give me the number of structures destroyed by PG&E-related ignitions in 2020 by county.
48. What percentage of PSPS customers lost power during each event in 2021?
49. Which utility had the highest ignition rate after adjusting for miles of distribution line?
50. For each EPSS event, tell me how many customers were interrupted.
51. How much did wildfire smoke increase during PSPS events in 2020?
52. What was the economic damage from CAL FIRE incidents in 2019 by county?
53. Can you predict which California distribution circuit will have an ignition next month?
54. Will PG&E have another PSPS event this fall?
55. Find all utility-caused ignitions reported after January 1, 2024.
56. Can you send me an alert whenever a new EPSS event occurs?
57. Show live wildfire incidents happening in California right now.
58. Can you recommend which utility should change its wildfire mitigation strategy?
59. Estimate how many acres will burn from utility ignitions in 2027.
60. What caused the fire near my house last night?
61. Can you pull the current HFTD map and tell me what changed this month?
62. Which utility will have the most ignitions next year?
63. tell me if a wildfire is burning near Oakland rn
64. How many PSPS events are expected during the 2026 fire season?
65. Can you identify the exact cause of every wildfire in California?
66. What are the current outage conditions on PG&E circuits?
67. Can you forecast daily ignition risk for every California grid cell for the next 30 days?
68. Which utility should the CPUC penalize based on its wildfire record?
69. How many PSPS events hit the Sierra foothills?
70. ignitions around Eureka last season
71. Which is worse for fires, SCE or PacifiCorp?
72. how many fires in Mendocino County
73. Show me EPSS outages from recently.
74. Map utility ignitions in my area for 2022.
75. Count CAL FIRE incidents along the north coast in 2021.
76. What did PSPS look like during fire season for SDG&E?
77. Rank the counties by wildfire incidents.
78. were there more ignitions near Ukiah than near Willits
79. historical ignition risk in Mariposa
80. How many utility-caused fires were there in the Inland Empire in 2020?
81. Show the trend in outages.
82. list calfire incidents close to Big Bear in 2019
83. Which utility had the most events over the last couple of fire seasons?
84. Compare Liberty's ignitions before and after the new rules.
85. how many psps shutoffs affected Hollister
86. What's the utility ignition count for the eastern Sierra?
87. Chart EPSS events for the worst months.
88. did Susanville see more fires this decade or last

/**
 * Visualization API base URL — change this one line to point at a deployed host.
 * Local default matches: uvicorn services.visualization.app:app --port 8002
 */
window.WILDFIRE_API_BASE = "http://127.0.0.1:8002";

/**
 * Agent API base URL — change this one line to point at a deployed host.
 * Local default matches: uvicorn services.agent.app:app --port 8004 --app-dir .
 */
window.WILDFIRE_AGENT_BASE = "http://127.0.0.1:8004";

/**
 * Data-query API base URL — record-table stale refetch.
 * Local default matches: uvicorn services.data_query.app:app --reload --app-dir .
 */
window.WILDFIRE_DATA_QUERY_BASE = "http://127.0.0.1:8000";

/**
 * CAL FIRE incident_type query value for /map-layer and /time-series.
 * - "all" during A/B verification against static CSV (website has no type filter)
 * - "" (omit / API default Wildfire+Fire) after verification for the wildfire demo
 * See frontend/VERIFICATION.md — switched to wildfire default after verification.
 */
window.WILDFIRE_CALFIRE_INCIDENT_TYPE = "";

// Wire contracts from services/agent/views.py and services/agent/schemas.py.
interface ScopedParams extends Record<string, unknown> {
  year?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  utility?: string | null;
  county?: string | null;
}
export interface MapViewParams extends ScopedParams {
  datasets: string[];
  extent?: 'auto_fit' | 'statewide' | 'conus' | 'territory';
  highlight_ids?: string[];
  show_territory?: boolean;
  show_hftd?: boolean;
  show_hdw?: boolean;
  map_mode?: 'events' | 'risk' | 'residual';
  risk_date?: string | null;
}
export interface TimeSeriesViewParams extends ScopedParams {
  dataset: string;
  interval?: 'daily' | 'weekly' | 'monthly';
  incident_type_mode?: 'wildfire_default' | 'all' | 'untyped' | null;
  series_mode?: 'yearly' | 'seasonal' | 'cumulative_acres' | 'customer_events' | 'regional' | 'timeline' | null;
  datasets?: string[] | null;
}
export interface ComparisonViewParams extends Record<string, unknown> {
  kind: 'utilities' | 'regions' | 'periods' | 'ranking';
  metric: string;
  normalize?: 'none' | 'per_circuit' | 'per_km2';
  ignition_definition?: 'attribute' | 'spatial' | null;
  utilities?: string[] | null;
  region_type?: 'county' | 'hftd' | null;
  regions?: string[] | null;
  scope_type?: 'utility' | 'county' | 'hftd' | null;
  scope?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  period_a_start?: string | null;
  period_a_end?: string | null;
  period_b_start?: string | null;
  period_b_end?: string | null;
  dataset?: string | null;
  group_by?: string | null;
  year?: number | null;
  limit?: number | null;
}
export interface RecordTableViewParams extends ScopedParams {
  dataset: string;
  columns?: 'default';
  row_limit?: number;
}
export interface StatCardViewParams extends Record<string, unknown> {
  kind: 'count' | 'risk' | 'spatial_metric';
  value: number;
  label: string;
  scope: string;
  period: string;
  source_dataset: string;
  unit?: 'events' | 'risk' | 'percentile' | null;
  stat_mode?: 'medical_exposure' | 'summary' | null;
  view_id?: 'medical-exposure' | 'summary-stats' | null;
  year?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  utility?: string | null;
  county?: string | null;
}
export interface SpatialContextViewParams extends Record<string, unknown> {
  lat: number;
  lon: number;
  iou?: string | null;
  hftd_tier?: string | null;
  county?: string | null;
  cell_id?: number | null;
}
export interface ComponentParams {
  map: MapViewParams;
  time_series: TimeSeriesViewParams;
  comparison: ComparisonViewParams;
  record_table: RecordTableViewParams;
  stat_card: StatCardViewParams;
  spatial_context: SpatialContextViewParams;
}
export type ComponentType = keyof ComponentParams;
export type ComponentSpec = {
  [K in ComponentType]: {
    type: K;
    params: ComponentParams[K];
    evidence_ids?: string[];
    artifact_refs?: string[];
  }
}[ComponentType];

export interface AgentToolError extends Record<string, unknown> { code?: string; message?: string }
export interface AgentTrajectoryStep extends Record<string, unknown> {
  type: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  ok?: boolean;
  error?: AgentToolError | null;
  evidence_id?: string | null;
  qualification_call?: boolean;
  latency_ms?: number;
}
export interface AgentEvidence {
  id: string;
  tool: string;
  arguments: Record<string, unknown>;
  summary: Record<string, unknown>;
  qualification_call?: boolean;
}
export interface AgentAnswer {
  request_id?: string;
  answer_text: string;
  status: string;
  route?: Record<string, unknown>;
  qualifications?: {id?: string; text: string; source?: string}[];
  evidence?: AgentEvidence[];
  artifacts?: {ref: string; kind?: string; [key: string]: unknown}[];
  trajectory?: AgentTrajectoryStep[];
  timings_ms?: Record<string, number>;
  model_metrics?: Record<string, number>;
  views?: ComponentSpec[];
  view_status?: 'applied' | 'planner_fallback' | 'none';
  view_scope?: Record<string, unknown>;
}
export interface AgentStreamEvent {event: string; data: Record<string, unknown>}

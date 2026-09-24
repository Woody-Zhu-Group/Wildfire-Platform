import type { AgentAnswer, AgentDecisionSource, AgentStreamEvent, AgentTrajectoryStep } from './agentContracts.ts';

const BACKSTOP_LABELS: Record<string, string> = {
  unsupported_live_web: 'live data',
  risk_future_date: 'future date',
  unsupported_future_prediction: 'future prediction',
  city_needs_place: 'city needs a place',
  hftd_constraint_unavailable: 'HFTD constraint unavailable',
};
const ROUTER_REASONS: Record<string, string> = {
  jev_below_gate: 'Jev below confidence gate',
  jev_error: 'Jev error',
  jev_timeout: 'Jev timed out',
  verified_fact: 'verified fact',
  router_only_route: 'router-only route',
  jev_agreed: 'Jev agreed',
  jev_off: 'Jev off',
  jev_shadow: 'Jev in shadow mode',
  jev_tool_pick: 'Jev picks tools only',
  jev_skipped: 'Jev skipped',
};

/** One line for the Tool chain: who made the answer, clarify, or refuse decision. */
export function decisionSourceLabel(source: AgentDecisionSource | null | undefined): string | null {
  if (!source) return null;
  if (source.source === 'jev') {
    return typeof source.confidence === 'number' ? `Decided by Jev (${source.confidence.toFixed(2)})` : 'Decided by Jev';
  }
  if (source.source === 'backstop') {
    const rule = source.rule ?? '';
    const label = BACKSTOP_LABELS[rule] ?? rule.replace(/^unsupported_/, '').replaceAll('_', ' ');
    return label ? `Safety rule: ${label}` : 'Safety rule';
  }
  const why = source.why ? ROUTER_REASONS[source.why] ?? source.why.replaceAll('_', ' ') : null;
  return why ? `Router (${why})` : 'Router';
}

/** The decision source from the final answer, or from the streamed routing event before it. */
export function decisionSource(answer: AgentAnswer | undefined, events: readonly AgentStreamEvent[]): AgentDecisionSource | null {
  if (answer?.decision_source) return answer.decision_source;
  const routed = events.find(event => event.event === 'routing')?.data.decision_source;
  return routed && typeof routed === 'object' ? routed as AgentDecisionSource : null;
}

export interface TraceStep {
  tool: string;
  service: string;
  status: 'Running' | 'Completed' | 'Failed' | 'No result';
  arguments: Record<string, unknown>;
  error?: string;
  evidenceId?: string;
  latencyMs?: number;
  qualification?: boolean;
}
function serviceName(tool: string): string {
  if (tool.startsWith('data_query_')) return 'Data query';
  if (tool.startsWith('visualization_')) return 'Visualization';
  if (tool === 'comparison_run') return 'Comparison';
  if (tool === 'risk_forecast') return 'Risk forecasting';
  return 'Tool';
}
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function step(data: Record<string, unknown>, finished: boolean): TraceStep {
  const tool = String(data.tool);
  const error = object(data.error);
  return {
    tool, service: serviceName(tool), arguments: object(data.arguments),
    status: data.ok === true ? 'Completed' : data.ok === false ? 'Failed' : finished ? 'No result' : 'Running',
    error: typeof error.message === 'string' ? error.message : typeof error.code === 'string' ? error.code : undefined,
    evidenceId: typeof data.evidence_id === 'string' ? data.evidence_id : undefined,
    latencyMs: typeof data.latency_ms === 'number' ? data.latency_ms : undefined,
    qualification: data.qualification_call === true,
  };
}
export function traceSteps(answer: AgentAnswer | undefined, events: readonly AgentStreamEvent[], finished: boolean): TraceStep[] {
  const finalCalls = answer?.trajectory?.filter((item: AgentTrajectoryStep) => item.type === 'tool_call' && typeof item.tool === 'string') ?? [];
  if (finalCalls.length) return finalCalls.map(item => step(item, true));
  const calls: TraceStep[] = [];
  for (const {event, data} of events) {
    if (typeof data.tool !== 'string') continue;
    const pending = [...calls].reverse().find(item => item.tool === data.tool && item.status === 'Running');
    if (event === 'tool_call') calls.push(step(data, false));
    if (event === 'retry') {
      if (pending) {
        pending.status = 'Failed';
        pending.error = typeof data.error_code === 'string' ? data.error_code : 'Retry requested';
      }
    }
    if (event === 'tool_result') {
      const result = step(data, true);
      if (pending) Object.assign(pending, result); else calls.push(result);
    }
  }
  if (calls.length) return calls.map(item => finished && item.status === 'Running' ? {...item, status: 'No result'} : item);
  return (answer?.evidence ?? []).map(item => step({...item, evidence_id: item.id, ok: true}, true));
}

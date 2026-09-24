import type { AgentAnswer, AgentStreamEvent } from './agentContracts.ts';
import { decisionSource, decisionSourceLabel, traceSteps } from './agentTrace.ts';

export function ToolTrace({answer, events, finished}: {answer?: AgentAnswer; events: readonly AgentStreamEvent[]; finished: boolean}) {
  const steps = traceSteps(answer, events, finished);
  const route = answer?.route ?? events.find(event => event.event === 'routing')?.data;
  const decidedBy = decisionSourceLabel(decisionSource(answer, events));
  if (!steps.length && !route && !decidedBy) return null;
  const path = route?.path === 'deterministic' ? 'Rule-based' : route?.path === 'model' ? 'Model-assisted' : null;
  return <details className="tool-trace"><summary>Tool chain{steps.length > 0 && ` · ${steps.length}`}</summary>
    {path && <p className="trace-route">{path}{answer?.route?.synthesis_fallback === true && ' · Synthesis fallback'}</p>}
    {decidedBy && <p className="trace-route trace-decision">{decidedBy}</p>}
    {steps.length ? <ol>{steps.map((item, index) => <li key={index}>
      <div className="trace-step-heading"><strong>{item.service}</strong><span>{item.status}{item.latencyMs !== undefined && ` · ${Math.round(item.latencyMs).toLocaleString()} ms`}</span></div>
      <code>{item.tool}</code>{item.qualification && <span className="trace-qualification"> · Qualification check</span>}
      {Object.keys(item.arguments).length > 0 && <dl>{Object.entries(item.arguments).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value === null ? 'Not set' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>}
      {item.error && <p className="trace-error">{item.error}</p>}
      {item.evidenceId && <small>Evidence: {item.evidenceId}</small>}
    </li>)}</ol> : <p className="trace-route">No tool calls reported.</p>}
  </details>;
}

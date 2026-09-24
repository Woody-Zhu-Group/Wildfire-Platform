import test from 'node:test';
import assert from 'node:assert/strict';
import { decisionSource, decisionSourceLabel } from '../src/agentTrace.ts';
import type { AgentAnswer, AgentStreamEvent } from '../src/agentContracts.ts';

test('a Jev decision shows its confidence', () => {
  assert.equal(decisionSourceLabel({source: 'jev', mode: 'decide', disposition: 'unsupported', confidence: 0.93}), 'Decided by Jev (0.93)');
  assert.equal(decisionSourceLabel({source: 'jev', mode: 'decide', disposition: 'answer', confidence: null}), 'Decided by Jev');
});

test('a router backstop shows the safety rule in words', () => {
  assert.equal(decisionSourceLabel({source: 'backstop', mode: 'off', rule: 'unsupported_live_web'}), 'Safety rule: live data');
  assert.equal(decisionSourceLabel({source: 'backstop', mode: 'decide', rule: 'risk_future_date'}), 'Safety rule: future date');
  assert.equal(decisionSourceLabel({source: 'backstop', mode: 'decide', rule: 'unsupported_air_quality'}), 'Safety rule: air quality');
});

test('a router decision says why Jev did not decide', () => {
  const cases: [string, string][] = [
    ['jev_below_gate', 'Router (Jev below confidence gate)'],
    ['jev_error', 'Router (Jev error)'],
    ['jev_timeout', 'Router (Jev timed out)'],
    ['jev_daily_cap', 'Router (Jev daily call cap reached)'],
    ['verified_fact', 'Router (verified fact)'],
    ['router_only_route', 'Router (router-only route)'],
    ['jev_agreed', 'Router (Jev agreed)'],
    ['jev_off', 'Router (Jev off)'],
    ['jev_shadow', 'Router (Jev in shadow mode)'],
  ];
  for (const [why, label] of cases) {
    assert.equal(decisionSourceLabel({source: 'router', mode: 'decide', why}), label);
  }
  assert.equal(decisionSourceLabel(null), null);
});

test('the source comes from the answer, or from the routing event while streaming', () => {
  const events: AgentStreamEvent[] = [{event: 'routing', data: {path: 'model', decision_source: {source: 'router', mode: 'off', why: 'jev_off'}}}];
  assert.equal(decisionSourceLabel(decisionSource(undefined, events)), 'Router (Jev off)');
  const answer: AgentAnswer = {status: 'unsupported', answer_text: 'No.', decision_source: {source: 'jev', mode: 'decide', disposition: 'unsupported', confidence: 0.9}};
  assert.equal(decisionSourceLabel(decisionSource(answer, events)), 'Decided by Jev (0.90)');
  assert.equal(decisionSource({status: 'ok', answer_text: 'x'}, []), null);
});

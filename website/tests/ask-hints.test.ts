import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ASK_HINTS, ASK_HINTS_SHOWN } from '../src/askHints.ts';

interface EvalCase { id: string; question: string; expected_status: unknown; expected_route: unknown }
const cases = JSON.parse(readFileSync(new URL('../../services/agent/eval/cases.json', import.meta.url), 'utf8')) as EvalCase[];

test('every Ask hint is, word for word, an eval case the router answers deterministically', () => {
  for (const hint of ASK_HINTS) {
    const match = cases.find(item => item.question === hint);
    assert.ok(match, `no eval case asks: ${hint}`);
    assert.equal(match.expected_status, 'answer', `${match.id} does not expect an answer`);
    assert.equal(match.expected_route, 'deterministic', `${match.id} is not a deterministic route`);
  }
});

test('hints are distinct and some wait behind Show more', () => {
  assert.equal(new Set(ASK_HINTS).size, ASK_HINTS.length);
  assert.ok(ASK_HINTS_SHOWN > 0 && ASK_HINTS_SHOWN < ASK_HINTS.length);
});

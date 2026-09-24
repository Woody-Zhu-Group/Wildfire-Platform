import test from 'node:test';
import assert from 'node:assert/strict';
// @ts-expect-error: plain ES module script without type declarations
import { buildDifferences, FIX } from '../scripts/check-build.mjs';

test('the committed site in docs/ is the build of the current website source', async () => {
  const problems: string[] = await buildDifferences();
  assert.deepEqual(problems, [], `docs/ does not match the website build:\n  ${problems.join('\n  ')}\n${FIX}`);
});

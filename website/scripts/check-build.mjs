// Fails when the committed site in docs/ is not the build of the current source.
// Builds exactly as scripts/build.mjs does, but into a temporary directory, and
// never writes to docs/. Run by the website tests (tests/build-freshness.test.ts)
// and by `npm run check-build`.
import { build } from 'vite';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const docs = resolve(root, '../docs');
export const FIX = 'Run `npm run build` in website/ and commit docs/index.html and docs/assets/workspace/ in the same PR as the website source change.';

async function files(dir, base = dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...await files(path, base));
    else out.push(relative(base, path).replaceAll('\\', '/'));
  }
  return out.sort();
}

// Git may check text files out with CRLF; the build writes LF.
const normalize = bytes => Buffer.from(bytes.toString('latin1').replaceAll('\r\n', '\n'), 'latin1');

/** Differences between docs/ and a fresh build, as readable lines. Empty means up to date. */
export async function buildDifferences() {
  const temporary = await mkdtemp(resolve(tmpdir(), 'wildfire-website-check-'));
  try {
    await build({ root, logLevel: 'silent', build: { outDir: temporary, assetsDir: 'assets/workspace', emptyOutDir: true } });
    const built = ['index.html', ...(await files(resolve(temporary, 'assets/workspace'))).map(name => `assets/workspace/${name}`)];
    const committed = ['index.html', ...(await files(resolve(docs, 'assets/workspace'))).map(name => `assets/workspace/${name}`)];
    const problems = [];
    for (const name of built.filter(name => !committed.includes(name))) problems.push(`missing from docs/: ${name}`);
    for (const name of committed.filter(name => !built.includes(name))) problems.push(`stale in docs/ (not produced by the build): ${name}`);
    for (const name of built.filter(name => committed.includes(name))) {
      const fresh = normalize(await readFile(resolve(temporary, name)));
      const current = normalize(await readFile(resolve(docs, name)));
      if (!fresh.equals(current)) problems.push(`differs from the build: docs/${name}`);
    }
    return problems;
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const problems = await buildDifferences();
  if (problems.length) {
    console.error(`docs/ does not match the website build:\n  ${problems.join('\n  ')}\n${FIX}`);
    process.exit(1);
  }
  console.log('docs/ matches the website build.');
}

// Fails when an inline <script> in a built page is not allowed by the
// Content-Security-Policy in security-headers.json. The deploy workflow runs it
// on the accounts build before uploading; website/tests/cloudfront.test.ts runs
// it on the source page.
//
//   node deploy/cloudfront/check-csp.mjs website/dist/accounts/index.html
import { createHash } from "node:crypto"
import { readFileSync } from "node:fs"
import { fileURLToPath, pathToFileURL } from "node:url"

const POLICY = fileURLToPath(new URL("./security-headers.json", import.meta.url))

export function contentSecurityPolicy(path = POLICY) {
  return JSON.parse(readFileSync(path, "utf8")).SecurityHeadersConfig.ContentSecurityPolicy.ContentSecurityPolicy
}

export function directive(policy, name) {
  const found = policy.split(";").map(part => part.trim().split(/\s+/)).find(([key]) => key === name)
  return found ? found.slice(1) : null
}

/** The CSP source for each inline classic script in the page, in order. */
export function inlineScriptHashes(html) {
  return [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
    .map(match => `'sha256-${createHash("sha256").update(match[1], "utf8").digest("base64")}'`)
}

/** Inline scripts the policy does not allow; empty when the page is covered. */
export function uncoveredScripts(html, policy = contentSecurityPolicy()) {
  const allowed = directive(policy, "script-src") ?? directive(policy, "default-src") ?? []
  return inlineScriptHashes(html).filter(hash => !allowed.includes(hash))
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const page = process.argv[2]
  if (!page) {
    console.error("Usage: node deploy/cloudfront/check-csp.mjs <built index.html>")
    process.exit(2)
  }
  const missing = uncoveredScripts(readFileSync(page, "utf8"))
  if (missing.length) {
    console.error(`${page} has inline scripts the CSP does not allow: ${missing.join(" ")}\n`
      + "Add these sources to script-src in deploy/cloudfront/security-headers.json, then update the CloudFront response headers policy.")
    process.exit(1)
  }
  console.log(`${page}: every inline script is allowed by the CSP.`)
}

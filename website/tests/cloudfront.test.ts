import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync, readdirSync } from "node:fs"
import { fileURLToPath } from "node:url"
// @ts-expect-error: plain ES module script without type declarations
import { contentSecurityPolicy, directive, uncoveredScripts } from "../../deploy/cloudfront/check-csp.mjs"
import { LOGIN_RETURN_PATHS } from "../src/access/accessFlow.ts"
import { SIGN_IN_ERROR_PATH } from "../src/access/accessRoute.ts"

const file = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8")
type Request = { uri: string; querystring: Record<string, unknown> }
const handler = new Function(`${file("../../deploy/cloudfront/site-rewrite.js")}; return handler`)() as (event: { request: Request }) => Request
const rewrite = (uri: string) => handler({ request: { uri, querystring: { code: { value: "x" } } } })

test("page paths load index.html and files keep their path", () => {
  for (const uri of ["/", "/workspace", "/workspace/", "/access-status", "/invite", "/admin", "/sign-in-error", "/unknown"]) {
    assert.equal(rewrite(uri).uri, "/index.html", uri)
  }
  for (const uri of ["/index.html", "/assets/index-abc123.js", "/assets/AccountsRoot-x.css", "/favicon.ico", "/.well-known/security.txt"]) {
    assert.equal(rewrite(uri).uri, uri, uri)
  }
  assert.deepEqual(rewrite("/sign-in-error").querystring, { code: { value: "x" } }, "the query string is untouched")
})

test("every path the site routes or the service redirects to reaches the page", () => {
  for (const path of [...LOGIN_RETURN_PATHS, SIGN_IN_ERROR_PATH]) assert.equal(rewrite(path).uri, "/index.html", path)
})

test("the CSP allows the page's inline script and nothing it does not need", () => {
  const policy: string = contentSecurityPolicy()
  assert.deepEqual(uncoveredScripts(file("../index.html"), policy), [], "website/index.html inline script hash missing from script-src")
  assert.notDeepEqual(uncoveredScripts(file("../index.html").replace("wildfire-workspace-theme", "changed"), policy), [], "an edited script must fail")
  assert.deepEqual(directive(policy, "connect-src"), ["'self'"])
  assert.deepEqual(directive(policy, "frame-ancestors"), ["'none'"])
  assert.deepEqual(directive(policy, "object-src"), ["'none'"])
  assert.ok(!directive(policy, "script-src")!.includes("'unsafe-inline'") && !directive(policy, "script-src")!.includes("'unsafe-eval'"))
})

test("every map tile host in the site is allowed as an image source", () => {
  const source = fileURLToPath(new URL("../src/", import.meta.url))
  const hosts = new Set(readdirSync(source).filter(name => /\.tsx?$/.test(name))
    .flatMap(name => [...readFileSync(source + name, "utf8").matchAll(/['"`](https:\/\/[^/'"`{]*tile[^/'"`]*)\//g)].map(match => match[1])))
  assert.ok(hosts.size > 0, "no tile hosts found; update this test if maps moved")
  const allowed: string[] = directive(contentSecurityPolicy(), "img-src")
  for (const host of hosts) assert.ok(allowed.includes(host), `${host} is not in img-src`)
})

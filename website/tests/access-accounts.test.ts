import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { AccountsError, acceptInvitation, claimInvitation, decideRequest, getMe, listRequests, listUsers, signOut, submitApplication, updateUser, type Me } from "../src/access/accountsApi.ts"
import { routeFor, signInErrorCode, SIGN_IN_ERROR_PATH } from "../src/access/accessRoute.ts"
import { LOGIN_RETURN_PATHS } from "../src/access/accessFlow.ts"

const me = (status: Me["user"]["status"], role: Me["user"]["role"] = "member"): Me => ({
  user: { id: "u", name: "Alex", email: "a@example.org", role, status }, application: null, csrf_token: "csrf-1",
})

test("signed-out visitors see the landing page or claim an invitation", () => {
  assert.deepEqual(routeFor("/", null), { view: { page: "landing", returnTo: "/workspace" }, path: "/" })
  assert.deepEqual(routeFor("/workspace", null), { view: { page: "landing", returnTo: "/workspace" }, path: "/workspace" })
  assert.equal(routeFor("/access-status", null).path, "/")
  assert.equal(routeFor("/anything", null).path, "/")
  assert.deepEqual(routeFor("/invite", null), { view: { page: "invite-claim" }, path: "/invite" })
})

test("membership status decides the page for a signed-in account", () => {
  assert.deepEqual(routeFor("/", me("active")), { view: { page: "workspace" }, path: "/workspace" })
  assert.deepEqual(routeFor("/access-status", me("active")), { view: { page: "workspace" }, path: "/workspace" })
  assert.deepEqual(routeFor("/workspace", me("pending")), { view: { page: "status" }, path: "/access-status" })
  assert.deepEqual(routeFor("/apply", me("pending")), { view: { page: "status" }, path: "/access-status" })
  assert.deepEqual(routeFor("/workspace", me("suspended")), { view: { page: "status" }, path: "/access-status" })
  assert.deepEqual(routeFor("/invite", me("pending")), { view: { page: "invite-accept" }, path: "/invite" })
  assert.deepEqual(routeFor("/invite", me("suspended")).view, { page: "status" })
})

test("only an active administrator reaches the console; a signed-out link returns there", () => {
  assert.deepEqual(routeFor("/admin", me("active", "admin")), { view: { page: "console" }, path: "/admin" })
  assert.deepEqual(routeFor("/admin", me("active")), { view: { page: "workspace" }, path: "/workspace" })
  assert.deepEqual(routeFor("/admin", me("suspended", "admin")).view, { page: "status" })
  assert.deepEqual(routeFor("/admin", null), { view: { page: "landing", returnTo: "/admin" }, path: "/admin" })
})

test("every path the router keeps is one the service can return to or redirects to", () => {
  const app = readFileSync(fileURLToPath(new URL("../../services/accounts/app.py", import.meta.url)), "utf8")
  const serviceDestinations = [...app.matchAll(/destination = "(\/[a-z-]*)"/g)].map(match => match[1])
  assert.ok(serviceDestinations.includes("/invite") && serviceDestinations.includes("/access-status"), "callback destinations not found in app.py")
  for (const path of ["/", "/workspace", "/admin", "/invite", "/access-status", ...serviceDestinations]) {
    for (const session of [null, me("active"), me("active", "admin"), me("pending"), me("suspended")]) {
      const kept = routeFor(path, session).path
      assert.ok((LOGIN_RETURN_PATHS as readonly string[]).includes(kept), `${path} -> ${kept}`)
    }
  }
})

type Call = { url: string; init: RequestInit }
function stubFetch(reply: (call: Call) => Response) {
  const calls: Call[] = []
  const previous = globalThis.fetch
  globalThis.fetch = (async (url: string, init: RequestInit = {}) => { const call = { url, init }; calls.push(call); return reply(call) }) as typeof fetch
  return { calls, restore: () => { globalThis.fetch = previous } }
}
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
const header = (call: Call, name: string) => new Headers(call.init.headers).get(name)

test("writes send the session's CSRF token and JSON body; reads send none", async () => {
  const fake = stubFetch(call => call.url.endsWith("/me") ? json(200, me("pending")) : call.url === "/auth/logout" ? new Response(null, { status: 204 }) : json(201, { id: "r", status: "pending", created_at: "2026-10-05T00:00:00Z" }))
  try {
    await getMe()
    await submitApplication("csrf-1", { name: "Alex", organization: "Lab", purpose: "Study" })
    assert.equal(await signOut("csrf-1"), undefined)
    const [read, apply, logout] = fake.calls
    assert.equal(read.init.method, undefined)
    assert.equal(header(read, "X-CSRF-Token"), null)
    assert.equal(read.init.credentials, "same-origin")
    assert.equal(apply.url, "/api/access-requests")
    assert.equal(apply.init.method, "POST")
    assert.equal(header(apply, "X-CSRF-Token"), "csrf-1")
    assert.deepEqual(JSON.parse(String(apply.init.body)), { name: "Alex", organization: "Lab", purpose: "Study" })
    assert.equal(header(logout, "X-CSRF-Token"), "csrf-1")
    assert.equal(logout.init.body, undefined)
  } finally { fake.restore() }
})

test("an invitation is claimed without a session and accepted with one", async () => {
  const fake = stubFetch(call => call.url.endsWith("/claim") ? json(200, { login_url: "/auth/login?return_to=/invite" }) : json(200, { status: "active" }))
  try {
    assert.deepEqual(await claimInvitation("t".repeat(43)), { login_url: "/auth/login?return_to=/invite" })
    await acceptInvitation("csrf-1")
    assert.equal(header(fake.calls[0], "X-CSRF-Token"), null)
    assert.deepEqual(JSON.parse(String(fake.calls[0].init.body)), { token: "t".repeat(43) })
    assert.equal(header(fake.calls[1], "X-CSRF-Token"), "csrf-1")
  } finally { fake.restore() }
})

test("service errors keep their code and message; a bare gateway reply still says something", async () => {
  const fake = stubFetch(call => call.url.endsWith("/claim")
    ? json(410, { error: { code: "invitation_unavailable", message: "This invitation is no longer available." } })
    : new Response("", { status: 401 }))
  try {
    await assert.rejects(claimInvitation("x".repeat(43)), (error: AccountsError) =>
      error.status === 410 && error.code === "invitation_unavailable" && error.message === "This invitation is no longer available.")
    await assert.rejects(getMe(), (error: AccountsError) => error.status === 401 && error.code === "http_401" && error.message.length > 0)
  } finally { fake.restore() }
  const offline = stubFetch(() => { throw new TypeError("Failed to fetch") })
  try {
    await assert.rejects(getMe(), (error: AccountsError) => error.status === 0 && error.code === "network")
  } finally { offline.restore() }
})

test("the workspace sends the CSRF token on Ask only when signed in, and reports a lost session", async () => {
  const { askAgent, clearDataCache, getJSON, setCsrfToken, setSessionGuard } = await import("../src/api.ts")
  const answer = 'event: answer\ndata: {"answer_text":"ok","status":"answer"}\n\n'
  const fake = stubFetch(call => call.url.endsWith("/ask/stream") ? new Response(answer, { status: 200 }) : new Response("", { status: 401 }))
  const seen: number[] = []
  try {
    await askAgent("q", new AbortController().signal, () => undefined)
    assert.equal(header(fake.calls[0], "X-CSRF-Token"), null, "the anonymous Pages build sends no token")
    setCsrfToken("csrf-1")
    await askAgent("q", new AbortController().signal, () => undefined)
    assert.equal(header(fake.calls[1], "X-CSRF-Token"), "csrf-1")
    setSessionGuard(status => seen.push(status))
    clearDataCache()
    await assert.rejects(getJSON("/api/data-query/summary?x=1"))
    assert.deepEqual(seen, [401])
  } finally {
    setCsrfToken(null)
    setSessionGuard(null)
    fake.restore()
  }
})

test("administrator calls filter by query and write with the session's CSRF token", async () => {
  const fake = stubFetch(() => json(200, { items: [], next_cursor: null }))
  try {
    await listUsers({ status: "active", q: "ada lovelace", cursor: undefined })
    await listUsers({ status: null, q: "" })
    await listRequests("pending", "c-1")
    await updateUser("csrf-1", "u-1", { status: "suspended" })
    await decideRequest("csrf-1", "r-1", { decision: "approve", note: "", public_note: "Welcome" })
    assert.deepEqual(fake.calls.slice(0, 3).map(call => call.url), [
      "/api/admin/users?status=active&q=ada+lovelace", "/api/admin/users", "/api/admin/access-requests?status=pending&cursor=c-1",
    ])
    const [patch, decide] = fake.calls.slice(3)
    assert.equal(patch.init.method, "PATCH")
    assert.equal(header(patch, "X-CSRF-Token"), "csrf-1")
    assert.deepEqual(JSON.parse(String(patch.init.body)), { status: "suspended" })
    assert.equal(decide.url, "/api/admin/access-requests/r-1/decision")
    assert.deepEqual(JSON.parse(String(decide.init.body)), { decision: "approve", note: "", public_note: "Welcome" })
  } finally { fake.restore() }
})

test("a failed sign-in lands on the explanation page the service redirects to, with only a safe code", () => {
  const app = readFileSync(fileURLToPath(new URL("../../services/accounts/app.py", import.meta.url)), "utf8")
  assert.ok(app.includes(`"${SIGN_IN_ERROR_PATH}?"`), "sign_in_failed in app.py redirects elsewhere")
  for (const session of [null, me("active"), me("pending"), me("active", "admin")]) {
    assert.deepEqual(routeFor(SIGN_IN_ERROR_PATH, session), { view: { page: "sign-in-error" }, path: SIGN_IN_ERROR_PATH })
  }
  assert.equal(signInErrorCode("?code=invalid_login_flow"), "invalid_login_flow")
  assert.equal(signInErrorCode("?code=<script>"), "unknown")
  assert.equal(signInErrorCode(""), "unknown")
  assert.equal(signInErrorCode("?code=" + "a".repeat(65)), "unknown")
})

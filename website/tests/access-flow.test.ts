import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import {
  APPLICATION_LIMITS,
  LOGIN_RETURN_PATHS,
  accessStage,
  applicationErrors,
  formatDay,
  loginUrl,
  trimmedDraft,
  type AccessApplication,
  type AccountUser,
} from "../src/access/accessFlow.ts"

const accounts = (name: string) => readFileSync(fileURLToPath(new URL(`../../services/accounts/${name}`, import.meta.url)), "utf8")

const user = (status: AccountUser["status"]): AccountUser => ({ id: "u", name: "Alex", email: "a@example.org", role: "member", status })
const request = (status: AccessApplication["status"]): AccessApplication => ({
  id: "r", organization: "Lab", purpose: "Study", status, public_note: "", created_at: "2026-10-04T15:20:00Z", reviewed_at: null,
})

test("login return paths are exactly the ones the accounts service accepts", () => {
  const allowed = accounts("security.py").match(/parsed\.path not in \(([^)]*)\)/)
  assert.ok(allowed, "return_path allow-list not found in services/accounts/security.py")
  const paths = [...allowed[1].matchAll(/"([^"]+)"/g)].map(match => match[1])
  assert.deepEqual([...LOGIN_RETURN_PATHS].sort(), paths.sort())
})

test("login urls carry the return path as one encoded query value", () => {
  assert.equal(loginUrl("/workspace"), "/auth/login?return_to=%2Fworkspace")
  assert.equal(new URL(loginUrl("/access-status"), "https://example.org").searchParams.get("return_to"), "/access-status")
})

test("application limits match the service schema", () => {
  const schema = accounts("schemas.py").match(/class AccessApplication\(Input\):([\s\S]*?)\n\n/)
  assert.ok(schema, "AccessApplication not found in services/accounts/schemas.py")
  const limits = Object.fromEntries([...schema[1].matchAll(/(\w+): str = Field\(min_length=1, max_length=(\d+)\)/g)].map(match => [match[1], Number(match[2])]))
  assert.deepEqual(limits, APPLICATION_LIMITS)
})

test("membership status decides before the request does", () => {
  assert.equal(accessStage(user("active"), null), "active")
  assert.equal(accessStage(user("active"), request("pending")), "active")
  assert.equal(accessStage(user("suspended"), request("pending")), "suspended")
  assert.equal(accessStage(user("pending"), null), "apply")
  assert.equal(accessStage(user("pending"), request("pending")), "review")
  assert.equal(accessStage(user("pending"), request("rejected")), "declined")
})

test("fields are required after trimming and bounded by the service limits", () => {
  assert.deepEqual(Object.keys(applicationErrors({ name: "  ", organization: "", purpose: "\n" })), ["name", "organization", "purpose"])
  assert.deepEqual(applicationErrors({ name: " Alex ", organization: "Lab", purpose: "Study" }), {})
  const tooLong = applicationErrors({ name: "Alex", organization: "x".repeat(301), purpose: "Study" })
  assert.equal(tooLong.organization, "Use at most 300 characters.")
  assert.deepEqual(applicationErrors({ name: "Alex", organization: ` ${"x".repeat(300)} `, purpose: "Study" }), {})
  assert.deepEqual(trimmedDraft({ name: " Alex ", organization: " Lab", purpose: "Study\n" }), { name: "Alex", organization: "Lab", purpose: "Study" })
})

test("submission dates read as a calendar day, and unknown values pass through", () => {
  assert.match(formatDay("2026-10-04T15:20:00Z"), /^Oct [45], 2026$/)
  assert.equal(formatDay("2026-10-05"), "Oct 5, 2026")
  assert.equal(formatDay("not a date"), "not a date")
})

// Which page a path shows for the current session. The accounts service decides
// membership; this only picks the page, and the gateway still refuses every
// business call from an account that is not active.
import type { LoginReturnPath } from "./accessFlow.ts"
import type { Me } from "./accountsApi.ts"

/** Where the accounts service sends a failed sign-in (`sign_in_failed` in services/accounts/app.py). */
export const SIGN_IN_ERROR_PATH = "/sign-in-error"

/** The service redirects there with a stable code; anything else reads as unknown. */
export function signInErrorCode(search: string): string {
  const code = new URLSearchParams(search).get("code") ?? ""
  return /^[a-z0-9_]{1,64}$/.test(code) ? code : "unknown"
}

export type View =
  | { page: "landing"; returnTo: LoginReturnPath }
  | { page: "invite-claim" }
  | { page: "invite-accept" }
  | { page: "status" }
  | { page: "workspace" }
  | { page: "console" }
  | { page: "sign-in-error" }

/** The page to show and the path the address bar should read. */
export function routeFor(path: string, me: Me | null): { view: View; path: string } {
  // A failed sign-in explains itself whoever is signed in.
  if (path === SIGN_IN_ERROR_PATH) return { view: { page: "sign-in-error" }, path }
  if (!me) {
    if (path === "/invite") return { view: { page: "invite-claim" }, path }
    // A deep link to the workspace or the console returns there after sign-in.
    return path === "/workspace" || path === "/admin"
      ? { view: { page: "landing", returnTo: path }, path }
      : { view: { page: "landing", returnTo: "/workspace" }, path: "/" }
  }
  const { status } = me.user
  // The service binds an invitation to the session that claimed it; a suspended
  // account cannot accept one.
  if (path === "/invite" && status !== "suspended") return { view: { page: "invite-accept" }, path }
  if (status !== "active") return { view: { page: "status" }, path: "/access-status" }
  // The console is for administrators; the service refuses its calls to anyone else.
  if (path === "/admin" && me.user.role === "admin") return { view: { page: "console" }, path }
  return { view: { page: "workspace" }, path: "/workspace" }
}

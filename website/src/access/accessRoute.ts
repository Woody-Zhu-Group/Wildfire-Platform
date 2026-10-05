// Which page a path shows for the current session. The accounts service decides
// membership; this only picks the page, and the gateway still refuses every
// business call from an account that is not active.
import type { LoginReturnPath } from "./accessFlow.ts"
import type { Me } from "./accountsApi.ts"

export type View =
  | { page: "landing"; returnTo: LoginReturnPath }
  | { page: "invite-claim" }
  | { page: "invite-accept" }
  | { page: "status" }
  | { page: "workspace" }

/** The page to show and the path the address bar should read. */
export function routeFor(path: string, me: Me | null): { view: View; path: string } {
  if (!me) {
    if (path === "/invite") return { view: { page: "invite-claim" }, path }
    // A deep link to the workspace returns there after sign-in.
    return path === "/workspace"
      ? { view: { page: "landing", returnTo: "/workspace" }, path }
      : { view: { page: "landing", returnTo: "/workspace" }, path: "/" }
  }
  const { status } = me.user
  // The service binds an invitation to the session that claimed it; a suspended
  // account cannot accept one.
  if (path === "/invite" && status !== "suspended") return { view: { page: "invite-accept" }, path }
  if (status !== "active") return { view: { page: "status" }, path: "/access-status" }
  return { view: { page: "workspace" }, path: "/workspace" }
}

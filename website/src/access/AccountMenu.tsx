import { useState } from "react"
import type { AccountUser } from "./accessFlow.ts"
import "./access.css"

/** Signed-in controls in the workspace header, beside the theme toggle. */
export function AccountMenu({ user, onSignOut }: { user: AccountUser; onSignOut: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState("")
  const signOut = async () => {
    setBusy(true)
    setFailed("")
    try {
      await onSignOut()
    } catch (error) {
      setFailed(error instanceof Error ? error.message : "Sign-out failed. Please try again.")
      setBusy(false)
    }
  }
  return <>
    <span className="access-account" title={user.email}>{user.email}</span>
    {user.role === "admin" && <a className="access-text-button console-header-link" href="/admin">Console</a>}
    <button type="button" className="access-text-button" onClick={signOut} disabled={busy} aria-describedby={failed ? "account-menu-error" : undefined}>
      {busy ? "Signing out…" : "Sign out"}
    </button>
    {failed && <span id="account-menu-error" className="access-header-error" role="alert">{failed}</span>}
  </>
}

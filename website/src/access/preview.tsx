// Development preview of the access pages (`npm run dev`, then
// /preview/access.html). Not part of the production build. Sample accounts
// stand in for the accounts service; links to /auth/login open the state a new
// or returning account would reach instead of the identity provider. Submitting
// a request whose intended use is "fail" previews a service error.
import React, { useEffect, useState } from "react"
import ReactDOM from "react-dom/client"
import "../index.css"
import "./preview.css"
import { applyTheme, readStoredTheme } from "../theme.ts"
import type { AccessApplication, AccountUser, ApplicationDraft } from "./accessFlow.ts"
import { AccessStatus } from "./AccessStatus.tsx"
import { Landing } from "./Landing.tsx"

const STATES = ["landing", "apply", "review", "declined", "approved", "suspended"] as const
type PreviewState = typeof STATES[number]

const USER: AccountUser = { id: "preview-user", name: "Alex Rivera", email: "alex.rivera@example.org", role: "member", status: "pending" }
const SUBMITTED: AccessApplication = {
  id: "preview-request",
  organization: "Example Policy Lab",
  purpose: "Comparing utility ignition trends with EPSS outages for a regulatory review.",
  status: "pending",
  public_note: "",
  created_at: "2026-10-04T15:20:00Z",
  reviewed_at: null,
}

function stateFromHash(): PreviewState {
  const value = location.hash.slice(1)
  return (STATES as readonly string[]).includes(value) ? value as PreviewState : "landing"
}

function sample(state: PreviewState): { user: AccountUser; application: AccessApplication | null } {
  switch (state) {
    case "review": return { user: USER, application: SUBMITTED }
    case "declined": return { user: USER, application: { ...SUBMITTED, status: "rejected", public_note: "Please name the proceeding or study this access supports.", reviewed_at: "2026-10-05T10:00:00Z" } }
    case "approved": return { user: { ...USER, status: "active" }, application: { ...SUBMITTED, status: "approved", reviewed_at: "2026-10-05T10:00:00Z" } }
    case "suspended": return { user: { ...USER, status: "suspended" }, application: null }
    default: return { user: { ...USER, name: "" }, application: null }
  }
}

function Preview() {
  const [state, setState] = useState(stateFromHash)
  const [submitted, setSubmitted] = useState<{ user: AccountUser; application: AccessApplication } | null>(null)
  useEffect(() => {
    const changed = () => { setState(stateFromHash()); setSubmitted(null) }
    // Sign-in links would leave for the identity provider; show where they lead.
    const intercept = (event: MouseEvent) => {
      const link = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>("a[href^='/auth/login']") : null
      if (!link) return
      event.preventDefault()
      const returnTo = new URL(link.href).searchParams.get("return_to")
      if (returnTo === "/workspace") location.assign("/")
      else location.hash = "apply"
    }
    window.addEventListener("hashchange", changed)
    document.addEventListener("click", intercept, true)
    return () => { window.removeEventListener("hashchange", changed); document.removeEventListener("click", intercept, true) }
  }, [])
  const account = submitted ?? sample(state)
  // Like the service: the name is saved on the account, then the account is read again.
  async function submit(draft: ApplicationDraft) {
    await new Promise(resolve => setTimeout(resolve, 700))
    if (draft.purpose.toLowerCase() === "fail") throw new Error("Account services are temporarily unavailable.")
    setSubmitted({
      user: { ...account.user, name: draft.name },
      application: { ...SUBMITTED, organization: draft.organization, purpose: draft.purpose, created_at: new Date().toISOString() },
    })
  }
  return <>
    {state === "landing"
      ? <Landing />
      : <AccessStatus user={account.user} application={account.application} onSubmit={submit} onSignOut={() => { location.hash = "landing" }} />}
    <nav className="access-preview-states" aria-label="Preview states">
      {STATES.map(item => <a key={item} href={`#${item}`} aria-current={item === state ? "page" : undefined}>{item}</a>)}
    </nav>
  </>
}

applyTheme(readStoredTheme())
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><Preview /></React.StrictMode>)

import { useEffect, useRef, useState } from "react"
import { AccessHeader } from "./AccessHeader.tsx"
import { AccountsError, acceptInvitation, claimInvitation, type Me } from "./accountsApi.ts"
import "./access.css"

type Step =
  | { kind: "working"; text: string }
  | { kind: "problem"; title: string; text: string; signOut?: boolean; workspace?: boolean }

const ASK_AGAIN = "Ask an administrator to send a new invitation."

/** Reads `#token=` from the address and removes it, so the token stays out of history. */
function takeToken(): string | null {
  const token = new URLSearchParams(location.hash.slice(1)).get("token")
  if (location.hash) history.replaceState(null, "", location.pathname)
  return token
}

/**
 * /invite. With the emailed token in the address: claim it, which binds it to
 * this browser, then sign in again, whoever is signed in now. Back from that
 * sign-in, without a token: accept it.
 */
export function InvitePage({ me, onAccepted, onSignOut }: { me: Me | null; onAccepted: () => Promise<void>; onSignOut: () => Promise<void> }) {
  const [step, setStep] = useState<Step>({ kind: "working", text: me ? "Accepting your invitation…" : "Opening your invitation…" })
  const started = useRef(false)
  useEffect(() => {
    // Claiming or accepting twice would fail the second time; run once per page load.
    if (started.current) return
    started.current = true
    const token = takeToken()
    if (token || !me) {
      if (!token) {
        setStep({ kind: "problem", title: "Open the link from your invitation email", text: "This page needs the full link from the email." })
        return
      }
      setStep({ kind: "working", text: "Opening your invitation…" })
      claimInvitation(token)
        .then(({ login_url }) => { setStep({ kind: "working", text: "Continuing to sign-in…" }); location.assign(login_url) })
        .catch(error => setStep({ kind: "problem", title: "This invitation cannot be opened", text: `${error instanceof Error ? error.message : ""} ${ASK_AGAIN}`.trim() }))
      return
    }
    acceptInvitation(me.csrf_token)
      .then(onAccepted)
      .catch(error => {
        const code = error instanceof AccountsError ? error.code : ""
        if (code === "invitation_identity_mismatch") {
          setStep({ kind: "problem", title: "This invitation is for a different email", text: `You are signed in as ${me.user.email}. Sign out, open the invitation link again and sign in with the invited address.`, signOut: true })
        } else {
          setStep({ kind: "problem", title: "This invitation is no longer available", text: me.user.status === "active" ? "Your account is already active." : `${error instanceof Error ? error.message : ""} ${ASK_AGAIN}`.trim(), workspace: me.user.status === "active", signOut: me.user.status !== "active" })
        }
      })
  }, [me, onAccepted])
  return (
    <div className="access-page">
      <AccessHeader>
        {me && <button type="button" className="access-text-button" onClick={() => { onSignOut().catch(() => undefined) }}>Sign out</button>}
      </AccessHeader>
      <main className="access-status">
        <article className="access-card" aria-labelledby="invite-title" aria-busy={step.kind === "working"}>
          {step.kind === "working"
            ? <><h1 id="invite-title">Invitation</h1><p className="loading-text" role="status">{step.text}</p></>
            : <>
              <h1 id="invite-title">{step.title}</h1>
              <p role="alert">{step.text}</p>
              {step.workspace && <a className="access-primary access-card-action" href="/workspace">Open workspace<span aria-hidden="true">→</span></a>}
              {step.signOut && <button type="button" className="quiet-button" onClick={() => { onSignOut().catch(() => undefined) }}>Sign out</button>}
            </>}
        </article>
      </main>
    </div>
  )
}

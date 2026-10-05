import { useCallback, useEffect, useRef, useState } from "react"
import App from "../App.tsx"
import { setCsrfToken, setSessionGuard } from "../api.ts"
import { AccessHeader } from "./AccessHeader.tsx"
import { AccessStatus } from "./AccessStatus.tsx"
import { AdminConsole } from "./AdminConsole.tsx"
import { AccountMenu } from "./AccountMenu.tsx"
import { AccountsError, getMe, signOut, submitApplication, type Me } from "./accountsApi.ts"
import type { ApplicationDraft } from "./accessFlow.ts"
import { routeFor } from "./accessRoute.ts"
import { InvitePage } from "./InvitePage.tsx"
import { Landing } from "./Landing.tsx"
import "./access.css"

type Session =
  | { state: "loading" }
  | { state: "unavailable"; message: string }
  | { state: "anonymous"; notice?: string }
  | { state: "signed-in"; me: Me }

const SESSION_ENDED = "Your session ended. Sign in again to continue."

/**
 * The site behind the accounts gateway. `GET /api/auth/me` decides which page a
 * path shows; a 401 or 403 from any later request reads the account again.
 */
export function AccountsRoot() {
  const [session, setSession] = useState<Session>({ state: "loading" })
  const [path, setPath] = useState(location.pathname)
  const reading = useRef<Promise<void> | null>(null)

  // Concurrent panels can all see the same 401; read the account once.
  const readAccount = useCallback((notice?: string) => {
    reading.current ??= getMe()
      .then(me => { setCsrfToken(me.csrf_token); setSession({ state: "signed-in", me }) })
      .catch(error => {
        setCsrfToken(null)
        if (error instanceof AccountsError && error.status === 401) setSession({ state: "anonymous", notice })
        else setSession({ state: "unavailable", message: error instanceof Error ? error.message : String(error) })
      })
      .finally(() => { reading.current = null })
    return reading.current
  }, [])

  useEffect(() => { readAccount() }, [readAccount])
  useEffect(() => {
    setSessionGuard(status => { readAccount(status === 401 ? SESSION_ENDED : undefined) })
    const back = () => setPath(location.pathname)
    window.addEventListener("popstate", back)
    return () => { setSessionGuard(null); window.removeEventListener("popstate", back) }
  }, [readAccount])

  const me = session.state === "signed-in" ? session.me : null
  const route = session.state === "signed-in" || session.state === "anonymous" ? routeFor(path, me) : null
  useEffect(() => {
    if (route && route.path !== location.pathname) {
      history.replaceState(null, "", route.path + location.hash)
      setPath(route.path)
    }
  }, [route?.path])

  // A full navigation after sign-out drops every request the page still has open,
  // including an Ask stream.
  const leave = useCallback(async () => {
    try {
      await signOut(me?.csrf_token ?? null)
    } catch (error) {
      if (!(error instanceof AccountsError && error.status === 401)) throw error
    }
    location.assign("/")
  }, [me])
  const submit = useCallback(async (draft: ApplicationDraft) => {
    try {
      await submitApplication(me!.csrf_token, draft)
    } catch (error) {
      if (error instanceof AccountsError && error.status === 401) await readAccount(SESSION_ENDED)
      throw error
    }
    await readAccount()
  }, [me, readAccount])
  const lost = useCallback((status: number) => { readAccount(status === 401 ? SESSION_ENDED : undefined) }, [readAccount])
  const accepted = useCallback(async () => {
    await readAccount()
    history.replaceState(null, "", "/workspace")
    setPath("/workspace")
  }, [readAccount])

  if (session.state === "loading") return <div className="access-loading" aria-busy="true"><span className="loading-text">Loading…</span></div>
  if (session.state === "unavailable") return (
    <div className="access-page">
      <AccessHeader />
      <main className="access-status">
        <article className="access-card" aria-labelledby="unavailable-title">
          <h1 id="unavailable-title">Wildfire is unavailable right now</h1>
          <p role="alert">{session.message}</p>
          <button type="button" className="access-primary access-card-action" onClick={() => { setSession({ state: "loading" }); readAccount() }}>Try again</button>
        </article>
      </main>
    </div>
  )
  const view = route!.view
  switch (view.page) {
    case "landing": return <Landing returnTo={view.returnTo} notice={session.state === "anonymous" ? session.notice : undefined} />
    case "invite-claim": return <InvitePage me={null} onAccepted={accepted} onSignOut={leave} />
    case "invite-accept": return <InvitePage me={me} onAccepted={accepted} onSignOut={leave} />
    case "status": return <AccessStatus user={me!.user} application={me!.application} onSubmit={submit} onSignOut={() => { leave().catch(() => undefined) }} />
    case "workspace": return <App account={<AccountMenu user={me!.user} onSignOut={leave} />} />
    case "console": return <AdminConsole me={me!} onSignOut={leave} onSessionLost={lost} />
  }
}

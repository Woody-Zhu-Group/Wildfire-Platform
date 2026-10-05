import { useEffect, useId, useRef, useState, type KeyboardEvent, type MouseEvent } from "react"
import { loginUrl, type LoginReturnPath } from "./accessFlow.ts"
import "./access.css"

export type AccessTab = "sign-in" | "request"

const TABS: { id: AccessTab; label: string }[] = [
  { id: "sign-in", label: "Sign in" },
  { id: "request", label: "Request access" },
]

// Accounts are created on the identity provider's page; the request form
// opens on /access-status once the new account is signed in.
const STEPS = [
  { title: "Create an account", detail: "Sign up on the secure sign-in page and verify your email." },
  { title: "Describe your work", detail: "Your name, organization and how you plan to use the data." },
  { title: "Wait for review", detail: "An administrator reviews each request. We email you the decision." },
]

/**
 * Sign in and request access share one dialog; both continue on the identity
 * provider's page. `notice` says why an action asked for an account.
 */
export function AccessDialog({ initialTab, notice, signInReturn = "/workspace", onClose }: {
  initialTab: AccessTab; notice?: string; signInReturn?: LoginReturnPath; onClose: () => void
}) {
  const [tab, setTab] = useState(initialTab)
  const [leaving, setLeaving] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const tabs = useRef<Partial<Record<AccessTab, HTMLButtonElement | null>>>({})
  const id = useId()
  useEffect(() => {
    const element = dialog.current!
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previous = document.documentElement.style.overflow
    document.documentElement.style.overflow = "hidden"
    element.showModal()
    tabs.current[initialTab]?.focus()
    // Back from the sign-in page can restore this page from the back/forward cache.
    const restored = (event: PageTransitionEvent) => { if (event.persisted) setLeaving(false) }
    window.addEventListener("pageshow", restored)
    return () => {
      window.removeEventListener("pageshow", restored)
      element.close()
      document.documentElement.style.overflow = previous
      opener?.focus()
    }
  }, [initialTab])
  function select(next: AccessTab, focus = false) {
    setTab(next)
    if (focus) tabs.current[next]?.focus()
  }
  function moveTab(event: KeyboardEvent<HTMLButtonElement>) {
    const index = TABS.findIndex(item => item.id === tab)
    const target = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: TABS.length - 1 }[event.key]
    if (target === undefined) return
    event.preventDefault()
    select(TABS[(target + TABS.length) % TABS.length].id, true)
  }
  function leave(event: MouseEvent<HTMLAnchorElement>) {
    // A modified click opens another tab or window; this page stays usable.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    if (leaving) event.preventDefault()
    else setLeaving(true)
  }
  const continueLink = (href: string, label: string) => (
    <a className="access-primary" href={href} onClick={leave} aria-disabled={leaving || undefined}>
      {leaving ? "Opening sign-in…" : <>{label}<span aria-hidden="true">→</span></>}
    </a>
  )
  return (
    <dialog ref={dialog} className="access-dialog" aria-label="Sign in or request access"
      onCancel={event => { event.preventDefault(); onClose() }}
      onClick={event => { if (event.target === event.currentTarget) onClose() }}>
      <div className="access-dialog-body">
        <header>
          <div className="site-brand">Wildfire <span>Analysis workspace</span></div>
          <button type="button" aria-label="Close" onClick={onClose}>×</button>
        </header>
        {notice && <p className="access-dialog-notice" role="status">{notice}</p>}
        <div className="measure-switch access-tabs" role="tablist" aria-label="Account access">
          {TABS.map(item => (
            <button key={item.id} ref={element => { tabs.current[item.id] = element }} type="button" role="tab"
              id={`${id}-${item.id}-tab`} aria-controls={`${id}-${item.id}`} aria-selected={tab === item.id}
              tabIndex={tab === item.id ? 0 : -1}
              onClick={() => select(item.id)} onKeyDown={moveTab}>{item.label}</button>
          ))}
        </div>
        {tab === "sign-in" ? (
          <section className="access-panel" role="tabpanel" id={`${id}-sign-in`} aria-labelledby={`${id}-sign-in-tab`}>
            <div className="access-panel-copy">
              <h2>Welcome back</h2>
              <p>Continue on the secure sign-in page, then return to your workspace.</p>
            </div>
            {continueLink(loginUrl(signInReturn), "Continue to sign in")}
            <p className="access-dialog-note">Invited by email? Open the link in your invitation.</p>
          </section>
        ) : (
          <section className="access-panel" role="tabpanel" id={`${id}-request`} aria-labelledby={`${id}-request-tab`}>
            <ol className="access-steps access-panel-copy">
              {STEPS.map(step => <li key={step.title}><strong>{step.title}</strong><span>{step.detail}</span></li>)}
            </ol>
            {continueLink(loginUrl("/access-status"), "Create an account")}
            <p className="access-dialog-note">
              Already have an account? <button type="button" className="text-button" onClick={() => select("sign-in", true)}>Sign in</button>
            </p>
          </section>
        )}
      </div>
    </dialog>
  )
}

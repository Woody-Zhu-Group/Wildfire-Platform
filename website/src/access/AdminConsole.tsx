import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react"
import { AccessHeader } from "./AccessHeader.tsx"
import { formatDay } from "./accessFlow.ts"
import {
  AccountsError, createInvitation, decideRequest, listAudit, listInvitations, listRequests, listUsers, resendDecision,
  resendInvitation, revokeInvitation, updateUser,
  type AdminInvitation, type AdminRequest, type AdminUser, type AuditEvent, type Me, type Page,
} from "./accountsApi.ts"
import "./access.css"

type Tab = "requests" | "invitations" | "members" | "audit"
const TABS: { id: Tab; label: string }[] = [
  { id: "requests", label: "Requests" },
  { id: "invitations", label: "Invitations" },
  { id: "members", label: "Members" },
  { id: "audit", label: "Audit" },
]
const tabFromHash = (): Tab => TABS.find(tab => "#" + tab.id === location.hash)?.id ?? "requests"

type Notice = { tone: "ok" | "problem"; text: string } | null
interface Context {
  csrf: string
  me: Me
  /** Runs an administrator call; returns undefined when it failed and the failure is already shown. */
  run: <T>(call: () => Promise<T>, success?: (result: T) => string | null) => Promise<T | undefined>
}

/**
 * /admin: review access requests, send invitations, manage members and read
 * the audit history. The server rechecks the active admin role on every call;
 * a 401 or 403 hands the account back to the root to be read again.
 */
export function AdminConsole({ me, onSignOut, onSessionLost }: {
  me: Me; onSignOut: () => Promise<void>; onSessionLost: (status: number) => void
}) {
  const [tab, setTab] = useState<Tab>(tabFromHash)
  const [notice, setNotice] = useState<Notice>(null)
  useEffect(() => {
    const changed = () => setTab(tabFromHash())
    window.addEventListener("hashchange", changed)
    return () => window.removeEventListener("hashchange", changed)
  }, [])
  const run: Context["run"] = useCallback(async (call, success) => {
    try {
      const result = await call()
      // Actions report their outcome; a list load leaves the last outcome in place.
      if (success) {
        const text = success(result)
        setNotice(text ? { tone: "ok", text } : null)
      }
      return result
    } catch (error) {
      if (error instanceof AccountsError && (error.status === 401 || error.code === "admin_required")) onSessionLost(error.status)
      setNotice({ tone: "problem", text: error instanceof Error ? error.message : String(error) })
      return undefined
    }
  }, [onSessionLost])
  const context: Context = { csrf: me.csrf_token, me, run }
  const select = (next: Tab) => {
    history.replaceState(null, "", "#" + next)
    setTab(next)
    setNotice(null)
  }
  return (
    <div className="access-page">
      <AccessHeader>
        <span className="access-account" title={me.user.email}>{me.user.email}</span>
        <a className="access-text-button console-header-link" href="/workspace">Workspace</a>
        <button type="button" className="access-text-button" onClick={() => { onSignOut().catch(error => setNotice({ tone: "problem", text: String(error?.message ?? error) })) }}>Sign out</button>
      </AccessHeader>
      <main className="console">
        <header className="console-heading">
          <h1>Console</h1>
          <div className="measure-switch console-tabs" role="tablist" aria-label="Console sections">
            {TABS.map(item => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} onClick={() => select(item.id)}>{item.label}</button>)}
          </div>
        </header>
        {notice && <p className={`console-notice is-${notice.tone}`} role={notice.tone === "problem" ? "alert" : "status"}>{notice.text}</p>}
        <section role="tabpanel" aria-label={TABS.find(item => item.id === tab)!.label}>
          {tab === "requests" && <Requests {...context} />}
          {tab === "invitations" && <Invitations {...context} />}
          {tab === "members" && <Members {...context} />}
          {tab === "audit" && <Audit {...context} />}
        </section>
      </main>
    </div>
  )
}

/** One cursor-paged list: first page on change of `key`, more pages on request. */
function usePages<T>(key: string, load: (cursor?: string) => Promise<Page<T>>, run: Context["run"]) {
  const [items, setItems] = useState<T[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  loadRef.current = load
  const fetchPage = useCallback(async (cursor?: string) => {
    setLoading(true)
    const page = await run(() => loadRef.current(cursor))
    if (page) {
      setItems(current => cursor ? [...current, ...page.items] : page.items)
      setNext(page.next_cursor)
    }
    setLoading(false)
  }, [run])
  useEffect(() => { fetchPage() }, [key, fetchPage])
  const replace = (match: (item: T) => boolean, change: (item: T) => T) => setItems(current => current.map(item => match(item) ? change(item) : item))
  return { items, loading, more: next ? () => fetchPage(next) : null, reload: () => fetchPage(), replace }
}

function ListFooter({ loading, more, empty, count }: { loading: boolean; more: (() => void) | null; empty: string; count: number }) {
  if (loading && !count) return <p className="console-empty loading-text">Loading…</p>
  if (!count) return <p className="console-empty">{empty}</p>
  return more ? <button type="button" className="quiet-button console-more" onClick={more} disabled={loading}>{loading ? "Loading…" : "Show more"}</button> : null
}

function Badge({ tone, children }: { tone: "pending" | "good" | "bad" | "admin"; children: ReactNode }) {
  return <span className={`console-badge is-${tone}`}>{children}</span>
}

const STATUS_FILTERS: { id: AdminRequest["status"]; label: string }[] = [
  { id: "pending", label: "Pending" }, { id: "approved", label: "Approved" }, { id: "rejected", label: "Declined" },
]

function Requests({ csrf, run }: Context) {
  const [status, setStatus] = useState<AdminRequest["status"]>("pending")
  const list = usePages(status, cursor => listRequests(status, cursor), run)
  const [deciding, setDeciding] = useState<{ request: AdminRequest; decision: "approve" | "reject" } | null>(null)
  const mailed = (state: string) => state === "sent" ? "The applicant was emailed." : "The email could not be sent; use Resend email."
  return <>
    <div className="console-toolbar">
      <div className="measure-switch" role="group" aria-label="Request status">
        {STATUS_FILTERS.map(item => <button key={item.id} type="button" aria-pressed={status === item.id} onClick={() => setStatus(item.id)}>{item.label}</button>)}
      </div>
    </div>
    <ul className="console-list" aria-label={`${STATUS_FILTERS.find(item => item.id === status)!.label} requests`}>
      {list.items.map(request => (
        <li key={request.id} className="console-request">
          <div className="console-request-who">
            <strong>{request.applicant_name || request.applicant_email}</strong>
            <span>{request.applicant_email}</span>
          </div>
          <dl className="console-request-facts">
            <div><dt>Organization</dt><dd>{request.organization}</dd></div>
            <div><dt>Intended use</dt><dd className="console-purpose">{request.purpose}</dd></div>
            <div><dt>Submitted</dt><dd>{formatDay(request.created_at)}</dd></div>
            {request.status !== "pending" && <div><dt>Reviewed</dt><dd>{request.reviewed_at ? formatDay(request.reviewed_at) : "—"} by {request.reviewer_email ?? "an invitation"}</dd></div>}
            {request.public_note && <div><dt>Message sent</dt><dd>{request.public_note}</dd></div>}
            {request.review_note && <div><dt>Internal note</dt><dd>{request.review_note}</dd></div>}
          </dl>
          <div className="console-actions">
            {request.status === "pending"
              ? <>
                <button type="button" className="access-primary console-button" onClick={() => setDeciding({ request, decision: "approve" })}>Approve</button>
                <button type="button" className="quiet-button console-button" onClick={() => setDeciding({ request, decision: "reject" })}>Decline</button>
              </>
              : <>
                <Badge tone={request.status === "approved" ? "good" : "bad"}>{request.status === "approved" ? "Approved" : "Declined"}</Badge>
                {request.notification_status === "failed" && <Badge tone="pending">Email failed</Badge>}
                {request.reviewer_email && <button type="button" className="text-button" onClick={() => run(() => resendDecision(csrf, request.id), result => {
                  list.replace(item => item.id === request.id, item => ({ ...item, notification_status: result.notification_status }))
                  return mailed(result.notification_status)
                })}>Resend email</button>}
              </>}
          </div>
        </li>
      ))}
    </ul>
    <ListFooter loading={list.loading} more={list.more} count={list.items.length} empty={status === "pending" ? "No requests are waiting for review." : "None yet."} />
    {deciding && <DecisionDialog {...deciding} onClose={() => setDeciding(null)} onDecide={async (note, publicNote) => {
      // Close either way so the outcome shows; a conflict means someone else decided first.
      await run(() => decideRequest(csrf, deciding.request.id, { decision: deciding.decision, note, public_note: publicNote }),
        outcome => `${deciding.decision === "approve" ? "Approved" : "Declined"} ${deciding.request.applicant_email}. ${mailed(outcome.notification_status)}`)
      setDeciding(null)
      list.reload()
    }} />}
  </>
}

function DecisionDialog({ request, decision, onClose, onDecide }: {
  request: AdminRequest; decision: "approve" | "reject"; onClose: () => void; onDecide: (note: string, publicNote: string) => Promise<void>
}) {
  const [publicNote, setPublicNote] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const id = useId()
  const verb = decision === "approve" ? "Approve" : "Decline"
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    await onDecide(note.trim(), publicNote.trim())
    setBusy(false)
  }
  return (
    <ConsoleDialog title={`${verb} ${request.applicant_name || request.applicant_email}?`} onClose={onClose}>
      <form className="access-form console-dialog-form" onSubmit={submit}>
        <p className="console-dialog-copy">{decision === "approve"
          ? `${request.applicant_email} can use the workspace as soon as you approve.`
          : `${request.applicant_email} keeps the account and can send a new request.`}</p>
        <div className="access-field">
          <label htmlFor={`${id}-public`}>Message to the applicant <span className="console-optional">optional, included in the email</span></label>
          <textarea id={`${id}-public`} rows={3} maxLength={1000} value={publicNote} onChange={event => setPublicNote(event.target.value)} />
        </div>
        <div className="access-field">
          <label htmlFor={`${id}-note`}>Internal note <span className="console-optional">optional, administrators only</span></label>
          <textarea id={`${id}-note`} rows={2} maxLength={2000} value={note} onChange={event => setNote(event.target.value)} />
        </div>
        <div className="console-dialog-actions">
          <button type="button" className="quiet-button" onClick={onClose}>Cancel</button>
          <button type="submit" className="access-primary console-button" disabled={busy}>{busy ? "Saving…" : verb}</button>
        </div>
      </form>
    </ConsoleDialog>
  )
}

function invitationState(invitation: AdminInvitation): { label: string; tone: "pending" | "good" | "bad"; open: boolean } {
  if (invitation.accepted_at) return { label: "Accepted", tone: "good", open: false }
  if (invitation.revoked_at) return { label: "Revoked", tone: "bad", open: false }
  if (new Date(invitation.expires_at).getTime() <= Date.now()) return { label: "Expired", tone: "bad", open: true }
  return { label: "Waiting", tone: "pending", open: true }
}

function Invitations({ csrf, run }: Context) {
  const list = usePages("invitations", listInvitations, run)
  const [email, setEmail] = useState("")
  const [busy, setBusy] = useState(false)
  const [revoking, setRevoking] = useState<AdminInvitation | null>(null)
  const sent = (state: string, address: string) => state === "sent" ? `Invitation emailed to ${address}.` : `The invitation for ${address} was saved, but the email could not be sent; use Resend.`
  const invite = async (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim()) return
    setBusy(true)
    const result = await run(() => createInvitation(csrf, email.trim()), outcome => sent(outcome.delivery_status, outcome.email))
    setBusy(false)
    if (result) { setEmail(""); list.reload() }
  }
  return <>
    <form className="console-toolbar console-invite" onSubmit={invite}>
      <label className="visually-hidden" htmlFor="console-invite-email">Email address to invite</label>
      <input id="console-invite-email" type="email" required maxLength={254} placeholder="name@institution.edu" value={email} onChange={event => setEmail(event.target.value)} />
      <button type="submit" className="access-primary console-button" disabled={busy}>{busy ? "Sending…" : "Send invitation"}</button>
      <span className="console-hint">The link works for 7 days and only for this address.</span>
    </form>
    <ul className="console-list" aria-label="Invitations">
      {list.items.map(invitation => {
        const state = invitationState(invitation)
        return (
          <li key={invitation.id} className="console-row">
            <div className="console-row-main"><strong>{invitation.email}</strong><span>Invited {formatDay(invitation.created_at)} by {invitation.invited_by_email ?? "an administrator"}{invitation.accepted_by_email ? ` · accepted by ${invitation.accepted_by_email}` : state.open ? ` · expires ${formatDay(invitation.expires_at)}` : ""}</span></div>
            <div className="console-actions">
              <Badge tone={state.tone}>{state.label}</Badge>
              {invitation.delivery_status === "failed" && state.open && <Badge tone="bad">Email failed</Badge>}
              {state.open && <>
                <button type="button" className="text-button" onClick={() => run(() => resendInvitation(csrf, invitation.id), outcome => { list.reload(); return sent(outcome.delivery_status, invitation.email) })}>Resend</button>
                <button type="button" className="text-button console-danger" onClick={() => setRevoking(invitation)}>Revoke</button>
              </>}
            </div>
          </li>
        )
      })}
    </ul>
    <ListFooter loading={list.loading} more={list.more} count={list.items.length} empty="No invitations yet." />
    {revoking && <ConfirmDialog title={`Revoke the invitation for ${revoking.email}?`} text="The link stops working. You can invite the address again later." action="Revoke"
      onClose={() => setRevoking(null)} onConfirm={async () => {
        await run(() => revokeInvitation(csrf, revoking.id).then(() => true), () => `Invitation for ${revoking.email} revoked.`)
        setRevoking(null)
        list.reload()
      }} />}
  </>
}

type MemberChange = { user: AdminUser; change: { role?: AdminUser["role"]; status?: "active" | "suspended" }; title: string; text: string; action: string }
const MEMBER_FILTERS: { id: AdminUser["status"] | null; label: string }[] = [
  { id: null, label: "All" }, { id: "active", label: "Active" }, { id: "pending", label: "Pending" }, { id: "suspended", label: "Suspended" },
]

function Members({ csrf, me, run }: Context) {
  const [status, setStatus] = useState<AdminUser["status"] | null>(null)
  const [search, setSearch] = useState("")
  const [applied, setApplied] = useState("")
  const list = usePages(`${status}|${applied}`, cursor => listUsers({ status, q: applied, cursor }), run)
  const [changing, setChanging] = useState<MemberChange | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => setApplied(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])
  const who = (user: AdminUser) => user.name || user.email
  return <>
    <div className="console-toolbar">
      <label className="visually-hidden" htmlFor="console-member-search">Search members by name or email</label>
      <input id="console-member-search" type="search" maxLength={254} placeholder="Search name or email" value={search} onChange={event => setSearch(event.target.value)} />
      <div className="measure-switch" role="group" aria-label="Member status">
        {MEMBER_FILTERS.map(item => <button key={item.label} type="button" aria-pressed={status === item.id} onClick={() => setStatus(item.id)}>{item.label}</button>)}
      </div>
    </div>
    <ul className="console-list" aria-label="Members">
      {list.items.map(user => {
        const self = user.id === me.user.id
        return (
          <li key={user.id} className="console-row">
            <div className="console-row-main"><strong>{who(user)}{self && <span className="console-you"> (you)</span>}</strong><span>{user.email} · joined {formatDay(user.created_at)}</span></div>
            <div className="console-actions">
              {user.role === "admin" && <Badge tone="admin">Admin</Badge>}
              <Badge tone={user.status === "active" ? "good" : user.status === "pending" ? "pending" : "bad"}>{user.status === "active" ? "Active" : user.status === "pending" ? "Awaiting review" : "Suspended"}</Badge>
              {user.status !== "pending" && !self && <>
                {user.status === "active" && <button type="button" className="text-button" onClick={() => setChanging(user.role === "admin"
                  ? { user, change: { role: "member" }, title: `Remove ${who(user)} as an administrator?`, text: "They keep workspace access.", action: "Remove admin" }
                  : { user, change: { role: "admin" }, title: `Make ${who(user)} an administrator?`, text: "Administrators review requests, send invitations and manage every member, including other administrators.", action: "Make admin" })}>
                  {user.role === "admin" ? "Remove admin" : "Make admin"}
                </button>}
                <button type="button" className={`text-button${user.status === "active" ? " console-danger" : ""}`} onClick={() => setChanging(user.status === "active"
                  ? { user, change: { status: "suspended" }, title: `Suspend ${who(user)}?`, text: "They are signed out everywhere and cannot use the workspace until restored.", action: "Suspend" }
                  : { user, change: { status: "active" }, title: `Restore ${who(user)}?`, text: "They can sign in and use the workspace again.", action: "Restore" })}>
                  {user.status === "active" ? "Suspend" : "Restore"}
                </button>
              </>}
            </div>
          </li>
        )
      })}
    </ul>
    <ListFooter loading={list.loading} more={list.more} count={list.items.length} empty={applied ? "No members match this search." : "No members in this group."} />
    {changing && <ConfirmDialog title={changing.title} text={changing.text} action={changing.action} onClose={() => setChanging(null)} onConfirm={async () => {
      const updated = await run(() => updateUser(csrf, changing.user.id, changing.change), () => `${changing.action}: ${changing.user.email}.`)
      setChanging(null)
      if (updated) list.replace(item => item.id === updated.id, item => ({ ...item, role: updated.role, status: updated.status }))
    }} />}
  </>
}

const ACTIONS: Record<string, string> = {
  access_requested: "requested access",
  access_approved: "approved the request of",
  access_rejected: "declined the request of",
  review_notification_requested: "resent the decision email to",
  invitation_created: "invited",
  invitation_resent: "resent the invitation to",
  invitation_revoked: "revoked the invitation for",
  invitation_accepted: "accepted the invitation for",
  access_granted_by_invitation: "joined by invitation, which closed the pending request of",
  account_updated: "changed the account of",
  admin_bootstrapped: "made an administrator:",
}

function Audit({ run }: Context) {
  const list = usePages("audit", listAudit, run)
  const details = (event: AuditEvent) => event.action === "account_updated" && event.metadata
    ? ` (role ${String(event.metadata.role)}, ${String(event.metadata.status)})` : ""
  return <>
    <ol className="console-list console-audit" aria-label="Audit history">
      {list.items.map(event => (
        <li key={event.id} className="console-row">
          <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })}</time>
          <span><strong>{event.actor_email ?? "Server command"}</strong> {ACTIONS[event.action] ?? event.action.replaceAll("_", " ")} {event.action === "access_requested" ? "" : <strong>{event.target_email ?? event.target_type}</strong>}{details(event)}</span>
        </li>
      ))}
    </ol>
    <ListFooter loading={list.loading} more={list.more} count={list.items.length} empty="No recorded actions yet." />
  </>
}

function ConsoleDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const element = dialog.current!
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element.showModal()
    return () => { element.close(); opener?.focus() }
  }, [])
  return (
    <dialog ref={dialog} className="access-dialog console-dialog" aria-labelledby={titleId}
      onCancel={event => { event.preventDefault(); onClose() }}
      onClick={event => { if (event.target === event.currentTarget) onClose() }}>
      <div className="access-dialog-body">
        <header><h2 id={titleId}>{title}</h2><button type="button" aria-label="Close" onClick={onClose}>×</button></header>
        {children}
      </div>
    </dialog>
  )
}

function ConfirmDialog({ title, text, action, onClose, onConfirm }: { title: string; text: string; action: string; onClose: () => void; onConfirm: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  return (
    <ConsoleDialog title={title} onClose={onClose}>
      <p className="console-dialog-copy">{text}</p>
      <div className="console-dialog-actions">
        <button type="button" className="quiet-button" onClick={onClose}>Cancel</button>
        <button type="button" className="access-primary console-button" disabled={busy} onClick={async () => { setBusy(true); await onConfirm(); setBusy(false) }}>{busy ? "Saving…" : action}</button>
      </div>
    </ConsoleDialog>
  )
}

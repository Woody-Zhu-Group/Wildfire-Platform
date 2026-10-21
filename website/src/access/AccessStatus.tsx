import { useId, useRef, useState, type FormEvent } from "react"
import {
  APPLICATION_FIELDS,
  APPLICATION_LIMITS,
  accessStage,
  applicationErrors,
  formatDay,
  trimmedDraft,
  type AccessApplication,
  type AccessStage,
  type AccountUser,
  type ApplicationDraft,
  type ApplicationErrors,
  type ApplicationField,
} from "./accessFlow.ts"
import { AccessHeader } from "./AccessHeader.tsx"
import "./access.css"

const PROGRESS = ["Account", "Request", "Review"]
const CURRENT_STEP: Record<Exclude<AccessStage, "suspended">, number> = { apply: 1, declined: 1, review: 2, active: 3 }

const FIELDS: Record<ApplicationField, { label: string; autoComplete?: string; placeholder?: string }> = {
  name: { label: "Full name", autoComplete: "name" },
  organization: { label: "Organization", autoComplete: "organization" },
  purpose: { label: "How you plan to use the platform", placeholder: "For example, reviewing utility ignition trends for a regulatory proceeding." },
}

/**
 * /access-status for a signed-in account that cannot use the workspace yet:
 * the request form, the pending review, a declined request, or a suspension.
 * `onSubmit` resolves once the account has been read again (`GET /api/auth/me`):
 * the service also saves the submitted name, and its POST response carries
 * only the request id, status and time. It rejects with an Error whose message
 * is shown to the applicant.
 */
export function AccessStatus({ user, application, onSubmit, onSignOut }: {
  user: AccountUser
  application: AccessApplication | null
  onSubmit: (draft: ApplicationDraft) => Promise<void>
  onSignOut: () => void
}) {
  const stage = accessStage(user, application)
  return (
    <div className="access-page">
      <AccessHeader>
        <span className="access-account" title={user.email}>{user.email}</span>
        <button type="button" className="access-text-button" onClick={onSignOut}>Sign out</button>
      </AccessHeader>
      <main className="access-status">
        <article className="access-card" aria-labelledby="access-status-title">
          {stage !== "suspended" && <Progress current={CURRENT_STEP[stage]} />}
          {stage === "apply" && <>
            <h1 id="access-status-title">Tell us about your work</h1>
            <p>An administrator reviews each request. We'll email the decision to <strong>{user.email}</strong>.</p>
            <ApplicationForm user={user} previous={null} submitLabel="Submit request" onSubmit={onSubmit} />
          </>}
          {stage === "declined" && application && <>
            <h1 id="access-status-title">Your request was not approved</h1>
            {application.public_note && <p className="chart-notice access-reviewer-note"><span>Note from the reviewer</span>{application.public_note}</p>}
            <p>You can update your details and send a new request.</p>
            <ApplicationForm user={user} previous={application} submitLabel="Send new request" onSubmit={onSubmit} />
          </>}
          {stage === "review" && application && <>
            <h1 id="access-status-title">Your request is under review</h1>
            <p>Submitted {formatDay(application.created_at)}. We'll email <strong>{user.email}</strong> when an administrator decides.</p>
            <dl className="access-summary">
              <div><dt>Name</dt><dd>{user.name}</dd></div>
              <div><dt>Organization</dt><dd>{application.organization}</dd></div>
              <div><dt>Intended use</dt><dd>{application.purpose}</dd></div>
            </dl>
          </>}
          {stage === "active" && <>
            <h1 id="access-status-title">Access approved</h1>
            <p>Your workspace is ready.</p>
            <a className="access-primary access-card-action" href="/workspace">Open workspace<span aria-hidden="true">→</span></a>
          </>}
          {stage === "suspended" && <>
            <h1 id="access-status-title">This account is suspended</h1>
            <p>Contact a Wildfire administrator if you think this is a mistake.</p>
          </>}
        </article>
      </main>
    </div>
  )
}

function Progress({ current }: { current: number }) {
  return (
    <ol className="access-progress" aria-label="Access steps">
      {PROGRESS.map((label, index) => {
        const state = index < current ? "done" : index === current ? "current" : "upcoming"
        return (
          <li key={label} data-state={state} aria-current={state === "current" ? "step" : undefined}>
            <span aria-hidden="true">{state === "done" ? "✓" : index + 1}</span>
            {label}
            {state === "done" && <span className="visually-hidden"> (done)</span>}
          </li>
        )
      })}
    </ol>
  )
}

function ApplicationForm({ user, previous, submitLabel, onSubmit }: {
  user: AccountUser
  previous: AccessApplication | null
  submitLabel: string
  onSubmit: (draft: ApplicationDraft) => Promise<void>
}) {
  const [draft, setDraft] = useState<ApplicationDraft>({
    name: user.name,
    organization: previous?.organization ?? "",
    purpose: previous?.purpose ?? "",
  })
  const [errors, setErrors] = useState<ApplicationErrors>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState("")
  const controls = useRef<Partial<Record<ApplicationField, HTMLInputElement | HTMLTextAreaElement | null>>>({})
  const id = useId()
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const found = applicationErrors(draft)
    setErrors(found)
    const invalid = APPLICATION_FIELDS.find(field => found[field])
    if (invalid) { controls.current[invalid]?.focus(); return }
    setBusy(true)
    setFailure("")
    try {
      await onSubmit(trimmedDraft(draft))
    } catch (error) {
      setFailure(error instanceof Error && error.message ? error.message : "The request could not be sent. Please try again.")
    } finally {
      setBusy(false)
    }
  }
  function update(field: ApplicationField, value: string) {
    setDraft(current => ({ ...current, [field]: value }))
    if (errors[field]) setErrors(current => ({ ...current, [field]: undefined }))
  }
  return (
    <form className="access-form" noValidate onSubmit={submit} aria-busy={busy}>
      {APPLICATION_FIELDS.map(field => {
        const config = FIELDS[field]
        const limit = APPLICATION_LIMITS[field]
        const props = {
          id: `${id}-${field}`,
          name: field,
          value: draft[field],
          maxLength: limit,
          autoComplete: config.autoComplete ?? "off",
          placeholder: config.placeholder,
          "aria-invalid": errors[field] ? true : undefined,
          "aria-describedby": errors[field] ? `${id}-${field}-error` : undefined,
        }
        return (
          <div className="access-field" key={field}>
            <label htmlFor={props.id}>{config.label}</label>
            {field === "purpose"
              ? <textarea {...props} ref={element => { controls.current[field] = element }} rows={4} onChange={event => update(field, event.target.value)} />
              : <input {...props} ref={element => { controls.current[field] = element }} type="text" onChange={event => update(field, event.target.value)} />}
            {errors[field] && <p className="access-field-error" id={`${id}-${field}-error`}>{errors[field]}</p>}
            {draft[field].length > limit * 0.9 && <p className="access-field-count">{draft[field].length.toLocaleString("en-US")} / {limit.toLocaleString("en-US")}</p>}
          </div>
        )
      })}
      {failure && <p className="access-form-error" role="alert">{failure}</p>}
      <button type="submit" className="access-primary" disabled={busy}>{busy ? "Sending…" : submitLabel}</button>
    </form>
  )
}

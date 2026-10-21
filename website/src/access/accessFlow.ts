// Contracts and pure rules for the access pages. The accounts service
// (services/accounts/) owns every decision; these mirror its public API so the
// pages never offer a path or accept a field the service would reject.

/** Paths `GET /auth/login` accepts as `return_to` (`return_path` in services/accounts/security.py). */
export const LOGIN_RETURN_PATHS = ["/", "/apply", "/access-status", "/invite", "/admin", "/workspace"] as const
export type LoginReturnPath = typeof LOGIN_RETURN_PATHS[number]

export function loginUrl(returnTo: LoginReturnPath): string {
  return `/auth/login?return_to=${encodeURIComponent(returnTo)}`
}

/** Maximum lengths of `AccessApplication` in services/accounts/schemas.py. */
export const APPLICATION_LIMITS = { name: 200, organization: 300, purpose: 2000 } as const
export type ApplicationField = keyof typeof APPLICATION_LIMITS
export type ApplicationDraft = Record<ApplicationField, string>
export type ApplicationErrors = Partial<Record<ApplicationField, string>>
export const APPLICATION_FIELDS = Object.keys(APPLICATION_LIMITS) as ApplicationField[]

export type AccountStatus = "pending" | "active" | "suspended"
/** `user` in `GET /api/auth/me`. */
export interface AccountUser {
  id: string
  name: string
  email: string
  role: "member" | "admin"
  status: AccountStatus
}

export type ApplicationStatus = "pending" | "approved" | "rejected"
/** `application` in `GET /api/auth/me` and `GET /api/access-requests/me`. */
export interface AccessApplication {
  id: string
  organization: string
  purpose: string
  status: ApplicationStatus
  public_note: string
  created_at: string
  reviewed_at: string | null
}

export type AccessStage = "active" | "suspended" | "apply" | "review" | "declined"

/** Which access page a signed-in account sees. Membership status decides before the request does. */
export function accessStage(user: AccountUser, application: AccessApplication | null): AccessStage {
  if (user.status === "active") return "active"
  if (user.status === "suspended") return "suspended"
  if (!application) return "apply"
  return application.status === "rejected" ? "declined" : "review"
}

const MISSING: Record<ApplicationField, string> = {
  name: "Enter your name.",
  organization: "Enter your organization.",
  purpose: "Describe how you plan to use the platform.",
}

/** The service strips whitespace, then requires 1..limit characters per field. */
export function applicationErrors(draft: ApplicationDraft): ApplicationErrors {
  const errors: ApplicationErrors = {}
  for (const field of APPLICATION_FIELDS) {
    const value = draft[field].trim()
    if (!value) errors[field] = MISSING[field]
    else if (value.length > APPLICATION_LIMITS[field]) errors[field] = `Use at most ${APPLICATION_LIMITS[field].toLocaleString("en-US")} characters.`
  }
  return errors
}

export function trimmedDraft(draft: ApplicationDraft): ApplicationDraft {
  return { name: draft.name.trim(), organization: draft.organization.trim(), purpose: draft.purpose.trim() }
}

/** A timestamp reads as its local day; a plain YYYY-MM-DD date is that calendar day everywhere. */
export function formatDay(iso: string): string {
  const date = new Date(iso)
  const calendarDay = /^\d{4}-\d{2}-\d{2}$/.test(iso)
  return Number.isNaN(date.getTime()) ? iso
    : date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", ...(calendarDay ? { timeZone: "UTC" } : {}) })
}

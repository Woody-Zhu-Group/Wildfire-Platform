// Browser client for the accounts service behind the gateway (services/accounts/app.py).
// Same-origin requests carry the HttpOnly session cookie; every write also sends
// the session's CSRF token, and the browser adds the Origin header the service checks.
import type { AccessApplication, AccountUser, ApplicationDraft } from "./accessFlow.ts"

export interface Me {
  user: AccountUser
  application: AccessApplication | null
  csrf_token: string
}

/** The service's `{"error": {"code", "message"}}` reply; the message is written for people. */
export class AccountsError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

const UNAVAILABLE = "Account services are temporarily unavailable. Please try again."

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { credentials: "same-origin", ...init, headers: { Accept: "application/json", ...init.headers } })
  } catch {
    throw new AccountsError(0, "network", UNAVAILABLE)
  }
  if (response.ok) return (response.status === 204 ? undefined : await response.json()) as T
  let code = "http_" + response.status, message = UNAVAILABLE
  try {
    const body = await response.json() as { error?: { code?: unknown; message?: unknown } }
    if (typeof body?.error?.code === "string") code = body.error.code
    if (typeof body?.error?.message === "string" && body.error.message) message = body.error.message
  } catch { /* the gateway can answer without a JSON body */ }
  throw new AccountsError(response.status, code, message)
}

const send = (method: string, csrf: string | null, body?: unknown): RequestInit => ({
  method,
  headers: { ...(csrf ? { "X-CSRF-Token": csrf } : {}), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
})
const write = (csrf: string | null, body?: unknown) => send("POST", csrf, body)
const query = (params: Record<string, string | null | undefined>) => {
  const search = new URLSearchParams(Object.entries(params).filter((entry): entry is [string, string] => Boolean(entry[1]))).toString()
  return search ? "?" + search : ""
}

/** Rejects with status 401 when nobody is signed in. */
export const getMe = () => request<Me>("/api/auth/me")
export const submitApplication = (csrf: string, draft: ApplicationDraft) =>
  request<{ id: string; status: string; created_at: string }>("/api/access-requests", write(csrf, draft))
export const signOut = (csrf: string | null) => request<void>("/auth/logout", write(csrf))
export const claimInvitation = (token: string) => request<{ login_url: string }>("/api/invitations/claim", write(null, { token }))
export const acceptInvitation = (csrf: string) => request<{ status: string }>("/api/invitations/accept", write(csrf))

// Administrator API. Every route rechecks the active admin role on the server.
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface AdminRequest {
  id: string; applicant_id: string; applicant_name: string; applicant_email: string; applicant_status: AccountUser["status"]
  organization: string; purpose: string; status: AccessApplication["status"]; review_note: string; public_note: string
  reviewer_email: string | null; reviewed_at: string | null; created_at: string; notification_status: "pending" | "sent" | "failed"
}
export interface AdminInvitation {
  id: string; email: string; invited_by_email: string | null; accepted_by_email: string | null
  expires_at: string; accepted_at: string | null; revoked_at: string | null; delivery_status: "pending" | "sent" | "failed"; created_at: string
}
export interface AdminUser { id: string; email: string; name: string; role: AccountUser["role"]; status: AccountUser["status"]; created_at: string }
export interface AuditEvent {
  id: string; actor_email: string | null; action: string; target_type: string; target_email: string | null
  metadata: Record<string, unknown>; created_at: string
}
export type Decision = { decision: "approve" | "reject"; note: string; public_note: string }

export const listRequests = (status: AccessApplication["status"], cursor?: string) =>
  request<Page<AdminRequest>>("/api/admin/access-requests" + query({ status, cursor }))
export const decideRequest = (csrf: string, id: string, decision: Decision) =>
  request<{ id: string; status: string; reviewed_at: string; notification_status: "sent" | "failed" }>(`/api/admin/access-requests/${id}/decision`, write(csrf, decision))
export const resendDecision = (csrf: string, id: string) =>
  request<{ notification_status: "sent" | "failed" }>(`/api/admin/access-requests/${id}/notify`, write(csrf))
export const listInvitations = (cursor?: string) => request<Page<AdminInvitation>>("/api/admin/invitations" + query({ cursor }))
export const createInvitation = (csrf: string, email: string) =>
  request<{ id: string; email: string; delivery_status: "sent" | "failed" }>("/api/admin/invitations", write(csrf, { email }))
export const resendInvitation = (csrf: string, id: string) =>
  request<{ delivery_status: "sent" | "failed" }>(`/api/admin/invitations/${id}/resend`, write(csrf))
export const revokeInvitation = (csrf: string, id: string) => request<void>(`/api/admin/invitations/${id}/revoke`, write(csrf))
export const listUsers = (filter: { status?: AccountUser["status"] | null; q?: string | null; cursor?: string }) =>
  request<Page<AdminUser>>("/api/admin/users" + query(filter))
export const updateUser = (csrf: string, id: string, change: { role?: AccountUser["role"]; status?: "active" | "suspended" }) =>
  request<AdminUser>(`/api/admin/users/${id}`, send("PATCH", csrf, change))
export const listAudit = (cursor?: string) => request<Page<AuditEvent>>("/api/admin/audit-events" + query({ cursor }))

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

const write = (csrf: string | null, body?: unknown): RequestInit => ({
  method: "POST",
  headers: { ...(csrf ? { "X-CSRF-Token": csrf } : {}), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
})

/** Rejects with status 401 when nobody is signed in. */
export const getMe = () => request<Me>("/api/auth/me")
export const submitApplication = (csrf: string, draft: ApplicationDraft) =>
  request<{ id: string; status: string; created_at: string }>("/api/access-requests", write(csrf, draft))
export const signOut = (csrf: string | null) => request<void>("/auth/logout", write(csrf))
export const claimInvitation = (token: string) => request<{ login_url: string }>("/api/invitations/claim", write(null, { token }))
export const acceptInvitation = (csrf: string) => request<{ status: string }>("/api/invitations/accept", write(csrf))

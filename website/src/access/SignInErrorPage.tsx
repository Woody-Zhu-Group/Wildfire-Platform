import { AccessHeader } from "./AccessHeader.tsx"
import { signInErrorCode } from "./accessRoute.ts"
import "./access.css"

/** /sign-in-error: where a failed sign-in lands. The code is all an administrator needs. */
export function SignInErrorPage() {
  const code = signInErrorCode(location.search)
  return (
    <div className="access-page">
      <AccessHeader />
      <main className="access-status">
        <article className="access-card" aria-labelledby="sign-in-error-title">
          <h1 id="sign-in-error-title">Sign-in did not complete</h1>
          <p>Please contact a Wildfire administrator and give them this error code.</p>
          <p className="access-error-code"><span>Error code</span><code>{code}</code></p>
          <a className="quiet-button access-card-link" href="/">Back to Wildfire</a>
        </article>
      </main>
    </div>
  )
}

-- Apply with the dedicated accounts migration role, never the warehouse loader.
BEGIN;
SELECT pg_advisory_xact_lock(174832091);
CREATE SCHEMA IF NOT EXISTS app;
REVOKE ALL ON SCHEMA app FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer text NOT NULL,
    subject text NOT NULL,
    email text NOT NULL,
    name text NOT NULL DEFAULT '',
    role text NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'suspended')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);
CREATE TABLE IF NOT EXISTS app.access_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    applicant_id uuid NOT NULL REFERENCES app.users(id),
    organization text NOT NULL,
    purpose text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewer_id uuid REFERENCES app.users(id),
    review_note text NOT NULL DEFAULT '',
    public_note text NOT NULL DEFAULT '',
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request ON app.access_requests(applicant_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS app.invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL,
    invited_by uuid NOT NULL REFERENCES app.users(id),
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    accepted_by uuid REFERENCES app.users(id),
    accepted_at timestamptz,
    revoked_at timestamptz,
    delivery_status text NOT NULL DEFAULT 'pending' CHECK (delivery_status IN ('pending', 'sent', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app.sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash text NOT NULL UNIQUE,
    user_id uuid NOT NULL REFERENCES app.users(id),
    invitation_id uuid REFERENCES app.invitations(id),
    invitation_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON app.sessions(user_id);
CREATE TABLE IF NOT EXISTS app.auth_flows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('login', 'invite')),
    token_hash text NOT NULL UNIQUE,
    browser_hash text NOT NULL,
    sealed_payload text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS flow_expiry ON app.auth_flows(expires_at);
CREATE TABLE IF NOT EXISTS app.audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid REFERENCES app.users(id),
    action text NOT NULL,
    target_type text NOT NULL,
    target_id uuid NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;

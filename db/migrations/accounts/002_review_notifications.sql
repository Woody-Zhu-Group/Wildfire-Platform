BEGIN;
SELECT pg_advisory_xact_lock(174832091);
ALTER TABLE app.access_requests ADD COLUMN IF NOT EXISTS notification_status text NOT NULL DEFAULT 'pending'
    CHECK (notification_status IN ('pending', 'sent', 'failed'));
COMMIT;

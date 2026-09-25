-- 0024_session_security.sql
-- Session revocation and login throttling.
--
--   users.token_version  bumped when a user's password changes or is reset;
--                        tokens carry the version they were issued with, so
--                        every older token stops working at once.
--   revoked_tokens       tokens signed out individually (logout), kept until
--                        they would have expired anyway.
--   login_failures       failed sign-ins per account and per client address
--                        in the current window, shared by every replica.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        TEXT        PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);

CREATE TABLE IF NOT EXISTS login_failures (
    key          TEXT        PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    failures     INTEGER     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_failures_window_start ON login_failures (window_start);

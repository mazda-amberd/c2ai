-- 0021_user_roles.sql
-- Access control moves out of the free-form metadata JSON into real columns:
--   user_type     'admin' | 'user' (was metadata->>'user_type', or the legacy
--                 metadata->>'role' = 'Admin'); the API still reports it as
--                 metadata.user_type = 'Admin' | 'User'.
--   is_superuser  sees and manages every user (was: identifier = 'admin').
--   created_by_id the creating account (was matched on the created_by text);
--                 created_by is kept as the audit label.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS user_type TEXT NOT NULL DEFAULT 'user',
    ADD COLUMN IF NOT EXISTS is_superuser BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS created_by_id UUID NULL REFERENCES users (id) ON DELETE SET NULL;

UPDATE users
SET user_type = 'admin'
WHERE lower(coalesce(metadata ->> 'user_type', '')) = 'admin'
   OR lower(coalesce(metadata ->> 'role', '')) = 'admin';

UPDATE users SET is_superuser = true WHERE identifier = 'admin';

UPDATE users AS created
SET created_by_id = creator.id
FROM users AS creator
WHERE creator.identifier = created.created_by
  AND creator.id <> created.id;

-- The column is now the only source of truth.
UPDATE users SET metadata = metadata - 'user_type' WHERE metadata ? 'user_type';

ALTER TABLE users
    ADD CONSTRAINT users_user_type_check CHECK (user_type IN ('admin', 'user')),
    -- A superuser is always an administrator.
    ADD CONSTRAINT users_superuser_is_admin CHECK (NOT is_superuser OR user_type = 'admin');

CREATE INDEX IF NOT EXISTS ix_users_created_by_id ON users (created_by_id);
CREATE INDEX IF NOT EXISTS ix_users_admins ON users (id) WHERE user_type = 'admin';

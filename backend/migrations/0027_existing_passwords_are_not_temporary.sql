-- 0027_existing_passwords_are_not_temporary.sql
-- metadata.needs_password_reset now blocks everything but choosing a new
-- password. Until this release nothing read it, so accounts created or reset
-- earlier carry it without anyone having been asked to change anything.
-- Existing accounts keep working as they are; only temporary passwords
-- issued from now on (new accounts, admin resets, forgot password) count.

UPDATE users
SET metadata = metadata || '{"needs_password_reset": false}'::jsonb
WHERE metadata ->> 'needs_password_reset' = 'true';

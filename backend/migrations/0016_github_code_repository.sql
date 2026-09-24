-- Optional application source repository; existing registrations use the
-- workflow repository for tag discovery when this is NULL.
ALTER TABLE registered_application_github_configs
    ADD COLUMN IF NOT EXISTS code_repository VARCHAR(255) NULL;

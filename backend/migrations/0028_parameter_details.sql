-- 0028_parameter_details.sql
-- A registered parameter now tells the deploy form what it is for (help
-- text), and may take a different value on each tier: tier_defaults maps a
-- tier number ("1".."4") to the value used there instead of default_value.
-- Existing parameters get neither, so they deploy exactly as before.

ALTER TABLE registered_application_parameters
    ADD COLUMN description TEXT,
    ADD COLUMN tier_defaults JSONB NOT NULL DEFAULT '{}'::jsonb;

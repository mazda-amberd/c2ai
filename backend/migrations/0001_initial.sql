-- 0001_initial.sql
-- Baseline schema: extensions, users, deployments, pipeline_runs, application_instances.
-- Safe to run against a fresh database; all statements are idempotent.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    identifier  TEXT        NOT NULL UNIQUE,
    password    TEXT        NOT NULL,
    first_name  TEXT        NOT NULL,
    last_name   TEXT        NOT NULL,
    metadata    JSONB       NOT NULL,
    created_by  TEXT        NOT NULL,
    updated_by  TEXT        NULL,
    "createdAt" TIMESTAMP   DEFAULT now(),
    "updatedAt" TIMESTAMP   DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deployments (
    id            SERIAL   PRIMARY KEY,
    subdomain     TEXT     NOT NULL,
    customer_name TEXT     NOT NULL,
    env_instance  TEXT     NOT NULL,
    tier          INTEGER  NOT NULL,
    branch        TEXT     NOT NULL,
    domain        TEXT     NOT NULL,
    status        TEXT     NOT NULL DEFAULT 'deploying',
    created_at    TIMESTAMP DEFAULT now(),
    completed_at  TIMESTAMP NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id            TEXT        PRIMARY KEY,
    subdomain     TEXT        NOT NULL,
    operation     TEXT        NOT NULL,
    event_type    TEXT        NOT NULL,
    triggered_by  TEXT        NOT NULL,
    run_id        BIGINT,
    tier          INTEGER,
    branch        TEXT,
    dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_subdomain_ended
    ON pipeline_runs (subdomain, ended_at);

CREATE TABLE IF NOT EXISTS application_instances (
    id        SERIAL           PRIMARY KEY,
    tier_name TEXT             NOT NULL,
    name      TEXT             NOT NULL,
    nodename  TEXT             NOT NULL,
    cpu       DOUBLE PRECISION NOT NULL,
    memory    DOUBLE PRECISION NOT NULL,
    gpu       DOUBLE PRECISION NOT NULL,
    status    TEXT             NOT NULL,
    updated_at TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT uq_application_instances_tier_nodename UNIQUE (tier_name, nodename)
);

CREATE INDEX IF NOT EXISTS ix_application_instances_tier_name
    ON application_instances (tier_name);

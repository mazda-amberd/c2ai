-- 0029_code_branch.sql
-- A GitHub Workflow template now names the code's branch as well as the
-- workflow's: code_repository and code_ref say what is deployed by default,
-- repository and ref where the workflow that deploys it runs. Both code
-- fields are required from now on.
--
-- Until now `ref` was both the workflow's branch and the version the deploy
-- form started on, and a template without a code repository kept its code
-- in the workflow repository. Existing versions keep exactly that.

ALTER TABLE registered_application_github_configs
    ADD COLUMN code_ref VARCHAR(255);

UPDATE registered_application_github_configs
SET code_repository = COALESCE(code_repository, repository),
    code_ref = ref;

ALTER TABLE registered_application_github_configs
    ALTER COLUMN code_repository SET NOT NULL,
    ALTER COLUMN code_ref SET NOT NULL;

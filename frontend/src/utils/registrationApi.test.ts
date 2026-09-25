import { describe, expect, it } from "vitest";

import type { ApiRegisteredApplicationDetail } from "@api/services/registeredApplications";
import { registrationFromDetail } from "./registrationApi";

const detail = (github: Partial<Extract<ApiRegisteredApplicationDetail, { application_type: "github_workflow" }>["github"]>) =>
  ({
    id: "app-1",
    name: "chatbot",
    description: null,
    status: "active",
    version: 1,
    created_by: "admin",
    created_at: "2026-09-25T00:00:00Z",
    application_type: "github_workflow",
    github: {
      github_connection: "c-1",
      trigger_method: "workflow_dispatch",
      code_repository: "amberd-ai/app",
      code_ref: "develop",
      repository: "amberd-ai/app",
      ref: "develop",
      workflow_file_path: ".github/workflows/deploy.yml",
      ...github,
    },
    parameters: [],
    llm: null,
  }) as ApiRegisteredApplicationDetail;

describe("registrationFromDetail", () => {
  it("shows a workflow that is the code's as blank, as it was entered", () => {
    expect(registrationFromDetail(detail({}))).toMatchObject({
      codeRepository: "amberd-ai/app",
      codeBranch: "develop",
      workflowRepository: "",
      branch: "",
    });
  });

  it("shows a workflow kept elsewhere, or on another branch", () => {
    expect(
      registrationFromDetail(detail({ repository: "amberd-ai/devops", ref: "main" })),
    ).toMatchObject({ workflowRepository: "amberd-ai/devops", branch: "main" });
    expect(registrationFromDetail(detail({ repository: "amberd-ai/devops" }))).toMatchObject({
      workflowRepository: "amberd-ai/devops",
      branch: "",
    });
  });
});

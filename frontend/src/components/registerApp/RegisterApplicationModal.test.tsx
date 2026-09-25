import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as registeredApplicationsApi from "@api/services/registeredApplications";
import { ToastProvider } from "@components/Toast";
import * as registrationApi from "@/utils/registrationApi";

import RegisterApplicationModal from "./RegisterApplicationModal";

function field(label: string): HTMLInputElement | HTMLSelectElement {
  const container = screen.getByText(label).closest("div")!;
  return container.querySelector("input, select") as HTMLInputElement | HTMLSelectElement;
}

const next = () => fireEvent.click(screen.getByRole("button", { name: /Next/ }));

describe("RegisterApplicationModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([
      { id: "c-1", name: "Devops", repoUrl: "https://github.com/amberd-ai/devops" },
    ]);
    vi.spyOn(registrationApi, "fetchContainerRegistries").mockResolvedValue([]);
    vi.spyOn(registeredApplicationsApi, "listPricedLlmModels").mockResolvedValue([]);
    vi.spyOn(registeredApplicationsApi, "checkLlmModelPricing").mockResolvedValue({
      model_name: "qwen3-coder-next",
      pricing_available: true,
      provider: null,
      message: null,
    } as Awaited<ReturnType<typeof registeredApplicationsApi.checkLlmModelPricing>>);
  });

  it("registers a GitHub workflow with the house defaults, ending at the LLM step", async () => {
    const register = vi.spyOn(registrationApi, "registerApplication").mockResolvedValue(
      {} as Awaited<ReturnType<typeof registrationApi.registerApplication>>,
    );
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} />
      </ToastProvider>,
    );

    fireEvent.change(field("Application Name"), { target: { value: "amberd_demo" } });
    next(); // -> type
    next(); // -> workflow

    expect(field("Branch / Ref").value).toBe("main");
    await screen.findByRole("option", { name: "Devops" });
    fireEvent.change(field("GitHub Connection"), { target: { value: "c-1" } });
    fireEvent.change(field("Workflow Repository"), { target: { value: "amberd-ai/devops" } });
    fireEvent.change(field("Workflow File"), {
      target: { value: ".github/workflows/ada-deploy.yaml" },
    });
    next();
    expect(await screen.findByText("Code Repository is required.")).toBeTruthy();

    fireEvent.change(field("Code Repository"), {
      target: { value: "amberd-ai/dealership_new" },
    });
    next(); // -> params
    next(); // -> llm, the last step
    expect(field("LLM Endpoint").value).toBe("http://amberd-llm-gateway:8010");
    expect(field("LLM Model Name").value).toBe("qwen3-coder-next");
    expect(screen.queryByText("Review & Confirm")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Create Application/ }));
    await waitFor(() => expect(register).toHaveBeenCalledOnce());
    expect(register.mock.calls[0][0]).toMatchObject({
      kind: "github",
      name: "amberd_demo",
      codeRepository: "amberd-ai/dealership_new",
      branch: "main",
      llmEndpoint: "http://amberd-llm-gateway:8010",
      llmApiToken: "EMPTY",
      llmModelName: "qwen3-coder-next",
    });
  });

  it("edits a saved container application, keeping the secrets left blank", async () => {
    vi.spyOn(registrationApi, "fetchContainerRegistries").mockResolvedValue([
      { value: "Docker Hub", label: "Docker Hub" },
    ]);
    vi.spyOn(registeredApplicationsApi, "getRegisteredApplication").mockResolvedValue({
      id: "app-1",
      name: "chat-service",
      description: "Chat",
      status: "active",
      version: 3,
      created_by: "admin@amberd.ai",
      created_at: "2026-09-24T00:00:00Z",
      application_type: "containerized",
      container: {
        registry: "Docker Hub",
        image_registry: "amberd/chat-service",
        registry_username: "amberd",
        tag: "1.2.3",
        port: 8080,
        pull_policy: "IfNotPresent",
        expose_public_service: true,
        gpu_request: null,
        cpu_request: "500m",
        memory_request: "512Mi",
        scaling: "1",
        storage: null,
      },
      parameters: [{ key: "LOG_LEVEL", value: "info" }],
      llm: { endpoint: "http://amberd-llm-gateway:8010", model_name: "qwen3-6" },
    });
    const update = vi
      .spyOn(registeredApplicationsApi, "updateRegisteredApplication")
      .mockResolvedValue({ version: 4 } as Awaited<
        ReturnType<typeof registeredApplicationsApi.updateRegisteredApplication>
      >);
    const saved = vi.fn();
    const editing = {
      id: "app-1",
      name: "chat-service",
      desc: "Chat",
      type: "container" as const,
      status: "active" as const,
      version: 3,
      instances: 0,
      tiers: {},
      canDelete: true,
      canEdit: true,
      created: "2026-09-24",
    };
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} onRegistered={saved} editing={editing} />
      </ToastProvider>,
    );

    expect(await screen.findByDisplayValue("chat-service")).toBeTruthy();
    expect(screen.getByText("Edit chat-service")).toBeTruthy();
    expect(screen.getByText(/Saving makes this version 4/)).toBeTruthy();
    next(); // the type is fixed: straight to the image
    expect(screen.getByText(/Step 2 of 5 · Container \/ Image Configuration/)).toBeTruthy();
    expect(field("Registry Password / Token").value).toBe("");
    fireEvent.change(field("Default Image Tag"), { target: { value: "2.0.0" } });
    next(); // -> resources
    next(); // -> environment
    next(); // -> llm
    expect(field("LLM API Token").value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/ }));

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const [id, sent] = update.mock.calls[0];
    const payload = sent as registeredApplicationsApi.RegisterContainerApplicationPayload;
    expect(id).toBe("app-1");
    expect(payload).toMatchObject({
      application_type: "containerized",
      name: "chat-service",
      container: { tag: "2.0.0", registry_username: "amberd", port: 8080, scaling: "1" },
      parameters: [{ key: "LOG_LEVEL", value: "info" }],
      llm: { endpoint: "http://amberd-llm-gateway:8010", model_name: "qwen3-6" },
    });
    // Blank secrets are left out, which keeps the stored ones.
    expect(payload.container).not.toHaveProperty("registry_password");
    expect(payload.llm).not.toHaveProperty("api_token");
    expect(await screen.findByText('"chat-service" saved as version 4.')).toBeTruthy();
    expect(saved).toHaveBeenCalledOnce();
  });

  it("edits ADA: its server-token connection stays, and it gets the house LLM settings", async () => {
    vi.spyOn(registeredApplicationsApi, "getRegisteredApplication").mockResolvedValue({
      id: "ada",
      name: "ADA",
      description: "Amberd ADA",
      status: "active",
      version: 1,
      created_by: "system",
      created_at: "2026-09-24T00:00:00Z",
      application_type: "github_workflow",
      github: {
        github_connection: "athena-environment",
        trigger_method: "workflow_dispatch",
        repository: "amberd-ai/devops",
        code_repository: "amberd-ai/dealership_new",
        workflow_file_path: ".github/workflows/ada-deploy.yaml",
        ref: "main",
      },
      parameters: [{ key: "customer_name", type: "text" }],
      llm: null,
    });
    const update = vi
      .spyOn(registeredApplicationsApi, "updateRegisteredApplication")
      .mockResolvedValue({ version: 2 } as Awaited<
        ReturnType<typeof registeredApplicationsApi.updateRegisteredApplication>
      >);
    const editing = {
      id: "ada",
      name: "ADA",
      desc: "Amberd ADA",
      type: "github" as const,
      status: "active" as const,
      version: 1,
      instances: 0,
      tiers: {},
      canDelete: true,
      canEdit: true,
      created: "2026-09-24",
    };
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} editing={editing} />
      </ToastProvider>,
    );

    expect(await screen.findByDisplayValue("ADA")).toBeTruthy();
    next(); // -> workflow
    const connection = field("GitHub Connection") as HTMLSelectElement;
    expect(connection.value).toBe("athena-environment");
    expect(
      screen.getByRole("option", { name: "C2AI server's GitHub token (athena-environment)" }),
    ).toBeTruthy();
    // Picking a saved connection and back again is possible.
    fireEvent.change(connection, { target: { value: "c-1" } });
    fireEvent.change(connection, { target: { value: "athena-environment" } });
    fireEvent.change(field("Branch / Ref"), { target: { value: "release-2" } });
    next(); // -> params
    next(); // -> llm
    expect(field("LLM Endpoint").value).toBe("http://amberd-llm-gateway:8010");
    expect(field("LLM API Token").value).toBe("EMPTY");
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/ }));

    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    const payload = update.mock.calls[0][1] as registeredApplicationsApi.RegisterGithubApplicationPayload;
    expect(payload.github).toMatchObject({ github_connection: "athena-environment", ref: "release-2" });
    expect(payload.llm).toEqual({
      endpoint: "http://amberd-llm-gateway:8010",
      api_token: "EMPTY",
      model_name: "qwen3-coder-next",
    });
  });
});

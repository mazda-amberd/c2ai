import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    vi.spyOn(registeredApplicationsApi, "getContainerSecretStorage").mockResolvedValue({
      configured: true,
    });
    vi.spyOn(registeredApplicationsApi, "listContainerSecrets").mockResolvedValue([]);
    vi.spyOn(registeredApplicationsApi, "listGithubRepositories").mockResolvedValue({
      items: [],
      truncated: false,
    });
    vi.spyOn(registeredApplicationsApi, "listGithubRefs").mockResolvedValue({
      default_branch: "main",
      branches: ["main"],
      tags: [],
    });
    vi.spyOn(registeredApplicationsApi, "listGithubWorkflows").mockResolvedValue({ items: [] });
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
    // The connection's repository fills in the Workflow Repository.
    await waitFor(() => expect(field("Workflow Repository").value).toBe("amberd-ai/devops"));
    next();
    expect(await screen.findByText("Workflow File is required.")).toBeTruthy();
    fireEvent.change(field("Workflow File"), {
      target: { value: ".github/workflows/ada-deploy.yaml" },
    });
    // Optional: blank means the code lives in the workflow repository.
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
      { value: "Docker Hub", label: "Docker Hub", imageHint: "company/application" },
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

  it("checks a workflow and imports its inputs as parameters", async () => {
    const inspect = vi.spyOn(registeredApplicationsApi, "inspectGithubWorkflow").mockResolvedValue({
      checks: [
        { name: "Repository", ok: true, detail: "amberd-ai/devops is reachable with this connection." },
        { name: "GitHub Actions", ok: false, detail: "GitHub Actions does not list it." },
      ],
      triggers: ["repository_dispatch"],
      inputs: [
        { key: "customer_name", type: "text", description: "Who it is for", required: true, default: null },
        { key: "dry_run", type: "boolean", required: false, default: false },
        { key: "size", type: "select", required: true, default: "large", options: ["small", "large"] },
        { key: "provider", type: "text", required: false },
      ],
    });
    const register = vi
      .spyOn(registeredApplicationsApi, "registerGithubApplication")
      .mockResolvedValue({ id: "gh-1" } as Awaited<
        ReturnType<typeof registeredApplicationsApi.registerGithubApplication>
      >);
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} />
      </ToastProvider>,
    );

    fireEvent.change(field("Application Name"), { target: { value: "chatbot" } });
    next(); // -> type
    next(); // -> workflow
    await screen.findByRole("option", { name: "Devops" });
    fireEvent.change(field("GitHub Connection"), { target: { value: "c-1" } });
    fireEvent.change(field("Workflow File"), { target: { value: ".github/workflows/deploy.yml" } });
    fireEvent.click(screen.getByRole("button", { name: /Check workflow/ }));
    expect(await screen.findByText(/GitHub Actions does not list it/)).toBeTruthy();
    expect(
      await screen.findByText("Trigger Method set to repository_dispatch, which the workflow listens for."),
    ).toBeTruthy();

    next(); // -> params
    fireEvent.click(screen.getByRole("button", { name: /Import from workflow/ }));
    expect(
      await screen.findByText("Imported 3 parameters from deploy.yml (1 already here or sent by C2AI)."),
    ).toBeTruthy();
    expect(inspect).toHaveBeenCalledOnce(); // the check's reading is reused
    expect(screen.getByText(/C2AI fills this in: the Customer Name entered when deploying/)).toBeTruthy();
    // Size gets another value on Tier 3.
    fireEvent.click(screen.getAllByRole("button", { name: /Different value per tier/ })[1]);
    fireEvent.change(screen.getByLabelText("Tier 3 value of size"), { target: { value: "small" } });
    next(); // -> llm
    fireEvent.click(screen.getByRole("button", { name: /Create Application/ }));

    await waitFor(() => expect(register).toHaveBeenCalledOnce());
    const payload = register.mock.calls[0][0];
    expect(payload.github).toMatchObject({
      repository: "amberd-ai/devops",
      trigger_method: "repository_dispatch",
    });
    expect(payload.github).not.toHaveProperty("code_repository");
    const base = { label: null, description: null, options: [], tier_defaults: {} };
    expect(payload.parameters).toEqual([
      { ...base, key: "customer_name", type: "text", description: "Who it is for", required: true, default: null },
      { ...base, key: "dry_run", type: "boolean", required: false, default: false },
      {
        ...base,
        key: "size",
        type: "select",
        required: true,
        default: "large",
        options: ["small", "large"],
        tier_defaults: { "3": "small" },
      },
    ]);
  });

  it("registers a public image from a pasted reference, with a tier value and a secret", async () => {
    vi.spyOn(registrationApi, "fetchContainerRegistries").mockResolvedValue(
      registrationApi.CONTAINER_REGISTRIES,
    );
    const check = vi.spyOn(registeredApplicationsApi, "checkContainerImage").mockResolvedValue({
      ok: true,
      detail: "Found ghcr.io/amberd-ai/chat:2.1.0.",
      image_reference: "ghcr.io/amberd-ai/chat:2.1.0",
      digest: null,
    });
    const register = vi
      .spyOn(registeredApplicationsApi, "registerContainerApplication")
      .mockResolvedValue({ id: "new-app" } as Awaited<
        ReturnType<typeof registeredApplicationsApi.registerContainerApplication>
      >);
    const createSecret = vi
      .spyOn(registeredApplicationsApi, "createContainerSecret")
      .mockResolvedValue({ id: "s-1", name: "api-key", environment_variable: "API_KEY" });
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} />
      </ToastProvider>,
    );

    fireEvent.change(field("Application Name"), { target: { value: "chat" } });
    next(); // -> type
    fireEvent.click(screen.getByText("Containerized Application"));
    next(); // -> container
    await screen.findByRole("option", { name: "GitHub Container Registry" });
    fireEvent.change(screen.getByLabelText("Image reference"), {
      target: { value: "ghcr.io/amberd-ai/chat:2.1.0" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Fill in" }));
    expect(field("Container Registry").value).toBe("GitHub Container Registry");
    expect(field("Image Registry").value).toBe("amberd-ai/chat");
    expect(field("Default Image Tag").value).toBe("2.1.0");
    fireEvent.change(field("Container Port"), { target: { value: "8080" } });
    fireEvent.click(screen.getByRole("button", { name: /Check image/ }));
    expect(await screen.findByText(/Found ghcr.io\/amberd-ai\/chat:2.1.0/)).toBeTruthy();
    expect(check).toHaveBeenCalledWith({
      registry: "GitHub Container Registry",
      image_registry: "amberd-ai/chat",
      tag: "2.1.0",
    });

    next(); // -> resources: a public image needs no login
    fireEvent.change(field("Storage — Persistent Volume"), { target: { value: "lots" } });
    next();
    expect(await screen.findByText("Storage must be a size such as 25Gi.")).toBeTruthy();
    fireEvent.change(field("Storage — Persistent Volume"), { target: { value: "25Gi" } });
    next(); // -> environment

    fireEvent.click(screen.getByRole("button", { name: /Add Variable/ }));
    fireEvent.change(screen.getAllByLabelText("Variable name")[0], { target: { value: "LOG_LEVEL" } });
    fireEvent.change(screen.getByLabelText("Value of LOG_LEVEL"), { target: { value: "info" } });
    fireEvent.click(screen.getByLabelText("Different value of LOG_LEVEL per tier"));
    fireEvent.change(screen.getByLabelText("Tier 1 value of LOG_LEVEL"), { target: { value: "debug" } });
    fireEvent.click(screen.getByRole("button", { name: /Add Variable/ }));
    fireEvent.change(screen.getAllByLabelText("Variable name")[1], { target: { value: "API_KEY" } });
    fireEvent.click(screen.getByLabelText("API_KEY is secret"));
    fireEvent.change(screen.getByLabelText("Value of API_KEY"), { target: { value: "s3cret" } });
    next(); // -> llm
    fireEvent.click(screen.getByRole("button", { name: /Create Application/ }));

    await waitFor(() => expect(createSecret).toHaveBeenCalledOnce());
    const payload = register.mock.calls[0][0];
    expect(payload.container).toMatchObject({
      registry: "GitHub Container Registry",
      image_registry: "amberd-ai/chat",
      tag: "2.1.0",
      port: 8080,
      storage: "25Gi",
    });
    expect(payload.container).not.toHaveProperty("registry_username");
    expect(payload.container).not.toHaveProperty("registry_password");
    // The secret is not part of the template; it goes to the secret provider.
    expect(payload.parameters).toEqual([
      { key: "LOG_LEVEL", value: "info", tier_values: { "1": "debug" } },
    ]);
    expect(createSecret).toHaveBeenCalledWith("new-app", {
      name: "api-key",
      environment_variable: "API_KEY",
      secret_value: "s3cret",
    });
  });

  it("duplicates an application: a new name, secrets copied, secret variables asked for again", async () => {
    vi.spyOn(registrationApi, "fetchContainerRegistries").mockResolvedValue(
      registrationApi.CONTAINER_REGISTRIES,
    );
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
      parameters: [{ key: "LOG_LEVEL", value: "info", tier_values: {} }],
      llm: { endpoint: "http://amberd-llm-gateway:8010", model_name: "qwen3-6" },
    });
    vi.spyOn(registeredApplicationsApi, "listContainerSecrets").mockResolvedValue([
      { id: "s-1", name: "api-key", environment_variable: "API_KEY" },
    ]);
    const duplicate = vi
      .spyOn(registeredApplicationsApi, "duplicateRegisteredApplication")
      .mockResolvedValue({ id: "copy-1" } as Awaited<
        ReturnType<typeof registeredApplicationsApi.duplicateRegisteredApplication>
      >);
    const createSecret = vi
      .spyOn(registeredApplicationsApi, "createContainerSecret")
      .mockResolvedValue({ id: "s-2", name: "api-key", environment_variable: "API_KEY" });
    const original = {
      id: "app-1",
      name: "chat-service",
      desc: "Chat",
      type: "container" as const,
      status: "active" as const,
      version: 3,
      instances: 2,
      tiers: { "Tier 1": 2 },
      canDelete: false,
      canEdit: false,
      created: "2026-09-24",
    };
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} duplicating={original} />
      </ToastProvider>,
    );

    expect(await screen.findByDisplayValue("chat-service copy")).toBeTruthy();
    expect(screen.getByText("Duplicate chat-service")).toBeTruthy();
    next(); // -> container: the type is the original's
    expect((field("Registry Password / Token") as HTMLInputElement).placeholder).toBe(
      "Copied from chat-service — type a new one to replace it",
    );
    next(); // -> resources
    next(); // -> environment
    next();
    expect(await screen.findByText("Enter the value of the secret 'API_KEY', or remove it.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Value of API_KEY"), { target: { value: "copy-secret" } });
    next(); // -> llm
    fireEvent.click(screen.getByRole("button", { name: /Create Copy/ }));

    await waitFor(() => expect(duplicate).toHaveBeenCalledOnce());
    const [sourceId, sent] = duplicate.mock.calls[0];
    const payload = sent as registeredApplicationsApi.RegisterContainerApplicationPayload;
    expect(sourceId).toBe("app-1");
    expect(payload.name).toBe("chat-service copy");
    expect(payload.container).toMatchObject({ registry_username: "amberd", tag: "1.2.3" });
    expect(payload.container).not.toHaveProperty("registry_password");
    expect(payload.llm).not.toHaveProperty("api_token");
    await waitFor(() =>
      expect(createSecret).toHaveBeenCalledWith("copy-1", {
        name: "api-key",
        environment_variable: "API_KEY",
        secret_value: "copy-secret",
      }),
    );
    expect(await screen.findByText('"chat-service copy" created from "chat-service".')).toBeTruthy();
  });

  it("picks the repositories, branch and workflow file from what the connection can see", async () => {
    const repositories = vi
      .spyOn(registeredApplicationsApi, "listGithubRepositories")
      .mockResolvedValue({
        items: [
          { full_name: "amberd-ai/dealership_new", default_branch: "main", private: true },
          { full_name: "amberd-ai/devops", default_branch: "develop", private: true },
        ],
        truncated: false,
      });
    vi.spyOn(registeredApplicationsApi, "listGithubRefs").mockResolvedValue({
      default_branch: "develop",
      branches: ["develop", "main"],
      tags: ["v1.0.0"],
    });
    const workflows = vi.spyOn(registeredApplicationsApi, "listGithubWorkflows").mockResolvedValue({
      items: [
        { path: ".github/workflows/ci.yml", name: "CI", triggers: [], readable: true },
        {
          path: ".github/workflows/deploy.yml",
          name: "Deploy",
          triggers: ["repository_dispatch"],
          readable: true,
        },
      ],
    });
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} />
      </ToastProvider>,
    );

    fireEvent.change(field("Application Name"), { target: { value: "chatbot" } });
    next(); // -> type
    next(); // -> workflow
    await screen.findByRole("option", { name: "Devops" });
    fireEvent.change(field("GitHub Connection"), { target: { value: "c-1" } });

    await waitFor(() => expect(repositories).toHaveBeenCalledWith("c-1"));
    expect(
      await screen.findByText(/2 repositories to pick from here and in Code Repository/),
    ).toBeTruthy();
    const offered = [...document.querySelectorAll("#github-repositories option")].map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(offered).toEqual(["amberd-ai/dealership_new", "amberd-ai/devops"]);
    // The workflow repository (from the connection) starts at its default branch.
    await waitFor(() => expect(field("Branch / Ref").value).toBe("develop"));
    const refs = [...document.querySelectorAll("#github-refs option")].map(
      (o) => `${(o as HTMLOptionElement).value} ${o.textContent}`,
    );
    expect(refs).toEqual(["develop default branch", "main branch", "v1.0.0 tag"]);

    await waitFor(() =>
      expect(workflows).toHaveBeenCalledWith("c-1", "amberd-ai/devops", "develop"),
    );
    const ci = await screen.findByRole("button", { name: /ci\.yml/ });
    expect(ci).toHaveProperty("disabled", true);
    expect(ci.textContent).toContain("C2AI can't start it");
    fireEvent.click(screen.getByRole("button", { name: /deploy\.yml/ }));
    expect(field("Workflow File").value).toBe(".github/workflows/deploy.yml");
    expect(
      await screen.findByText("Trigger Method set to repository_dispatch, which the workflow listens for."),
    ).toBeTruthy();
  });

  it("manages connections in their own dialog, choosing one just added", async () => {
    const created = { id: "c-9", name: "New app", repoUrl: "https://github.com/amberd-ai/new-app" };
    vi.spyOn(registrationApi, "saveGithubConnection").mockResolvedValue(created);
    render(
      <ToastProvider>
        <RegisterApplicationModal open onOpenChange={() => {}} />
      </ToastProvider>,
    );
    fireEvent.change(field("Application Name"), { target: { value: "chatbot" } });
    next(); // -> type
    next(); // -> workflow
    expect(screen.queryByRole("button", { name: /Add new connection/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Manage Connections/ }));
    expect(await screen.findByText("GitHub Connections")).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: /Add connection/ }));
    fireEvent.change(screen.getByLabelText("Connection Name"), { target: { value: "New app" } });
    fireEvent.change(screen.getByLabelText("Repository URL"), { target: { value: created.repoUrl } });
    fireEvent.change(screen.getByLabelText("Personal Access Token"), { target: { value: "ghp_new" } });
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([
      { id: "c-1", name: "Devops", repoUrl: "https://github.com/amberd-ai/devops" },
      { ...created, usedBy: [] },
    ]);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save connection/ }));
    });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(field("GitHub Connection").value).toBe("c-9"));
    // Its repository fills in the Workflow Repository.
    await waitFor(() => expect(field("Workflow Repository").value).toBe("amberd-ai/new-app"));
  });
});

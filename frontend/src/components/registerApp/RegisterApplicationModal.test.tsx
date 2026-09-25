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
});

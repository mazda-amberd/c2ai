import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@components/Toast";
import * as registrationApi from "@/utils/registrationApi";

import ManageConnectionsDialog from "./ManageConnectionsDialog";

const DEVOPS = {
  id: "c-1",
  name: "Devops",
  repoUrl: "https://github.com/amberd-ai/devops",
  usedBy: ["chatbot"],
};
const SPARE = { id: "c-2", name: "Spare", repoUrl: "https://github.com/amberd-ai/spare", usedBy: [] };
const SERVER = { id: "athena-environment", name: "athena-environment", repoUrl: "", legacy: true, usedBy: ["ADA"] };

function renderDialog(onChanged = vi.fn()) {
  render(
    <ToastProvider>
      <ManageConnectionsDialog open onOpenChange={() => {}} onChanged={onChanged} />
    </ToastProvider>,
  );
  return onChanged;
}

describe("ManageConnectionsDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([DEVOPS, SPARE, SERVER]);
  });

  it("lists the connections, what uses each, and keeps one in use", async () => {
    renderDialog();
    expect(await screen.findByText("Used by chatbot")).toBeTruthy();
    expect(screen.getByText("Not used by any application")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete Devops" })).toHaveProperty("disabled", true);
    // The server's own token is listed, but it is not a saved connection to change.
    expect(screen.getByText("C2AI server's GitHub token")).toBeTruthy();
    expect(screen.getByText("Used by ADA")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit athena-environment" })).toBeNull();
  });

  it("deletes an unused connection after asking", async () => {
    const remove = vi.spyOn(registrationApi, "removeGithubConnection").mockResolvedValue();
    const onChanged = renderDialog();
    fireEvent.click(await screen.findByRole("button", { name: "Delete Spare" }));
    expect(screen.getByText(/Delete “Spare”\?/)).toBeTruthy();
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue([DEVOPS, SERVER]);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Delete connection/ }));
    });
    expect(remove).toHaveBeenCalledWith("c-2");
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith([DEVOPS, SERVER], undefined));
    expect(screen.queryByText("Spare")).toBeNull();
  });

  it("renames a connection, keeping its token when none is typed", async () => {
    const saved = vi
      .spyOn(registrationApi, "editGithubConnection")
      .mockResolvedValue({ ...DEVOPS, name: "Devops prod" });
    renderDialog();
    fireEvent.click(await screen.findByRole("button", { name: "Edit Devops" }));
    expect((screen.getByLabelText("Repository URL") as HTMLInputElement).value).toBe(DEVOPS.repoUrl);
    const token = screen.getByLabelText("Personal Access Token") as HTMLInputElement;
    expect([token.value, token.placeholder]).toEqual(["", "Unchanged — type a new one to replace it"]);
    expect(screen.getByText("Used by chatbot: they deploy with the changes from then on.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Test connection/ })).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByLabelText("Connection Name"), { target: { value: "Devops prod" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save connection/ }));
    });
    expect(saved).toHaveBeenCalledWith("c-1", "Devops prod", DEVOPS.repoUrl, "");
    expect(await screen.findByText('Connection "Devops prod" saved.')).toBeTruthy();
  });

  it("adds a connection, testing it first, and hands it back to be chosen", async () => {
    vi.spyOn(registrationApi, "validateGithubConnection").mockResolvedValue({
      success: true,
      message: "Connected to amberd-ai/new-app.",
    });
    const created = { id: "c-3", name: "New app", repoUrl: "https://github.com/amberd-ai/new-app" };
    const save = vi.spyOn(registrationApi, "saveGithubConnection").mockResolvedValue(created);
    const onChanged = renderDialog();

    fireEvent.click(await screen.findByRole("button", { name: /Add connection/ }));
    fireEvent.change(screen.getByLabelText("Connection Name"), { target: { value: "New app" } });
    fireEvent.change(screen.getByLabelText("Repository URL"), { target: { value: created.repoUrl } });
    fireEvent.change(screen.getByLabelText("Personal Access Token"), { target: { value: "ghp_new" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Test connection/ }));
    });
    expect(screen.getByText("Connected to amberd-ai/new-app.")).toBeTruthy();

    const listed = [DEVOPS, SPARE, SERVER, { ...created, usedBy: [] }];
    vi.spyOn(registrationApi, "fetchGithubConnections").mockResolvedValue(listed);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save connection/ }));
    });
    expect(save).toHaveBeenCalledWith("New app", created.repoUrl, "ghp_new");
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(listed, listed[3]));
  });
});

import { useEffect, useState, type FormEvent } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { ArrowLeft, Check, Github, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";

import { Dialog, DialogContent } from "@ui/dialog";
import { useToast } from "@components/Toast";
import {
  editGithubConnection,
  fetchGithubConnections,
  removeGithubConnection,
  saveGithubConnection,
  validateGithubConnection,
  type GithubConnection,
} from "@/utils/registrationApi";
import { ACCENT, FIELD_CLASS, Field, PANEL_BACKGROUND } from "./wizardStyles";

type ConnectionForm = { id: string | null; name: string; repoUrl: string; token: string };
type TestResult = { status: "idle" | "testing" | "passed" | "failed"; message: string };

const NOT_TESTED: TestResult = { status: "idle", message: "" };
const LINK_BUTTON =
  "flex items-center gap-1 text-[12px] font-semibold text-[#20abc7] hover:text-[#4dc6dd] disabled:opacity-50";
const ICON_BUTTON =
  "grid h-7 w-7 place-items-center rounded-[6px] transition-colors disabled:cursor-not-allowed disabled:opacity-30";

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

/** The saved GitHub connections: add one, rename it, point it elsewhere,
 *  give it a new token, or delete it once no template deploys with it. */
export default function ManageConnectionsDialog({
  open,
  onOpenChange,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The connections as they now are; `created` when one was just added. */
  onChanged: (connections: GithubConnection[], created?: GithubConnection) => void;
}) {
  const { showToast } = useToast();
  const [connections, setConnections] = useState<GithubConnection[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [form, setForm] = useState<ConnectionForm | null>(null);
  const [test, setTest] = useState<TestResult>(NOT_TESTED);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  // The connection whose deletion is being confirmed.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setForm(null);
    setConfirming(null);
    fetchGithubConnections()
      .then((list) => {
        if (!cancelled) setConnections(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(message(err, "Could not load the connections."));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const reload = async (created?: GithubConnection) => {
    try {
      const list = await fetchGithubConnections();
      setConnections(list);
      setLoadError("");
      onChanged(list, created ? (list.find((c) => c.id === created.id) ?? created) : undefined);
    } catch (err) {
      setLoadError(message(err, "Could not load the connections."));
    }
  };

  const edit = (next: ConnectionForm | null) => {
    setForm(next);
    setTest(NOT_TESTED);
    setSaveError("");
    setConfirming(null);
  };
  const change = (patch: Partial<ConnectionForm>) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
    setTest(NOT_TESTED);
    setSaveError("");
  };

  /** A dry run with the token typed: nothing is saved. */
  const runTest = async () => {
    if (!form) return;
    setTest({ status: "testing", message: "" });
    const result = await validateGithubConnection(form.name, form.repoUrl, form.token);
    setTest({ status: result.success ? "passed" : "failed", message: result.message });
  };

  /** Saving checks the URL and token with GitHub too (a new URL with the stored token). */
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!form || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      const saved = form.id
        ? await editGithubConnection(form.id, form.name, form.repoUrl, form.token)
        : await saveGithubConnection(form.name, form.repoUrl, form.token);
      showToast(`Connection "${saved.name}" saved.`);
      edit(null);
      await reload(form.id ? undefined : saved);
    } catch (err) {
      setSaveError(message(err, "Could not save the connection."));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (connection: GithubConnection) => {
    setDeleting(true);
    try {
      await removeGithubConnection(connection.id);
      showToast(`Connection "${connection.name}" deleted.`);
      setConfirming(null);
      await reload();
    } catch (err) {
      showToast(message(err, "Could not delete the connection."));
    } finally {
      setDeleting(false);
    }
  };

  const editing = form?.id ? connections?.find((c) => c.id === form.id) : undefined;
  const complete = !!form && !!form.name.trim() && !!form.repoUrl.trim() && (!!form.id || !!form.token);

  return (
    <Dialog open={open} onOpenChange={(next) => !saving && !deleting && onOpenChange(next)}>
      <DialogContent
        className="top-[10vh] max-h-[80vh] w-full max-w-xl -translate-y-0 overflow-y-auto rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
        style={{ background: PANEL_BACKGROUND }}
      >
        <div className="mb-1 flex items-start gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
            style={{ background: "rgba(32,171,199,0.12)", color: ACCENT }}
          >
            <Github className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
              Registered Applications
            </p>
            <DialogPrimitive.Title asChild>
              <h3 className="text-lg font-bold text-[#eef2f6]">
                {!form ? "GitHub Connections" : form.id ? `Edit ${editing?.name ?? "connection"}` : "Add connection"}
              </h3>
            </DialogPrimitive.Title>
          </div>
        </div>
        <DialogPrimitive.Description asChild>
          <p className="mb-4 text-[13px] leading-normal text-[#8b97a5]">
            A connection is a GitHub token C2AI reads workflows and starts deployments with. Every
            application using one deploys with its changes.
          </p>
        </DialogPrimitive.Description>

        {!form && (
          <>
            {loadError && (
              <p role="alert" className="mb-2 text-[12.5px] text-[#f0655f]">
                {loadError}
              </p>
            )}
            <div className="space-y-2">
              {connections === null && !loadError && (
                <p className="flex items-center gap-2 py-4 text-[12.5px] text-[#8b97a5]">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading connections…
                </p>
              )}
              {connections?.length === 0 && (
                <p className="rounded-[8px] border border-[#1c2836] px-4 py-5 text-center text-[12px] text-[#57606c]">
                  No connections yet.
                </p>
              )}
              {connections?.map((c) => {
                const used = (c.usedBy ?? []).length > 0;
                return (
                  <div key={c.id} className="rounded-[8px] border border-[#1c2836] p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-[13px] font-semibold text-[#eef2f6]">
                          {c.legacy ? "C2AI server's GitHub token" : c.name}
                        </div>
                        <div className="truncate text-[11.5px] text-[#8b97a5]">
                          {c.legacy ? `${c.id} — set by GITHUB_PAT on the server` : c.repoUrl}
                        </div>
                        <div className="text-[11px] text-[#57606c]">
                          {used ? `Used by ${c.usedBy!.join(", ")}` : "Not used by any application"}
                        </div>
                      </div>
                      {!c.legacy && (
                        <div className="flex shrink-0 items-center gap-1">
                          <button
                            type="button"
                            aria-label={`Edit ${c.name}`}
                            title="Edit connection"
                            onClick={() => edit({ id: c.id, name: c.name, repoUrl: c.repoUrl, token: "" })}
                            className={`${ICON_BUTTON} text-[#20abc7]/80 hover:bg-[rgba(32,171,199,0.12)] hover:text-[#20abc7]`}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label={`Delete ${c.name}`}
                            disabled={used}
                            title={
                              used
                                ? "Choose another connection for the applications using it first"
                                : "Delete connection"
                            }
                            onClick={() => setConfirming(c.id)}
                            className={`${ICON_BUTTON} text-[#f0655f]/70 hover:bg-[rgba(240,101,95,0.12)] hover:text-[#f0655f]`}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                    </div>
                    {confirming === c.id && (
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-[6px] bg-[rgba(240,101,95,0.08)] px-3 py-2 text-[12px] text-[#eef2f6]">
                        <span>Delete “{c.name}”? Its token is forgotten.</span>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setConfirming(null)}
                            disabled={deleting}
                            className="rounded-[6px] border border-[#2b3a4a] px-2.5 py-1 font-semibold text-[#8b97a5] hover:text-[#eef2f6]"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => void remove(c)}
                            disabled={deleting}
                            className="flex items-center gap-1 rounded-[6px] px-2.5 py-1 font-semibold text-white disabled:opacity-60"
                            style={{ background: "#f0655f" }}
                          >
                            {deleting && <Loader2 className="h-3 w-3 animate-spin" />}
                            Delete connection
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="mt-4 flex justify-between border-t border-[#1c2836] pt-4">
              <button
                type="button"
                className={LINK_BUTTON}
                onClick={() => edit({ id: null, name: "", repoUrl: "", token: "" })}
              >
                <Plus className="h-3.5 w-3.5" /> Add connection
              </button>
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                className="rounded-[6px] border border-[#2b3a4a] px-3.5 py-2 text-[12.5px] font-semibold text-[#8b97a5] transition-colors hover:text-[#eef2f6]"
              >
                Done
              </button>
            </div>
          </>
        )}

        {form && (
          <form onSubmit={save} className="space-y-3">
            <button type="button" className={LINK_BUTTON} onClick={() => edit(null)}>
              <ArrowLeft className="h-3.5 w-3.5" /> All connections
            </button>
            <Field label="Connection Name" required>
              <input
                aria-label="Connection Name"
                autoFocus
                className={FIELD_CLASS}
                placeholder="e.g. my-application-prod"
                value={form.name}
                onChange={(e) => change({ name: e.target.value })}
              />
            </Field>
            <Field
              label="Repository URL"
              required
              helper="The repository or owner the token is for, e.g. https://github.com/org/repo."
            >
              <input
                aria-label="Repository URL"
                className={FIELD_CLASS}
                placeholder="https://github.com/org/repo"
                value={form.repoUrl}
                onChange={(e) => change({ repoUrl: e.target.value })}
              />
            </Field>
            <Field label="Personal Access Token" required={!form.id} optional={!!form.id}>
              <input
                aria-label="Personal Access Token"
                type="password"
                autoComplete="new-password"
                className={FIELD_CLASS}
                placeholder={form.id ? "Unchanged — type a new one to replace it" : "ghp_xxxxxxxxxxxxxxxxxxxx"}
                value={form.token}
                onChange={(e) => change({ token: e.target.value })}
              />
            </Field>
            {!!editing?.usedBy?.length && (
              <p className="text-[12px] text-[#93c5fd]">
                Used by {editing.usedBy.join(", ")}: they deploy with the changes from then on.
              </p>
            )}
            {test.status === "passed" && (
              <p className="flex items-center gap-1.5 text-[12.5px] text-[#4ade80]">
                <Check className="h-3.5 w-3.5" /> {test.message}
              </p>
            )}
            {test.status === "failed" && (
              <p className="flex items-center gap-1.5 text-[12.5px] text-[#f0655f]">
                <X className="h-3.5 w-3.5" /> {test.message}
              </p>
            )}
            {saveError && (
              <p role="alert" className="text-[12.5px] text-[#f0655f]">
                {saveError}
              </p>
            )}
            <div className="flex items-center justify-end gap-2 border-t border-[#1c2836] pt-4">
              <button
                type="button"
                onClick={() => void runTest()}
                disabled={!form.token || !form.repoUrl.trim() || test.status === "testing"}
                title={form.token ? "Check the token with GitHub; nothing is saved" : "Type a token to test it"}
                className="flex items-center gap-1.5 rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors hover:bg-[rgba(32,171,199,0.12)] disabled:cursor-not-allowed disabled:opacity-40"
                style={{ borderColor: ACCENT, color: ACCENT }}
              >
                {test.status === "testing" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Test connection
              </button>
              <button
                type="submit"
                disabled={!complete || saving}
                className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-[#04121a] transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                style={{ background: "#5eead4" }}
              >
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                Save connection
              </button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

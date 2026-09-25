import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ArrowLeft, KeyRound, Plus, Trash2 } from "lucide-react";

import { useAuth } from "@auth/AuthContext";
import { Button } from "@ui/button";
import { useToast } from "@components/Toast";
import { ACCENT } from "@components/registerApp/wizardStyles";
import Avatar from "@components/users/Avatar";
import ConfirmDialog from "@components/users/ConfirmDialog";
import UserDialog from "@components/users/UserDialog";
import {
  addUser,
  fetchUsers,
  removeUser,
  resetUserPassword,
  updateUser,
  type IssuedPassword,
  type ManagedUser,
  type UserDirectory,
  type UserInput,
} from "@/api/services/users";

const AMBER = "#fbbf24";
const COLUMNS = ["User", "Role", "Status", "Date created", ""];

type Pending = { kind: "reset" | "remove"; user: ManagedUser };
type Issued = { person: IssuedPassword; what: "added" | "reset" };

const message = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;

/** Admin → Users, as in Amberd Agents: who can sign in, and as what. */
export default function Users() {
  const navigate = useNavigate();
  const { isAdmin, checkingAuth, user: me, refreshUser } = useAuth();
  const { showToast } = useToast();

  const [directory, setDirectory] = useState<UserDirectory | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<ManagedUser | "new" | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);

  const load = useCallback(async () => {
    try {
      setDirectory(await fetchUsers());
      setLoadError(null);
    } catch (error) {
      setLoadError(message(error, "Could not load the users."));
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  // The API refuses these routes too; this is what that looks like.
  if (checkingAuth) return null;
  if (!isAdmin) return <Navigate to="/" replace />;

  /* The password exists in the clear for exactly this moment: what is stored
     is a hash. If the email went, say so and show nothing; if not, show it
     once - the alternative is an account nobody can get into. */
  const announce = (person: IssuedPassword, what: Issued["what"]) => {
    if (person.email_sent) {
      setIssued(null);
      showToast(`${person.name} ${what} — invitation emailed to ${person.email}`);
      return;
    }
    setIssued({ person, what });
    showToast(`${person.name} ${what} — the password is shown below`);
  };

  const save = async (input: UserInput) => {
    const target = editing === "new" ? null : editing;
    try {
      if (target) {
        const saved = await updateUser(target.user_id, input);
        showToast(`${saved.name} updated`);
        if (me && target.email === me.identifier) await refreshUser();
      } else {
        announce(await addUser(input), "added");
      }
    } catch (error) {
      showToast(message(error, "Could not save"));
      throw error;
    }
    setEditing(null);
    await load();
  };

  const confirmPending = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.kind === "reset") {
        const person = await resetUserPassword(pending.user.user_id);
        await load();
        announce(person, "reset");
      } else {
        await removeUser(pending.user.user_id);
        showToast(`${pending.user.name} removed`);
        await load();
      }
      setPending(null);
    } catch (error) {
      showToast(message(error, pending.kind === "reset" ? "Could not reset" : "Could not remove"));
    } finally {
      setBusy(false);
    }
  };

  const users = directory?.users ?? [];
  const onlyAdmin = (u: ManagedUser) => u.role === "admin" && (directory?.admins ?? 0) <= 1;

  return (
    <div className="p-6">
      <button
        type="button"
        onClick={() => navigate(-1)}
        className="inline-flex items-center gap-1.5 text-[13px] text-[#8b97a5] transition-colors hover:text-chart-accent"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back
      </button>

      <div className="mt-4 mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="m-0 text-2xl font-bold">Users</h1>
          <p className="mt-1.5 max-w-[640px] text-[12.5px] leading-normal text-[#8b97a5]">
            Manage users and permissions for your organization.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[#1c2836] bg-[rgba(8,21,36,0.6)] px-2.5 py-1 text-[11.5px] text-[#8b97a5]">
            <strong className="text-[#eef2f6]">{users.length}</strong> user{users.length === 1 ? "" : "s"}
          </span>
          <Button onClick={() => setEditing("new")}>
            <Plus className="h-4 w-4" />
            Add user
          </Button>
        </div>
      </div>

      <div
        className="overflow-hidden rounded-[10px] border border-[#1c2836]"
        style={{ background: "linear-gradient(150deg, rgba(14,32,52,.94), rgba(5,17,31,.99) 72%)" }}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] border-collapse">
            <thead>
              <tr>
                {COLUMNS.map((label) => (
                  <th
                    key={label || "actions"}
                    className="border-b border-[#1c2836] bg-[rgba(6,17,29,0.6)] px-3.5 py-3 text-left text-[10.5px] font-semibold uppercase tracking-wide text-[#8b97a5]"
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!directory ? (
                <tr>
                  <td colSpan={5} className="px-3.5 py-12 text-center text-[12.5px] text-[#57606c]">
                    {loadError ? `Could not load: ${loadError}` : "Loading…"}
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3.5 py-12 text-center text-[12.5px] text-[#57606c]">
                    Nobody yet. Add the first person — make them an Admin, or there is nobody who
                    can add anyone else.
                  </td>
                </tr>
              ) : (
                users.map((u) => (
                  <tr
                    key={u.user_id}
                    className="border-b border-[#1f3348] transition-colors last:border-b-0 hover:bg-[rgba(24,48,73,0.35)]"
                  >
                    <td className="px-3.5 py-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <Avatar name={u.first_name} email={u.email} />
                        <div className="min-w-0">
                          <div className="text-[12.5px] font-semibold text-white">{u.name}</div>
                          <div className="truncate text-[11px] text-[#8b97a5]">{u.email}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3.5 py-3">
                      <span
                        className="inline-block rounded-full border px-3 py-0.5 text-[10.5px] font-semibold"
                        style={
                          u.role === "admin"
                            ? { color: AMBER, borderColor: "rgba(251,191,36,0.35)", background: "rgba(251,191,36,0.08)" }
                            : { color: "#8b97a5", borderColor: "#2b4057", background: "rgba(255,255,255,0.03)" }
                        }
                      >
                        {u.role_label}
                      </span>
                    </td>
                    <td className="px-3.5 py-3 text-[11.5px]" style={{ color: u.status === "invited" ? AMBER : "#8b97a5" }}>
                      {u.status === "invited" ? "Invited — must set a password" : "Active"}
                    </td>
                    <td className="whitespace-nowrap px-3.5 py-3 text-[11px] text-[#8b97a5]">
                      {u.created_at?.slice(0, 10) || "—"}
                    </td>
                    <td className="px-3.5 py-3">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          type="button"
                          onClick={() => setEditing(u)}
                          className="rounded-[6px] border px-3 py-1 text-[11.5px] font-semibold transition-colors hover:bg-[rgba(32,171,199,0.08)]"
                          style={{ borderColor: "rgba(32,171,199,0.4)", color: ACCENT }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          aria-label={`Issue ${u.name} a new temporary password`}
                          title="Issue a new temporary password"
                          onClick={() => setPending({ kind: "reset", user: u })}
                          className="grid h-7 w-7 place-items-center rounded-[6px] border transition-colors hover:bg-[rgba(251,191,36,0.14)]"
                          style={{ color: AMBER, borderColor: "rgba(251,191,36,0.35)", background: "rgba(251,191,36,0.06)" }}
                        >
                          <KeyRound className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          aria-label={`Remove ${u.name}`}
                          disabled={onlyAdmin(u)}
                          title={
                            onlyAdmin(u)
                              ? "The only Admin cannot be removed - there would be nobody left to administer this deployment"
                              : `Remove ${u.name}`
                          }
                          onClick={() => setPending({ kind: "remove", user: u })}
                          className="grid h-7 w-7 place-items-center rounded-[6px] text-[#f0655f]/70 transition-colors hover:bg-[rgba(240,101,95,0.12)] hover:text-[#f0655f] disabled:cursor-not-allowed disabled:text-[#57606c]/50 disabled:hover:bg-transparent"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="border-t border-[#1c2836] bg-[rgba(6,17,29,0.4)] px-3.5 py-3 text-[11px] text-[#8b97a5]">
          {!directory ? null : directory.email_configured ? (
            "A new user is emailed a temporary password, and has to choose their own the first time they sign in."
          ) : (
            <>
              <strong className="text-[#eef2f6]">Email is not configured on this deployment</strong>, so
              the temporary password is shown once here instead and has to be passed on by hand.
            </>
          )}
        </div>
      </div>

      {issued && (
        <div
          role="status"
          className="mt-4 rounded-[10px] border px-4 py-3.5 text-[12.5px] leading-relaxed text-[#e6ecf2]"
          style={{ borderColor: "rgba(251,191,36,0.35)", background: "rgba(251,191,36,0.07)" }}
        >
          <strong>
            {issued.person.name} {issued.what}, but the email did not go.
          </strong>
          <br />
          {issued.person.email_error || "Email is not configured on this deployment."}
          <br />
          Give them this password yourself. It is shown once and cannot be retrieved.
          <div className="my-2 block w-fit select-all rounded-[7px] border border-[#2b4057] bg-[rgba(3,14,25,0.87)] px-3 py-2 font-mono text-[17px] font-bold tracking-wide text-white">
            {issued.person.temporary_password}
          </div>
          <div className="text-[11.5px] text-[#8b97a5]">They will be asked to replace it when they sign in.</div>
        </div>
      )}

      <UserDialog
        open={editing !== null}
        user={editing === "new" ? null : editing}
        onSave={save}
        onClose={() => setEditing(null)}
      />

      <ConfirmDialog
        open={pending?.kind === "reset"}
        kicker="Users"
        title="Issue a new temporary password?"
        body={`${pending?.user.name ?? ""}'s current password stops working immediately.`}
        confirmLabel="Issue it"
        icon={KeyRound}
        tone="warn"
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending?.kind === "remove"}
        kicker="Users"
        title={`Remove ${pending?.user.name ?? ""}?`}
        body="They lose access immediately."
        confirmLabel="Remove"
        icon={Trash2}
        tone="danger"
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}

import { useEffect, useState, type FormEvent } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Check, Loader2, UserPen, UserPlus } from "lucide-react";

import { Dialog, DialogContent } from "@ui/dialog";
import {
  ACCENT,
  FIELD_CLASS,
  Field,
  PANEL_BACKGROUND,
  Select,
} from "@components/registerApp/wizardStyles";
import type { ManagedUser, UserInput, UserRole } from "@/api/services/users";

const ROLE_HINT: Record<UserRole, string> = {
  admin:
    "Admins can do everything a User can, plus register and deploy applications, " +
    "manage GitHub connections, and add and remove people here.",
  user:
    "Users see the tiers, deployments, metrics and logs. Registering and deploying " +
    "applications, and managing people, are for Admins.",
};

const EMPTY: UserInput = { first_name: "", last_name: "", email: "", role: "user" };

/** Add someone, or edit them: first name, last name, email and role. */
export default function UserDialog({
  open,
  user,
  onSave,
  onClose,
}: {
  open: boolean;
  /** The person being edited; null to add someone. */
  user: ManagedUser | null;
  /** Resolves when saved; a rejection keeps the dialog open. */
  onSave: (input: UserInput) => Promise<void>;
  onClose: () => void;
}) {
  const [form, setForm] = useState<UserInput>(EMPTY);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm(
      user
        ? { first_name: user.first_name, last_name: user.last_name, email: user.email, role: user.role }
        : EMPTY,
    );
  }, [open, user]);

  const filled = [form.first_name, form.last_name, form.email].every((v) => v.trim());
  const set = (key: keyof UserInput) => (value: string) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!filled || saving) return;
    setSaving(true);
    try {
      await onSave({ ...form, first_name: form.first_name.trim(), last_name: form.last_name.trim(), email: form.email.trim() });
    } catch {
      // The page has said why; the form stays as typed.
    } finally {
      setSaving(false);
    }
  };

  const Icon = user ? UserPen : UserPlus;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !saving && onClose()}>
      <DialogContent
        className="top-[10vh] w-full max-w-xl -translate-y-0 rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
        style={{ background: PANEL_BACKGROUND }}
      >
        <form onSubmit={submit}>
          <div className="mb-1 flex items-start gap-3">
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
              style={{ background: "rgba(32,171,199,0.12)", color: ACCENT }}
            >
              <Icon className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">Users</p>
              <DialogPrimitive.Title asChild>
                <h3 className="text-lg font-bold text-[#eef2f6]">{user ? "Edit user" : "Add user"}</h3>
              </DialogPrimitive.Title>
            </div>
          </div>
          <DialogPrimitive.Description asChild>
            <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
              {user
                ? "A new role takes effect at once. Use the key button in the list to issue a new temporary password."
                : "They are emailed a temporary password and asked to choose their own when they first sign in."}
            </p>
          </DialogPrimitive.Description>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="First name" required>
                <input
                  aria-label="First name"
                  autoFocus
                  autoComplete="off"
                  className={FIELD_CLASS}
                  value={form.first_name}
                  onChange={(e) => set("first_name")(e.target.value)}
                />
              </Field>
              <Field label="Last name" required>
                <input
                  aria-label="Last name"
                  autoComplete="off"
                  className={FIELD_CLASS}
                  value={form.last_name}
                  onChange={(e) => set("last_name")(e.target.value)}
                />
              </Field>
            </div>
            <Field label="Email" required helper="What they sign in with, and where the invitation goes.">
              <input
                aria-label="Email"
                type="email"
                autoComplete="off"
                className={FIELD_CLASS}
                value={form.email}
                onChange={(e) => set("email")(e.target.value)}
              />
            </Field>
            <Field label="Role" required helper={ROLE_HINT[form.role]}>
              <Select
                aria-label="Role"
                value={form.role}
                onChange={(e) => set("role")(e.target.value as UserRole)}
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </Select>
            </Field>
          </div>

          <div className="mt-5 flex items-center justify-end gap-2 border-t border-[#1c2836] pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="rounded-[6px] border border-[#2b3a4a] px-3.5 py-2 text-[12.5px] font-semibold text-[#8b97a5] transition-colors hover:text-[#eef2f6]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!filled || saving}
              className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-[#04121a] transition-colors disabled:cursor-not-allowed disabled:opacity-40"
              style={{ background: "#5eead4" }}
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              {user ? "Save" : "Add user"}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

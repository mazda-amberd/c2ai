import { useEffect, useState, type FormEvent } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { KeyRound, Loader2 } from "lucide-react";

import { Dialog, DialogContent } from "@ui/dialog";
import { FIELD_CLASS, Field, PANEL_BACKGROUND, ACCENT } from "@components/registerApp/wizardStyles";
import { choosePasswordRequest } from "@/api/services/auth";

/** Change your own password, from the account menu. Both boxes in one
 *  dialog, and every complaint answered inside it. */
export default function ChangePasswordDialog({
  open,
  onClose,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPassword("");
    setAgain("");
    setError("");
  }, [open]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (password !== again) {
      setError("The two passwords are not the same.");
      return;
    }
    setSaving(true);
    try {
      await choosePasswordRequest(password);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change it.");
    } finally {
      setSaving(false);
    }
  };

  const typed = (set: (value: string) => void) => (value: string) => {
    setError("");
    set(value);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && !saving && onClose()}>
      <DialogContent
        className="top-[20vh] w-full max-w-md -translate-y-0 rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
        style={{ background: PANEL_BACKGROUND }}
      >
        <form onSubmit={submit}>
          <div className="mb-1 flex items-start gap-3">
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
              style={{ background: "rgba(32,171,199,0.12)", color: ACCENT }}
            >
              <KeyRound className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
                Your account
              </p>
              <DialogPrimitive.Title asChild>
                <h3 className="text-lg font-bold text-[#eef2f6]">Change your password</h3>
              </DialogPrimitive.Title>
            </div>
          </div>
          <DialogPrimitive.Description asChild>
            <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
              Every other browser you are signed in on is signed out.
            </p>
          </DialogPrimitive.Description>

          <div className="space-y-3">
            <Field label="New password" required>
              <input
                aria-label="New password"
                type="password"
                autoFocus
                autoComplete="new-password"
                placeholder="At least 8 characters"
                className={FIELD_CLASS}
                value={password}
                onChange={(e) => typed(setPassword)(e.target.value)}
              />
            </Field>
            <Field label="Type it again" required>
              <input
                aria-label="Type it again"
                type="password"
                autoComplete="new-password"
                className={FIELD_CLASS}
                value={again}
                onChange={(e) => typed(setAgain)(e.target.value)}
              />
            </Field>
            {error && (
              <p role="alert" className="text-[12.5px] text-[#f0655f]">
                {error}
              </p>
            )}
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
              disabled={!password || !again || saving}
              className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-[#04121a] transition-colors disabled:cursor-not-allowed disabled:opacity-40"
              style={{ background: "#5eead4" }}
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
              Change password
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

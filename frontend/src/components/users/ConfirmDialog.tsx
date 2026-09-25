import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Loader2, type LucideIcon } from "lucide-react";

import { Dialog, DialogContent } from "@ui/dialog";
import { ACCENT, PANEL_BACKGROUND } from "@components/registerApp/wizardStyles";

const DANGER = "#f0655f";
const WARN = "#fbbf24";

/** A yes/no question in the same panel language as the wizards. */
export default function ConfirmDialog({
  open,
  kicker,
  title,
  body,
  confirmLabel,
  icon: Icon,
  tone,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  kicker: string;
  title: string;
  body: string;
  confirmLabel: string;
  icon: LucideIcon;
  tone: "danger" | "warn";
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const colour = tone === "danger" ? DANGER : WARN;
  return (
    <Dialog open={open} onOpenChange={(next) => !next && !busy && onCancel()}>
      <DialogContent
        className="top-[20vh] w-full max-w-md -translate-y-0 rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
        style={{ background: PANEL_BACKGROUND }}
      >
        <div className="mb-1 flex items-start gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
            style={{ background: `${colour}1f`, color: colour }}
          >
            <Icon className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
              {kicker}
            </p>
            <DialogPrimitive.Title asChild>
              <h3 className="text-lg font-bold text-[#eef2f6]">{title}</h3>
            </DialogPrimitive.Title>
          </div>
        </div>
        <DialogPrimitive.Description asChild>
          <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">{body}</p>
        </DialogPrimitive.Description>
        <div className="flex items-center justify-end gap-2 border-t border-[#1c2836] pt-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors disabled:opacity-50"
            style={{ borderColor: "rgba(32,171,199,0.4)", color: ACCENT }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold transition-opacity hover:opacity-90 disabled:opacity-60"
            style={{ background: colour, color: tone === "danger" ? "#fff" : "#1a1204" }}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
            {confirmLabel}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

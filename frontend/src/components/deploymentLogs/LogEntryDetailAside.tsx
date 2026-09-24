import { Copy, X } from "lucide-react";

import { Button } from "@ui/button";
import type { DeploymentLogEntry } from "@/types/logs";
import {
  formatLogLevelLabel,
  formatLogLineForCopy,
  formatLogMessageBodyForDisplay,
} from "@/utils/logDisplay";
import { cn } from "@/lib/utils";

import { levelTextClass } from "./logViewerLevelText";
import { MetaRow } from "./logViewerParts";

export type LogEntryDetailAsideProps = {
  selectedEntry: DeploymentLogEntry | null;
  selectRow: (line: DeploymentLogEntry | null) => void;
  copyMessage: (text: string) => void | Promise<void>;
};

export function LogEntryDetailAside({
  selectedEntry,
  selectRow,
  copyMessage,
}: LogEntryDetailAsideProps) {
  return (
    <aside
      id="log-entry-detail"
      className={cn(
        "absolute inset-y-0 right-0 z-10 flex w-[min(40vw,28rem)] max-w-[100%] flex-col border-l border-popover-border bg-header shadow-lg transition-transform duration-200 ease-out",
        selectedEntry
          ? "translate-x-0"
          : "translate-x-full pointer-events-none",
      )}
      aria-hidden={!selectedEntry}
    >
      {selectedEntry && (
        <>
          <div className="flex items-center justify-between border-b border-popover-border px-3 py-2">
            <span className="text-xs font-medium text-muted-foreground">
              Log detail
            </span>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 gap-1 px-2 font-mono text-[11px]"
                onClick={() =>
                  void copyMessage(formatLogLineForCopy(selectedEntry))
                }
              >
                <Copy className="h-3.5 w-3.5" />
                Copy
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                aria-label="Close detail"
                onClick={() => selectRow(null)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3 text-left text-xs">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Level
              </p>
              <p className={cn("font-mono", levelTextClass(selectedEntry.level))}>
                {formatLogLevelLabel(selectedEntry.level)}
              </p>
            </div>
            <dl className="flex flex-col gap-2">
              <MetaRow label="Component" value={selectedEntry.logType} />
              <MetaRow label="User" value={selectedEntry.user} />
              <MetaRow label="Instance" value={selectedEntry.db} />
              <MetaRow label="App" value={selectedEntry.app} />
              <MetaRow label="Unit" value={selectedEntry.client} />
              <MetaRow label="Role" value={selectedEntry.role} />
              <MetaRow
                label="Duration"
                value={
                  selectedEntry.durationMs != null
                    ? `${selectedEntry.durationMs} ms`
                    : "—"
                }
              />
            </dl>
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Line id
              </p>
              <p className="break-all font-mono text-[11px] text-muted-foreground">
                {selectedEntry.id}
              </p>
              <p className="mt-3 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Stream reference
              </p>
              <p className="break-all font-mono text-[11px] text-muted-foreground">
                {selectedEntry.connectionId}
              </p>
            </div>
            {selectedEntry.labels &&
              Object.keys(selectedEntry.labels).length > 0 && (
                <div>
                  <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    All labels
                  </p>
                  <dl className="mt-1 flex max-h-48 flex-col gap-1.5 overflow-y-auto rounded border border-popover-border bg-input p-2">
                    {Object.entries(selectedEntry.labels)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([k, v]) => (
                        <MetaRow key={k} label={k} value={v} />
                      ))}
                  </dl>
                </div>
              )}
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Message
              </p>
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-foreground/90">
                {formatLogMessageBodyForDisplay(selectedEntry.message)}
              </pre>
            </div>
          </div>
        </>
      )}
    </aside>
  );
}

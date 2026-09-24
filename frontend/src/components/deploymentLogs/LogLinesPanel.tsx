import type { RefObject } from "react";

import { Button } from "@ui/button";
import type { DeploymentLogEntry } from "@/types/logs";
import {
  formatLogLevelLabel,
  formatLogMessageBodyForDisplay,
  formatLogTimestampDisplay,
} from "@/utils/logDisplay";
import { cn } from "@/lib/utils";

import { LevelDot } from "./logViewerParts";
import { levelTextClass } from "./logViewerLevelText";

export type LogLinesPanelProps = {
  scrollRef: RefObject<HTMLDivElement | null>;
  loadMoreSentinelRef: RefObject<HTMLDivElement | null>;
  onLogScroll: () => void;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  filtered: DeploymentLogEntry[];
  isRowSelected: (line: DeploymentLogEntry) => boolean;
  selectRow: (line: DeploymentLogEntry | null) => void;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
};

export function LogLinesPanel({
  scrollRef,
  loadMoreSentinelRef,
  onLogScroll,
  isLoading,
  isError,
  error,
  filtered,
  isRowSelected,
  selectRow,
  hasNextPage,
  isFetchingNextPage,
}: LogLinesPanelProps) {
  return (
    <div
      ref={scrollRef}
      onScroll={onLogScroll}
      className="h-full min-h-0 overflow-y-auto overflow-x-auto bg-background px-3 py-2 font-mono text-[10px] leading-relaxed sm:text-[11px]"
    >
      {isLoading && (
        <p className="text-muted-foreground">Loading logs…</p>
      )}
      {isError && (
        <p className="whitespace-pre-wrap break-words text-destructive/90">
          {error instanceof Error ? error.message : "Failed to load logs"}
        </p>
      )}
      {!isLoading && !isError && filtered.length === 0 && (
        <p className="text-muted-foreground">No log lines match.</p>
      )}
      {!isLoading &&
        !isError &&
        filtered.map((line, i) => (
          <Button
            key={line.id || `${line.timestamp}-${line.connectionId}-${i}`}
            type="button"
            variant="ghost"
            aria-expanded={isRowSelected(line)}
            onClick={() =>
              selectRow(isRowSelected(line) ? null : line)
            }
            className={cn(
              "inline-flex h-auto min-h-0 w-full max-w-full items-baseline justify-start gap-2 rounded-none border-b border-popover-border py-1.5 px-2 font-mono font-normal text-left last:border-0",
              "hover:bg-accent/50",
              isRowSelected(line) ? "bg-accent/40" : "",
            )}
          >
            <span className="shrink-0 text-muted-foreground">
              {formatLogTimestampDisplay(line.timestamp)}
            </span>
            <span className="inline-flex shrink-0 align-baseline">
              <LevelDot level={line.level} />
            </span>
            <span
              className={cn(
                "w-14 shrink-0 font-medium tracking-wide",
                levelTextClass(line.level),
              )}
            >
              {formatLogLevelLabel(line.level)}
            </span>
            <span className="hidden max-w-[20rem] shrink-0 truncate text-muted-foreground/85 xl:inline">
              instance={line.db}, app={line.app}, component={line.logType}
              {line.client && line.client !== "—" ? `, unit=${line.client}` : ""}
            </span>
            <span className="shrink-0 font-semibold text-[hsla(var(--cyan-text))]/70">
              |
            </span>
            <span className="min-w-0 flex-1 truncate text-foreground/90">
              {formatLogMessageBodyForDisplay(line.message)}
            </span>
          </Button>
        ))}
      {hasNextPage ? (
        <div
          ref={loadMoreSentinelRef}
          className="flex min-h-8 items-center justify-center py-2 text-[11px] text-muted-foreground"
          aria-hidden
        >
          {isFetchingNextPage ? "Loading more logs…" : " "}
        </div>
      ) : null}
    </div>
  );
}

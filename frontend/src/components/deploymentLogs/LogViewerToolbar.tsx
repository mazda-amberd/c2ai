import { ChevronDown, Search, X, Zap } from "lucide-react";

import { Button } from "@ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@ui/dropdown-menu";
import type { LogTimePreset } from "@/types/logs";
import {
  LOG_LEVEL_SEARCH_CHIPS,
  buildSearchFromLevelsAndText,
  levelTokensInOrder,
  logTimePresetLabel,
  removeLevelTokenRaw,
  stripLevelTokens,
} from "@/utils/logDisplay";
import { cn } from "@/lib/utils";

import { CustomDateTimeRangePicker } from "./CustomDateTimeRangePicker";
import { TIME_PRESETS } from "./logViewerConstants";

function presetMenuLabel(preset: LogTimePreset) {
  if (preset === "live") {
    return (
      <span className="inline-flex items-center gap-2">
        <Zap
          className="h-3.5 w-3.5 shrink-0 text-[hsla(var(--cyan-text))]/90"
          aria-hidden
        />
        {logTimePresetLabel("live")}
      </span>
    );
  }
  return logTimePresetLabel(preset);
}

export type LogViewerToolbarProps = {
  timePreset: LogTimePreset;
  setTimePreset: (p: LogTimePreset) => void;
  enterCustomMode: () => void;
  customFromAt: Date;
  customToAt: Date;
  setCustomFromAt: (d: Date) => void;
  setCustomToAt: (d: Date) => void;
  customInvalid: boolean;
  searchActive: boolean;
  search: string;
  setSearch: (s: string) => void;
  showSearchHints: boolean;
  setShowSearchHints: (v: boolean) => void;
  appendSearchChip: (chip: string) => void;
  commitSearch: () => void;
  setAndCommitSearch: (s: string) => void;
  isSearchPending: boolean;
  showServerFetching: boolean;
  committedSearch: string;
};

export function LogViewerToolbar({
  timePreset,
  setTimePreset,
  enterCustomMode,
  customFromAt,
  customToAt,
  setCustomFromAt,
  setCustomToAt,
  customInvalid,
  searchActive,
  search,
  setSearch,
  showSearchHints,
  setShowSearchHints,
  appendSearchChip,
  commitSearch,
  setAndCommitSearch,
  isSearchPending,
  showServerFetching,
  committedSearch,
}: LogViewerToolbarProps) {
  return (
    <div className="flex flex-col gap-2 border-b border-popover-border bg-muted/25 px-3 py-2">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-3">
        <div
          className={cn(
            "flex shrink-0 flex-col gap-2 lg:flex-row lg:items-center lg:gap-3",
            searchActive && "pointer-events-none opacity-40",
          )}
        >
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-8 shrink-0 justify-between gap-2 font-mono text-xs lg:w-52"
              >
                <span className="inline-flex min-w-0 flex-1 items-center gap-2 truncate">
                  {presetMenuLabel(timePreset)}
                </span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-70" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="font-mono text-xs">
              {TIME_PRESETS.map((p) => (
                <DropdownMenuItem key={p} onSelect={() => setTimePreset(p)}>
                  {presetMenuLabel(p)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <div className="flex flex-wrap items-center gap-2">
            <CustomDateTimeRangePicker
              from={customFromAt}
              to={customToAt}
              onBeforeUserEdit={enterCustomMode}
              onFromChange={setCustomFromAt}
              onToChange={setCustomToAt}
            />
            {timePreset === "custom" && customInvalid && (
              <span className="text-[11px] text-destructive/90">
                Invalid range
              </span>
            )}
          </div>
        </div>

        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 z-[1] h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <div
            className={cn(
              "flex min-h-8 flex-wrap items-center gap-1.5 rounded-md border border-popover-border bg-background/60 py-1 pl-8 pr-2 font-mono text-xs shadow-none",
              "focus-within:ring-1 focus-within:ring-muted-foreground/30",
            )}
            aria-label="Filter log lines"
          >
            {levelTokensInOrder(search).map((tok, i) => (
              <span
                key={`${tok.raw}-${i}`}
                className="inline-flex items-center gap-0.5 rounded-md border border-popover-border bg-input px-1.5 py-0.5 text-[11px] text-foreground/90 shadow-sm"
              >
                <span className="max-w-[9rem] truncate">{tok.raw}</span>
                <button
                  type="button"
                  className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                  aria-label={`Remove ${tok.raw}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() =>
                    setAndCommitSearch(removeLevelTokenRaw(search, tok.raw))
                  }
                >
                  <X className="h-3 w-3 shrink-0" />
                </button>
              </span>
            ))}
            <input
              type="text"
              placeholder={
                levelTokensInOrder(search).length
                  ? "Add text filter…"
                  : "Search logs…"
              }
              value={stripLevelTokens(search)}
              onChange={(e) =>
                setSearch(
                  buildSearchFromLevelsAndText(
                    levelTokensInOrder(search),
                    e.target.value,
                  ),
                )
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitSearch();
                  setShowSearchHints(false);
                }
              }}
              onFocus={() => setShowSearchHints(true)}
              onBlur={() => {
                window.setTimeout(() => setShowSearchHints(false), 150);
              }}
              className="min-h-6 min-w-[6rem] flex-1 border-0 bg-transparent py-0.5 text-xs outline-none ring-0 placeholder:text-muted-foreground"
              aria-expanded={showSearchHints}
              aria-controls="log-search-hints"
            />
            {isSearchPending && (
              <span className="pointer-events-none ml-1 shrink-0 font-sans text-[10px] text-muted-foreground/60">
                Press{" "}
                <kbd className="rounded border border-popover-border bg-input px-1 py-px font-sans text-[10px]">
                  Enter
                </kbd>{" "}
                to search · 30 days
              </span>
            )}
          </div>
          {showSearchHints && (
            <div
              id="log-search-hints"
              className="absolute left-0 right-0 top-full z-20 mt-1 rounded-md border border-popover-border bg-popover px-2 py-1.5 shadow-lg"
              role="listbox"
            >
              <p className="mb-1 text-[10px] text-muted-foreground">
                Suggestions
              </p>
              <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
                {LOG_LEVEL_SEARCH_CHIPS.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    role="option"
                    className="rounded border border-popover-border bg-input px-2 py-0.5 font-mono text-[11px] text-foreground/90 hover:bg-accent/60"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => appendSearchChip(chip)}
                  >
                    {chip}
                  </button>
                ))}
              </div>
            </div>
          )}
          {showServerFetching ? (
            <p className="mt-1 text-[10px] text-muted-foreground">
              {searchActive ? (
                <>
                  Searching last 30 days for{" "}
                  <span className="font-medium text-foreground/70">
                    {committedSearch}
                  </span>
                  …
                </>
              ) : (
                <>Loading…</>
              )}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

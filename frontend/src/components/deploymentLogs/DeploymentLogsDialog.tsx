import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@ui/dialog";
import { useLogViewerUi } from "@hooks/useLogViewerUi";
import type { DeploymentLogEntry, DeploymentLogsDialogProps } from "@/types/logs";
import { cn } from "@/lib/utils";

import { LogEntryDetailAside } from "./LogEntryDetailAside";
import { LogLinesPanel } from "./LogLinesPanel";
import { LogViewerToolbar } from "./LogViewerToolbar";

export default function DeploymentLogsDialog({
  open,
  onOpenChange,
  subdomain,
  rayAppName,
  tier,
}: DeploymentLogsDialogProps) {
  const {
    timePreset,
    setTimePreset,
    enterCustomMode,
    customFromAt,
    setCustomFromAt,
    customToAt,
    setCustomToAt,
    customInvalid,
    search,
    setSearch,
    showSearchHints,
    setShowSearchHints,
    appendSearchChip,
    filtered,
    onLogScroll,
    scrollRef,
    loadMoreSentinelRef,
    reset,
    selectedEntry,
    selectRow,
    isLoading,
    isError,
    error,
    isFetchingNextPage,
    hasNextPage,
    isSearchPending,
    isFetchingLogs,
    commitSearch,
    setAndCommitSearch,
    searchActive,
    committedSearch,
  } = useLogViewerUi({ open, subdomain, rayAppName, tier });

  const showServerFetching = isFetchingLogs && !isLoading && !isFetchingNextPage;

  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const copyMessage = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* ignore */
    }
  };

  const isRowSelected = (line: DeploymentLogEntry) =>
    selectedEntry != null && selectedEntry.id === line.id;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className={cn(
          "flex h-[min(92vh,64rem)] max-h-[min(92vh,64rem)] w-[min(98vw,96rem)] max-w-[min(98vw,96rem)] flex-col gap-0 overflow-hidden p-0",
          "border border-popover-border bg-header text-foreground shadow-2xl ring-1 ring-muted-foreground/15",
        )}
        onEscapeKeyDown={(e) => {
          if (selectedEntry) {
            e.preventDefault();
            selectRow(null);
          }
        }}
      >
        <DialogHeader className="border-b border-popover-border px-4 py-3 text-left">
          <DialogTitle className="text-base font-semibold tracking-tight">
            View logs — {subdomain}
            {rayAppName ? (
              <span className="block text-xs font-normal text-muted-foreground">
                App: {rayAppName}
              </span>
            ) : null}
          </DialogTitle>
        </DialogHeader>

        <LogViewerToolbar
          timePreset={timePreset}
          setTimePreset={setTimePreset}
          enterCustomMode={enterCustomMode}
          customFromAt={customFromAt}
          customToAt={customToAt}
          setCustomFromAt={setCustomFromAt}
          setCustomToAt={setCustomToAt}
          customInvalid={customInvalid}
          searchActive={searchActive}
          search={search}
          setSearch={setSearch}
          showSearchHints={showSearchHints}
          setShowSearchHints={setShowSearchHints}
          appendSearchChip={appendSearchChip}
          commitSearch={commitSearch}
          setAndCommitSearch={setAndCommitSearch}
          isSearchPending={isSearchPending}
          showServerFetching={showServerFetching}
          committedSearch={committedSearch}
        />

        <div className="relative min-h-0 flex-1">
          <LogLinesPanel
            scrollRef={scrollRef}
            loadMoreSentinelRef={loadMoreSentinelRef}
            onLogScroll={onLogScroll}
            isLoading={isLoading}
            isError={isError}
            error={error}
            filtered={filtered}
            isRowSelected={isRowSelected}
            selectRow={selectRow}
            hasNextPage={hasNextPage}
            isFetchingNextPage={isFetchingNextPage}
          />

          <LogEntryDetailAside
            selectedEntry={selectedEntry}
            selectRow={selectRow}
            copyMessage={copyMessage}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

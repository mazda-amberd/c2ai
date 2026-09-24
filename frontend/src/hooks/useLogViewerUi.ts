import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * When a committed search is active the query is expanded to this lookback regardless of the time picker.
 * Capped at 30 days — the Loki server enforces a hard max_query_length of 30d1h.
 */
const SEARCH_GLOBAL_LOOKBACK_MS = 30 * 24 * 60 * 60 * 1000;

import {
  useDeploymentLogsInfinite,
  useLiveTailSlabInfinite,
} from "@hooks/useDeploymentLogs";
import type { DeploymentLogEntry, LogTimePreset } from "@/types/logs";
import {
  LOG_RELATIVE_PRESET_MS,
  computeTimeWindowMs,
  dedupeLogEntriesById,
  filterEntriesByTimeWindow,
} from "@/utils/logDisplay";

function defaultCustomRange(): [Date, Date] {
  const to = new Date();
  return [new Date(to.getTime() - 60 * 60 * 1000), to];
}

const LIVE_FOLLOW_TOP_OFFSET_PX = 80;

function compareEntriesChronological(a: DeploymentLogEntry, b: DeploymentLogEntry): number {
  const ta = new Date(a.timestamp).getTime();
  const tb = new Date(b.timestamp).getTime();
  if (ta !== tb) return ta - tb;
  return a.id.localeCompare(b.id);
}

function compareEntriesNewestFirst(a: DeploymentLogEntry, b: DeploymentLogEntry): number {
  const ta = new Date(a.timestamp).getTime();
  const tb = new Date(b.timestamp).getTime();
  if (ta !== tb) return tb - ta;
  return a.id.localeCompare(b.id);
}

export function useLogViewerUi(options: {
  open: boolean;
  subdomain: string;
  rayAppName: string;
  tier?: number;
}) {
  const { open, subdomain, rayAppName, tier } = options;

  const [timePreset, setTimePreset] = useState<LogTimePreset>("live");
  const [customFromAt, setCustomFromAt] = useState<Date>(() => {
    const [from] = defaultCustomRange();
    return from;
  });
  const [customToAt, setCustomToAt] = useState<Date>(() => {
    const [, to] = defaultCustomRange();
    return to;
  });
  /** Display value — what the user is currently typing. */
  const [search, setSearch] = useState("");
  /** Committed value — what is actually sent to Loki. Only changes on Enter or an explicit chip action. */
  const [committedSearch, setCommittedSearch] = useState("");
  const [showSearchHints, setShowSearchHints] = useState(false);
  const [selectedEntry, setSelectedEntry] = useState<DeploymentLogEntry | null>(
    null,
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadMoreSentinelRef = useRef<HTMLDivElement>(null);
  /** Live tail: if true, new batches stick to the newest (top) rows. False after user scrolls down. */
  const liveFollowsNewestRef = useRef(true);
  const timePresetRef = useRef(timePreset);
  timePresetRef.current = timePreset;
  const searchRef = useRef(search);
  searchRef.current = search;

  /** True when the user has typed something that hasn't been committed yet (pending Enter). */
  const isSearchPending = search.trim() !== committedSearch.trim();

  /** True when a search filter is actively applied (committed, non-empty). */
  const searchActive = Boolean(committedSearch.trim());

  const { fromMs, toMs, customInvalid } = useMemo(
    () => computeTimeWindowMs(timePreset, customFromAt, customToAt),
    [timePreset, customFromAt, customToAt],
  );

  const isLiveTail = timePreset === "live";

  const fromIso =
    isLiveTail ? undefined : fromMs != null ? new Date(fromMs).toISOString() : undefined;
  const toIso =
    isLiveTail ? undefined : toMs != null ? new Date(toMs).toISOString() : undefined;

  /**
   * When search is active we override the time window with a fixed lookback so the user
   * gets results from across all recent history, not just the currently-visible slab.
   * The "from" is anchored to the moment the search was committed (committedSearch changes)
   * so re-renders don't drift the window.
   */
  const searchWindowFrom = useMemo(
    () => new Date(Date.now() - SEARCH_GLOBAL_LOOKBACK_MS).toISOString(),
    // Recalculate only when a new search is committed, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [committedSearch],
  );
  const searchWindowTo = useMemo(
    () => new Date().toISOString(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [committedSearch],
  );

  const effectiveFromIso = searchActive ? searchWindowFrom : fromIso;
  const effectiveToIso   = searchActive ? searchWindowTo   : toIso;

  // While search is active we always run the historical (paged) query, even in live-tail mode,
  // because live slabs only cover 15-minute windows and would miss older matching lines.
  const queryIsLiveTail = isLiveTail && !searchActive;

  const historicalInfinite = useDeploymentLogsInfinite({
    open,
    subdomain,
    deployment: rayAppName,
    tier,
    tail: false,
    from: effectiveFromIso,
    to: effectiveToIso,
    search: committedSearch,
    // Enable for normal historical mode OR whenever a search is active (overrides live tail).
    enabled: !queryIsLiveTail && !(customInvalid && !searchActive),
  });

  const liveInfinite = useLiveTailSlabInfinite({
    open,
    subdomain,
    deployment: rayAppName,
    tier,
    search: committedSearch,
    enabled: queryIsLiveTail,
  });

  const infinite = queryIsLiveTail ? liveInfinite : historicalInfinite;

  const serverEntries = useMemo(() => {
    const flat = infinite.data?.pages.flatMap((p) => p.entries) ?? [];
    const merged = dedupeLogEntriesById(flat);
    if (isLiveTail) {
      return [...merged].sort(compareEntriesNewestFirst);
    }
    return [...merged].sort(compareEntriesChronological);
  }, [infinite.data?.pages, isLiveTail]);

  const reset = useCallback(() => {
    setSearch("");
    setCommittedSearch("");
    setShowSearchHints(false);
    setTimePreset("live");
    const [from, to] = defaultCustomRange();
    setCustomFromAt(from);
    setCustomToAt(to);
    setSelectedEntry(null);
    liveFollowsNewestRef.current = true;
  }, []);

  /** Switch to custom range without resetting dates (e.g. user edited dates while on a relative preset). */
  const enterCustomMode = useCallback(() => {
    const prev = timePresetRef.current;
    if (prev === "custom") return;
    setSelectedEntry(null);
    setTimePreset("custom");
  }, []);

  const setTimePresetSafe = useCallback((next: LogTimePreset) => {
    setSelectedEntry(null);
    const to = new Date();
    if (next === "live") {
      liveFollowsNewestRef.current = true;
      setTimePreset("live");
      setCustomFromAt(new Date(to.getTime() - 60 * 60 * 1000));
      setCustomToAt(to);
      return;
    }
    if (next === "custom") {
      setTimePreset("custom");
      return;
    }
    const delta = LOG_RELATIVE_PRESET_MS[next as Exclude<LogTimePreset, "live" | "custom">];
    setCustomFromAt(new Date(to.getTime() - delta));
    setCustomToAt(to);
    setTimePreset(next);
  }, []);

  const timeFiltered = useMemo(
    // Skip client-side time filter when search is active: Loki already constrained the
    // results to the global lookback window, and applying the narrow live-tail window
    // would silently hide matching entries that fall outside it.
    () =>
      searchActive
        ? serverEntries
        : filterEntriesByTimeWindow(serverEntries, fromMs, toMs),
    [serverEntries, fromMs, toMs, searchActive],
  );

  const logLines = timeFiltered;

  const onLogScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (!isLiveTail) {
      return;
    }
    liveFollowsNewestRef.current =
      el.scrollTop <= LIVE_FOLLOW_TOP_OFFSET_PX;
  }, [isLiveTail]);

  useLayoutEffect(() => {
    if (!isLiveTail) return;
    if (!scrollRef.current) return;
    if (!liveFollowsNewestRef.current) {
      return;
    }
    scrollRef.current.scrollTop = 0;
  }, [logLines, isLiveTail]);

  useEffect(() => {
    const root = scrollRef.current;
    const target = loadMoreSentinelRef.current;
    if (!open || !root || !target) return;
    if (!isLiveTail && customInvalid) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const hit = entries.some((e) => e.isIntersecting);
        if (!hit) return;
        if (!infinite.hasNextPage || infinite.isFetchingNextPage) return;
        void infinite.fetchNextPage();
      },
      { root, rootMargin: "120px", threshold: 0 },
    );
    obs.observe(target);
    return () => obs.disconnect();
  }, [
    open,
    isLiveTail,
    customInvalid,
    infinite.hasNextPage,
    infinite.isFetchingNextPage,
    infinite.fetchNextPage,
    logLines.length,
  ]);

  const selectRow = useCallback((entry: DeploymentLogEntry | null) => {
    setSelectedEntry(entry);
  }, []);

  /** Commit the current typed search to Loki (called on Enter). */
  const commitSearch = useCallback(() => {
    setCommittedSearch(searchRef.current);
  }, []);

  /**
   * Set search AND immediately commit (used by chip add/remove so clicking a
   * chip always fires the query without requiring an extra Enter press).
   */
  const setAndCommitSearch = useCallback((value: string) => {
    setSearch(value);
    setCommittedSearch(value);
  }, []);

  const appendSearchChip = useCallback((chip: string) => {
    setSearch((prev) => {
      const next = prev.trim() ? `${prev.trim()} ${chip}` : chip;
      setCommittedSearch(next);
      return next;
    });
  }, []);

  return {
    timePreset,
    setTimePreset: setTimePresetSafe,
    enterCustomMode,
    customFromAt,
    setCustomFromAt,
    customToAt,
    setCustomToAt,
    customInvalid,
    isLiveTail,
    search,
    setSearch,
    showSearchHints,
    setShowSearchHints,
    appendSearchChip,
    filtered: logLines,
    onLogScroll,
    scrollRef,
    loadMoreSentinelRef,
    reset,
    selectedEntry,
    selectRow,
    isLoading: infinite.isPending,
    isError: infinite.isError,
    error: infinite.error,
    isFetchingNextPage: infinite.isFetchingNextPage,
    hasNextPage: infinite.hasNextPage ?? false,
    isSearchPending,
    searchActive,
    committedSearch,
    isFetchingLogs: infinite.isFetching,
    commitSearch,
    setAndCommitSearch,
  };
}

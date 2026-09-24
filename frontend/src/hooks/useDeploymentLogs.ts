import { useEffect, useMemo } from "react";
import {
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";

import { getDeploymentLogs } from "@api/services/logs";

import type { DeploymentLogEntry, DeploymentLogsResponse } from "@/types/logs";

/** Historical: forward Loki pages within one client time range. */
export const DEPLOYMENT_LOG_PAGE_SIZE = 500;

/** Live: rolling “head” window and each older slab (minutes). */
export const LIVE_HEAD_WINDOW_MS = 15 * 60 * 1000;
export const LIVE_SLAB_MS = 15 * 60 * 1000;

/** Max lines per 15m slab (Loki); large slabs may still truncate dense streams. */
const LIVE_SLAB_LINE_LIMIT = 2000;

const LIVE_HEAD_REFETCH_MS = 4000;

export const deploymentLogsInfiniteQueryKey = (
  subdomain: string,
  deployment: string,
  tier: number | undefined,
  tail: boolean,
  from: string | undefined,
  to: string | undefined,
  search: string,
  pageSize: number,
) =>
  [
    "deploymentLogs",
    "infinite",
    subdomain,
    deployment,
    tier ?? "none",
    tail ? "tail" : "head",
    from ?? "default-window",
    to ?? "default-window",
    search,
    pageSize,
  ] as const;

export const deploymentLogsLiveSlabsQueryKey = (
  subdomain: string,
  deployment: string,
  tier: number | undefined,
  search: string,
) =>
  [
    "deploymentLogs",
    "liveSlabs",
    subdomain,
    deployment,
    tier ?? "none",
    search,
    LIVE_HEAD_WINDOW_MS,
    LIVE_SLAB_MS,
  ] as const;

function toIso(ms: number): string {
  return new Date(ms).toISOString();
}

function minEntryTimeMs(entries: DeploymentLogEntry[]): number | null {
  let m = Infinity;
  for (const e of entries) {
    const t = new Date(e.timestamp).getTime();
    if (!Number.isNaN(t) && t < m) m = t;
  }
  return m === Infinity ? null : m;
}

/** Upper bound (ms) for the next older slab; lines strictly older than the previous oldest chunk. */
function encodeSlabUpperMs(toMs: number): string {
  return `slab:${Math.floor(toMs)}`;
}

function decodeSlabUpperMs(param: string): number | null {
  if (!param.startsWith("slab:")) return null;
  const n = Number(param.slice(5));
  return Number.isFinite(n) ? n : null;
}

export function useDeploymentLogsInfinite(options: {
  open: boolean;
  subdomain: string;
  deployment: string;
  tier?: number;
  /** Live tail: backward paging without client ``from``/``to``. */
  tail: boolean;
  from?: string;
  to?: string;
  search: string;
  enabled: boolean;
}) {
  const { open, subdomain, deployment, tier, tail, from, to, search, enabled } =
    options;
  return useInfiniteQuery({
    queryKey: deploymentLogsInfiniteQueryKey(
      subdomain,
      deployment,
      tier,
      tail,
      from,
      to,
      search,
      DEPLOYMENT_LOG_PAGE_SIZE,
    ),
    queryFn: ({ pageParam }) =>
      getDeploymentLogs({
        subdomain,
        deployment,
        tier,
        from: tail ? undefined : from,
        to: tail ? undefined : to,
        search: search.trim() || undefined,
        limit: DEPLOYMENT_LOG_PAGE_SIZE,
        cursor: pageParam,
        tail,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.next_cursor
        ? lastPage.next_cursor
        : undefined,
    enabled:
      open &&
      Boolean(subdomain) &&
      Boolean(deployment) &&
      enabled &&
      (tail || (Boolean(from) && Boolean(to))),
    refetchInterval: false,
  });
}

/**
 * Live tail: page 0 is always ``[now - 15m, now]`` and refetched on an interval.
 * Further pages are older non-overlapping 15m slabs; scroll-to-bottom loads the next slab.
 */
export function useLiveTailSlabInfinite(options: {
  open: boolean;
  subdomain: string;
  deployment: string;
  tier?: number;
  search: string;
  enabled: boolean;
}) {
  const { open, subdomain, deployment, tier, search, enabled } = options;
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () =>
      deploymentLogsLiveSlabsQueryKey(
        subdomain,
        deployment,
        tier,
        search,
      ),
    [subdomain, deployment, tier, search],
  );

  const infinite = useInfiniteQuery({
    queryKey,
    queryFn: async ({ pageParam }: { pageParam: string | undefined }) => {
      const q = search.trim() || undefined;
      if (pageParam == null || pageParam === undefined) {
        const to = Date.now();
        const fromMs = to - LIVE_HEAD_WINDOW_MS;
        return getDeploymentLogs({
          subdomain,
          deployment,
          tier,
          from: toIso(fromMs),
          to: toIso(to),
          search: q,
          limit: LIVE_SLAB_LINE_LIMIT,
          tail: false,
        });
      }
      const upperMs = decodeSlabUpperMs(pageParam);
      if (upperMs == null) {
        throw new Error("Invalid live log slab cursor");
      }
      const fromMs = Math.max(0, upperMs - LIVE_SLAB_MS);
      return getDeploymentLogs({
        subdomain,
        deployment,
        tier,
        from: toIso(fromMs),
        to: toIso(upperMs),
        search: q,
        limit: LIVE_SLAB_LINE_LIMIT,
        tail: false,
      });
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.entries.length) return undefined;
      const oldest = minEntryTimeMs(lastPage.entries);
      if (oldest == null) return undefined;
      const nextUpper = Math.floor(oldest) - 1;
      if (nextUpper <= 0) return undefined;
      return encodeSlabUpperMs(nextUpper);
    },
    enabled: open && Boolean(subdomain) && Boolean(deployment) && enabled,
  });

  useEffect(() => {
    if (!open || !enabled) return;
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const to = Date.now();
          const fromMs = to - LIVE_HEAD_WINDOW_MS;
          const fresh = await getDeploymentLogs({
            subdomain,
            deployment,
            tier,
            from: toIso(fromMs),
            to: toIso(to),
            search: search.trim() || undefined,
            limit: LIVE_SLAB_LINE_LIMIT,
            tail: false,
          });
          queryClient.setQueryData<
            InfiniteData<DeploymentLogsResponse, string | undefined>
          >(queryKey, (old) => {
            if (!old?.pages.length) return old;
            const pages = [...old.pages];
            pages[0] = fresh;
            return { ...old, pages, pageParams: old.pageParams };
          });
        } catch {
          /* ignore transient head refetch errors */
        }
      })();
    }, LIVE_HEAD_REFETCH_MS);
    return () => window.clearInterval(id);
  }, [
    open,
    enabled,
    queryClient,
    queryKey,
    subdomain,
    deployment,
    tier,
    search,
  ]);

  return infinite;
}

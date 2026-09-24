import { useCallback, useEffect, useState } from "react";

import { getFinancialCosts, parseCost } from "@api/services/financial";

/* ------------------------------------------------------------------ */
/* Financial tracking (Epic 10) — backed by GET /api/financial/costs. */
/* ------------------------------------------------------------------ */

export type FinanceFilter = {
  /** ISO date (yyyy-mm-dd), inclusive. */
  start: string;
  /** ISO date (yyyy-mm-dd), inclusive. */
  end: string;
};

const STORAGE_KEY = "athena-finance-filter";
const FILTER_EVENT = "athena:fin-filter";

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultFilter(): FinanceFilter {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 29);
  return { start: isoDate(start), end: isoDate(end) };
}

export function getFinanceFilter(): FinanceFilter {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as FinanceFilter;
      if (parsed.start && parsed.end) return parsed;
    }
  } catch {
    /* fall through to default */
  }
  return defaultFilter();
}

export function setFinanceFilter(filter: FinanceFilter): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(filter));
  window.dispatchEvent(new CustomEvent(FILTER_EVENT));
}

/** Shared date-range filter — persisted so every page shows the same range,
 *  and change events re-render every mounted cost view. */
export function useFinanceFilter(): [FinanceFilter, (f: FinanceFilter) => void] {
  const [filter, setFilter] = useState<FinanceFilter>(getFinanceFilter);

  useEffect(() => {
    const onChange = () => setFilter(getFinanceFilter());
    window.addEventListener(FILTER_EVENT, onChange);
    return () => window.removeEventListener(FILTER_EVENT, onChange);
  }, []);

  const update = useCallback((f: FinanceFilter) => {
    setFinanceFilter(f);
  }, []);

  return [filter, update];
}

export function daysBetween(filter: FinanceFilter): number {
  const start = new Date(filter.start);
  const end = new Date(filter.end);
  const days = Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
  return Math.max(1, days);
}

export function formatCost(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/* ---------------- Cost queries (GET /api/financial/costs) ---------------- */

/** A cost figure, or the marker for "could not be calculated" — the backend
 *  can't always price a workload (unknown model, no usage rows, endpoint
 *  down). UI shows "Not available" with an explanatory tooltip for it. */
export const COST_UNAVAILABLE = "unavailable" as const;
export type CostValue = number | typeof COST_UNAVAILABLE;

export const COST_UNAVAILABLE_LABEL = "Not available";
export const COST_UNAVAILABLE_HINT = "Unable to calculate the cost";

export function isCostAvailable(cost: CostValue): cost is number {
  return typeof cost === "number";
}

const TIERS = [1, 2, 3, 4];

function toCostValue(total: number | null): CostValue {
  return total === null ? COST_UNAVAILABLE : total;
}

/** Cost for one tier (urlTier 1-4) over the filter's date range. The
 *  filtered summary's `total_cost` is the tier total. */
export async function fetchTierCost(
  urlTier: number,
  filter: FinanceFilter,
): Promise<CostValue> {
  try {
    const res = await getFinancialCosts({
      start_date: filter.start,
      end_date: filter.end,
      tier: urlTier,
    });
    return toCostValue(parseCost(res.total_cost));
  } catch {
    return COST_UNAVAILABLE;
  }
}

/** Overall (cluster) cost over the filter's date range. */
export async function fetchClusterCost(filter: FinanceFilter): Promise<CostValue> {
  try {
    const res = await getFinancialCosts({
      start_date: filter.start,
      end_date: filter.end,
    });
    return toCostValue(parseCost(res.total_cost));
  } catch {
    return COST_UNAVAILABLE;
  }
}

/** Cost of one application deployment in one tier over the filter's range.
 *  `appKey` is `namespace/name`; the API keys application costs by
 *  namespace (`applications[].application_key`), so that's what's matched.
 *  The backend only breaks costs down per application for a tier-scoped
 *  query, hence `urlTier` is required. An app absent from `applications[]`
 *  has no stored cost for the range — the "Not available" case, not $0. */
export async function fetchAppCost(
  appKey: string,
  filter: FinanceFilter,
  urlTier: number,
): Promise<CostValue> {
  const slash = appKey.indexOf("/");
  const namespace = slash === -1 ? appKey : appKey.slice(0, slash);

  try {
    const res = await getFinancialCosts({
      start_date: filter.start,
      end_date: filter.end,
      tier: urlTier,
      source_namespace: namespace,
    });
    const row = res.applications.find((a) => a.application_key === namespace);
    const cost = row ? parseCost(row.cost) : null;
    return toCostValue(cost);
  } catch {
    return COST_UNAVAILABLE;
  }
}

/** Per-tier costs keyed by urlTier (1-4) over the filter's range — one
 *  cluster-wide request; `tiers[]` carries each tier's aggregate. A tier
 *  missing from the list has no stored cost for the range. */
export async function fetchAllTierCosts(
  filter: FinanceFilter,
): Promise<Record<number, CostValue>> {
  try {
    const res = await getFinancialCosts({
      start_date: filter.start,
      end_date: filter.end,
    });
    const byTier = new Map(res.tiers.map((t) => [t.tier, parseCost(t.cost)]));
    return Object.fromEntries(
      TIERS.map((tier) => [tier, toCostValue(byTier.get(tier) ?? null)]),
    );
  } catch {
    return Object.fromEntries(TIERS.map((tier) => [tier, COST_UNAVAILABLE]));
  }
}

import { apiFetch } from "..";

/* GET /api/financial/costs — stored costs for the cluster or one tier.
 * Response shape mirrors backend/src/service/schemas/financial.py
 * (FinancialCostsOut). Decimal fields arrive as JSON strings. */

export type CostType = "public_api" | "private_llm" | "both";

export type FinancialCostsQuery = {
  start_date?: string;
  end_date?: string;
  cost_type?: CostType;
  /** 1-4 */
  tier?: number;
  /** Filters `applications[]` to one application_key (the app namespace). */
  source_namespace?: string;
};

/** pydantic serializes Decimal as a string; tolerate a number too. */
export type DecimalString = string | number;

export type FinancialTierCost = { tier: number; cost: DecimalString };

export type FinancialApplicationCost = {
  /** The application's namespace (e.g. `amberd-test-deploy`). */
  application_key: string;
  tier: number;
  cost: DecimalString;
};

export type FinancialCostsResponse = {
  filters: {
    start_date: string;
    end_date: string;
    cost_type: CostType;
    tier: number | null;
    source_namespace: string | null;
  };
  currency: string;
  total_cost: DecimalString;
  tiers: FinancialTierCost[];
  applications: FinancialApplicationCost[];
};

export const getFinancialCosts = async (
  query: FinancialCostsQuery,
): Promise<FinancialCostsResponse> => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return await apiFetch<FinancialCostsResponse>(
    `/api/financial/costs${qs ? `?${qs}` : ""}`,
  );
};

/** Parse a Decimal-as-string cost; null when it isn't a finite number. */
export function parseCost(value: DecimalString | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

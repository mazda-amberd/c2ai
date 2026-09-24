export type TierSummary = {
  name: string;
  apps: number;
  status: { healthy: number; warning: number; critical: number };
  /** Tier GPU utilization %, null when the API has no value for the tier. */
  gpu: number | null;
};

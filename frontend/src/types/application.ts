/** Application instance health as returned by the metrics API. */
export type AppStatus = "Healthy" | "Warning" | "Critical";

export type Application = {
  id: number;
  name: string;
  nodename: string;
  client_name: string | null;
  instance_name: string | null;
  version: string | null;
  cpu: number;
  memory: number;
  gpu: number;
  status: AppStatus;
};

export type TiersResponse = {
  tiers: {
    [tierName: string]: Application[] | null;
  };
  tier_gpu_totals?: {
    [tierName: string]: number | null;
  };
};

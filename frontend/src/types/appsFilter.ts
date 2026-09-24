import type { AppStatus } from "@/types/application";

export type AppsFilterState = {
  application: string | null;
  clientName: string | null;
  instanceName: string | null;
  statuses: Array<AppStatus>;
};

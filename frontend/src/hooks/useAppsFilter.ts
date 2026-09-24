import { useMemo, useState } from "react";

import type { Application, AppStatus } from "@/types/application";
import type { AppsFilterState } from "@/types/appsFilter";

export type { AppsFilterState };

const EMPTY_FILTER: AppsFilterState = {
  application: null,
  clientName: null,
  instanceName: null,
  statuses: [],
};

export function useAppsFilter(apps: Application[]) {
  const [filters, setFilters] = useState<AppsFilterState>(EMPTY_FILTER);

  const filterOptions = useMemo(() => {
    const applications = new Set<string>();
    const clientNames = new Set<string>();
    const instanceNames = new Set<string>();
    const statuses = new Set<AppStatus>();

    apps.forEach((app) => {
      applications.add(app.name);
      if (app.client_name) clientNames.add(app.client_name);
      if (app.instance_name) instanceNames.add(app.instance_name);
      statuses.add(app.status);
    });

    return {
      applications: Array.from(applications).sort(),
      clientNames: Array.from(clientNames).sort(),
      instanceNames: Array.from(instanceNames).sort(),
      statuses: Array.from(statuses),
    };
  }, [apps]);

  const filteredApps = useMemo(() => {
    return apps.filter((app) => {
      if (filters.application && app.name !== filters.application) {
        return false;
      }
      if (filters.clientName && app.client_name !== filters.clientName) {
        return false;
      }
      if (filters.instanceName && app.instance_name !== filters.instanceName) {
        return false;
      }
      if (filters.statuses.length && !filters.statuses.includes(app.status)) {
        return false;
      }
      return true;
    });
  }, [apps, filters]);

  const statusCounts = useMemo(() => {
    return apps.reduce(
      (acc, app) => {
        if (app.status === "Healthy") acc.healthy++;
        else if (app.status === "Warning") acc.warning++;
        else if (app.status === "Critical") acc.critical++;
        return acc;
      },
      { healthy: 0, warning: 0, critical: 0 },
    );
  }, [apps]);

  return { filters, setFilters, filterOptions, filteredApps, statusCounts };
}

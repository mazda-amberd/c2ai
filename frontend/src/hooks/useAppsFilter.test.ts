import { renderHook, act } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Application } from "@/types/application";

import { useAppsFilter } from "./useAppsFilter";

const app = (overrides: Partial<Application>): Application => ({
  id: 1,
  name: "ada",
  nodename: "amberd-acme-ada",
  client_name: "acme",
  instance_name: "ada",
  version: null,
  cpu: 0,
  memory: 0,
  gpu: 0,
  status: "Healthy",
  ...overrides,
});

describe("useAppsFilter", () => {
  it("builds filter option sets from apps", () => {
    const apps: Application[] = [
      app({ id: 1, name: "a1", client_name: "c1", instance_name: "i1", status: "Healthy" }),
      app({ id: 2, name: "a2", client_name: "c2", instance_name: "i2", status: "Warning" }),
    ];

    const { result } = renderHook(() => useAppsFilter(apps));

    expect(result.current.filterOptions.applications).toEqual(["a1", "a2"]);
    expect(result.current.filterOptions.clientNames).toEqual(["c1", "c2"]);
    expect(result.current.filterOptions.instanceNames).toEqual(["i1", "i2"]);
    expect(result.current.filterOptions.statuses.sort()).toEqual(["Healthy", "Warning"]);
  });

  it("filters by application name", () => {
    const apps: Application[] = [
      app({ id: 1, name: "keep", client_name: "c1", instance_name: "i1", status: "Healthy" }),
      app({ id: 2, name: "drop", client_name: "c2", instance_name: "i2", status: "Critical" }),
    ];

    const { result } = renderHook(() => useAppsFilter(apps));

    act(() => {
      result.current.setFilters({ application: "keep", clientName: null, instanceName: null, statuses: [] });
    });
    expect(result.current.filteredApps.map((a) => a.id)).toEqual([1]);
  });

  it("filters by client name", () => {
    const apps: Application[] = [
      app({ id: 1, name: "keep", client_name: "c-keep", instance_name: "i1", status: "Healthy" }),
      app({ id: 2, name: "drop", client_name: "c-drop", instance_name: "i2", status: "Critical" }),
    ];

    const { result } = renderHook(() => useAppsFilter(apps));

    act(() => {
      result.current.setFilters({ application: null, clientName: "c-keep", instanceName: null, statuses: [] });
    });
    expect(result.current.filteredApps.map((a) => a.id)).toEqual([1]);
  });

  it("filters by instance name", () => {
    const apps: Application[] = [
      app({ id: 1, name: "keep", client_name: "c1", instance_name: "i-keep", status: "Healthy" }),
      app({ id: 2, name: "drop", client_name: "c2", instance_name: "i-drop", status: "Critical" }),
    ];

    const { result } = renderHook(() => useAppsFilter(apps));

    act(() => {
      result.current.setFilters({ application: null, clientName: null, instanceName: "i-keep", statuses: [] });
    });
    expect(result.current.filteredApps.map((a) => a.id)).toEqual([1]);
  });

  it("filters by status", () => {
    const apps: Application[] = [
      app({ id: 1, name: "keep", client_name: "c1", instance_name: "i1", status: "Healthy" }),
      app({ id: 2, name: "drop", client_name: "c2", instance_name: "i2", status: "Critical" }),
    ];

    const { result } = renderHook(() => useAppsFilter(apps));

    act(() => {
      result.current.setFilters({ application: null, clientName: null, instanceName: null, statuses: ["Critical"] });
    });
    expect(result.current.filteredApps.map((a) => a.id)).toEqual([2]);
  });

  it("counts statuses across all apps", () => {
    const apps: Application[] = [
      app({ id: 1, status: "Healthy" }),
      app({ id: 2, status: "Healthy" }),
      app({ id: 3, status: "Warning" }),
      app({ id: 4, status: "Critical" }),
    ];

    const { result } = renderHook(() => useAppsFilter(apps));

    expect(result.current.statusCounts).toEqual({ healthy: 2, warning: 1, critical: 1 });
  });
});

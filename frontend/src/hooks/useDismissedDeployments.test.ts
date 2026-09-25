import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useDismissedDeployments } from "./useDismissedDeployments";

const KEY = "athena.dismissedDeployments";

describe("useDismissedDeployments", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("remembers dismissed deployments across mounts", () => {
    const first = renderHook(() => useDismissedDeployments());
    act(() => first.result.current.dismiss("op-1"));
    expect(first.result.current.isDismissed("op-1")).toBe(true);
    expect(first.result.current.isDismissed("op-2")).toBe(false);

    const second = renderHook(() => useDismissedDeployments());
    expect(second.result.current.isDismissed("op-1")).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual(["op-1"]);
  });

  it("keeps only the most recent 200 ids", () => {
    const { result } = renderHook(() => useDismissedDeployments());
    act(() => {
      for (let i = 0; i < 205; i += 1) result.current.dismiss(`op-${i}`);
    });
    const stored: string[] = JSON.parse(window.localStorage.getItem(KEY)!);
    expect(stored).toHaveLength(200);
    expect(stored[0]).toBe("op-5");
    expect(result.current.isDismissed("op-0")).toBe(false);
  });

  it("ignores unreadable storage", () => {
    window.localStorage.setItem(KEY, "{not json");
    const { result } = renderHook(() => useDismissedDeployments());
    expect(result.current.isDismissed("op-1")).toBe(false);
  });
});

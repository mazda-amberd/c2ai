import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useDebouncedValue } from "./useDebouncedValue";

describe("useDebouncedValue", () => {
  it("returns value immediately when ms is 0", () => {
    const { result, rerender } = renderHook(
      ({ v, ms }: { v: string; ms: number }) => useDebouncedValue(v, ms),
      { initialProps: { v: "a", ms: 0 } },
    );
    expect(result.current).toBe("a");
    rerender({ v: "b", ms: 0 });
    expect(result.current).toBe("b");
  });

  it("delays updates when ms > 0", async () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ v }: { v: string }) => useDebouncedValue(v, 100),
      { initialProps: { v: "a" } },
    );
    expect(result.current).toBe("a");
    rerender({ v: "b" });
    expect(result.current).toBe("a");
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("b");
    vi.useRealTimers();
  });
});

import { useEffect, useState } from "react";

/**
 * Returns `value` after it has stayed unchanged for `ms` milliseconds.
 * When `ms` is 0, updates synchronously on the next commit (no timer).
 */
export function useDebouncedValue<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    if (ms <= 0) {
      setDebounced(value);
      return;
    }
    const id = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);

  return debounced;
}

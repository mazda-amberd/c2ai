import { useCallback, useState } from "react";

const STORAGE_KEY = "athena.dismissedDeployments";
/** Keeps the list bounded; old ids fall out as new ones are dismissed. */
const MAX_REMEMBERED = 200;

function readDismissed(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function writeDismissed(ids: string[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    /* storage unavailable (private mode) — dismissal lasts until reload */
  }
}

/** Failed deployment cards the user removed from the tier page. Kept in this
 *  browser only; the operation itself is untouched and drops off the page on
 *  its own once the server stops reporting it. */
export function useDismissedDeployments() {
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);

  const dismiss = useCallback((id: string) => {
    setDismissed((current) => {
      if (current.includes(id)) return current;
      const next = [...current, id].slice(-MAX_REMEMBERED);
      writeDismissed(next);
      return next;
    });
  }, []);

  const isDismissed = useCallback((id: string) => dismissed.includes(id), [dismissed]);

  return { dismiss, isDismissed };
}

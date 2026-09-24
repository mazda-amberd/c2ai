import { createContext, useCallback, useContext, useRef, useState } from "react";

/* Exact match of the template's showToast()/.flash-toast:
 *   position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
 *   padding: 11px 18px; border-radius: 8px;
 *   border: 1px solid rgba(33,226,223,.5); background: rgba(6,26,32,.96);
 *   color: #a9fbf8; font-size: 12px; font-weight: 600;
 *   box-shadow: var(--shadow) -> 0 20px 60px rgba(0,0,0,.34);
 *   opacity/transform transition .2s ease; pointer-events: none (always,
 *   even while shown — the template's toast is purely informational).
 * Auto-dismiss after 2.6s, matching the template's setTimeout. */

const TOAST_DURATION_MS = 2600;

type ToastContextValue = {
  showToast: (message: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const showToast = useCallback((next: string) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setMessage(next);
    // Two rAF ticks so re-triggering while already visible still replays
    // the opacity/transform transition instead of collapsing into one frame.
    setVisible(false);
    requestAnimationFrame(() => requestAnimationFrame(() => setVisible(true)));
    timerRef.current = setTimeout(() => setVisible(false), TOAST_DURATION_MS);
  }, []);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {message && (
        <div
          aria-live="polite"
          className="fixed bottom-6 left-1/2 z-[200] rounded-lg border pointer-events-none px-[18px] py-[11px] text-xs font-semibold transition-[opacity,transform] duration-200 ease-in-out"
          style={{
            borderColor: "rgba(33,226,223,.5)",
            background: "rgba(6,26,32,.96)",
            color: "#a9fbf8",
            boxShadow: "0 20px 60px rgba(0,0,0,.34)",
            opacity: visible ? 1 : 0,
            transform: `translateX(-50%) translateY(${visible ? 0 : 12}px)`,
          }}
        >
          {message}
        </div>
      )}
    </ToastContext.Provider>
  );
}

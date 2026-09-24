import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { ChevronDown, Loader2 } from "lucide-react";

import { Input } from "@ui/input";
import { cn } from "@/lib/utils";
import { useDebouncedValue } from "@hooks/useDebouncedValue";

export type ComboboxProps = {
  /** Selection is communicated only via `onChange`; search text is internal. */
  inputRef?: RefObject<HTMLInputElement | null>;
  onChange: (next: string) => void;
  items: string[];
  loading?: boolean;
  error?: string;
  placeholder?: string;
  inputId?: string;
  /** Icon shown next to each option. Pass a ReactNode for a uniform icon or a function for per-item icons. */
  itemIcon?: ReactNode | ((item: string) => ReactNode);
  emptyMessage?: string;
  noMatchMessage?: string;
  /** When `items.length` is greater than this, filtering uses a debounced query. */
  debounceFilterThreshold?: number;
  debounceMs?: number;
  className?: string;
};

function filterBySubstring(items: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((item) => item.toLowerCase().includes(q));
}

export default function Combobox({
  inputRef: inputRefProp,
  onChange,
  items,
  loading = false,
  error,
  placeholder = "Search…",
  inputId,
  itemIcon,
  emptyMessage = "No items",
  noMatchMessage = "No matches",
  debounceFilterThreshold = 50,
  debounceMs = 180,
  className,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const innerInputRef = useRef<HTMLInputElement>(null);
  const inputRef = inputRefProp ?? innerInputRef;

  const shouldDebounce = items.length > debounceFilterThreshold;
  const debouncedQuery = useDebouncedValue(query, shouldDebounce ? debounceMs : 0);
  const effectiveQuery = shouldDebounce ? debouncedQuery : query;

  const filtered = useMemo(
    () => filterBySubstring(items, effectiveQuery),
    [items, effectiveQuery],
  );

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const select = useCallback(
    (item: string) => {
      onChange(item);
      setQuery("");
      setOpen(false);
    },
    [onChange],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (e.key === "Enter" && open && filtered.length === 1) {
      e.preventDefault();
      select(filtered[0]!);
    }
  };

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <div className="relative flex items-center">
        <Input
          ref={inputRef}
          id={inputId}
          value={query}
          placeholder={placeholder}
          autoComplete="off"
          className="pr-8"
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          tabIndex={-1}
          className="absolute right-2 flex items-center text-muted-foreground hover:text-foreground"
          onClick={() => setOpen((o) => !o)}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      {open && !loading && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-popover-border bg-popover shadow-md">
          <ul className="max-h-48 overflow-y-auto py-1 no-scrollbar">
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-xs text-muted-foreground">
                {items.length === 0 ? emptyMessage : noMatchMessage}
              </li>
            ) : (
              filtered.map((item) => (
                <li key={item}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground"
                    onMouseDown={(ev) => {
                      ev.preventDefault();
                      select(item);
                    }}
                  >
                    {itemIcon != null ? (
                      <span className="shrink-0 text-muted-foreground [&>svg]:h-3.5 [&>svg]:w-3.5">
                        {typeof itemIcon === "function" ? itemIcon(item) : itemIcon}
                      </span>
                    ) : null}
                    <span className="truncate font-mono">{item}</span>
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      )}

      {error ? <p className="mt-1 text-xs text-critical-text">{error}</p> : null}
    </div>
  );
}

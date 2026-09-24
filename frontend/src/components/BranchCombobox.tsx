import { useMemo, useRef } from "react";
import { GitBranch, Tag, X } from "lucide-react";

import { cn } from "@/lib/utils";
import Combobox from "@ui/combobox";

export type BranchComboboxProps = {
  value: string;
  onChange: (val: string) => void;
  branches: string[];
  tags: string[];
  loading: boolean;
  error?: string;
};

export default function BranchCombobox({
  value,
  onChange,
  branches,
  tags,
  loading,
  error,
}: BranchComboboxProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const tagSet = useMemo(() => new Set(tags), [tags]);
  const items = useMemo(() => [...branches, ...tags], [branches, tags]);

  function clear() {
    onChange("");
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  const isTag = tagSet.has(value);

  if (value) {
    return (
      <div className="flex h-8 w-full items-center gap-2">
        <div
          className={cn(
            "flex flex-1 min-w-0 items-center gap-1.5 rounded-esm",
            "border border-border bg-button-primary",
            "px-2.5 py-0 h-8",
          )}
        >
          {isTag ? (
            <Tag className="h-3.5 w-3.5 shrink-0 text-text-primary" />
          ) : (
            <GitBranch className="h-3.5 w-3.5 shrink-0 text-text-primary" />
          )}
          <span className="flex-1 truncate font-mono text-sm text-text-primary">
            {value}
          </span>
        </div>
        <button
          type="button"
          aria-label="Clear selection"
          className={cn(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-esm",
            "border border-border bg-button-primary",
            "text-muted-foreground transition-colors hover:text-foreground",
          )}
          onClick={clear}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  return (
    <Combobox
      inputRef={inputRef}
      inputId="branch"
      onChange={onChange}
      items={items}
      loading={loading}
      error={error}
      placeholder="e.g. main or v1.0.0"
      emptyMessage="No branches or tags found"
      noMatchMessage="No matches"
      itemIcon={(item) => (tagSet.has(item) ? <Tag /> : <GitBranch />)}
      debounceFilterThreshold={50}
      debounceMs={180}
      className="[&_input]:h-8"
    />
  );
}

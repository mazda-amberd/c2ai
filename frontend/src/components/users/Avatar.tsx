import { cn } from "@/lib/utils";

/* One colour per address, so a person looks the same in the menu and the
 * list. Muted enough to sit on the dark panels. */
const COLOURS = ["#0e7490", "#7c3aed", "#b45309", "#047857", "#be185d", "#1d4ed8", "#4d7c0f", "#9f1239"];

function colourFor(seed: string): string {
  let hash = 0;
  for (const ch of seed) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return COLOURS[hash % COLOURS.length];
}

export default function Avatar({
  name,
  email,
  className,
}: {
  name?: string | null;
  email: string;
  className?: string;
}) {
  const initial = (name?.trim()[0] || email[0] || "?").toUpperCase();
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[13px] font-bold text-white",
        className,
      )}
      style={{ background: colourFor(email.toLowerCase()) }}
    >
      {initial}
    </span>
  );
}

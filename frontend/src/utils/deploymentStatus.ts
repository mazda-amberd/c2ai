import type { PipelineStatusRecord } from "@/types/deployment";

/** True when the pipeline row reflects a finished run (GitHub conclusion or DB `ended_at`). */
export const isTerminal = (record: PipelineStatusRecord): boolean =>
  record.gh_conclusion != null || record.ended_at != null;

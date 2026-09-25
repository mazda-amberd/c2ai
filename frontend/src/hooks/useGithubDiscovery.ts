import { useEffect, useState } from "react";

import {
  listGithubRefs,
  listGithubRepositories,
  listGithubWorkflows,
  type ApiGithubRefOptions,
  type ApiGithubRepositoryOptions,
  type ApiGithubWorkflowOptions,
} from "@api/services/registeredApplications";
import { useDebouncedValue } from "./useDebouncedValue";

export type Listing<T> = {
  status: "idle" | "loading" | "ready" | "error";
  data: T | null;
  error: string;
};

const IDLE = { status: "idle", data: null, error: "" } as const;
const REPOSITORY = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

/** Load `load()` whenever `key` changes (nothing while it is null). */
function useListing<T>(key: string | null, load: () => Promise<T>): Listing<T> {
  const [listing, setListing] = useState<Listing<T>>(IDLE);
  useEffect(() => {
    if (key === null) {
      setListing(IDLE);
      return;
    }
    let cancelled = false;
    setListing({ status: "loading", data: null, error: "" });
    load()
      .then((data) => {
        if (!cancelled) setListing({ status: "ready", data, error: "" });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setListing({
            status: "error",
            data: null,
            error: err instanceof Error ? err.message : "GitHub could not be asked.",
          });
        }
      });
    return () => {
      cancelled = true;
    };
    // `load` is rebuilt every render; `key` says when it asks for something else.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return listing;
}

/** What a GitHub connection can see, for the register wizard to offer: its
 *  repositories, the workflow repository's branches and tags, and the
 *  workflow files on the chosen branch. Typing is debounced. */
export function useGithubDiscovery({
  enabled,
  connection,
  repository,
  ref,
}: {
  enabled: boolean;
  connection: string;
  repository: string;
  ref: string;
}) {
  const repo = useDebouncedValue(repository.trim(), 400);
  const branch = useDebouncedValue(ref.trim(), 400);
  const on = enabled && !!connection;
  const repoKnown = on && REPOSITORY.test(repo);

  const repositories = useListing<ApiGithubRepositoryOptions>(on ? connection : null, () =>
    listGithubRepositories(connection),
  );
  const refs = useListing<ApiGithubRefOptions>(repoKnown ? `${connection}|${repo}` : null, () =>
    listGithubRefs(connection, repo),
  );
  const workflows = useListing<ApiGithubWorkflowOptions>(
    repoKnown && branch ? `${connection}|${repo}|${branch}` : null,
    () => listGithubWorkflows(connection, repo, branch),
  );
  return { repositories, refs, workflows, branch };
}

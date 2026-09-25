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
 *  repositories, the branches and tags of the code and workflow
 *  repositories, and the workflow files on the workflow's branch. A blank
 *  workflow repository or branch is the code's. Typing is debounced. */
export function useGithubDiscovery({
  enabled,
  connection,
  codeRepository,
  codeRef,
  workflowRepository,
  workflowRef,
}: {
  enabled: boolean;
  connection: string;
  codeRepository: string;
  codeRef: string;
  workflowRepository: string;
  workflowRef: string;
}) {
  const code = useDebouncedValue(codeRepository.trim(), 400);
  const codeBranch = useDebouncedValue(codeRef.trim(), 400);
  const workflowRepo = useDebouncedValue(workflowRepository.trim(), 400) || code;
  const workflowBranch = useDebouncedValue(workflowRef.trim(), 400) || codeBranch;
  const on = enabled && !!connection;
  const known = (repository: string) => on && REPOSITORY.test(repository);

  const repositories = useListing<ApiGithubRepositoryOptions>(on ? connection : null, () =>
    listGithubRepositories(connection),
  );
  const codeRefs = useListing<ApiGithubRefOptions>(known(code) ? `${connection}|${code}` : null, () =>
    listGithubRefs(connection, code),
  );
  // Only asked for when the workflow lives in another repository.
  const otherRefs = useListing<ApiGithubRefOptions>(
    known(workflowRepo) && workflowRepo !== code ? `${connection}|${workflowRepo}` : null,
    () => listGithubRefs(connection, workflowRepo),
  );
  const workflows = useListing<ApiGithubWorkflowOptions>(
    known(workflowRepo) && workflowBranch ? `${connection}|${workflowRepo}|${workflowBranch}` : null,
    () => listGithubWorkflows(connection, workflowRepo, workflowBranch),
  );
  return {
    repositories,
    codeRefs,
    workflowRefs: workflowRepo === code ? codeRefs : otherRefs,
    workflows,
    workflowRepository: workflowRepo,
    workflowBranch,
  };
}

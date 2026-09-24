import { apiFetch, ApiFetchError, parseFastApiDetail } from "..";

import type {
  DeploymentLogsResponse,
  GetDeploymentLogsParams,
} from "@/types/logs";

export type { GetDeploymentLogsParams } from "@/types/logs";

export async function getDeploymentLogs(
  params: GetDeploymentLogsParams,
): Promise<DeploymentLogsResponse> {
  const q = new URLSearchParams({
    subdomain: params.subdomain,
    deployment: params.deployment,
  });
  if (params.tier != null) {
    q.set("tier", String(params.tier));
  }
  if (params.from) {
    q.set("from", params.from);
  }
  if (params.to) {
    q.set("to", params.to);
  }
  if (params.search != null && params.search.trim() !== "") {
    q.set("search", params.search.trim());
  }
  if (params.limit != null) {
    q.set("limit", String(params.limit));
  }
  if (params.cursor) {
    q.set("cursor", params.cursor);
  }
  if (params.tail) {
    q.set("tail", "true");
  }
  try {
    return await apiFetch<DeploymentLogsResponse>(
      `/api/logs/deployment?${q.toString()}`,
    );
  } catch (e) {
    if (e instanceof ApiFetchError && e.status === 404) {
      const detail = parseFastApiDetail(e.body);
      const hint =
        "Logs endpoint not found (404). Restart or redeploy the backend with the latest code. " +
        "If you use Vite dev, ensure the API is running on port 8007 (proxied from /api) or set VITE_BACKEND_URL.";
      const suffix =
        detail && detail.trim().length > 0 ? ` Server: ${detail.trim()}` : "";
      throw new Error(hint + suffix);
    }
    throw e;
  }
}

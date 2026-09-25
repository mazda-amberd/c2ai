import { useEffect, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Box, Github, Loader2 } from "lucide-react";

import { Dialog, DialogContent } from "@ui/dialog";
import {
  getRegisteredApplication,
  type ApiGithubParameter,
  type ApiParameterType,
  type ApiRegisteredApplicationDetail,
} from "@api/services/registeredApplications";
import { fetchGithubConnections, type GithubConnection } from "@/utils/registrationApi";
import type { RegisteredApp } from "@/utils/registeredAppsApi";
import { ACCENT, PANEL_BACKGROUND } from "./wizardStyles";

const MASKED = "••••••••";

const PARAMETER_TYPE_LABEL: Record<ApiParameterType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Boolean",
  select: "Choice",
  key_value: "Key-Value",
};

const REGISTRY_LABEL: Record<string, string> = {
  "docker.io": "Docker Hub",
  ecr: "Amazon ECR",
  "ghcr.io": "GitHub Container Registry",
  private: "Private Registry",
};

/** "debug on Tier 1, error on Tier 4" — the tiers with their own value. */
function onTiers(values: Record<string, unknown> | undefined): string {
  return Object.entries(values ?? {})
    .map(([tier, value]) => `${String(value)} on Tier ${tier}`)
    .join(", ");
}

/** A GitHub parameter in a line: its type, and what the deploy form starts with. */
function describeParameter(p: ApiGithubParameter): string {
  const parts = [PARAMETER_TYPE_LABEL[p.type] ?? p.type];
  if (p.required === false) parts.push("optional");
  if (p.options?.length) parts.push(`one of ${p.options.join(", ")}`);
  if (p.default !== null && p.default !== undefined) {
    parts.push(`default ${typeof p.default === "object" ? JSON.stringify(p.default) : String(p.default)}`);
  }
  const tiers = onTiers(p.tier_defaults);
  if (tiers) parts.push(tiers);
  return parts.join(" · ");
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[8px] border border-[#1c2836]">
      <div className="border-b border-[#1c2836] bg-[rgba(6,17,29,0.6)] px-3.5 py-2 text-[10.5px] font-semibold uppercase tracking-wide text-[#8b97a5]">
        {title}
      </div>
      <dl className="divide-y divide-[#1c2836]">{children}</dl>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  const empty = value === "" || value === null || value === undefined;
  return (
    <div className="grid grid-cols-[minmax(140px,38%)_1fr] gap-3 px-3.5 py-2 text-[12.5px]">
      <dt className="text-[#8b97a5]">{label}</dt>
      <dd className={`min-w-0 break-words ${empty ? "text-[#57606c]" : "text-[#eef2f6]"}`}>
        {empty ? "—" : value}
      </dd>
    </div>
  );
}

/** Read-only summary of a registered application template, opened by
 *  clicking its row in the catalog. Secrets are never returned by the API
 *  and are shown masked. */
export default function ApplicationSummaryModal({
  app,
  onOpenChange,
}: {
  app: RegisteredApp | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [detail, setDetail] = useState<ApiRegisteredApplicationDetail | null>(null);
  const [connections, setConnections] = useState<GithubConnection[]>([]);
  const [error, setError] = useState<string | null>(null);

  const appId = app?.id;
  useEffect(() => {
    if (!appId) return;
    let cancelled = false;
    getRegisteredApplication(appId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setDetail(null);
        setError(err instanceof Error ? err.message : "Could not load the application.");
      });
    fetchGithubConnections()
      .then((list) => !cancelled && setConnections(list))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [appId]);

  // Only show the detail that belongs to the app currently open.
  const shown = detail && app && String(detail.id) === app.id ? detail : null;
  const Icon = app?.type === "container" ? Box : Github;

  return (
    <Dialog open={app !== null} onOpenChange={onOpenChange}>
      <DialogContent
        className="top-[6vh] max-h-[88vh] w-full max-w-2xl -translate-y-0 overflow-y-auto rounded-[10px] border-[#1c2836] p-4"
        closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
        style={{ background: PANEL_BACKGROUND }}
      >
        <div className="mb-1 flex items-start gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
            style={{ background: "rgba(32,171,199,0.12)", color: ACCENT }}
          >
            <Icon className="h-[18px] w-[18px]" />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
              Registered Application
            </p>
            {/* Radix primitives (not the styled DialogTitle) so screen readers get
                a title without changing the look. */}
            <DialogPrimitive.Title asChild>
              <h3 className="truncate text-lg font-bold text-[#eef2f6]">{app?.name}</h3>
            </DialogPrimitive.Title>
          </div>
        </div>
        <DialogPrimitive.Description asChild>
          <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
            Everything registered for this application template.
          </p>
        </DialogPrimitive.Description>

        {error && <p className="text-[12.5px] text-[#f0655f]">{error}</p>}
        {!error && !shown && (
          <p className="flex items-center gap-1.5 text-[12px] text-[#57606c]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
          </p>
        )}

        {shown && (
          <div className="space-y-3">
            <Section title="Basic Information">
              <Row label="Application Name" value={shown.name} />
              <Row label="Description" value={shown.description ?? ""} />
              <Row
                label="Application Type"
                value={
                  shown.application_type === "containerized"
                    ? "Containerized Application"
                    : "GitHub Workflow"
                }
              />
              <Row label="Template Version" value={`v${shown.version}`} />
              <Row label="Created By" value={shown.created_by} />
              <Row label="Created" value={shown.created_at?.slice(0, 10)} />
            </Section>

            {shown.application_type === "github_workflow" && (
              <>
                <Section title="Workflow Configuration">
                  <Row
                    label="GitHub Connection"
                    value={
                      connections.find((c) => c.id === shown.github.github_connection)?.name ??
                      shown.github.github_connection
                    }
                  />
                  <Row label="Code Repository" value={shown.github.code_repository ?? ""} />
                  <Row label="Workflow Repository" value={shown.github.repository} />
                  <Row label="Branch / Ref" value={shown.github.ref} />
                  <Row label="Workflow File" value={shown.github.workflow_file_path} />
                  <Row label="Trigger Method" value={shown.github.trigger_method} />
                </Section>
                <Section title="Parameters">
                  {shown.parameters.length === 0 ? (
                    <Row label="Parameters" value="" />
                  ) : (
                    shown.parameters.map((p) => (
                      <Row key={p.key} label={p.label || p.key} value={describeParameter(p)} />
                    ))
                  )}
                </Section>
              </>
            )}

            {shown.application_type === "containerized" && (
              <>
                <Section title="Container / Image Configuration">
                  <Row
                    label="Container Registry"
                    value={REGISTRY_LABEL[shown.container.registry] ?? shown.container.registry}
                  />
                  <Row label="Image Registry" value={shown.container.image_registry} />
                  <Row label="Registry Username" value={shown.container.registry_username ?? ""} />
                  <Row label="Registry Password / Token" value={MASKED} />
                  <Row label="Default Image Tag" value={shown.container.tag ?? ""} />
                  <Row label="Container Port" value={shown.container.port ?? ""} />
                  <Row label="Image Pull Policy" value={shown.container.pull_policy} />
                  <Row
                    label="Public Service (Ingress)"
                    value={shown.container.expose_public_service ? "Yes" : "No"}
                  />
                </Section>
                <Section title="Resources & Scaling">
                  <Row label="CPU Request" value={shown.container.cpu_request ?? ""} />
                  <Row label="Memory Request" value={shown.container.memory_request ?? ""} />
                  <Row label="Replica Count" value={shown.container.scaling ?? ""} />
                  <Row
                    label="Persistent Volume"
                    value={shown.container.storage || "No persistent volume"}
                  />
                </Section>
                <Section title="Environment Variables">
                  {shown.parameters.length === 0 ? (
                    <Row label="Environment Variables" value="" />
                  ) : (
                    shown.parameters.map((p) => {
                      const tiers = onTiers(p.tier_values);
                      return (
                        <Row
                          key={p.key}
                          label={p.key}
                          value={tiers ? `${p.value} (${tiers})` : p.value}
                        />
                      );
                    })
                  )}
                </Section>
              </>
            )}

            <Section title="LLM Configuration">
              <Row label="LLM Endpoint" value={shown.llm?.endpoint ?? ""} />
              <Row label="LLM API Token" value={shown.llm ? MASKED : ""} />
              <Row label="LLM Model Name" value={shown.llm?.model_name ?? ""} />
            </Section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Box, Copy, Eye, Github, Loader2, Pencil, Plus, Search, Trash2 } from "lucide-react";

import { useAuth } from "@auth/AuthContext";
import { Button } from "@ui/button";
import { Dialog, DialogContent } from "@ui/dialog";
import { useToast } from "@components/Toast";
import ApplicationSummaryModal from "@components/registerApp/ApplicationSummaryModal";
import RegisterApplicationModal from "@components/registerApp/RegisterApplicationModal";
import { ACCENT, PANEL_BACKGROUND, Select } from "@components/registerApp/wizardStyles";
import {
  deleteRegisteredApp,
  fetchRegisteredApps,
  TYPE_LABEL,
  type RegisteredApp,
  type RegisteredAppType,
} from "@/utils/registeredAppsApi";

const TIER_NAMES = ["Tier 1", "Tier 2", "Tier 3", "Tier 4"];

/* Type-specific accent — not in the template's CSS (which colors every
 * type icon the same cyan), added per explicit request: GitHub workflows
 * are purple, containerized applications are blue. */
const TYPE_ACCENT: Record<RegisteredAppType, { text: string; border: string; bg: string }> = {
  github: { text: "#c084fc", border: "rgba(192,132,252,0.35)", bg: "rgba(168,85,247,0.12)" },
  container: { text: "#60a5fa", border: "rgba(96,165,250,0.35)", bg: "rgba(59,130,246,0.12)" },
};

type SortKey = "name" | "type" | "instances" | "tiers" | "created";
type SortDir = "asc" | "desc";

const COLUMNS: Array<{ key: SortKey | null; label: string; width: string }> = [
  { key: "name", label: "Application", width: "28%" },
  { key: "type", label: "Type", width: "16%" },
  { key: "instances", label: "Instances", width: "10%" },
  { key: "tiers", label: "Tiers Deployed To", width: "24%" },
  { key: "created", label: "Date Created", width: "14%" },
  { key: null, label: "", width: "8%" },
];

function sortValue(app: RegisteredApp, key: SortKey): string | number {
  switch (key) {
    case "name":
      return app.name.toLowerCase();
    case "type":
      return TYPE_LABEL[app.type].toLowerCase();
    case "instances":
      return app.instances;
    case "tiers":
      return Object.values(app.tiers).reduce((sum, c) => sum + c, 0);
    case "created":
      return app.created;
  }
}

function TypeIcon({ type, className }: { type: RegisteredAppType; className?: string }) {
  return type === "github" ? (
    <Github className={className} />
  ) : (
    <Box className={className} />
  );
}

export default function RegisteredApplications() {
  const navigate = useNavigate();
  // Everyone may browse the catalog; registering, editing and deleting
  // templates is for Admins (the API refuses the rest).
  const { isAdmin } = useAuth();

  const [apps, setApps] = useState<RegisteredApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [tierScope, setTierScope] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [registerOpen, setRegisterOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<RegisteredApp | null>(null);
  const [copyTarget, setCopyTarget] = useState<RegisteredApp | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RegisteredApp | null>(null);
  const [summaryApp, setSummaryApp] = useState<RegisteredApp | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { showToast } = useToast();

  /** Story 4.x delete gating — a template with live instances can't be
   *  removed; the button is disabled and explains why in a toast. */
  const handleDeleteClick = (e: React.MouseEvent, app: RegisteredApp) => {
    e.stopPropagation();
    if (app.instances > 0) {
      showToast(
        `Can't delete '${app.name}' — delete its ${app.instances} instance${
          app.instances === 1 ? "" : "s"
        } across every Tier first.`,
      );
      return;
    }
    setDeleteTarget(app);
  };

  /** A template is edited only while nothing of it is live on a tier —
   *  deployments run the version they started with. Blocked, the button
   *  says why instead. */
  const handleEditClick = (e: React.MouseEvent, app: RegisteredApp) => {
    e.stopPropagation();
    if (!app.canEdit) {
      showToast(
        `Can't edit '${app.name}' while it's deployed — terminate its ${app.instances} instance${
          app.instances === 1 ? "" : "s"
        } first.`,
      );
      return;
    }
    setEditTarget(app);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteRegisteredApp(deleteTarget);
      showToast(`'${deleteTarget.name}' was deleted.`);
      setDeleteTarget(null);
      loadApps();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not delete the application.");
    } finally {
      setDeleting(false);
    }
  };

  const loadApps = () => {
    setLoading(true);
    fetchRegisteredApps()
      .then(setApps)
      .catch(() => setApps([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadApps();
  }, []);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = apps.filter((a) => {
      if (q && !a.name.toLowerCase().includes(q) && !a.desc.toLowerCase().includes(q)) {
        return false;
      }
      if (typeFilter !== "all" && a.type !== typeFilter) return false;
      if (tierScope !== "all" && !(tierScope in a.tiers)) return false;
      return true;
    });

    if (sortKey) {
      filtered.sort((a, b) => {
        const va = sortValue(a, sortKey);
        const vb = sortValue(b, sortKey);
        const cmp =
          typeof va === "number" && typeof vb === "number"
            ? va - vb
            : String(va).localeCompare(String(vb));
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return filtered;
  }, [apps, query, typeFilter, tierScope, sortKey, sortDir]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <div className="p-6">
      <button
        type="button"
        onClick={() => navigate(-1)}
        className="inline-flex items-center gap-1.5 text-[13px] text-[#8b97a5] transition-colors hover:text-chart-accent"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back
      </button>

      {/* .page-head */}
      <div className="mt-4 mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="m-0 text-2xl font-bold">Registered Applications</h1>
          <p className="mt-1.5 max-w-[640px] text-[12.5px] leading-normal text-[#8b97a5]">
            Centralized catalog of every registered application template, independent of
            Tier. Register once, deploy multiple times.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          {/* .count-pill */}
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[#1c2836] bg-[rgba(8,21,36,0.6)] px-2.5 py-1 text-[11.5px] text-[#8b97a5]">
            <strong className="text-[#eef2f6]">{rows.length}</strong> of {apps.length}{" "}
            applications
          </span>
          {/* Template's plain .btn — the app's standard primary action
              button (same style as other default-variant buttons). */}
          {isAdmin ? (
            <Button onClick={() => setRegisterOpen(true)}>
              <Plus className="h-4 w-4" />
              Register Application
            </Button>
          ) : (
            <span
              title="Ask an Admin to register, edit, duplicate or delete applications"
              className="inline-flex items-center gap-1.5 rounded-full border border-[#1c2836] px-2.5 py-1 text-[11.5px] text-[#8b97a5]"
            >
              <Eye className="h-3.5 w-3.5" />
              View only
            </span>
          )}
        </div>
      </div>

      {/* .toolbar */}
      <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-[11px] top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[#57606c]" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search registered applications…"
            className="h-9 w-full rounded-[6px] border border-[#16202c] bg-[rgba(3,14,25,0.87)] pl-8 pr-3 text-xs text-[#eaf0f7] outline-none transition-colors focus:border-chart-accent"
          />
        </div>
        <Select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="min-w-[148px] text-[11.5px]"
        >
          <option value="all">All Types</option>
          <option value="github">GitHub Workflow</option>
          <option value="container">Containerized Application</option>
        </Select>
        {/* .scope-toggle */}
        <div className="inline-flex overflow-hidden rounded-[6px] border border-[#16202c]">
          {["all", ...TIER_NAMES].map((tier, i) => (
            <button
              key={tier}
              type="button"
              onClick={() => setTierScope(tier)}
              className={`h-9 px-3 text-[11.5px] transition-colors ${i > 0 ? "border-l border-[#16202c]" : ""} ${
                tierScope === tier
                  ? "bg-[rgba(45,212,191,0.14)] font-semibold text-chart-accent"
                  : "text-[#8b97a5] hover:text-[#eef2f6]"
              }`}
            >
              {tier === "all" ? "All Tiers" : tier}
            </button>
          ))}
        </div>
      </div>

      {/* .catalog-panel */}
      <div
        className="overflow-hidden rounded-[10px] border border-[#1c2836]"
        style={{
          background:
            "linear-gradient(150deg, rgba(14,32,52,.94), rgba(5,17,31,.99) 72%)",
        }}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1080px] border-collapse">
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th
                    key={col.label || "actions"}
                    style={{ width: col.width }}
                    onClick={col.key ? () => handleSort(col.key!) : undefined}
                    className={`border-b border-[#1c2836] bg-[rgba(6,17,29,0.6)] px-3.5 py-3 text-left text-[10.5px] font-semibold uppercase tracking-wide ${
                      col.key ? "cursor-pointer select-none hover:text-[#cfd8e3]" : ""
                    } ${sortKey === col.key ? "text-chart-accent" : "text-[#8b97a5]"}`}
                  >
                    {col.label}
                    {sortKey === col.key && (
                      <span className="ml-1 text-[8px]">
                        {sortDir === "asc" ? "▲" : "▼"}
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-3.5 py-12 text-center text-[12.5px] text-[#57606c]">
                    Loading registered applications…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-3.5 py-12 text-center text-[12.5px] text-[#57606c]">
                    No registered applications match your filters.
                  </td>
                </tr>
              ) : (
                rows.map((app) => {
                  const accent = TYPE_ACCENT[app.type];
                  return (
                    <tr
                      key={app.id}
                      onClick={() => setSummaryApp(app)}
                      className="cursor-pointer border-b border-[#1f3348] transition-colors last:border-b-0 hover:bg-[rgba(24,48,73,0.35)]"
                    >
                      <td className="px-3.5 py-3.5">
                        <div className="flex items-center gap-2.5">
                          <span
                            className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-[6px] border border-[#1c2836] text-chart-accent"
                            style={{ background: "linear-gradient(145deg, #123146, #0a1c2c)" }}
                          >
                            <TypeIcon type={app.type} className="h-4 w-4" />
                          </span>
                          <div className="min-w-0">
                            <div className="text-[12.5px] font-semibold text-white">
                              {app.name}
                            </div>
                            <div className="max-w-[260px] truncate text-[10.3px] text-[#57606c]">
                              {app.desc}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-3.5 py-3.5">
                        <span
                          className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10.5px] font-medium"
                          style={{
                            color: accent.text,
                            borderColor: accent.border,
                            background: accent.bg,
                          }}
                        >
                          <TypeIcon type={app.type} className="h-3 w-3" />
                          {TYPE_LABEL[app.type]}
                        </span>
                      </td>
                      <td className="px-3.5 py-3.5">
                        <strong className="text-[13px] text-white">{app.instances}</strong>{" "}
                        <span className="text-[10px] text-[#57606c]">
                          instance{app.instances === 1 ? "" : "s"}
                        </span>
                      </td>
                      <td className="px-3.5 py-3.5">
                        {Object.keys(app.tiers).length === 0 ? (
                          <span className="text-[10.5px] text-[#57606c]">
                            Not deployed
                          </span>
                        ) : (
                          <div className="flex max-w-[220px] flex-wrap gap-[5px]">
                            {Object.entries(app.tiers).map(([tier, count]) => (
                              <span
                                key={tier}
                                className="whitespace-nowrap rounded-[4px] border border-[#2b4057] bg-white/[0.04] px-[7px] py-0.5 text-[9.6px] text-[#8b97a5]"
                              >
                                <b className="font-semibold text-[#e6ecf2]">{count}</b> {tier}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3.5 py-3.5 text-[11px] text-[#8b97a5]">
                        {app.created || "—"}
                      </td>
                      <td className="px-3.5 py-3.5">
                        {isAdmin && (
                          <div className="flex items-center justify-end gap-1">
                            {/* aria-disabled (not `disabled`) so the click still
                                lands and can explain why it is blocked. */}
                            <button
                              type="button"
                              aria-disabled={!app.canEdit}
                              aria-label={`Edit ${app.name}`}
                              title={
                                app.canEdit
                                  ? "Edit application"
                                  : "Terminate its deployed instances to edit it"
                              }
                              onClick={(e) => handleEditClick(e, app)}
                              className={`grid h-7 w-7 place-items-center rounded-[6px] transition-colors ${
                                app.canEdit
                                  ? "text-[#20abc7]/80 hover:bg-[rgba(32,171,199,0.12)] hover:text-[#20abc7]"
                                  : "cursor-not-allowed text-[#57606c]/50"
                              }`}
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            {/* A copy changes nothing of the original, so it is
                                always offered, deployed or not. */}
                            <button
                              type="button"
                              aria-label={`Duplicate ${app.name}`}
                              title="Duplicate application"
                              onClick={(e) => {
                                e.stopPropagation();
                                setCopyTarget(app);
                              }}
                              className="grid h-7 w-7 place-items-center rounded-[6px] text-[#20abc7]/80 transition-colors hover:bg-[rgba(32,171,199,0.12)] hover:text-[#20abc7]"
                            >
                              <Copy className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              aria-disabled={app.instances > 0}
                              aria-label={`Delete ${app.name}`}
                              title={
                                app.instances > 0
                                  ? "Delete its deployed instances first"
                                  : "Delete application"
                              }
                              onClick={(e) => handleDeleteClick(e, app)}
                              className={`grid h-7 w-7 place-items-center rounded-[6px] transition-colors ${
                                app.instances > 0
                                  ? "cursor-not-allowed text-[#57606c]/50"
                                  : "text-[#f0655f]/70 hover:bg-[rgba(240,101,95,0.12)] hover:text-[#f0655f]"
                              }`}
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {/* .panel-footer */}
        <div className="flex items-center justify-end border-t border-[#1c2836] bg-[rgba(6,17,29,0.4)] px-3.5 py-3 text-[10.8px] text-[#57606c]">
          <span>Filtering is global by default and can be refined by Tier.</span>
        </div>
      </div>

      {isAdmin && (
        <RegisterApplicationModal
          open={registerOpen || editTarget !== null || copyTarget !== null}
          editing={editTarget}
          duplicating={copyTarget}
          onOpenChange={(next) => {
            if (next) return;
            setRegisterOpen(false);
            setEditTarget(null);
            setCopyTarget(null);
          }}
          onRegistered={loadApps}
        />
      )}

      <ApplicationSummaryModal app={summaryApp} onOpenChange={(o) => !o && setSummaryApp(null)} />

      {/* Delete confirmation — same panel language as the wizards. */}
      <Dialog open={deleteTarget !== null} onOpenChange={(o) => !o && !deleting && setDeleteTarget(null)}>
        <DialogContent
          className="top-[20vh] w-full max-w-md -translate-y-0 rounded-[10px] border-[#1c2836] p-4"
          closeClassName="right-4 top-4 text-[#8b97a5] hover:text-[#eef2f6]"
          style={{ background: PANEL_BACKGROUND }}
        >
          <div className="mb-1 flex items-start gap-3">
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px]"
              style={{ background: "rgba(240,101,95,0.12)", color: "#f0655f" }}
            >
              <Trash2 className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-wide text-[#57606c]">
                Registered Applications
              </p>
              <h3 className="truncate text-lg font-bold text-[#eef2f6]">
                Delete {deleteTarget?.name}?
              </h3>
            </div>
          </div>
          <p className="mb-5 text-[13px] leading-normal text-[#8b97a5]">
            Are you sure you want to delete '{deleteTarget?.name}' registered application?
          </p>
          <div className="flex items-center justify-end gap-2 border-t border-[#1c2836] pt-4">
            <button
              type="button"
              onClick={() => setDeleteTarget(null)}
              disabled={deleting}
              className="rounded-[6px] border px-3.5 py-2 text-[12.5px] font-semibold transition-colors disabled:opacity-50"
              style={{ borderColor: "rgba(32,171,199,0.4)", color: ACCENT }}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirmDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[12.5px] font-semibold text-white transition-colors hover:bg-[#e04f49] disabled:opacity-60"
              style={{ background: "#f0655f" }}
            >
              {deleting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              <Trash2 className="h-3.5 w-3.5" />
              Delete Application
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

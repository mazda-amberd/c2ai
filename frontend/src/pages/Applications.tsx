import { useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Button } from "@ui/button";
import {
  ArrowLeft,
  ArrowRight,
  CircleAlert,
  CircleCheckBig,
  CircleX,
  Rocket,
  Zap,
} from "lucide-react";
import { getMetricNumberColor, getMetricBarColor } from "@/utils/metrics";
import ApplicationList from "@components/ApplicationList";
import Cube from "@components/Cube";
import { useTierData } from "@hooks/useMetrics";
import { useAppsFilter } from "@hooks/useAppsFilter";
import AppsFilter from "@/components/AppsFilter";
import DeployApplicationModal from "@components/registerApp/DeployApplicationModal";
import CostChip from "@components/CostChip";
import RegisteredAppsButton from "@components/RegisteredAppsButton";
import { useInvalidateDeploymentsForTierIndex } from "@hooks/useDeployments";
import { useAuth } from "@auth/AuthContext";
import { fetchTierCost, useFinanceFilter, type CostValue } from "@/utils/financeApi";

export type { AppsFilterState } from "@/types/appsFilter";

// conditional sizing for each screen size for appropriate layout
const responsiveListWidth =
  "w-[40rem] sm:w-[48rem] md:w-[56rem] lg:w-[64rem] xl:w-[72rem] 2xl:w-[88rem]";

export default function Applications() {
  const { tierIndex } = useParams<{ tierIndex: string }>();
  const navigate = useNavigate();

  // URL uses 1-based indexing (apps/1, apps/2, etc.)
  // Convert to 0-based internal index
  const urlIndex = Number(tierIndex);
  const index = urlIndex - 1;
  const { apps, isLoading: loading, tierGpuTotal } = useTierData(index);

  const { filters, setFilters, filterOptions, filteredApps, statusCounts } =
    useAppsFilter(apps);

  const [deployModalOpen, setDeployModalOpen] = useState(false);
  // Deploying is for Admins; a User sees the tier, its metrics and logs.
  const { isAdmin } = useAuth();
  const invalidateDeployments = useInvalidateDeploymentsForTierIndex(index);

  // Tier-level cost for the shared finance date filter (Epic 10).
  const [financeFilter] = useFinanceFilter();
  const [tierCost, setTierCost] = useState<CostValue | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (isNaN(urlIndex) || urlIndex < 1 || urlIndex > 4) return;
    fetchTierCost(urlIndex, financeFilter).then((cost) => {
      if (!cancelled) setTierCost(cost);
    });
    return () => {
      cancelled = true;
    };
  }, [urlIndex, financeFilter]);

  const handleTierMetricsClick = () => {
    navigate(`/metrics/${urlIndex}/tier`);
  };

  // Validate URL index (1-4 are valid, which maps to internal 0-3)
  if (isNaN(urlIndex) || urlIndex < 1 || urlIndex > 4) {
    return <Navigate to="/" replace />;
  }

  return (
    <>
    {isAdmin && (
      <DeployApplicationModal
        open={deployModalOpen}
        onOpenChange={setDeployModalOpen}
        tierIndex={index}
        onDeployed={invalidateDeployments}
      />
    )}
    <div className="p-6 h-[calc(100vh-60px)]">
      <div className="w-full h-full relative border rounded-md flex flex-col overflow-hidden pt-6">
        {/* Single-row toolbar. overflow-x keeps the buttons on one line on
            narrow windows; overflow-y is clipped (with a little vertical
            padding for focus rings) so a stray pixel of overflow can't
            produce a useless toolbar-height scrollbar on Windows, and the
            horizontal track is hidden — the row still scrolls by wheel/touch. */}
        <div className="no-scrollbar flex items-center justify-between gap-4 overflow-x-auto overflow-y-hidden px-6 py-1">
          <div className="flex shrink-0 items-center gap-4 lg:gap-6">
            <Button onClick={() => navigate("/")}>
              <ArrowLeft />
            </Button>

            <h1 className="font-semibold text-[2rem] leading-none whitespace-nowrap">
              Tier {index + 1}
            </h1>

            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <CircleCheckBig className="h-6 w-6 text-green-500" />
                <span className="text-gray-400">{statusCounts.healthy}</span>
              </div>

              <div className="flex items-center gap-2">
                <CircleAlert className="h-6 w-6 text-orange-500" />
                <span className="text-gray-400">{statusCounts.warning}</span>
              </div>

              <div className="flex items-center gap-2">
                <CircleX className="h-6 w-6 text-red-500" />
                <span className="text-gray-400">{statusCounts.critical}</span>
              </div>
            </div>

            <Button
              variant="link"
              className="flex items-center gap-1 whitespace-nowrap"
              onClick={handleTierMetricsClick}
            >
              Tier Metrics
              <ArrowRight />
            </Button>

          </div>

          <div className="flex shrink-0 items-center gap-3">
            {tierGpuTotal !== null && (
              <div className="flex items-center gap-2">
                <Zap className={`h-4 w-4 shrink-0 ${getMetricNumberColor(tierGpuTotal)}`} />
                <div className="flex flex-col gap-1">
                  <div className="flex items-center justify-between w-28 sm:w-32">
                    <span className="text-sm text-gray-400">GPU</span>
                    <span className={`text-sm font-semibold ${getMetricNumberColor(tierGpuTotal)}`}>
                      {tierGpuTotal.toFixed(1)}%
                    </span>
                  </div>
                  <div className="w-28 sm:w-32 h-1.5 rounded-full bg-muted overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${getMetricBarColor(tierGpuTotal)}`}
                      style={{ width: `${Math.min(tierGpuTotal, 100)}%` }}
                    />
                  </div>
                </div>
              </div>
            )}
            <CostChip cost={tierCost} />
            <RegisteredAppsButton />
            {isAdmin && (
              <Button
                variant="healthy"
                className="h-9 px-3.5 py-2 text-sm"
                onClick={() => setDeployModalOpen(true)}
              >
                <Rocket className="h-4 w-4" />
                Deploy
              </Button>
            )}
            <AppsFilter
              filters={filters}
              options={{
                applications: filterOptions.applications,
                clientNames: filterOptions.clientNames,
                instanceNames: filterOptions.instanceNames,
              }}
              onChange={setFilters}
            />
          </div>
        </div>
        <div className="flex flex-1 overflow-hidden ">
          <div
            className={`flex-none ${responsiveListWidth} max-w-full overflow-y-auto p-6`}
          >
            <ApplicationList
              tierIndex={index}
              apps={filteredApps}
              loading={loading}
            />
          </div>

          {/* conditional sizing for each screen size for appropriate layout */}
          <div className="relative w-[16rem] sm:w-[20rem] md:w-[24rem] lg:w-[28rem] xl:w-[30rem] 2xl:w-[32rem] shrink-0">
            <div
              className="absolute left-1/2 top-1/2 -translate-y-1/2 w-[32rem] sm:w-[40rem] md:w-[48rem] lg:w-[52rem] xl:w-[56rem] h-[32rem] sm:h-[40rem] md:h-[48rem] lg:h-[52rem] xl:h-[56rem] flex flex-col items-center justify-center
              -translate-x-[30%] scale-[0.6]
              sm:-translate-x-[32%] sm:scale-[0.65]
              md:-translate-x-[35%] md:scale-[0.7]
              lg:-translate-x-[38%] lg:scale-[0.75]
              xl:-translate-x-[46%] xl:scale-[0.8]
              2xl:-translate-x-[32%] 2xl:scale-[0.92]"
            >
              <Cube activeTier={index} viewSize={3.6} />
            </div>
          </div>
        </div>
      </div>
    </div>
    </>
  );
}

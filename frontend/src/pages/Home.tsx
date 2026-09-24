import { useEffect, useState } from "react";

import Cube from "@components/Cube";
import CostChip from "@components/CostChip";
import RegisteredAppsButton from "@components/RegisteredAppsButton";
import { fetchClusterCost, useFinanceFilter, type CostValue } from "@/utils/financeApi";

export default function Home() {
  const [filter] = useFinanceFilter();
  const [clusterCost, setClusterCost] = useState<CostValue | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchClusterCost(filter).then((cost) => {
      if (!cancelled) setClusterCost(cost);
    });
    return () => {
      cancelled = true;
    };
  }, [filter]);

  return (
    <div className="h-[calc(100vh-60px)]">
      <div className="w-full h-full relative flex flex-col">
        <div className="p-4 shrink-0 flex items-start justify-between gap-4">
          <div>
            <div className="font-semibold text-2xl pb-2">Tier Management</div>
            <div className="text-xl">
              Assign application to the right tier.
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <CostChip cost={clusterCost} />
            <RegisteredAppsButton />
          </div>
        </div>
        {/* Positioned wrapper so Cube's absolute canvas + tier info column
            anchor below the header text instead of overlapping it. */}
        <div className="relative flex-1 min-h-0">
          {/* Smaller viewSize = larger cube; compensates for the shorter
              canvas area now that the header sits above it. */}
          <Cube viewSize={3.4} />
        </div>
      </div>
    </div>
  );
}

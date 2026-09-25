import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu";
import type { AppsFilterState } from "@/types/appsFilter";
import { ChevronDown, Funnel, X } from "lucide-react";

type Status = "Healthy" | "Warning" | "Critical";

type AppsFilterProps = {
  filters: AppsFilterState;
  options: {
    applications: string[];
    clientNames: string[];
    instanceNames: string[];
  };
  onChange: (value: AppsFilterState) => void;
};


const CAPACITIES: Array<{
  status: Status;
  label: string;
  container: string;
  text: string;
  dot: string;
}> = [
  {
    status: "Healthy",
    label: "Healthy",
    container: "border-healthy-text bg-healthy",
    text: "border-healthy-text text-healthy-text",
    dot: "bg-healthy-text",
  },
  {
    status: "Warning",
    label: "Warning",
    container: "border-warning-text bg-warning",
    text: "border-warning-text text-warning-text",
    dot: "bg-warning-text",
  },
  {
    status: "Critical",
    label: "Critical",
    container: "border-critical-text bg-critical",
    text: "border-critical-text text-critical-text",
    dot: "bg-critical-text",
  },
];

const selectablePillClasses = (selected: boolean) =>
  `px-4 py-2 rounded-md border text-sm transition-colors
   max-w-full whitespace-normal break-words text-left min-w-0
   ${
     selected
       ? "border-blue-500 bg-blue-500/20 text-blue-400"
       : "border-slate-600 bg-slate-800 text-gray-300 hover:border-slate-500"
   }`;

export default function AppsFilter({
  filters,
  options,
  onChange,
}: AppsFilterProps) {
  const toggleValue = <T,>(list: T[], value: T) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  const clearAll = () => {
    onChange({
      application: null,
      clientName: null,
      instanceName: null,
      statuses: [],
    });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" className="h-9 px-3.5 py-2 text-sm">
          <Funnel />
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent className="w-[21rem] p-0" align="end" forceMount>
        {/* Header */}
        <div className="flex items-center justify-between p-4 pb-2 bg-slate-900">
          <h2 className="text-lg font-semibold text-white">Filters</h2>
          <Button
            size="sm"
            variant="destructive"
            onClick={clearAll}
            className="rounded-md"
          >
            <X className="w-4 h-4" />
            Clear All
          </Button>
        </div>

        <div className="px-4 pb-4 space-y-6 bg-slate-900 max-h-[60vh] overflow-y-auto">
          {/* Applications */}
          <div>
            <h3 className="text-sm mb-2">Filter by Applications</h3>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="dropdown"
                  className="w-full text-left border-slate-600 bg-slate-800 text-gray-300 hover:border-slate-500"
                >
                  {filters.application ?? "Select Application"}
                  <ChevronDown className="h-6 w-6" />
                </Button>
              </DropdownMenuTrigger>

              <DropdownMenuContent className="w-[18rem] max-w-[18rem] max-h-[24rem] overflow-y-auto">
                <DropdownMenuRadioGroup value={filters.application ?? ""} onValueChange={() => {}}>
                  {options.applications.map((app) => (
                    <DropdownMenuRadioItem
                      key={app}
                      value={app}
                      onSelect={(e) => e.preventDefault()}
                      onClick={() =>
                        onChange({
                          ...filters,
                          application: filters.application === app ? null : app,
                        })
                      }
                    >
                      {app}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            {filters.application && (
              <div className="mt-2 flex flex-wrap gap-2">
                <span className={selectablePillClasses(true)}>
                  {filters.application}
                </span>
              </div>
            )}
          </div>

          {/* Client Name — single-select */}
          <div>
            <h3 className="text-sm mb-2">Filter by Customer</h3>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="dropdown"
                  className="w-full text-left border-slate-600 bg-slate-800 text-gray-300 hover:border-slate-500"
                >
                  {filters.clientName ?? "Select Customer"}
                  <ChevronDown className="h-6 w-6" />
                </Button>
              </DropdownMenuTrigger>

              <DropdownMenuContent className="w-[18rem] max-w-[18rem] max-h-[24rem] overflow-y-auto">
                <DropdownMenuRadioGroup
                  value={filters.clientName ?? ""}
                  onValueChange={(val) =>
                    onChange({
                      ...filters,
                      clientName: val === filters.clientName ? null : val,
                    })
                  }
                >
                  {options.clientNames.map((name) => (
                    <DropdownMenuRadioItem
                      key={name}
                      value={name}
                      onSelect={(e) => e.preventDefault()}
                      onClick={() =>
                        onChange({
                          ...filters,
                          clientName: filters.clientName === name ? null : name,
                        })
                      }
                    >
                      {name}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            {filters.clientName && (
              <div className="mt-2 flex flex-wrap gap-2">
                <span className={selectablePillClasses(true)}>
                  {filters.clientName}
                </span>
              </div>
            )}
          </div>

          {/* Instance Name — single-select */}
          <div>
            <h3 className="text-sm mb-2">Filter by Instance</h3>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="dropdown"
                  className="w-full text-left border-slate-600 bg-slate-800 text-gray-300 hover:border-slate-500"
                >
                  {filters.instanceName ?? "Select Instance"}
                  <ChevronDown className="h-6 w-6" />
                </Button>
              </DropdownMenuTrigger>

              <DropdownMenuContent className="w-[18rem] max-w-[18rem] max-h-[24rem] overflow-y-auto">
                <DropdownMenuRadioGroup
                  value={filters.instanceName ?? ""}
                  onValueChange={() => {}}
                >
                  {options.instanceNames.map((name) => (
                    <DropdownMenuRadioItem
                      key={name}
                      value={name}
                      onSelect={(e) => e.preventDefault()}
                      onClick={() =>
                        onChange({
                          ...filters,
                          instanceName:
                            filters.instanceName === name ? null : name,
                        })
                      }
                    >
                      {name}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            {filters.instanceName && (
              <div className="mt-2 flex flex-wrap gap-2">
                <span className={selectablePillClasses(true)}>
                  {filters.instanceName}
                </span>
              </div>
            )}
          </div>

          {/* Application Health */}
          <div>
            <h3 className="text-sm mb-2">Filter by Application Health</h3>
            <div className="space-y-2">
              {CAPACITIES.map((cap) => {
                const selected = filters.statuses.includes(cap.status);

                return (
                  <button
                    key={cap.status}
                    onClick={() =>
                      onChange({
                        ...filters,
                        statuses: toggleValue(filters.statuses, cap.status),
                      })
                    }
                    className={`w-full flex items-center justify-between px-3 py-2 rounded-md border text-sm transition-all ${
                      selected
                        ? cap.container
                        : "border-popover-border bg-slate-800 hover:border-slate-500"
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={`w-2.5 h-2.5 rounded-full ${cap.dot}`} />
                      <span className="break-words whitespace-normal">
                        {cap.label}
                      </span>
                    </div>

                    <span
                      className={`px-2 py-1 rounded text-xs font-medium border ${cap.text}`}
                    >
                      {cap.status}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

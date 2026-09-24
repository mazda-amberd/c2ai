import cpuTier1 from "@/assets/cpu_tier1.png";
import cpuTier2 from "@/assets/cpu_tier2.png";
import cpuTier3 from "@/assets/cpu_tier3.png";
import cpuTier4 from "@/assets/cpu_tier4.png";
import cpuWarning from "@/assets/cpu_warning.png";
import cpuCritical from "@/assets/cpu_critical.png";

import memoryTier1 from "@/assets/memory_tier1.png";
import memoryTier2 from "@/assets/memory_tier2.png";
import memoryTier3 from "@/assets/memory_tier3.png";
import memoryTier4 from "@/assets/memory_tier4.png";
import memoryWarning from "@/assets/memory_warning.png";
import memoryCritical from "@/assets/memory_critical.png";

import gpuTier1 from "@/assets/gpu_tier1.png";
import gpuTier2 from "@/assets/gpu_tier2.png";
import gpuTier3 from "@/assets/gpu_tier3.png";
import gpuTier4 from "@/assets/gpu_tier4.png";
import gpuWarning from "@/assets/gpu_warning.png";
import gpuCritical from "@/assets/gpu_critical.png";

type MetricKey = "cpu" | "memory" | "gpu";

const tierAssets: Record<MetricKey, string[]> = {
  cpu:    [cpuTier1, cpuTier2, cpuTier3, cpuTier4],
  memory: [memoryTier1, memoryTier2, memoryTier3, memoryTier4],
  gpu:    [gpuTier1, gpuTier2, gpuTier3, gpuTier4],
};

const warningAssets: Record<MetricKey, string> = {
  cpu:    cpuWarning,
  memory: memoryWarning,
  gpu:    gpuWarning,
};

const criticalAssets: Record<MetricKey, string> = {
  cpu:    cpuCritical,
  memory: memoryCritical,
  gpu:    gpuCritical,
};

export function getMetricIcon(metric: MetricKey, value: number, tierIndex: number): string {
  if (value >= 76) return criticalAssets[metric];
  if (value > 50)  return warningAssets[metric];
  return tierAssets[metric][tierIndex] ?? tierAssets[metric][0];
}

import tier1 from "@/assets/tier1.png";
import tier2 from "@/assets/tier2.png";
import tier3 from "@/assets/tier3.png";
import tier4 from "@/assets/tier4.png";

const tierCubeAssets = [tier1, tier2, tier3, tier4];

export function getTierCubeAsset(tierIndex: number): string {
  return tierCubeAssets[tierIndex] ?? tierCubeAssets[0];
}

export type { MetricKey };

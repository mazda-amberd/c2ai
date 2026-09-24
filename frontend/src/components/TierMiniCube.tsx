import { getTierCubeAsset } from "@/utils/metricAssets";

type Props = { tierIndex: number };

export default function TierMiniCube({ tierIndex }: Props) {
  return (
    <img
      src={getTierCubeAsset(tierIndex)}
      alt={`Tier ${tierIndex + 1}`}
      className="w-32 h-32 object-contain"
    />
  );
}

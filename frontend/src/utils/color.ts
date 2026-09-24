export function hexNumberToHexString(hex: number): string {
  return `#${hex.toString(16).padStart(6, "0")}`;
}

export function colorToRgba(
  color: { r: number; g: number; b: number },
  opacity: number,
): string {
  return `rgba(${Math.round(color.r * 255)}, ${Math.round(color.g * 255)}, ${Math.round(color.b * 255)}, ${opacity})`;
}

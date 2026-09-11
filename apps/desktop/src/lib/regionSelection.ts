import type { PhysicalBounds } from "./coreClient";
import type { RegionSelectionGeometry } from "./desktop";

export interface LogicalPoint {
  x: number;
  y: number;
}

export function logicalSelectionToPhysical(
  start: LogicalPoint,
  end: LogicalPoint,
  monitor: RegionSelectionGeometry,
): PhysicalBounds {
  if (
    !Number.isFinite(monitor.scale_factor) ||
    monitor.scale_factor < 0.5 ||
    monitor.scale_factor > 4 ||
    monitor.width <= 0 ||
    monitor.height <= 0
  )
    throw new Error("Nieprawidłowa geometria monitora");
  const logicalWidth = monitor.width / monitor.scale_factor;
  const logicalHeight = monitor.height / monitor.scale_factor;
  const clamp = (value: number, maximum: number) =>
    Math.min(maximum, Math.max(0, value));
  const left = clamp(Math.min(start.x, end.x), logicalWidth);
  const right = clamp(Math.max(start.x, end.x), logicalWidth);
  const top = clamp(Math.min(start.y, end.y), logicalHeight);
  const bottom = clamp(Math.max(start.y, end.y), logicalHeight);
  if (right - left < 4 || bottom - top < 4)
    throw new Error("Zaznacz większy fragment");
  return {
    left: monitor.left + Math.round(left * monitor.scale_factor),
    top: monitor.top + Math.round(top * monitor.scale_factor),
    right: monitor.left + Math.round(right * monitor.scale_factor),
    bottom: monitor.top + Math.round(bottom * monitor.scale_factor),
  };
}

import { useEffect, useState, type PointerEvent } from "react";
import { emit } from "@tauri-apps/api/event";
import { captureScreenRegion, type RegionCapture } from "../lib/coreClient";
import {
  finishRegionSelection,
  getCoreSessionCredential,
  getRegionSelectionGeometry,
  hideRegionSelector,
  type RegionSelectionGeometry,
} from "../lib/desktop";
import {
  logicalSelectionToPhysical,
  type LogicalPoint,
} from "../lib/regionSelection";

export function RegionSelector() {
  const [start, setStart] = useState<LogicalPoint>();
  const [end, setEnd] = useState<LogicalPoint>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key === "Escape") void finishRegionSelection();
    };
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, []);

  const point = (event: PointerEvent): LogicalPoint => ({
    x: event.clientX,
    y: event.clientY,
  });
  const finish = async (event: PointerEvent) => {
    if (!start || busy) return;
    const final = point(event);
    setEnd(final);
    setBusy(true);
    try {
      const geometry: RegionSelectionGeometry =
        await getRegionSelectionGeometry();
      const region = logicalSelectionToPhysical(start, final, geometry);
      await hideRegionSelector();
      await new Promise((resolve) => window.setTimeout(resolve, 120));
      const credential = await getCoreSessionCredential();
      const capture = await captureScreenRegion(
        region,
        {
          left: geometry.left,
          top: geometry.top,
          right: geometry.left + geometry.width,
          bottom: geometry.top + geometry.height,
        },
        geometry.scale_factor,
        credential,
      );
      await emit<RegionCapture>("region-selection-completed", capture);
    } catch (error) {
      await emit("region-selection-failed", {
        message:
          error instanceof Error
            ? error.message
            : "Nie udało się przechwycić fragmentu",
      });
    } finally {
      await finishRegionSelection();
      setBusy(false);
    }
  };

  const rectangle =
    start && end
      ? {
          left: Math.min(start.x, end.x),
          top: Math.min(start.y, end.y),
          width: Math.abs(end.x - start.x),
          height: Math.abs(end.y - start.y),
        }
      : undefined;
  return (
    <main
      className="region-selector"
      onPointerDown={(event) => {
        if (busy) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const next = point(event);
        setStart(next);
        setEnd(next);
      }}
      onPointerMove={(event) => start && !busy && setEnd(point(event))}
      onPointerUp={(event) => void finish(event)}
    >
      <div className="region-selector__hint">
        {busy
          ? "Przechwytuję fragment…"
          : "Przeciągnij, aby wybrać fragment · Esc anuluje"}
      </div>
      {rectangle && (
        <div className="region-selector__selection" style={rectangle} />
      )}
    </main>
  );
}

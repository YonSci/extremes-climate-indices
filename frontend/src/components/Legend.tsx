import type { OverlayResponse } from "../types";

// CSS approximations of the matplotlib colormaps the backend actually uses
// (mapping/three_panel.py, mapping/overlay.py) — close enough for a legend
// gradient; the authoritative color values are computed server-side per pixel.
const GRADIENTS: Record<string, string> = {
  YlGnBu: "linear-gradient(to right, #ffffcc, #a1dab4, #41b6c4, #225ea8, #081d58)",
  YlOrBr: "linear-gradient(to right, #ffffe5, #fed98e, #fe9929, #cc4c02, #662506)",
  BrBG: "linear-gradient(to right, #543005, #bf812d, #f5f5f5, #35978f, #003c30)",
  RdYlGn: "linear-gradient(to right, #a50026, #f46d43, #ffffbf, #66bd63, #006837)",
};

export function Legend({ overlay, units }: { overlay: OverlayResponse; units: string }) {
  const gradient = GRADIENTS[overlay.cmap] ?? GRADIENTS.YlGnBu;
  return (
    <div className="legend">
      <div className="legend-bar" style={{ background: gradient }} />
      <div className="legend-labels">
        <span>{overlay.vmin.toFixed(1)}</span>
        <span className="legend-units">{units}</span>
        <span>{overlay.vmax.toFixed(1)}</span>
      </div>
    </div>
  );
}

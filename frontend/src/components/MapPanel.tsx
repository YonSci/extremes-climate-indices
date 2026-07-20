import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { basemapStyle } from "../basemapStyle";
import type { MapSyncGroup } from "../syncMaps";
import type { BoundaryGeoJSON, OverlayResponse } from "../types";

const OVERLAY_SOURCE_ID = "product-overlay";
const OVERLAY_LAYER_ID = "product-overlay-layer";
const BOUNDARY_SOURCE_ID = "admin-boundary";
const BOUNDARY_LAYER_ID = "admin-boundary-layer";

const ETHIOPIA_BOUNDS: [[number, number], [number, number]] = [
  [33.0, 3.0],
  [48.0, 15.0],
];

interface MapPanelProps {
  title: string;
  overlay: OverlayResponse | null;
  boundary?: BoundaryGeoJSON | null;
  loading: boolean;
  syncGroup: MapSyncGroup;
  onMapClick: (lat: number, lon: number) => void;
}

export function MapPanel({ title, overlay, boundary, loading, syncGroup, onMapClick }: MapPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: basemapStyle,
      bounds: ETHIOPIA_BOUNDS,
      fitBoundsOptions: { padding: 10 },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("click", (e) => onMapClick(e.lngLat.lat, e.lngLat.lng));

    const unregister = syncGroup.register(map);
    mapRef.current = map;

    return () => {
      unregister();
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Add/replace the overlay image whenever the product changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !overlay) return;

    const applyOverlay = () => {
      const [minlon, minlat, maxlon, maxlat] = overlay.bounds;
      const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
        [minlon, maxlat],
        [maxlon, maxlat],
        [maxlon, minlat],
        [minlon, minlat],
      ];
      const dataUrl = `data:image/png;base64,${overlay.png_base64}`;

      if (map.getLayer(OVERLAY_LAYER_ID)) map.removeLayer(OVERLAY_LAYER_ID);
      if (map.getSource(OVERLAY_SOURCE_ID)) map.removeSource(OVERLAY_SOURCE_ID);

      map.addSource(OVERLAY_SOURCE_ID, { type: "image", url: dataUrl, coordinates });
      map.addLayer({ id: OVERLAY_LAYER_ID, type: "raster", source: OVERLAY_SOURCE_ID, paint: { "raster-opacity": 0.85 } });
    };

    if (map.isStyleLoaded()) applyOverlay();
    else map.once("load", applyOverlay);
  }, [overlay]);

  // Add/replace the admin-boundary reference layer whenever the selected level changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const applyBoundary = () => {
      if (map.getLayer(BOUNDARY_LAYER_ID)) map.removeLayer(BOUNDARY_LAYER_ID);
      if (map.getSource(BOUNDARY_SOURCE_ID)) map.removeSource(BOUNDARY_SOURCE_ID);
      if (!boundary) return;

      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- avoids depending on the global
      // GeoJSON.* ambient namespace, which this project's tsconfig doesn't pull in (types: ["vite/client"]).
      map.addSource(BOUNDARY_SOURCE_ID, { type: "geojson", data: boundary as any });
      map.addLayer({
        id: BOUNDARY_LAYER_ID,
        type: "line",
        source: BOUNDARY_SOURCE_ID,
        paint: { "line-color": "#333333", "line-width": 1, "line-opacity": 0.8 },
      });
    };

    if (map.isStyleLoaded()) applyBoundary();
    else map.once("load", applyBoundary);
  }, [boundary]);

  return (
    <div className="map-panel">
      <div className="map-panel-title">{title}</div>
      <div ref={containerRef} className="map-panel-canvas" />
      {loading && <div className="map-panel-loading">Loading…</div>}
    </div>
  );
}

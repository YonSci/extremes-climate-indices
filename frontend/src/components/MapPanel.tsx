import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import type { MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { basemapStyle } from "../basemapStyle";
import type { CursorHandler, MapSyncGroup } from "../syncMaps";
import type { BoundaryGeoJSON, OverlayResponse } from "../types";

const OVERLAY_SOURCE_ID = "product-overlay";
const OVERLAY_LAYER_ID = "product-overlay-layer";
const BOUNDARY_SOURCE_ID = "admin-boundary";
const BOUNDARY_LAYER_ID = "admin-boundary-layer";
const GRATICULE_SOURCE_ID = "graticule";
const GRATICULE_LAYER_ID = "graticule-layer";

const ETHIOPIA_BOUNDS: [[number, number], [number, number]] = [
  [33.0, 3.0],
  [48.0, 15.0],
];

export interface RegionBounds {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
}

interface GraticuleLabel {
  key: string;
  text: string;
  style: React.CSSProperties;
}

function niceInterval(span: number): number {
  const options = [0.5, 1, 2, 2.5, 5, 10, 20];
  return options.find((o) => span / o <= 6) ?? 20;
}

function ticksFor(min: number, max: number, interval: number): number[] {
  const start = Math.ceil(min / interval) * interval;
  const ticks: number[] = [];
  for (let t = start; t <= max; t += interval) ticks.push(Math.round(t * 100) / 100);
  return ticks;
}

function formatLat(lat: number): string {
  if (lat === 0) return "0°";
  return `${Math.abs(lat)}°${lat > 0 ? "N" : "S"}`;
}

function formatLon(lon: number): string {
  if (lon === 0) return "0°";
  return `${Math.abs(lon)}°${lon > 0 ? "E" : "W"}`;
}

function buildGraticuleGeoJSON(bounds: RegionBounds): BoundaryGeoJSON {
  const { latMin, latMax, lonMin, lonMax } = bounds;
  const latInterval = niceInterval(latMax - latMin);
  const lonInterval = niceInterval(lonMax - lonMin);
  const features: BoundaryGeoJSON["features"] = [];
  for (const lat of ticksFor(latMin, latMax, latInterval)) {
    features.push({
      type: "Feature", properties: { lat },
      geometry: { type: "LineString", coordinates: [[lonMin, lat], [lonMax, lat]] },
    });
  }
  for (const lon of ticksFor(lonMin, lonMax, lonInterval)) {
    features.push({
      type: "Feature", properties: { lon },
      geometry: { type: "LineString", coordinates: [[lon, latMin], [lon, latMax]] },
    });
  }
  return { type: "FeatureCollection", features };
}

interface MapPanelProps {
  title: string;
  subtitle: string;
  overlay: OverlayResponse | null;
  boundary?: BoundaryGeoJSON | null;
  regionBounds?: RegionBounds | null;
  showGraticule: boolean;
  heightPx: number;
  loading: boolean;
  syncGroup: MapSyncGroup;
  onMapClick: (lat: number, lon: number) => void;
}

export function MapPanel({
  title, subtitle, overlay, boundary, regionBounds, showGraticule, heightPx, loading, syncGroup, onMapClick,
}: MapPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const crosshairMarkerRef = useRef<maplibregl.Marker | null>(null);
  const [graticuleLabels, setGraticuleLabels] = useState<GraticuleLabel[]>([]);

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

    const crosshairEl = document.createElement("div");
    crosshairEl.className = "map-crosshair";
    const crosshairMarker = new maplibregl.Marker({ element: crosshairEl }).setLngLat([0, 0]);
    crosshairMarkerRef.current = crosshairMarker;

    const onMouseMove = (e: MapMouseEvent) => syncGroup.broadcastCursor(e.lngLat, map);
    const onMouseLeave = () => syncGroup.broadcastCursor(null, map);
    map.on("mousemove", onMouseMove);
    map.on("mouseleave", onMouseLeave);

    const onCursor: CursorHandler = (lngLat, source) => {
      if (source === map || !crosshairMarkerRef.current) return;
      if (lngLat === null) {
        crosshairMarkerRef.current.remove();
      } else {
        crosshairMarkerRef.current.setLngLat(lngLat).addTo(map);
      }
    };

    const unregisterSync = syncGroup.register(map);
    const unregisterCursor = syncGroup.onCursor(onCursor);
    mapRef.current = map;

    return () => {
      unregisterSync();
      unregisterCursor();
      map.off("mousemove", onMouseMove);
      map.off("mouseleave", onMouseLeave);
      crosshairMarker.remove();
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

  // Add/remove the graticule layer + keep its edge labels positioned on every move.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !regionBounds) return;

    const applyGraticule = () => {
      if (map.getLayer(GRATICULE_LAYER_ID)) map.removeLayer(GRATICULE_LAYER_ID);
      if (map.getSource(GRATICULE_SOURCE_ID)) map.removeSource(GRATICULE_SOURCE_ID);
      if (!showGraticule) {
        setGraticuleLabels([]);
        return;
      }

      const geojson = buildGraticuleGeoJSON(regionBounds);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- see the boundary-layer effect above.
      map.addSource(GRATICULE_SOURCE_ID, { type: "geojson", data: geojson as any });
      map.addLayer({
        id: GRATICULE_LAYER_ID,
        type: "line",
        source: GRATICULE_SOURCE_ID,
        paint: { "line-color": "#94a3b8", "line-width": 1, "line-opacity": 0.5, "line-dasharray": [2, 2] },
      });

      const updateLabels = () => {
        const container = containerRef.current;
        if (!container) return;
        const latInterval = niceInterval(regionBounds.latMax - regionBounds.latMin);
        const lonInterval = niceInterval(regionBounds.lonMax - regionBounds.lonMin);
        const labels: GraticuleLabel[] = [];
        for (const lat of ticksFor(regionBounds.latMin, regionBounds.latMax, latInterval)) {
          const pt = map.project([regionBounds.lonMin, lat]);
          labels.push({ key: `lat-${lat}`, text: formatLat(lat), style: { top: pt.y, left: 4 } });
        }
        for (const lon of ticksFor(regionBounds.lonMin, regionBounds.lonMax, lonInterval)) {
          const pt = map.project([lon, regionBounds.latMin]);
          labels.push({ key: `lon-${lon}`, text: formatLon(lon), style: { left: pt.x, bottom: 2 } });
        }
        setGraticuleLabels(labels);
      };
      updateLabels();
      map.on("move", updateLabels);
      return () => map.off("move", updateLabels);
    };

    let cleanup: (() => void) | undefined;
    if (map.isStyleLoaded()) cleanup = applyGraticule();
    else map.once("load", () => { cleanup = applyGraticule(); });

    return () => cleanup?.();
  }, [regionBounds, showGraticule]);

  // Resize the map whenever the configured height changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const id = requestAnimationFrame(() => map.resize());
    return () => cancelAnimationFrame(id);
  }, [heightPx]);

  return (
    <div className="map-card">
      <div className="map-card-header">
        <div className="map-card-title">{title}</div>
        <div className="map-card-subtitle">{subtitle}</div>
      </div>
      <div className="map-card-canvas-wrap" style={{ height: heightPx }}>
        <div ref={containerRef} className="map-card-canvas" />
        {showGraticule &&
          graticuleLabels.map((label) => (
            <span key={label.key} className="map-graticule-label" style={label.style}>
              {label.text}
            </span>
          ))}
        {loading && <div className="map-panel-loading">Loading…</div>}
      </div>
    </div>
  );
}

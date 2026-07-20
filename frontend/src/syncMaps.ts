import type maplibregl from "maplibre-gl";

/**
 * Synchronizes pan/zoom/rotation across every MapPanel registered to the same
 * group — the spec's "use the same geographic extent and synchronized zoom
 * across all maps" requirement (section 10). A `syncing` guard prevents the
 * `jumpTo` calls this triggers on the other maps from re-triggering their own
 * `move` handlers and creating an infinite loop.
 */
export function createMapSyncGroup() {
  const maps: maplibregl.Map[] = [];
  let syncing = false;

  function register(map: maplibregl.Map): () => void {
    maps.push(map);
    const onMove = () => {
      if (syncing) return;
      syncing = true;
      const center = map.getCenter();
      const zoom = map.getZoom();
      const bearing = map.getBearing();
      const pitch = map.getPitch();
      for (const other of maps) {
        if (other === map) continue;
        other.jumpTo({ center, zoom, bearing, pitch });
      }
      syncing = false;
    };
    map.on("move", onMove);
    return () => {
      map.off("move", onMove);
      const idx = maps.indexOf(map);
      if (idx >= 0) maps.splice(idx, 1);
    };
  }

  return { register };
}

export type MapSyncGroup = ReturnType<typeof createMapSyncGroup>;

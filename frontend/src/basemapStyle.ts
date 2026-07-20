import type { StyleSpecification } from "maplibre-gl";

/**
 * Minimal raster basemap using OpenStreetMap tiles. Fine for local development;
 * swap for a proper tile provider (MapTiler, Stadia Maps, a self-hosted
 * instance, ...) before any real deployment — see docs/deployment.md. OSM's
 * tile server has a restrictive usage policy not meant for production traffic.
 */
export const basemapStyle: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "&copy; OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

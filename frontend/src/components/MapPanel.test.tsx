import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MapPanel } from "./MapPanel";
import { createMapSyncGroup } from "../syncMaps";
import type { OverlayResponse } from "../types";

const layers = new Set<string>();
const sources = new Set<string>();

vi.mock("maplibre-gl", () => {
  class FakeMap {
    on() {}
    off() {}
    once(_event: string, cb: () => void) {
      cb();
    }
    addControl() {}
    remove() {}
    resize() {}
    isStyleLoaded() {
      return true;
    }
    getLayer(id: string) {
      return layers.has(id) ? {} : undefined;
    }
    getSource(id: string) {
      return sources.has(id) ? {} : undefined;
    }
    addSource(id: string) {
      sources.add(id);
    }
    addLayer(spec: { id: string }) {
      layers.add(spec.id);
    }
    removeLayer(id: string) {
      layers.delete(id);
    }
    removeSource(id: string) {
      sources.delete(id);
    }
    project() {
      return { x: 0, y: 0 };
    }
    getCenter() {
      return { lng: 0, lat: 0 };
    }
    getZoom() {
      return 5;
    }
    getBearing() {
      return 0;
    }
    getPitch() {
      return 0;
    }
    jumpTo() {}
  }
  class FakeMarker {
    setLngLat() {
      return this;
    }
    addTo() {
      return this;
    }
    remove() {
      return this;
    }
  }
  class FakeNavigationControl {}
  return { default: { Map: FakeMap, Marker: FakeMarker, NavigationControl: FakeNavigationControl } };
});

const overlay: OverlayResponse = {
  png_base64: "abc",
  bounds: [33, 3, 48, 15],
  vmin: 0,
  vmax: 100,
  cmap: "YlGnBu",
  diverging: false,
};

function renderPanel(props: Partial<Parameters<typeof MapPanel>[0]> = {}) {
  const syncGroup = createMapSyncGroup();
  return render(
    <MapPanel
      title="Forecast"
      subtitle="test"
      overlay={overlay}
      showGraticule={false}
      heightPx={400}
      loading={false}
      syncGroup={syncGroup}
      onMapClick={() => {}}
      {...props}
    />
  );
}

describe("MapPanel overlay layer", () => {
  it("adds a raster layer + source when given an overlay", () => {
    layers.clear();
    sources.clear();
    renderPanel();
    expect(layers.has("product-overlay-layer")).toBe(true);
    expect(sources.has("product-overlay")).toBe(true);
  });

  it("removes the previous overlay layer + source when overlay goes back to null", () => {
    // Regression test: a stale MapLibre layer/source used to stay glued to the
    // canvas when `overlay` became null (e.g. clearing state at the start of a
    // new "Generate maps" run, or after a failed fetch) — the legend correctly
    // disappeared (React-controlled) but the old map image kept showing, which
    // reads as "the map isn't updating" when the index/period selection changes.
    layers.clear();
    sources.clear();
    const { rerender } = renderPanel();
    expect(layers.has("product-overlay-layer")).toBe(true);

    rerender(
      <MapPanel
        title="Forecast"
        subtitle="test"
        overlay={null}
        showGraticule={false}
        heightPx={400}
        loading={false}
        syncGroup={createMapSyncGroup()}
        onMapClick={() => {}}
      />
    );

    expect(layers.has("product-overlay-layer")).toBe(false);
    expect(sources.has("product-overlay")).toBe(false);
  });
});

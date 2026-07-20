import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Legend } from "./Legend";
import type { OverlayResponse } from "../types";

const overlay: OverlayResponse = {
  png_base64: "",
  bounds: [33, 3, 48, 15],
  vmin: 0.2,
  vmax: 168.6,
  cmap: "YlGnBu",
  diverging: false,
};

describe("Legend", () => {
  it("renders the min/max values and units from the overlay response", () => {
    render(<Legend overlay={overlay} units="mm" />);
    expect(screen.getByText("0.2")).toBeInTheDocument();
    expect(screen.getByText("168.6")).toBeInTheDocument();
    expect(screen.getByText("mm")).toBeInTheDocument();
  });

  it("falls back to a default gradient for an unknown colormap name", () => {
    const { container } = render(<Legend overlay={{ ...overlay, cmap: "not_a_real_cmap" }} units="mm" />);
    const bar = container.querySelector(".legend-bar") as HTMLElement;
    expect(bar.style.background).toContain("linear-gradient");
  });
});

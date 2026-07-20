import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BottomDrawer } from "./BottomDrawer";
import type { TimeseriesResponse } from "../types";

const sample: TimeseriesResponse = {
  lat: 9.001,
  lon: 38.002,
  nearest_grid_lat: 9.0,
  nearest_grid_lon: 38.0,
  ensemble_member_totals_mm: [40, 42, 38, 55, 30],
  ensemble_median_mm: 40,
  ensemble_mean_mm: 41,
  historical_totals_mm: [35, 45, 50, 30, 60],
  historical_years: [1993, 1994, 1995, 1996, 1997],
  climatological_mean_mm: 44,
  forecast_percentile: 37,
  absolute_anomaly_mm: -3,
};

describe("BottomDrawer", () => {
  it("shows the collapsed hint when no cell has been clicked yet", () => {
    render(<BottomDrawer data={null} onClose={() => {}} />);
    expect(screen.getByText("Click any map to inspect a grid cell")).toBeInTheDocument();
  });

  it("renders stat cards for forecast/climatology stats once a grid cell is loaded", () => {
    render(<BottomDrawer data={sample} onClose={() => {}} />);
    expect(screen.getByText("Grid cell 9.00, 38.00")).toBeInTheDocument();
    expect(screen.getByText("40.0 mm")).toBeInTheDocument(); // ensemble median
    expect(screen.getByText("44.0 mm")).toBeInTheDocument(); // climatological mean
    expect(screen.getByText("-3.0 mm")).toBeInTheDocument(); // negative anomaly, no "+"
    expect(screen.getByText("37th")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument(); // historical_years.length
  });

  it("prefixes a positive anomaly with a plus sign and uses the positive tone", () => {
    render(<BottomDrawer data={{ ...sample, absolute_anomaly_mm: 12.3 }} onClose={() => {}} />);
    const value = screen.getByText("+12.3 mm");
    expect(value).toBeInTheDocument();
    expect(value).toHaveClass("positive");
  });

  it("marks a negative anomaly with the negative tone", () => {
    render(<BottomDrawer data={sample} onClose={() => {}} />);
    expect(screen.getByText("-3.0 mm")).toHaveClass("negative");
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<BottomDrawer data={sample} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders both chart blocks with a shared domain across the axis labels", () => {
    render(<BottomDrawer data={sample} onClose={() => {}} />);
    expect(screen.getByText("Forecast ensemble members (5)")).toBeInTheDocument();
    expect(screen.getByText("Historical distribution (5 years)")).toBeInTheDocument();
    // Shared domain: min across both arrays is 30, max is 60.
    expect(screen.getAllByText("30.0 mm")).toHaveLength(2);
    expect(screen.getAllByText("60.0 mm")).toHaveLength(2);
  });
});

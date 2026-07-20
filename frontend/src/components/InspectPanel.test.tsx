import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InspectPanel } from "./InspectPanel";
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

describe("InspectPanel", () => {
  it("shows a placeholder prompt when no cell has been clicked yet", () => {
    render(<InspectPanel data={null} onClose={() => {}} />);
    expect(screen.getByText(/click any point/i)).toBeInTheDocument();
  });

  it("renders forecast/climatology stats once a grid cell is loaded", () => {
    render(<InspectPanel data={sample} onClose={() => {}} />);
    expect(screen.getByText("40.0 mm")).toBeInTheDocument(); // ensemble median
    expect(screen.getByText("44.0 mm")).toBeInTheDocument(); // climatological mean
    expect(screen.getByText("-3.0 mm")).toBeInTheDocument(); // negative anomaly, no "+"
    expect(screen.getByText("37th")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument(); // historical_years.length
  });

  it("prefixes a positive anomaly with a plus sign", () => {
    render(<InspectPanel data={{ ...sample, absolute_anomaly_mm: 12.3 }} onClose={() => {}} />);
    expect(screen.getByText("+12.3 mm")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<InspectPanel data={sample} onClose={onClose} />);
    fireEvent.click(screen.getByText("×"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

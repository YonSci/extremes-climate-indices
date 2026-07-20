const VIEW_W = 500;
const VIEW_H = 130;
const PAD = 14;
const BAR_GAP = 2;
const CORNER_R = 4;

interface HistogramProps {
  values: number[];
  domainMin: number;
  domainMax: number;
  referenceValue?: number;
  color: string;
  height?: number;
  binCount?: number;
}

/** SVG path for a bar with rounded top corners, square baseline — per the mark spec. */
function roundedTopRectPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(CORNER_R, w / 2, h);
  if (h <= 0) return "";
  if (r <= 0) return `M${x},${y + h} L${x},${y} L${x + w},${y} L${x + w},${y + h} Z`;
  return [
    `M${x},${y + h}`,
    `L${x},${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    `L${x + w - r},${y}`,
    `Q${x + w},${y} ${x + w},${y + r}`,
    `L${x + w},${y + h}`,
    "Z",
  ].join(" ");
}

export function Histogram({
  values, domainMin, domainMax, referenceValue, color, height = 130, binCount = 8,
}: HistogramProps) {
  const span = domainMax - domainMin || 1;
  const xOf = (v: number) => PAD + ((v - domainMin) / span) * (VIEW_W - 2 * PAD);

  const counts = new Array(binCount).fill(0);
  for (const v of values) {
    const idx = Math.min(binCount - 1, Math.max(0, Math.floor(((v - domainMin) / span) * binCount)));
    counts[idx] += 1;
  }
  const maxCount = Math.max(1, ...counts);
  const baselineY = VIEW_H - 1;
  const plotH = VIEW_H - 10;
  const binWidth = (VIEW_W - 2 * PAD) / binCount;

  return (
    <svg
      className="histogram-chart"
      width="100%"
      height={height}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      role="img"
      aria-label={`Histogram of ${values.length} values across ${binCount} bins`}
    >
      <line x1={PAD} y1={baselineY} x2={VIEW_W - PAD} y2={baselineY} className="chart-baseline" />
      {counts.map((count, i) => {
        if (count === 0) return null;
        const barH = (count / maxCount) * plotH;
        const x = PAD + i * binWidth + BAR_GAP / 2;
        const w = binWidth - BAR_GAP;
        const y = baselineY - barH;
        return <path key={i} d={roundedTopRectPath(x, y, w, barH)} fill={color} />;
      })}
      {referenceValue !== undefined && (
        <line x1={xOf(referenceValue)} y1={4} x2={xOf(referenceValue)} y2={VIEW_H - 4} className="chart-reference-line" />
      )}
    </svg>
  );
}

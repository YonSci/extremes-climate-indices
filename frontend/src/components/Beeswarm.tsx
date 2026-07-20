const VIEW_W = 500;
const VIEW_H = 130;
const PAD = 14;
const DOT_R = 5;
const ROW_STEP = DOT_R * 2 + 2;

interface BeeswarmProps {
  values: number[];
  domainMin: number;
  domainMax: number;
  referenceValue?: number;
  color: string;
  height?: number;
}

interface PlacedDot {
  x: number;
  y: number;
  v: number;
}

/** Classic "dodge" beeswarm layout: sort by x, stack collisions into alternating rows above/below center. */
function layoutBeeswarm(values: number[], xOf: (v: number) => number): PlacedDot[] {
  const centerY = VIEW_H / 2;
  const sorted = [...values].sort((a, b) => xOf(a) - xOf(b));
  const placedByRow: number[][] = []; // placedByRow[rowIndex+offset] = list of x's already in that row
  const rowOffset = 40; // supports up to 40 rows in either direction before clamping
  const out: PlacedDot[] = [];

  for (const v of sorted) {
    const x = xOf(v);
    let chosenRow = 0;
    for (let step = 0; step < rowOffset; step++) {
      const candidates = step === 0 ? [0] : [step, -step];
      const free = candidates.find((row) => {
        const xs = placedByRow[row + rowOffset] ?? [];
        return xs.every((px) => Math.abs(px - x) >= DOT_R * 2 + 1);
      });
      if (free !== undefined) {
        chosenRow = free;
        break;
      }
    }
    placedByRow[chosenRow + rowOffset] = [...(placedByRow[chosenRow + rowOffset] ?? []), x];
    const y = Math.min(VIEW_H - DOT_R, Math.max(DOT_R, centerY - chosenRow * ROW_STEP));
    out.push({ x, y, v });
  }
  return out;
}

export function Beeswarm({ values, domainMin, domainMax, referenceValue, color, height = 130 }: BeeswarmProps) {
  const span = domainMax - domainMin || 1;
  const xOf = (v: number) => PAD + ((v - domainMin) / span) * (VIEW_W - 2 * PAD);
  const dots = values.length > 0 ? layoutBeeswarm(values, xOf) : [];

  return (
    <svg
      className="swarm-chart"
      width="100%"
      height={height}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      role="img"
      aria-label={`Beeswarm of ${values.length} values`}
    >
      <line x1={PAD} y1={VIEW_H - 1} x2={VIEW_W - PAD} y2={VIEW_H - 1} className="chart-baseline" />
      {referenceValue !== undefined && (
        <line x1={xOf(referenceValue)} y1={4} x2={xOf(referenceValue)} y2={VIEW_H - 4} className="chart-reference-line" />
      )}
      {dots.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r={DOT_R} fill={color} fillOpacity={0.85} className="swarm-dot" />
      ))}
    </svg>
  );
}

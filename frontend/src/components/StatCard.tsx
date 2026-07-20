interface StatCardProps {
  label: string;
  value: string;
  tone?: "neutral" | "negative" | "positive";
}

export function StatCard({ label, value, tone = "neutral" }: StatCardProps) {
  return (
    <div className="stat-card">
      <div className="stat-card-label">{label}</div>
      <div className={`stat-card-value${tone !== "neutral" ? ` ${tone}` : ""}`}>{value}</div>
    </div>
  );
}

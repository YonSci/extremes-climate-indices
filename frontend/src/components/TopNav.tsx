export type TabId = "forecasting" | "probabilistic" | "multi_model" | "validation" | "historical" | "about";

interface Tab {
  id: TabId;
  label: string;
  ready: boolean;
}

const TABS: Tab[] = [
  { id: "forecasting", label: "Forecasting", ready: true },
  { id: "probabilistic", label: "Probabilistic", ready: false },
  { id: "multi_model", label: "Multi-Model", ready: false },
  { id: "validation", label: "Validation", ready: false },
  { id: "historical", label: "Historical", ready: false },
  { id: "about", label: "About", ready: false },
];

interface TopNavProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  regionBoundsLabel: string | null;
}

export function TopNav({ activeTab, onTabChange, regionBoundsLabel }: TopNavProps) {
  return (
    <header className="top-nav">
      <div className="top-nav-brand">
        <span className="top-nav-logo" aria-hidden="true" />
        <span className="top-nav-title">Forecast Dashboard</span>
      </div>
      <nav className="top-nav-tabs" aria-label="Dashboard sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`top-nav-tab${tab.id === activeTab ? " active" : ""}`}
            onClick={() => onTabChange(tab.id)}
            aria-current={tab.id === activeTab ? "page" : undefined}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {regionBoundsLabel && <div className="top-nav-region">{regionBoundsLabel}</div>}
    </header>
  );
}

export { TABS };

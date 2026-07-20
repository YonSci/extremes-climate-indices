interface TabPlaceholderProps {
  label: string;
}

export function TabPlaceholder({ label }: TabPlaceholderProps) {
  return (
    <div className="tab-placeholder">
      <h2>{label}</h2>
      <p>This section isn't built yet — coming in a future pass.</p>
    </div>
  );
}

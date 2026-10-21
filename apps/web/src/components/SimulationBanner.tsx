export function SimulationBanner({ mode }: { mode: "mock" | "off" | "live" | "unknown" }) {
  if (mode === "off") {
    return <div className="notice notice-muted" role="status">Bantuan model dimatikan. Konteks tetap dapat dilengkapi manual.</div>;
  }
  if (mode === "mock") {
    return (
      <div className="simulation-banner" role="status">
        <span className="status-dot" aria-hidden="true" />
        SIMULASI • data sintetis
      </div>
    );
  }
  if (mode === "live") {
    return (
      <div className="notice notice-muted" role="status">
        Layanan model live dibatasi oleh konfigurasi operasional.
      </div>
    );
  }
  return null;
}


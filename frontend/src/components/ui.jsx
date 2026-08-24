import React, { useEffect, useState, useCallback } from "react";

// Small data-fetching hook with refresh support.
export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const run = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    fn()
      .then((data) => setState({ loading: false, data, error: null }))
      .catch((error) => setState({ loading: false, data: null, error: error.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { run(); }, [run]);
  return { ...state, refresh: run };
}

export function Loading({ what = "data" }) {
  return <div className="loading">Loading {what}…</div>;
}

export function ErrorBox({ msg }) {
  return <div className="banner err">⚠ {msg}</div>;
}

export function Tag({ kind, children }) {
  const cls = String(kind || "").toLowerCase().replace(/[^a-z-]/g, "");
  return <span className={`tag ${cls}`}>{children}</span>;
}

export function StatusDot({ status }) {
  const map = { on_target: "dot-on", watch: "dot-watch", off_target: "dot-off", info: "dot-info" };
  return <span className={`status-dot ${map[status] || "dot-info"}`} />;
}

export function Stat({ value, label }) {
  return (
    <div className="stat">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
    </div>
  );
}

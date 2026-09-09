"use client";

import { useCallback, useEffect, useState } from "react";

import type { LiveCanary } from "@/lib/types";
import { Icon } from "./icon";

type Props = { apiUrl: string; demoMode: boolean };

export function CanaryPanel({ apiUrl, demoMode }: Props) {
  const [items, setItems] = useState<LiveCanary[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (demoMode) return;
    const response = await fetch(`${apiUrl}/api/canaries`, { cache: "no-store" });
    if (!response.ok) throw new Error("Live canary status is unavailable.");
    const payload = (await response.json()) as { items: LiveCanary[] };
    setItems(payload.items);
  }, [apiUrl, demoMode]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      void load().catch((caught) =>
        setError(caught instanceof Error ? caught.message : "Live canaries are unavailable."),
      );
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const response = await fetch(`${apiUrl}/api/canaries/run`, { method: "POST" });
      if (!response.ok) throw new Error("Live canaries could not be executed.");
      const payload = (await response.json()) as { items: LiveCanary[] };
      setItems(payload.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Live canaries failed.");
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="panel canary-panel" id="canaries">
      <div className="panel-head">
        <div><span className="eyebrow">Non-blocking live checks</span><h3>External API canaries</h3></div>
        <button
          className="secondary-button"
          type="button"
          disabled={demoMode || running}
          onClick={() => void run()}
        >
          <Icon name="refresh" />{running ? "Checking…" : "Run live checks"}
        </button>
      </div>
      <p className="canary-intro">
        Canaries detect current SEC or GitHub connectivity and schema drift. They never change the
        frozen 218-case deployment gate.
      </p>
      {error ? <p className="canary-error" role="alert">{error}</p> : null}
      {items.length ? (
        <div className="canary-list">
          {items.map((item) => (
            <div key={item.canary_id}>
              <span className={`canary-state ${item.status}`}><i />{item.status}</span>
              <span><strong>{item.canary_id}</strong><small>{item.connector} · {item.provenance ?? "UNVERIFIED"}</small></span>
              <span><strong>{item.latency_ms === null ? "—" : `${item.latency_ms}ms`}</strong><small>{new Date(item.checked_at).toLocaleString()}</small></span>
              {item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">Source</a> : <span />}
              {item.error ? <small className="canary-error">{item.error}</small> : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="experiment-empty">
          {demoMode ? "Live canaries are disabled in demo mode." : "No live canary has run yet."}
        </div>
      )}
    </section>
  );
}

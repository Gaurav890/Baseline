"use client";

import { useCallback, useEffect, useState } from "react";

import type { SafetySnapshot } from "@/lib/types";

type LoadStatus = "loading" | "ready" | "error" | "demo";

const readable = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

const formatBytes = (value: number) =>
  value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)} MB` : `${Math.round(value / 1000)} KB`;

export function AgentSafetyPanel({ apiUrl, demoMode }: { apiUrl: string; demoMode: boolean }) {
  const [snapshot, setSnapshot] = useState<SafetySnapshot | null>(null);
  const [status, setStatus] = useState<LoadStatus>(demoMode ? "demo" : "loading");

  const load = useCallback(async () => {
    if (demoMode) return;
    setStatus("loading");
    try {
      const response = await fetch(`${apiUrl}/api/safety`, { cache: "no-store" });
      if (!response.ok) throw new Error("Safety state unavailable");
      setSnapshot((await response.json()) as SafetySnapshot);
      setStatus("ready");
    } catch {
      setSnapshot(null);
      setStatus("error");
    }
  }, [apiUrl, demoMode]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const missingChecks = snapshot?.checks.filter((check) => check.status === "not_implemented") ?? [];

  return (
    <section className="panel safety-panel" id="safety" aria-labelledby="safety-title">
      <div className="panel-head safety-head">
        <div><span className="eyebrow">Governance plane</span><h3 id="safety-title">Agent safety control plane</h3></div>
        {snapshot ? (
          <span className={`execution-state ${snapshot.execution_enabled ? "enabled" : "stopped"}`}>
            <i />{snapshot.execution_enabled ? "Execution enabled" : "Emergency stop active"}
          </span>
        ) : <span className={`execution-state ${status}`}>{status === "loading" ? "Loading" : "Unavailable"}</span>}
      </div>
      <p className="safety-intro">
        Reliability asks whether the answer was good. Safety asks whether the agent stayed inside the tools, authority, time, and money it was given. This view reports enforced controls and known gaps separately.
      </p>

      {status === "demo" ? <div className="safety-state">Safety policy state is not simulated in demo mode.</div> : null}
      {status === "error" ? (
        <div className="safety-state error" role="alert"><span>The safety API could not be reached.</span><button className="text-button" type="button" onClick={() => void load()}>Retry</button></div>
      ) : null}
      {status === "loading" ? <div className="safety-state" aria-live="polite">Loading active contracts and policy decisions…</div> : null}

      {status === "ready" && snapshot ? (
        <>
          <div className="safety-control-grid">
            {snapshot.controls.map((control) => (
              <article className="safety-control" key={control.name}>
                <span>{control.name}</span>
                <strong className={control.status}>{readable(control.status)}</strong>
                <p>{control.detail}</p>
              </article>
            ))}
          </div>

          <div className="safety-summary-strip">
            <div><span>Active contracts</span><strong>{snapshot.summary.policy_count}</strong></div>
            <div><span>Policy decisions</span><strong>{snapshot.summary.event_count.toLocaleString()}</strong></div>
            <div><span>Blocked or timed out</span><strong>{snapshot.summary.blocked_count.toLocaleString()}</strong></div>
            <div><span>Mutating tool policies</span><strong>{snapshot.summary.mutating_tools_enabled}</strong></div>
          </div>

          <div className="safety-layout">
            <article className="safety-subpanel policy-contracts">
              <div className="safety-subhead"><div><span className="eyebrow">Scope · authority · budget</span><h4>Enforced agent contracts</h4></div><small>Persisted policy</small></div>
              <div className="policy-table">
                <div className="policy-row policy-header"><span>Agent</span><span>Allowed tools</span><span>Calls</span><span>Time</span><span>Cost</span><span>Approval</span><span>Writes</span></div>
                {snapshot.policies.map((policy) => (
                  <div className="policy-row" key={policy.id}>
                    <span><strong>{readable(policy.agent_type)}</strong><small>v{policy.version} · {policy.enabled ? "enabled" : "disabled"}</small></span>
                    <span className="policy-tools">{policy.allowed_tools.map((tool) => <code key={tool}>{tool}</code>)}</span>
                    <strong>{policy.max_tool_calls}</strong>
                    <strong>{policy.max_runtime_seconds}s</strong>
                    <strong>${policy.max_estimated_cost_usd.toFixed(2)}</strong>
                    <strong>${policy.approval_threshold_usd.toFixed(0)}</strong>
                    <em className={policy.allow_mutating_tools ? "allowed" : "blocked"}>{policy.allow_mutating_tools ? "Allowed" : "Blocked"}</em>
                  </div>
                ))}
              </div>
            </article>

            <article className="safety-subpanel containment-card">
              <div className="safety-subhead"><div><span className="eyebrow">Containment</span><h4>Network & runtime boundary</h4></div><small>Effective config</small></div>
              <div className="containment-list">
                <div><span>HTTPS-only retrieval</span><strong>{snapshot.containment.https_only ? "Enforced" : "Off"}</strong></div>
                <div><span>Private network blocked</span><strong>{snapshot.containment.private_network_blocked ? "Enforced" : "Off"}</strong></div>
                <div><span>Redirects blocked</span><strong>{snapshot.containment.redirects_blocked ? "Enforced" : "Off"}</strong></div>
                <div><span>Response ceiling</span><strong>{formatBytes(snapshot.containment.max_response_bytes)}</strong></div>
                <div><span>Connector / provider timeout</span><strong>{snapshot.containment.connector_timeout_seconds}s / {snapshot.containment.provider_timeout_seconds}s</strong></div>
                <div className="containment-domains"><span>Allowed connector domains</span><p>{snapshot.containment.allowed_domains.length ? snapshot.containment.allowed_domains.join(" · ") : "No domains configured"}</p></div>
              </div>
            </article>
          </div>

          <div className="safety-layout lower">
            <article className="safety-subpanel">
              <div className="safety-subhead"><div><span className="eyebrow">Auditability</span><h4>Policy decision ledger</h4></div><small>Inputs stored as hashes</small></div>
              {snapshot.events.length ? (
                <div className="safety-events">
                  {snapshot.events.slice(0, 8).map((event) => (
                    <div className="safety-event" key={event.id}>
                      <span className={`decision ${event.decision}`}>{readable(event.decision)}</span>
                      <span><strong>{event.action}</strong><small>{readable(event.category)} · {event.reason}</small></span>
                      <code>{event.run_id?.slice(0, 8) ?? "system"}</code>
                    </div>
                  ))}
                </div>
              ) : <div className="safety-empty">No agent has produced a policy decision yet.</div>}
            </article>

            <article className="safety-subpanel">
              <div className="safety-subhead"><div><span className="eyebrow">Safety control coverage</span><h4>Threat checks</h4></div><small>{snapshot.checks.length - missingChecks.length} configured · {missingChecks.length} gaps</small></div>
              <div className="safety-checks">
                {snapshot.checks.map((check) => (
                  <div key={check.id}><i className={check.status} /><span><strong>{check.label}</strong><small>{readable(check.category)}</small></span><em className={check.status}>{readable(check.status)}</em></div>
                ))}
              </div>
              {missingChecks.length ? <p className="known-gap"><strong>Known boundary:</strong> isolated code execution, covert-channel detection, and authenticated workspace roles are not represented as implemented controls.</p> : null}
            </article>
          </div>
        </>
      ) : null}
    </section>
  );
}

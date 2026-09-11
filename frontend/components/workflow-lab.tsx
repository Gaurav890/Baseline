"use client";

import { useEffect, useState } from "react";

import type { WorkflowCatalogResponse } from "@/lib/types";
import { Icon } from "./icon";

type WorkflowStatus = "loading" | "ready" | "empty" | "error" | "demo";

export function WorkflowLab({ apiUrl, demoMode }: { apiUrl: string; demoMode: boolean }) {
  const [catalog, setCatalog] = useState<WorkflowCatalogResponse | null>(null);
  const [status, setStatus] = useState<WorkflowStatus>(demoMode ? "demo" : "loading");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (demoMode) return;

    const controller = new AbortController();
    const load = async () => {
      setStatus("loading");
      try {
        const response = await fetch(`${apiUrl}/api/workflows`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Workflow catalog unavailable");
        const payload = (await response.json()) as WorkflowCatalogResponse;
        setCatalog(payload);
        setStatus(payload.agents.length ? "ready" : "empty");
      } catch {
        if (controller.signal.aborted) return;
        setCatalog(null);
        setStatus("error");
      }
    };

    void load();
    return () => controller.abort();
  }, [apiUrl, demoMode, reloadKey]);

  return (
    <section className="panel workflow-lab" id="workflow-lab" aria-labelledby="workflow-title">
      <div className="panel-head workflow-head">
        <div>
          <span className="eyebrow">Agent portfolio</span>
          <h3 id="workflow-title">Complex workflow lab</h3>
        </div>
        <div className="workflow-badges">
          <span className="workflow-nonblocking">Exploratory · non-blocking</span>
          {catalog ? <span>{catalog.agents.length} agents · {catalog.scenarios.length} cases</span> : null}
        </div>
      </div>
      <p className="workflow-intro">
        These are complete business workflows, not one-prompt demos. Each agent gathers evidence,
        applies a versioned decision rule, produces a typed result, and stops at its authority
        boundary. Frozen public evidence makes the examples repeatable; synthetic inputs are labeled.
      </p>

      <div className="workflow-proof-strip" aria-label="What these workflows demonstrate">
        <div><strong>Scope</strong><span>Only named tools and sources</span></div>
        <div><strong>Reasoning</strong><span>Four inspectable stages</span></div>
        <div><strong>Decision</strong><span>Typed business output</span></div>
        <div><strong>Control</strong><span>Human gate before risky action</span></div>
      </div>

      {status === "loading" ? <div className="workflow-state" aria-live="polite">Loading workflow contracts…</div> : null}
      {status === "demo" ? <div className="workflow-state">Workflow contracts are available from the connected API, not synthetic demo mode.</div> : null}
      {status === "empty" ? <div className="workflow-state">No complex workflows are registered.</div> : null}
      {status === "error" ? (
        <div className="workflow-state error" role="alert">
          <span>The workflow catalog could not be reached.</span>
          <button className="text-button" type="button" onClick={() => setReloadKey((value) => value + 1)}>Retry</button>
        </div>
      ) : null}

      {status === "ready" && catalog ? (
        <div className="workflow-agent-list">
          {catalog.agents.map((agent, agentIndex) => {
            const scenarios = catalog.scenarios.filter((scenario) => scenario.agent_type === agent.id);
            return (
              <details className="workflow-agent" key={agent.id} open={agentIndex === 0}>
                <summary>
                  <span className={`workflow-agent-mark ${agent.id}`}><Icon name="trace" size={17} /></span>
                  <span><strong>{agent.name}</strong><small>{agent.description}</small></span>
                  <span className="workflow-complexity">{agent.complexity}</span>
                  <span className="workflow-chevron"><Icon name="chevron" size={15} /></span>
                </summary>
                <div className="workflow-agent-body">
                  <div className="workflow-value">
                    <span className="eyebrow">Why a company uses it</span>
                    <strong>{agent.business_value}</strong>
                    <p><b>Example assignment:</b> {agent.example_task}</p>
                  </div>

                  <ol className="workflow-stages">
                    {agent.workflow.map((stage, index) => (
                      <li key={stage.name}>
                        <span>{index + 1}</span>
                        <div><strong>{stage.name}</strong><small>{stage.description}</small></div>
                      </li>
                    ))}
                  </ol>

                  <div className="workflow-contract-grid">
                    <div><span>Allowed tools</span><p>{agent.tools.map((tool) => <code key={tool}>{tool}</code>)}</p></div>
                    <div><span>Result contract</span><p>{agent.output_fields.map((field) => <code key={field}>{field}</code>)}</p></div>
                  </div>

                  <div className="workflow-cases">
                    <div className="workflow-cases-head">
                      <div><span className="eyebrow">Versioned examples</span><strong>{agent.scenario_count} repeatable cases</strong></div>
                      <code>{catalog.dataset_version}</code>
                    </div>
                    {scenarios.map((scenario) => (
                      <article key={scenario.id}>
                        <div className="workflow-case-id">
                          <code>{scenario.id}</code>
                          <span>{scenario.blocking ? "Blocking" : "Does not change release gate"}</span>
                        </div>
                        <p>{scenario.task}</p>
                        <div className="workflow-case-meta">
                          <span>Evidence</span>
                          {scenario.provenance.map((provenance) => (
                            <em className={`provenance ${provenance.toLowerCase()}`} key={provenance}>{provenance}</em>
                          ))}
                          <span>·</span>
                          <span>{scenario.source_urls.length} evidence reference{scenario.source_urls.length === 1 ? "" : "s"}</span>
                          <span>·</span>
                          <span>{scenario.expected_tools.length} traceable steps</span>
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
              </details>
            );
          })}
          <p className="workflow-footnote">
            The original {catalog.release_gate_scenario_count}-case golden dataset remains the release gate. These cases are deliberately separate so richer demos cannot silently make CI easier.
          </p>
        </div>
      ) : null}
    </section>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { demoData } from "@/lib/demo-data";
import type { DashboardData, RecentRun } from "@/lib/types";
import { Icon } from "./icon";
import { TrendChart } from "./trend-chart";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const formatPercent = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const formatCost = (value: number) => `$${value.toFixed(value < 0.1 ? 3 : 2)}`;
const formatLatency = (value: number) => (value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value}ms`);
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());

function MetricCard({
  eyebrow,
  value,
  delta,
  intent = "good",
  spark,
}: {
  eyebrow: string;
  value: string;
  delta: string;
  intent?: "good" | "bad" | "neutral";
  spark: number[];
}) {
  const max = Math.max(...spark);
  const min = Math.min(...spark);
  const points = spark
    .map((item, index) => `${(index / (spark.length - 1)) * 106},${34 - ((item - min) / Math.max(1, max - min)) * 25}`)
    .join(" ");
  return (
    <article className="metric-card">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <strong>{value}</strong>
      </div>
      <div className="metric-foot">
        <span className={`delta ${intent}`}>{delta}</span>
        <svg className="spark" viewBox="0 0 106 38" aria-hidden="true">
          <polyline points={points} />
        </svg>
      </div>
    </article>
  );
}

function ProviderMark({ provider }: { provider: string }) {
  return <span className={`provider-mark ${provider}`}>{provider.slice(0, 1).toUpperCase()}</span>;
}

function RunDrawer({ run, onClose }: { run: RecentRun; onClose: () => void }) {
  const steps = [
    { type: "Input", name: "User task", detail: `${run.task.slice(0, 42)}…`, time: "0ms" },
    { type: "Plan", name: "Task planner", detail: "Selected tools from agent contract", time: "42ms" },
    { type: "Tool", name: "document_search", detail: "3 grounded chunks returned", time: "518ms" },
    { type: "Model", name: run.model, detail: "Evidence synthesis", time: formatLatency(run.latency_ms - 612) },
    { type: "Eval", name: "Rubric + deterministic", detail: run.failure ? titleCase(run.failure) : "All checks passed", time: "94ms" },
  ];
  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside className="run-drawer" onMouseDown={(event) => event.stopPropagation()} aria-label="Run trace">
        <div className="drawer-head">
          <div>
            <span className="eyebrow">Trace explorer</span>
            <h2>{run.id}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close trace">×</button>
        </div>
        <div className="drawer-summary">
          <div><span>Score</span><strong>{run.score ? formatPercent(run.score, 0) : "—"}</strong></div>
          <div><span>Latency</span><strong>{formatLatency(run.latency_ms)}</strong></div>
          <div><span>Cost</span><strong>{formatCost(run.cost_usd)}</strong></div>
        </div>
        <p className="drawer-task">{run.task}</p>
        <div className="trace-list">
          {steps.map((step, index) => (
            <div className="trace-step" key={step.type}>
              <div className={`trace-dot ${run.failure && index === 4 ? "error" : ""}`}>
                {index + 1}
              </div>
              <div className="trace-copy">
                <span>{step.type}</span>
                <strong>{step.name}</strong>
                <p>{step.detail}</p>
              </div>
              <code>{step.time}</code>
            </div>
          ))}
        </div>
        <div className="json-preview">
          <div><span>Evaluator output</span><code>application/json</code></div>
          <pre>{JSON.stringify({
            score: run.score,
            passed: !run.failure,
            failure_classification: run.failure,
            grounded: true,
          }, null, 2)}</pre>
        </div>
      </aside>
    </div>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardData>(demoData);
  const [connection, setConnection] = useState<"demo" | "live">("demo");
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<RecentRun | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/api/dashboard`, { cache: "no-store" });
      if (!response.ok) throw new Error("API unavailable");
      setData(await response.json());
      setConnection("live");
    } catch {
      setConnection("demo");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiUrl}/api/dashboard`, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error("API unavailable");
        return response.json() as Promise<DashboardData>;
      })
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setConnection("live");
        }
      })
      .catch(() => {
        if (!cancelled) setConnection("demo");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runSuite = async () => {
    setRunning(true);
    setNotice(null);
    try {
      const response = await fetch(`${apiUrl}/api/evaluations/regression`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: "mock", limit: 218, baseline_success_rate: 0.912 }),
      });
      if (!response.ok) throw new Error("Could not start suite");
      const result = await response.json();
      setNotice(`${result.scenario_count} evals complete · ${formatPercent(result.success_rate)} passed`);
      await loadDashboard();
    } catch {
      setNotice("Start the API to run the live 218-scenario suite.");
    } finally {
      setRunning(false);
    }
  };

  const bestValue = useMemo(
    () => ({
      quality: Math.max(...data.model_comparison.map((item) => item.quality)),
      latency: Math.min(...data.model_comparison.map((item) => item.latency_ms)),
      cost: Math.min(...data.model_comparison.map((item) => item.cost_per_task)),
    }),
    [data.model_comparison],
  );
  const suite = data.latest_suite;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="Baseline home">
          <span className="brand-glyph"><i /><i /><i /></span>
          <span>baseline<span>.</span></span>
        </a>
        <nav>
          <span className="nav-label">Workspace</span>
          <a className="active" href="#overview"><Icon name="overview" />Overview</a>
          <a href="#evaluation"><Icon name="flask" />Evaluation suites<span className="nav-count">3</span></a>
          <a href="#traces"><Icon name="trace" />Run traces</a>
          <a href="#datasets"><Icon name="database" />Golden datasets<span className="nav-count">218</span></a>
          <span className="nav-label second">Analyze</span>
          <a href="#models"><Icon name="compare" />Model comparison</a>
          <a href="#settings"><Icon name="settings" />Configuration</a>
        </nav>
        <div className="sidebar-foot">
          <div className="status-line"><span className="status-dot" />All systems operational</div>
          <div className="workspace-switcher">
            <span className="workspace-avatar">AR</span>
            <div><strong>Agent Reliability</strong><span>production</span></div>
            <span>···</span>
          </div>
        </div>
      </aside>

      <main id="top">
        <header className="topbar">
          <div>
            <span className="breadcrumb">WORKSPACE / OVERVIEW</span>
            <h1>Agent Reliability Lab</h1>
          </div>
          <div className="top-actions">
            <span className={`environment ${connection}`}><i />{connection === "live" ? "Live API" : "Demo data"}</span>
            <button className="secondary-button" onClick={() => void loadDashboard()}><Icon name="refresh" />Refresh</button>
            <button className="primary-button" onClick={() => void runSuite()} disabled={running}>
              <Icon name="play" />{running ? "Running 218 evals…" : "Run evaluation"}
            </button>
          </div>
        </header>

        <div className="content">
          {notice && <div className="notice">{notice}<button onClick={() => setNotice(null)}>×</button></div>}

          <section className="hero" id="overview">
            <div>
              <span className="kicker"><i />Reliability intelligence</span>
              <h2>Know if your agents<br /><em>actually work.</em></h2>
              <p>Evaluation, observability, and model economics in one control plane.</p>
            </div>
            <div className="hero-stat">
              <span>Evaluated today</span>
              <strong>1,284</strong>
              <small>across 4 production agents</small>
            </div>
          </section>

          {suite && (
            <section className={`regression-banner ${suite.regression_detected ? "failed" : "passed"}`} id="evaluation">
              <div className="regression-icon"><Icon name={suite.regression_detected ? "warning" : "check"} size={22} /></div>
              <div className="regression-title">
                <span>Latest PR gate</span>
                <strong>{suite.regression_detected ? "Regression detected" : "Evaluation passed"}</strong>
                <code>{suite.commit_sha}</code>
              </div>
              <div className="suite-stat"><span>Scenarios</span><strong>{suite.scenario_count}</strong></div>
              <div className="suite-stat"><span>Previous</span><strong>{formatPercent(suite.previous_success_rate)}</strong></div>
              <div className="suite-stat emphasis"><span>New version</span><strong>{formatPercent(suite.success_rate)}</strong></div>
              <div className="suite-delta">
                <span>{formatPercent(suite.success_rate - suite.previous_success_rate)}</span>
                <small>success rate</small>
              </div>
              <button className="text-button" onClick={() => document.querySelector("#traces")?.scrollIntoView({ behavior: "smooth" })}>
                Inspect failures <Icon name="arrow" />
              </button>
            </section>
          )}

          <div className="section-heading">
            <div><span className="eyebrow">Quality signals</span><h3>Production confidence</h3></div>
            <span className="period">Last 7 days⌄</span>
          </div>
          <section className="metrics-grid">
            <MetricCard eyebrow="Task completion" value={formatPercent(data.quality.task_completion)} delta="↑ 1.8% vs prior" spark={[81, 84, 82, 87, 88, 92, 94]} />
            <MetricCard eyebrow="Answer correctness" value={formatPercent(data.quality.answer_correctness)} delta="↑ 0.7% vs prior" spark={[80, 82, 83, 82, 86, 89, 91]} />
            <MetricCard eyebrow="Tool selection" value={formatPercent(data.quality.tool_selection_accuracy)} delta="↓ 11 failures" intent="bad" spark={[94, 92, 96, 93, 91, 87, 82]} />
            <MetricCard eyebrow="Hallucination rate" value={formatPercent(data.quality.hallucination_rate)} delta="↓ 0.4% improved" spark={[33, 28, 29, 23, 25, 19, 16]} />
            <MetricCard eyebrow="Eval pass rate" value={formatPercent(data.quality.eval_pass_rate)} delta="↓ 2.5% regression" intent="bad" spark={[90, 91, 92, 92, 91, 89, 88]} />
          </section>

          <section className="two-col" id="models">
            <article className="panel trend-panel">
              <div className="panel-head">
                <div><span className="eyebrow">Quality over time</span><h3>Evaluation score</h3></div>
                <div className="legend"><i />Pass rate <span>Target 90%</span></div>
              </div>
              <TrendChart data={data.trend} />
            </article>
            <article className="panel operations-panel">
              <div className="panel-head">
                <div><span className="eyebrow">Operations</span><h3>Runtime health</h3></div>
                <span className="live-pill"><i />Live</span>
              </div>
              <div className="ops-grid">
                <div><Icon name="clock" /><span>p50 latency</span><strong>{formatLatency(data.operations.latency_p50_ms)}</strong><small>p95 {formatLatency(data.operations.latency_p95_ms)}</small></div>
                <div><Icon name="coins" /><span>Cost / task</span><strong>{formatCost(data.operations.cost_per_task)}</strong><small>{formatCost(data.operations.cost_total)} total</small></div>
                <div><Icon name="refresh" /><span>Retries</span><strong>{data.operations.retries}</strong><small>2.1% of runs</small></div>
                <div><Icon name="arrow" /><span>Escalations</span><strong>{formatPercent(data.operations.escalation_rate)}</strong><small>within 8% target</small></div>
              </div>
              <div className="token-strip"><span>Token volume</span><strong>{(data.operations.tokens / 1_000_000).toFixed(1)}M</strong><div><i style={{ width: "68%" }} /></div><small>{data.operations.runs.toLocaleString()} total runs</small></div>
            </article>
          </section>

          <section className="panel model-panel">
            <div className="panel-head">
              <div><span className="eyebrow">Quality × cost × latency</span><h3>Model benchmark</h3></div>
              <span className="suite-label">Same {data.evaluation.scenario_count} scenarios · 3 runs each</span>
            </div>
            <div className="model-table" role="table" aria-label="Model benchmark comparison">
              <div className="model-row model-header" role="row">
                <span>Model</span><span>Quality</span><span>Completion</span><span>p50 latency</span><span>Cost / task</span><span>Signal</span>
              </div>
              {data.model_comparison.map((model) => (
                <div className="model-row" role="row" key={model.provider}>
                  <span className="model-name"><ProviderMark provider={model.provider} /><span><strong>{model.model}</strong><small>{titleCase(model.provider)}</small></span></span>
                  <span className="score-cell"><strong>{formatPercent(model.quality)}</strong><i><b style={{ width: formatPercent(model.quality) }} /></i>{model.quality === bestValue.quality && <em>Best quality</em>}</span>
                  <span>{formatPercent(model.task_completion)}</span>
                  <span className={model.latency_ms === bestValue.latency ? "winner" : ""}>{formatLatency(model.latency_ms)}{model.latency_ms === bestValue.latency && <em>Fastest</em>}</span>
                  <span className={model.cost_per_task === bestValue.cost ? "winner" : ""}>{formatCost(model.cost_per_task)}{model.cost_per_task === bestValue.cost && <em>Lowest</em>}</span>
                  <span className="recommendation">{model.quality === bestValue.quality ? "Quality" : model.cost_per_task === bestValue.cost ? "Efficiency" : "Balanced"}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="two-col lower-grid">
            <article className="panel" id="datasets">
              <div className="panel-head">
                <div><span className="eyebrow">Evaluation coverage</span><h3>Confidence stack</h3></div>
                <strong className="coverage-total">{data.evaluation.scenario_count}<small>scenarios</small></strong>
              </div>
              <div className="method-list">
                {data.evaluation.methods.map((method, index) => (
                  <div className="method-row" key={method.name}>
                    <span className={`method-icon method-${index + 1}`}><Icon name={index === 4 ? "overview" : index === 1 ? "database" : "flask"} /></span>
                    <div><strong>{method.name}</strong><span>{method.status === "active" ? "Runs on every PR" : method.status === "available" ? "Provider-backed judge" : "Reviewer queue"}</span></div>
                    <span className={`method-status ${method.status}`}>{method.status}</span>
                    <strong>{method.coverage}</strong>
                  </div>
                ))}
              </div>
            </article>
            <article className="panel failures-panel">
              <div className="panel-head">
                <div><span className="eyebrow">Failure intelligence</span><h3>Regression breakdown</h3></div>
                <span className="bad-badge">+11 tool failures</span>
              </div>
              <div className="failure-bars">
                {Object.entries(data.failure_breakdown).map(([name, count], index) => (
                  <div key={name}>
                    <span>{titleCase(name)}</span>
                    <div><i className={`failure-${index + 1}`} style={{ width: `${Math.max(9, count * 4.8)}%` }} /></div>
                    <strong>{count}</strong>
                  </div>
                ))}
              </div>
              <div className="failure-callout"><Icon name="warning" /><div><strong>Primary regression source</strong><span><code>web_search</code> selected instead of <code>document_search</code> in 11 scenarios.</span></div></div>
            </article>
          </section>

          <section className="panel runs-panel" id="traces">
            <div className="panel-head">
              <div><span className="eyebrow">Trace-level observability</span><h3>Recent agent runs</h3></div>
              <button className="text-button">View all traces <Icon name="arrow" /></button>
            </div>
            <div className="runs-table">
              <div className="run-row run-header"><span>Run / task</span><span>Agent</span><span>Model</span><span>Score</span><span>Latency</span><span>Cost</span><span>Status</span><span /></div>
              {data.recent_runs.map((run) => (
                <button className="run-row" key={run.id} onClick={() => setSelectedRun(run)}>
                  <span><code>{run.id.slice(0, 12)}</code><small>{run.task}</small></span>
                  <span className={`agent-chip ${run.agent_type}`}>{titleCase(run.agent_type)}</span>
                  <span className="run-model"><ProviderMark provider={run.provider} />{run.model}</span>
                  <span className={run.score && run.score < 0.85 ? "score-bad" : "score-good"}>{run.score ? formatPercent(run.score, 0) : "—"}</span>
                  <span>{formatLatency(run.latency_ms)}</span>
                  <span>{formatCost(run.cost_usd)}</span>
                  <span className={run.failure ? "run-failed" : "run-passed"}><i />{run.failure ? titleCase(run.failure) : "Passed"}</span>
                  <span><Icon name="chevron" /></span>
                </button>
              ))}
            </div>
          </section>

          <footer><span>Baseline agent reliability platform</span><span>Last synced {new Date(data.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></footer>
        </div>
      </main>
      {selectedRun && <RunDrawer run={selectedRun} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}

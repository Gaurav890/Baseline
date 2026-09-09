"use client";

import { useCallback, useEffect, useState } from "react";

import type {
  DatasetVersionSummary,
  ReviewQueueItem,
  ReviewQueueResponse,
} from "@/lib/types";

type LoadStatus = "loading" | "ready" | "error" | "demo";

const readable = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

export function ProductionLearningPanel({ apiUrl, demoMode }: { apiUrl: string; demoMode: boolean }) {
  const [status, setStatus] = useState<LoadStatus>(demoMode ? "demo" : "loading");
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [versions, setVersions] = useState<DatasetVersionSummary[]>([]);
  const [selected, setSelected] = useState<ReviewQueueItem | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (demoMode) return;
    setStatus("loading");
    setMessage(null);
    try {
      const [queueResponse, versionsResponse] = await Promise.all([
        fetch(`${apiUrl}/api/review-queue`, { cache: "no-store" }),
        fetch(`${apiUrl}/api/dataset-versions`, { cache: "no-store" }),
      ]);
      if (!queueResponse.ok || !versionsResponse.ok) throw new Error("Production learning state unavailable");
      const queue = (await queueResponse.json()) as ReviewQueueResponse;
      const datasetVersions = (await versionsResponse.json()) as DatasetVersionSummary[];
      setItems(queue.items ?? []);
      setVersions(datasetVersions ?? []);
      setStatus("ready");
    } catch {
      setItems([]);
      setVersions([]);
      setStatus("error");
    }
  }, [apiUrl, demoMode]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const submitReview = async (verdict: "pass" | "fail" | "needs_review") => {
    if (!selected || !reviewer.trim()) {
      setMessage("Enter the reviewer name before saving a decision.");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch(`${apiUrl}/api/runs/${encodeURIComponent(selected.run_id)}/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reviewer: reviewer.trim(),
          verdict,
          score: verdict === "pass" ? 1 : verdict === "fail" ? 0 : 0.5,
          notes: notes.trim(),
        }),
      });
      if (!response.ok) throw new Error("Review could not be saved");
      setSelected(null);
      setNotes("");
      setMessage(
        verdict === "needs_review"
          ? "Notes saved. The run remains open for a final decision."
          : "Human decision saved. The run was removed from the open queue.",
      );
      await load();
    } catch {
      setMessage("The review could not be saved. Check the API and try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel learning-panel" id="review-queue">
      <div className="panel-head">
        <div><span className="eyebrow">Production learning loop</span><h3>Human review & dataset candidates</h3></div>
        <button className="text-button" type="button" disabled={status === "loading" || demoMode} onClick={() => void load()}>
          {status === "loading" ? "Loading…" : "Refresh"}
        </button>
      </div>
      <p className="learning-intro">
        Weak production runs wait here for a person. A review records the decision; promotion creates a new candidate dataset version and never changes the 218 release tests automatically.
      </p>

      {status === "demo" ? <div className="panel-empty">The production review queue is disabled in demo mode.</div> : null}
      {status === "error" ? <div className="learning-error" role="alert">The review queue could not be reached.</div> : null}
      {message ? <div className="learning-message" role="status">{message}</div> : null}

      {status === "ready" ? (
        <div className="learning-grid">
          <div className="learning-column">
            <div className="learning-subhead"><strong>Needs a decision</strong><span>{items.length} open</span></div>
            {items.length ? items.slice(0, 6).map((item) => (
              <button className={`review-item ${selected?.run_id === item.run_id ? "selected" : ""}`} type="button" key={item.run_id} onClick={() => { setSelected(item); setMessage(null); }}>
                <span><strong>{item.task}</strong><small>{readable(item.agent_type)} · {item.provider}/{item.model}</small></span>
                <em>{item.reason === "failed_run" ? "Run failed" : "Quality signal"}</em>
              </button>
            )) : <div className="learning-empty"><strong>Queue clear</strong><span>No unreviewed failure or negative feedback is waiting.</span></div>}
          </div>

          <div className="learning-column">
            <div className="learning-subhead"><strong>Candidate datasets</strong><span>{versions.length} versions</span></div>
            {versions.length ? versions.slice(0, 6).map((version) => (
              <div className="dataset-version" key={version.id}>
                <span><strong>{version.dataset_name} v{version.version}</strong><small>{version.scenario_count} proposed cases</small></span>
                <em>{readable(version.status)}</em>
              </div>
            )) : <div className="learning-empty"><strong>No candidates yet</strong><span>Reviewed failures can be promoted through the dataset promotion API.</span></div>}
          </div>
        </div>
      ) : null}

      {selected ? (
        <div className="review-editor">
          <div><span className="eyebrow">Human decision</span><strong>{selected.run_id.slice(0, 12)}</strong><p>{selected.task}</p></div>
          <label>Reviewer name<input value={reviewer} maxLength={120} onChange={(event) => setReviewer(event.target.value)} placeholder="Your name" /></label>
          <label>Notes<textarea value={notes} maxLength={5000} onChange={(event) => setNotes(event.target.value)} placeholder="What failed, or why this output is acceptable" /></label>
          <div className="review-actions">
            <button className="secondary-button" type="button" disabled={saving} onClick={() => void submitReview("needs_review")}>Keep open</button>
            <button className="secondary-button danger" type="button" disabled={saving} onClick={() => void submitReview("fail")}>Confirm failure</button>
            <button className="primary-button" type="button" disabled={saving} onClick={() => void submitReview("pass")}>Accept output</button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

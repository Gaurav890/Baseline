export type ModelComparison = {
  experiment_id?: string;
  provider: string;
  model: string;
  quality: number;
  pass_rate?: number;
  task_completion: number;
  latency_ms: number;
  latency_p95_ms?: number;
  cost_per_task: number;
  runs: number;
  baseline_quality?: number | null;
  quality_delta?: number | null;
  failure_breakdown?: Record<string, number>;
};

export type RecentRun = {
  id: string;
  task: string;
  agent_type: string;
  provider: string;
  model: string;
  status: string;
  score: number | null;
  failure: string | null;
  latency_ms: number;
  cost_usd: number;
  created_at: string;
};

export type TraceSpan = {
  id: string;
  parent_id: string | null;
  kind: string;
  name: string;
  status: string;
  duration_ms: number;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  attributes: Record<string, unknown>;
  started_at: string;
};

export type RunDetail = RecentRun & {
  spans: TraceSpan[];
  evaluations: Array<{
    score: number;
    passed: boolean;
    failure_classification: string | null;
    rubric: Record<string, unknown>;
    rationale: string;
  }>;
};

export type DashboardData = {
  generated_at: string;
  quality: {
    task_completion: number;
    answer_correctness: number;
    tool_selection_accuracy: number;
    hallucination_rate: number;
    failure_rate: number;
    eval_pass_rate: number;
  };
  operations: {
    runs: number;
    latency_p50_ms: number;
    latency_p95_ms: number;
    tokens: number;
    cost_total: number;
    cost_per_task: number;
    retries: number;
    escalation_rate: number;
  };
  model_comparison: ModelComparison[];
  trend: Array<{ date: string; quality: number; latency_ms: number; runs: number }>;
  failure_breakdown: Record<string, number>;
  evaluation: {
    scenario_count: number;
    methods: Array<{ name: string; status: string; coverage: number }>;
  };
  latest_suite: {
    id: string;
    commit_sha: string;
    scenario_count: number;
    success_rate: number;
    previous_success_rate: number | null;
    baseline_source: "observed_previous_suite" | "no_baseline" | "supplied" | "legacy_unverified" | "synthetic_demo";
    regression_detected: boolean;
    failure_breakdown: Record<string, number>;
    total_cost_usd: number;
    p95_latency_ms: number;
    created_at: string;
  } | null;
  recent_runs: RecentRun[];
};

export type IntegrationHealth =
  | "unconfigured"
  | "unchecked"
  | "healthy"
  | "degraded"
  | "unhealthy"
  | "unknown";

export type Provenance = "LIVE" | "SNAPSHOT" | "SYNTHETIC";

export type Integration = {
  id: string;
  name: string;
  display_name: string;
  configured: boolean;
  health_status: IntegrationHealth;
  provenance: Provenance | null;
  live: boolean;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  latency_ms?: number | null;
  event_count: number;
  metadata: Record<string, unknown>;
};

export type IntegrationsResponse = {
  items: Integration[];
  generated_at: string;
};

export type GoldenScenario = {
  id: string;
  category: "research" | "document" | "support" | "extraction";
  task: string;
  expected_tools: string[];
  tags: string[];
  fixture_kind: "frozen_snapshot" | "synthetic";
  blocking: boolean;
  dataset_version: string;
};

export type GoldenScenariosResponse = {
  total: number;
  items: GoldenScenario[];
};

export type WorkflowStage = {
  name: string;
  description: string;
};

export type WorkflowAgent = {
  id: string;
  name: string;
  description: string;
  complexity: string;
  business_value: string;
  example_task: string;
  tools: string[];
  workflow: WorkflowStage[];
  output_fields: string[];
  scenario_count: number;
};

export type WorkflowScenario = {
  id: string;
  agent_type: string;
  task: string;
  expected_tools: string[];
  output_fields: string[];
  tags: string[];
  fixture_kind: string;
  blocking: boolean;
  provenance: Array<"LIVE" | "SNAPSHOT" | "SYNTHETIC" | "UNVERIFIED">;
  source_urls: string[];
};

export type WorkflowCatalogResponse = {
  dataset_version: string;
  blocking: boolean;
  release_gate_scenario_count: number;
  summary: string;
  agents: WorkflowAgent[];
  scenarios: WorkflowScenario[];
};

export type EvaluationJob = {
  id: string;
  kind: "regression" | "benchmark";
  status: "queued" | "running" | "completed" | "failed";
  request: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  attempts: number;
  queue_backend: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type ProviderAvailability = {
  provider: "mock" | "openai" | "anthropic" | "gemini";
  model: string;
  configured: boolean;
};

export type ExperimentCandidate = {
  id: string;
  label: string;
  provider: string;
  model: string;
  status: string;
  run_count: number;
  quality: number | null;
  pass_rate: number | null;
  task_completion: number | null;
  answer_correctness: number | null;
  tool_selection_accuracy: number | null;
  hallucination_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  total_cost_usd: number;
  cost_per_task: number | null;
  baseline_quality: number | null;
  quality_delta: number | null;
  failure_breakdown: Record<string, number>;
  completed_at: string | null;
};

export type Experiment = {
  id: string;
  name: string;
  dataset_version: string;
  scenario_ids: string[];
  scenario_count: number;
  repetitions: number;
  status: "queued" | "running" | "completed" | "failed";
  baseline_experiment_id: string | null;
  job_id: string | null;
  recommendation: {
    quality_candidate_id?: string;
    fastest_candidate_id?: string;
    lowest_cost_candidate_id?: string;
    summary?: string;
  } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  candidates: ExperimentCandidate[];
};

export type ExperimentFailure = {
  run_id: string;
  candidate_id: string;
  candidate_label: string;
  provider: string;
  model: string;
  scenario_id: string;
  category: string;
  task: string;
  output: string;
  score: number;
  failure_classification: string | null;
  rationale: string;
  rubric: Record<string, unknown>;
};

export type LiveCanary = {
  id: string;
  canary_id: string;
  connector: string;
  status: "passed" | "failed";
  provenance: Provenance | null;
  source_url: string | null;
  latency_ms: number | null;
  content_hash: string | null;
  error: string | null;
  checked_at: string;
};

export type ReviewQueueItem = {
  run_id: string;
  task: string;
  agent_type: string;
  provider: string;
  model: string;
  status: string;
  reason: "failed_run" | "quality_signal";
  created_at: string;
};

export type ReviewQueueResponse = {
  total: number;
  items: ReviewQueueItem[];
};

export type DatasetVersionSummary = {
  id: string;
  dataset_name: string;
  version: number;
  status: string;
  scenario_count: number;
  source_run_id: string | null;
  created_at: string;
};

export type SafetyControl = {
  name: "Scope" | "Authority" | "Budget" | "Verification" | "Containment" | "Auditability";
  status: "enforced" | "active" | "partial" | "not_implemented";
  detail: string;
};

export type SafetyPolicy = {
  id: string;
  agent_type: string;
  version: number;
  enabled: boolean;
  allowed_tools: string[];
  allow_mutating_tools: boolean;
  max_tool_calls: number;
  max_runtime_seconds: number;
  max_estimated_cost_usd: number;
  approval_threshold_usd: number;
  created_at: string;
  updated_at: string;
};

export type SafetyEvent = {
  id: string;
  run_id: string | null;
  policy_id: string | null;
  category: string;
  decision: string;
  action: string;
  reason: string;
  input_hash: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type SafetyCheck = {
  id: string;
  category: string;
  label: string;
  status: "enforced" | "monitored" | "not_implemented";
};

export type SafetySnapshot = {
  generated_at: string;
  execution_enabled: boolean;
  summary: {
    policy_count: number;
    event_count: number;
    blocked_count: number;
    mutating_tools_enabled: number;
  };
  controls: SafetyControl[];
  containment: {
    https_only: boolean;
    private_network_blocked: boolean;
    redirects_blocked: boolean;
    allowed_domains: string[];
    max_response_bytes: number;
    connector_timeout_seconds: number;
    provider_timeout_seconds: number;
    isolated_execution_sandbox: boolean;
    covert_channel_detection: boolean;
  };
  policies: SafetyPolicy[];
  events: SafetyEvent[];
  checks: SafetyCheck[];
};

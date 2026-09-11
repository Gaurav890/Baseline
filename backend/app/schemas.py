from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentType = Literal[
    "research",
    "document",
    "support",
    "extraction",
    "due_diligence",
    "release_risk",
    "incident_response",
]
ProviderType = Literal["mock", "openai", "anthropic", "gemini"]


class RunCreate(BaseModel):
    task: str = Field(min_length=3, max_length=20_000)
    agent_type: AgentType
    provider: ProviderType = "mock"
    model: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    scenario_id: str | None = None


class SpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_id: str | None
    kind: str
    name: str
    status: str
    duration_ms: int
    input: dict[str, Any]
    output: dict[str, Any]
    attributes: dict[str, Any]
    started_at: datetime


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evaluator_type: str
    score: float
    passed: bool
    task_completion: float
    answer_correctness: float
    tool_selection_accuracy: float
    hallucination_rate: float
    failure_classification: str | None
    rubric: dict[str, Any]
    rationale: str


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    agent_type: str
    provider: str
    model: str
    status: str
    planner_output: dict[str, Any]
    final_output: str
    scenario_id: str | None
    external_trace_id: str | None
    experiment_candidate_id: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    retries: int
    escalated: bool
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    spans: list[SpanRead] = Field(default_factory=list)
    evaluations: list[EvaluationRead] = Field(default_factory=list)


class HumanReviewCreate(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    verdict: Literal["pass", "fail", "needs_review"]
    score: float = Field(ge=0, le=1)
    notes: str = Field(default="", max_length=5000)


class SafetyPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_type: str
    version: int
    enabled: bool
    allowed_tools: list[str]
    allow_mutating_tools: bool
    max_tool_calls: int
    max_runtime_seconds: int
    max_estimated_cost_usd: float
    approval_threshold_usd: float
    created_at: datetime
    updated_at: datetime


class SafetyPolicyUpdate(BaseModel):
    enabled: bool | None = None
    allowed_tools: list[str] | None = Field(default=None, max_length=20)
    allow_mutating_tools: bool | None = None
    max_tool_calls: int | None = Field(default=None, ge=0, le=50)
    max_runtime_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_estimated_cost_usd: float | None = Field(default=None, ge=0, le=10_000)
    approval_threshold_usd: float | None = Field(default=None, ge=0, le=10_000_000)

    @model_validator(mode="after")
    def validate_update(self) -> SafetyPolicyUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one policy field must be supplied")
        if self.allowed_tools is not None and len(set(self.allowed_tools)) != len(
            self.allowed_tools
        ):
            raise ValueError("allowed_tools must not contain duplicates")
        return self


class SafetyEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None
    policy_id: str | None
    category: str
    decision: str
    action: str
    reason: str
    input_hash: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    created_at: datetime


class RegressionRequest(BaseModel):
    provider: ProviderType = "mock"
    model: str | None = None
    baseline_success_rate: float | None = Field(default=None, ge=0, le=1)
    threshold: float = Field(default=0.01, ge=0, le=0.25)
    limit: int = Field(default=218, ge=1, le=218)


class BenchmarkRequest(BaseModel):
    scenario_limit: int = Field(default=12, ge=1, le=218)
    providers: list[Literal["openai", "anthropic", "gemini", "mock"]] = Field(
        default_factory=lambda: ["openai", "anthropic", "gemini"]
    )


class JudgeRequest(BaseModel):
    provider: ProviderType = "mock"
    model: str | None = None


JobKind = Literal["regression", "benchmark", "pr_evaluation"]
JobStatus = Literal["queued", "running", "completed", "failed"]


class EvaluationJobCreate(BaseModel):
    kind: JobKind
    regression: RegressionRequest | None = None
    benchmark: BenchmarkRequest | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> EvaluationJobCreate:
        if self.kind in {"regression", "pr_evaluation"} and self.benchmark is not None:
            raise ValueError("benchmark parameters are not valid for this job kind")
        if self.kind == "benchmark" and self.regression is not None:
            raise ValueError("regression parameters are not valid for benchmark jobs")
        return self

    def job_payload(self) -> dict[str, Any]:
        if self.kind in {"regression", "pr_evaluation"}:
            request = self.regression or RegressionRequest()
        else:
            request = self.benchmark or BenchmarkRequest()
        return {
            "kind": self.kind,
            "parameters": request.model_dump(mode="json"),
            "metadata": self.metadata,
        }


class EvaluationJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: JobStatus
    request: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    attempts: int
    max_attempts: int
    lease_expires_at: datetime | None
    queue_backend: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EvaluationJobResult(BaseModel):
    job_id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    display_name: str
    configured: bool
    health_status: str
    provenance: str | None
    live: bool
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    latency_ms: int | None
    event_count: int
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")


class ExperimentCandidateCreate(BaseModel):
    provider: ProviderType
    model: str | None = Field(default=None, min_length=1, max_length=128)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    role: Literal["baseline", "candidate"] = "candidate"


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    dataset_version: str = Field(default="golden-v2", min_length=1, max_length=80)
    scenario_limit: int = Field(default=20, ge=1, le=218)
    scenario_ids: list[str] | None = Field(default=None, min_length=1, max_length=218)
    repetitions: int = Field(default=1, ge=1, le=3)
    candidates: list[ExperimentCandidateCreate] = Field(min_length=2, max_length=4)
    baseline_experiment_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_cohort(self) -> ExperimentCreate:
        identities = [(candidate.provider, candidate.model or "") for candidate in self.candidates]
        if len(set(identities)) != len(identities):
            raise ValueError("experiment candidates must use unique provider/model pairs")
        baseline_count = sum(candidate.role == "baseline" for candidate in self.candidates)
        if baseline_count > 1:
            raise ValueError("experiment candidates may designate only one baseline")
        if self.scenario_ids and len(set(self.scenario_ids)) != len(self.scenario_ids):
            raise ValueError("scenario_ids must not contain duplicates")
        return self


class ExperimentCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    role: str
    provider: str
    model: str
    status: str
    run_count: int
    quality: float | None
    pass_rate: float | None
    task_completion: float | None
    answer_correctness: float | None
    tool_selection_accuracy: float | None
    hallucination_rate: float | None
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    total_cost_usd: float
    cost_per_task: float | None
    baseline_quality: float | None
    quality_delta: float | None
    failure_breakdown: dict[str, int]
    completed_at: datetime | None


class ExperimentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    dataset_version: str
    scenario_ids: list[str]
    scenario_count: int
    repetitions: int
    status: str
    baseline_experiment_id: str | None
    job_id: str | None
    recommendation: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    candidates: list[ExperimentCandidateRead] = Field(default_factory=list)


class IngestedSpan(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    status: str = Field(default="ok", max_length=24)
    duration_ms: int = Field(default=0, ge=0)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProductionTraceCreate(BaseModel):
    external_trace_id: str = Field(min_length=1, max_length=255)
    task: str = Field(min_length=3, max_length=20_000)
    agent_type: AgentType
    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=128)
    status: Literal["completed", "failed"]
    final_output: str = Field(default="", max_length=100_000)
    latency_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=5000)
    spans: list[IngestedSpan] = Field(default_factory=list, max_length=200)


class FeedbackCreate(BaseModel):
    rating: Literal[-1, 0, 1]
    comment: str = Field(default="", max_length=5000)
    source: str = Field(default="product", min_length=1, max_length=80)


class DatasetPromotionCreate(BaseModel):
    dataset_name: str = Field(default="production-failures", min_length=3, max_length=120)
    expected: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    expected_tools: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=lambda: ["production-failure"], max_length=20)

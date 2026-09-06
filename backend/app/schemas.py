from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentType = Literal["research", "document", "support", "extraction"]
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


class RegressionRequest(BaseModel):
    provider: ProviderType = "mock"
    model: str | None = None
    baseline_success_rate: float = Field(default=0.912, ge=0, le=1)
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

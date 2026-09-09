from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float_env(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value else None


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("ENVIRONMENT", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./baseline.db")
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    demo_mode: bool = _bool_env("DEMO_MODE")
    fixture_connectors: bool = _bool_env("BASELINE_FIXTURE_CONNECTORS")
    job_backend: str = os.getenv("JOB_BACKEND", "inline")
    redis_url: str | None = os.getenv("REDIS_URL") or None
    job_queue_name: str = os.getenv("JOB_QUEUE_NAME", "baseline:evaluation-jobs")
    job_max_attempts: int = _int_env("JOB_MAX_ATTEMPTS", 3)
    job_lease_seconds: int = _int_env("JOB_LEASE_SECONDS", 120)
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or None
    tavily_api_key: str | None = os.getenv("TAVILY_API_KEY") or None
    github_token: str | None = os.getenv("GITHUB_TOKEN") or None
    github_app_id: int | None = _optional_int_env("GITHUB_APP_ID")
    github_private_key: str | None = (
        os.getenv("GITHUB_PRIVATE_KEY", "").replace("\\n", "\n") or None
    )
    github_webhook_secret: str | None = os.getenv("GITHUB_WEBHOOK_SECRET") or None
    github_checks_enabled: bool = _bool_env("GITHUB_CHECKS_ENABLED")
    github_repository_allowlist: tuple[str, ...] = _csv_env("GITHUB_REPOSITORY_ALLOWLIST")
    github_check_name: str = os.getenv("GITHUB_CHECK_NAME", "Baseline reliability evaluation")
    github_evaluation_limit: int = _int_env("GITHUB_EVALUATION_LIMIT", 218)
    github_evaluation_provider: str = os.getenv("GITHUB_EVALUATION_PROVIDER", "openai")
    github_evaluation_model: str | None = os.getenv("GITHUB_EVALUATION_MODEL") or None
    sec_user_agent: str | None = os.getenv("SEC_USER_AGENT") or None
    sec_requests_per_second: float = _float_env("SEC_REQUESTS_PER_SECOND", 4.0)
    connector_timeout_seconds: float = _float_env("CONNECTOR_TIMEOUT_SECONDS", 8.0)
    connector_max_response_bytes: int = _int_env("CONNECTOR_MAX_RESPONSE_BYTES", 2_000_000)
    connector_max_retries: int = _int_env("CONNECTOR_MAX_RETRIES", 2)
    connector_allowed_domains: tuple[str, ...] = _csv_env("CONNECTOR_ALLOWED_DOMAINS")
    online_eval_sample_rate: float = _float_env("ONLINE_EVAL_SAMPLE_RATE", 0.1)
    provider_timeout_seconds: float = _float_env("PROVIDER_TIMEOUT_SECONDS", 45.0)
    agent_execution_enabled: bool = _bool_env("AGENT_EXECUTION_ENABLED", default=True)
    safety_admin_key: str | None = os.getenv("SAFETY_ADMIN_KEY") or None
    ingest_api_key: str | None = os.getenv("INGEST_API_KEY") or None
    experiment_max_calls: int = _int_env("EXPERIMENT_MAX_CALLS", 872)
    experiment_max_concurrent: int = _int_env("EXPERIMENT_MAX_CONCURRENT", 3)
    experiment_max_estimated_cost_usd: float = _float_env(
        "EXPERIMENT_MAX_ESTIMATED_COST_USD", 25.0
    )
    experiment_estimated_input_tokens: int = _int_env(
        "EXPERIMENT_ESTIMATED_INPUT_TOKENS", 2000
    )
    experiment_estimated_output_tokens: int = _int_env(
        "EXPERIMENT_ESTIMATED_OUTPUT_TOKENS", 600
    )
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def validate(self) -> None:
        """Fail fast on unsafe or incomplete production configuration."""
        errors: list[str] = []
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        if self.job_backend not in {"inline", "redis"}:
            errors.append("JOB_BACKEND must be either 'inline' or 'redis'")
        if self.job_backend == "redis" and not self.redis_url:
            errors.append("REDIS_URL is required when JOB_BACKEND=redis")
        if self.job_max_attempts < 1 or self.job_lease_seconds < 30:
            errors.append("JOB_MAX_ATTEMPTS must be positive and JOB_LEASE_SECONDS at least 30")
        if self.connector_timeout_seconds <= 0:
            errors.append("CONNECTOR_TIMEOUT_SECONDS must be positive")
        if not 0 < self.sec_requests_per_second <= 10:
            errors.append("SEC_REQUESTS_PER_SECOND must be greater than 0 and at most 10")
        if self.connector_max_response_bytes <= 0:
            errors.append("CONNECTOR_MAX_RESPONSE_BYTES must be positive")
        if self.connector_max_retries < 0:
            errors.append("CONNECTOR_MAX_RETRIES cannot be negative")
        if not 0 <= self.online_eval_sample_rate <= 1:
            errors.append("ONLINE_EVAL_SAMPLE_RATE must be between 0 and 1")
        if self.provider_timeout_seconds <= 0:
            errors.append("PROVIDER_TIMEOUT_SECONDS must be positive")
        if self.experiment_max_calls < 1 or self.experiment_max_concurrent < 1:
            errors.append("Experiment call and concurrency limits must be positive")
        if self.experiment_max_estimated_cost_usd <= 0:
            errors.append("EXPERIMENT_MAX_ESTIMATED_COST_USD must be positive")
        if not 1 <= self.github_evaluation_limit <= 218:
            errors.append("GITHUB_EVALUATION_LIMIT must be between 1 and 218")
        if self.is_production:
            if self.demo_mode:
                errors.append("DEMO_MODE must be disabled in production")
            if self.fixture_connectors:
                errors.append("BASELINE_FIXTURE_CONNECTORS must be disabled in production")
            if self.database_url.startswith("sqlite"):
                errors.append("Production DATABASE_URL must use PostgreSQL")
            if self.job_backend != "redis":
                errors.append("Production JOB_BACKEND must be 'redis'")
            if not self.frontend_origin.startswith("https://"):
                errors.append("Production FRONTEND_ORIGIN must use HTTPS")
            if not self.sec_user_agent or "@" not in self.sec_user_agent:
                errors.append(
                    "Production SEC_USER_AGENT must identify the application and a contact email"
                )
            if not self.ingest_api_key or len(self.ingest_api_key) < 24:
                errors.append("Production INGEST_API_KEY must contain at least 24 characters")
            if not self.safety_admin_key or len(self.safety_admin_key) < 24:
                errors.append("Production SAFETY_ADMIN_KEY must contain at least 24 characters")
            if self.github_checks_enabled:
                if not self.github_webhook_secret:
                    errors.append(
                        "GITHUB_WEBHOOK_SECRET is required when GITHUB_CHECKS_ENABLED=true"
                    )
                if not self.github_token and not (
                    self.github_app_id and self.github_private_key
                ):
                    errors.append(
                        "GITHUB_TOKEN or GITHUB_APP_ID plus GITHUB_PRIVATE_KEY is required "
                        "when GITHUB_CHECKS_ENABLED=true"
                    )
                if not self.github_repository_allowlist:
                    errors.append(
                        "GITHUB_REPOSITORY_ALLOWLIST is required when GITHUB_CHECKS_ENABLED=true"
                    )
                provider_key = {
                    "openai": self.openai_api_key,
                    "anthropic": self.anthropic_api_key,
                    "gemini": self.gemini_api_key,
                }.get(self.github_evaluation_provider)
                if not provider_key:
                    errors.append(
                        "GITHUB_EVALUATION_PROVIDER must name a configured real provider"
                    )
        if errors:
            raise RuntimeError("Invalid configuration: " + "; ".join(errors))


settings = Settings()

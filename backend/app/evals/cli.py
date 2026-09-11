from __future__ import annotations

import argparse
import asyncio

from app.database import Base, SessionLocal, engine
from app.evals.suite import run_regression_suite
from app.schemas import RegressionRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Baseline regression suite")
    parser.add_argument(
        "--provider",
        default="mock",
        choices=["mock", "openai", "anthropic", "gemini"],
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--baseline",
        type=float,
        default=None,
        help="Optional verified comparison rate; omitted runs use persisted matching history.",
    )
    parser.add_argument("--threshold", type=float, default=0.01)
    parser.add_argument("--limit", type=int, default=218)
    parser.add_argument("--all", action="store_true", help="Run all 218 golden scenarios")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    Base.metadata.create_all(bind=engine)
    limit = 218 if args.all else args.limit
    with SessionLocal() as db:
        suite = await run_regression_suite(
            RegressionRequest(
                provider=args.provider,
                model=args.model,
                baseline_success_rate=args.baseline,
                threshold=args.threshold,
                limit=limit,
            ),
            db,
        )
    marker = "REGRESSION" if suite.regression_detected else "PASS"
    print(f"{marker}: {suite.scenario_count} agent evals")
    if suite.previous_success_rate is None:
        print("Previous: no matching persisted baseline")
        print(f"Current:  {suite.success_rate:.1%}")
    else:
        delta = suite.success_rate - suite.previous_success_rate
        print(f"Previous: {suite.previous_success_rate:.1%}")
        print(f"Current:  {suite.success_rate:.1%} ({delta:+.1%})")
    print(f"Failures: {suite.failure_breakdown}")
    print(f"p95 latency: {suite.p95_latency_ms}ms | cost: ${suite.total_cost_usd:.4f}")
    return 1 if suite.regression_detected else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

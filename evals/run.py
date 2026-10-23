"""Run the evaluation cases against real models and save the scored results.

Usage:
    uv run python -m evals.run --model gemini:gemini-3.5-flash-lite
    uv run python -m evals.run --model groq:openai/gpt-oss-120b --task change --pause 20
    uv run python -m evals.run --model chain          # the app's own fallback chain

Uses the development keys from `.env`. Every case gets a fresh chain with the cache off,
so each answer is a real call. Results go to `evals/results/`; `python -m evals.report`
turns them into `evals/RESULTS.md`.
"""

import argparse
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.ai.cache import MemoryCache
from app.ai.chain import FallbackChain
from app.ai.client import AIResult, AttemptOutcome, Provider
from app.ai.contexts import AlternativesContext, ChangeContext, PersonalizeContext
from app.ai.providers import build_providers
from app.ai.schemas import Alternatives, ChangeIntent, PlanSuggestions
from app.ai.tasks import INTERPRET_CHANGE, PERSONALIZE, SUGGEST_ALTERNATIVES
from app.config import Settings
from app.domain.budget import summarize
from evals.cases import (
    ALTERNATIVES_CASES,
    CHANGE_CASES,
    PERSONALIZE_CASES,
    TODAY,
    AlternativesCase,
    ChangeCase,
    PersonalizeCase,
)
from evals.scoring import Verdict, score_alternatives, score_change, score_personalize

logger = logging.getLogger("evals")

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RATE_LIMIT_WAIT_S = 60.0
TASKS = ("change", "personalize", "alternatives")
REJECTED = "answered, but the guardrails rejected the answer and its repair"
PROMPT_OF = {
    "change": "interpret_change",
    "personalize": "personalize",
    "alternatives": "alternatives",
}

type Case = ChangeCase | PersonalizeCase | AlternativesCase


@dataclass(frozen=True)
class CaseResult:
    """One scored answer."""

    task: str
    case_id: str
    passed: bool
    error: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float]
    provider: str | None
    latency_ms: int
    first_try_valid: bool
    attempts: tuple[str, ...]
    answer: dict[str, Any]


def run_case(case: Case, providers: Sequence[Provider]) -> CaseResult:
    """Ask the models for one case and score the answer."""
    chain = FallbackChain(providers, MemoryCache())
    if isinstance(case, ChangeCase):
        context = ChangeContext(plan=case.plan, request=case.request, today=TODAY)
        change = chain.run(INTERPRET_CHANGE, context, use_cache=False)
        return _result("change", case.id, change, lambda: score_change(case, change.value))
    if isinstance(case, PersonalizeCase):
        context_ideas = PersonalizeContext(
            plan=case.plan, today=TODAY, previous_ideas=case.previous_ideas
        )
        ideas = chain.run(PERSONALIZE, context_ideas, use_cache=False)
        return _result(
            "personalize",
            case.id,
            ideas,
            lambda: score_personalize(case, ideas.value, PERSONALIZE.system),
        )
    shortfall = summarize(case.plan).shortfall_inr
    context_alt = AlternativesContext(plan=case.plan, shortfall_inr=shortfall)
    options = chain.run(SUGGEST_ALTERNATIVES, context_alt, use_cache=False)
    return _result(
        "alternatives", case.id, options, lambda: score_alternatives(case, options.value)
    )


def _result(
    task: str, case_id: str, result: AIResult[Any], score: Callable[[], Verdict]
) -> CaseResult:
    provenance = result.provenance
    attempts = tuple(f"{a.provider}: {a.outcome.value}" for a in provenance.attempts)
    error = provenance.fallback and not rejected(attempts)
    if error:
        verdict = Verdict(False, ("no AI answer: " + ", ".join(attempts),))
    elif provenance.fallback:
        verdict = Verdict(False, (REJECTED,))
    else:
        verdict = score()
    value: BaseModel = result.value
    return CaseResult(
        task=task,
        case_id=case_id,
        passed=verdict.passed,
        error=error,
        reasons=verdict.reasons,
        metrics=verdict.metrics,
        provider=provenance.provider,
        latency_ms=provenance.latency_ms,
        first_try_valid=bool(provenance.attempts)
        and provenance.attempts[0].outcome is AttemptOutcome.OK,
        attempts=attempts,
        answer=value.model_dump(mode="json"),
    )


def rejected(attempts: Sequence[str]) -> bool:
    """Whether every attempt was answered but rejected as invalid.

    That is the model's failure (a wrong answer), unlike a timeout or outage, where the
    model never answered.

    Examples:
        >>> rejected(["gemini:x: invalid", "gemini:x: invalid"])
        True
        >>> rejected(["gemini:x: timeout"])
        False
    """
    return bool(attempts) and all(a.endswith(f": {AttemptOutcome.INVALID.value}") for a in attempts)


def run_cases(
    cases: Sequence[Case],
    providers: Sequence[Provider],
    *,
    pause_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[CaseResult]:
    """Run cases one at a time, pausing between calls and waiting out per-minute limits."""
    results = []
    for number, case in enumerate(cases, start=1):
        result = run_case(case, providers)
        if result.error and any("rate_limited" in a for a in result.attempts):
            logger.info("rate limited; waiting %.0f s and retrying %s", RATE_LIMIT_WAIT_S, case.id)
            sleep(RATE_LIMIT_WAIT_S)
            result = run_case(case, providers)
        status = "pass" if result.passed else "ERROR" if result.error else "fail"
        logger.info(
            "[%d/%d] %s %s: %s %s",
            number,
            len(cases),
            result.task,
            case.id,
            status,
            "; ".join(result.reasons),
        )
        results.append(result)
        if number < len(cases):
            sleep(pause_s)
    return results


def rescore(path: Path) -> list[CaseResult]:
    """Score the saved answers in a results file again with the current scoring code.

    The answers are not changed and no model is called; use this after fixing a scoring
    bug. Results with no AI answer stay as they were, except that answers the guardrails
    rejected twice are counted as the model's failure rather than as no answer.
    """
    document, results = _load(path)
    cases = _all_cases()
    updated = [_rescored(result, cases) for result in results]
    _write(path, document, updated, "rescored_at")
    return updated


def _rescored(result: CaseResult, cases: dict[tuple[str, str], Case]) -> CaseResult:
    if result.error and rejected(result.attempts):
        return replace(result, error=False, reasons=(REJECTED,))
    if result.error or result.reasons == (REJECTED,):
        return result
    verdict = _score_saved(cases[(result.task, result.case_id)], result.answer)
    return replace(result, passed=verdict.passed, reasons=verdict.reasons, metrics=verdict.metrics)


def retry_errors(
    path: Path,
    providers: Sequence[Provider],
    *,
    pause_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[CaseResult]:
    """Run again only the cases of a results file that got no answer, and update the file.

    Use it when a quota or outage cut a run short. The prompts of the tasks being retried
    must be the versions the run used, so every answer in the file stays comparable.

    Raises:
        ValueError: If a retried task's prompt changed since the run.
    """
    document, results = _load(path)
    current = _prompt_versions()
    retried = {PROMPT_OF[r.task] for r in results if r.error}
    if any(document["prompt_versions"][name] != current[name] for name in retried):
        raise ValueError("the prompts changed since this run; start a new run instead")
    cases = _all_cases()
    todo = [cases[(r.task, r.case_id)] for r in results if r.error]
    fresh = {
        (r.task, r.case_id): r for r in run_cases(todo, providers, pause_s=pause_s, sleep=sleep)
    }
    updated = [fresh.get((r.task, r.case_id), r) for r in results]
    _write(path, document, updated, "retried_at")
    return updated


def _load(path: Path) -> tuple[dict[str, Any], list[CaseResult]]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    results = [
        CaseResult(**{**r, "reasons": tuple(r["reasons"]), "attempts": tuple(r["attempts"])})
        for r in document["results"]
    ]
    return document, results


def _write(path: Path, document: dict[str, Any], results: Sequence[CaseResult], stamp: str) -> None:
    document["results"] = [asdict(r) for r in results]
    document[stamp] = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _all_cases() -> dict[tuple[str, str], Case]:
    return {(task, case.id): case for task in TASKS for case in select_cases(task)}


def _prompt_versions() -> dict[str, str]:
    return {t.name: t.version for t in (PERSONALIZE, INTERPRET_CHANGE, SUGGEST_ALTERNATIVES)}


def _score_saved(case: Case, answer: dict[str, Any]) -> Verdict:
    if isinstance(case, ChangeCase):
        return score_change(case, ChangeIntent.model_validate(answer))
    if isinstance(case, PersonalizeCase):
        return score_personalize(case, PlanSuggestions.model_validate(answer), PERSONALIZE.system)
    return score_alternatives(case, Alternatives.model_validate(answer))


def select_cases(task: str, only: Sequence[str] = ()) -> list[Case]:
    """Cases for a task (or all tasks), optionally limited to the given ids."""
    groups: dict[str, Sequence[Case]] = {
        "change": CHANGE_CASES,
        "personalize": PERSONALIZE_CASES,
        "alternatives": ALTERNATIVES_CASES,
    }
    chosen = [case for name in TASKS if task in {name, "all"} for case in groups[name]]
    return [case for case in chosen if not only or case.id in only]


def save(results: Sequence[CaseResult], model: str, directory: Path = RESULTS_DIR) -> Path:
    """Write a run's results as JSON, named by date and model."""
    directory.mkdir(parents=True, exist_ok=True)
    run_at = datetime.now(UTC)
    slug = re.sub(r"[^a-z0-9.]+", "-", model.lower()).strip("-")
    path = directory / f"{run_at:%Y-%m-%dT%H%M}-{slug}.json"
    document = {
        "model": model,
        "run_at": run_at.isoformat(timespec="seconds"),
        "prompt_versions": _prompt_versions(),
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--model", help="provider:model, or 'chain'")
    parser.add_argument("--rescore", type=Path, help="re-score a saved results file instead")
    parser.add_argument(
        "--retry-errors", type=Path, help="re-run only the unanswered cases of a results file"
    )
    parser.add_argument("--task", choices=(*TASKS, "all"), default="all")
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between calls")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.rescore:
        rescored = rescore(args.rescore)
        logger.info("%d/%d passed after re-scoring", sum(r.passed for r in rescored), len(rescored))
        return 0
    if args.retry_errors:
        args.model = json.loads(args.retry_errors.read_text(encoding="utf-8"))["model"]
    if not args.model:
        parser.error("--model is required unless --rescore is given")

    settings = Settings()
    if args.model != "chain":
        settings = settings.model_copy(update={"llm_chain": args.model})
    providers = build_providers(settings)
    if not providers:
        logger.error("no provider available for %s (is its API key in .env?)", args.model)
        return 2

    if args.retry_errors:
        retried = retry_errors(args.retry_errors, providers, pause_s=args.pause)
        logger.info("%d/%d passed after retrying", sum(r.passed for r in retried), len(retried))
        return 0
    only = tuple(filter(None, args.only.split(",")))
    results = run_cases(select_cases(args.task, only), providers, pause_s=args.pause)
    path = save(results, settings.llm_chain if args.model == "chain" else args.model)
    passed = sum(r.passed for r in results)
    logger.info("%d/%d passed; saved %s", passed, len(results), path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

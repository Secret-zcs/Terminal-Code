"""Model-assisted self-evolution orchestration for non-interactive runs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from mewcode.client import LLMClient
from mewcode.evolution.auto_review import (
    complete_fork_reviewer_run,
    start_fork_reviewer_run,
)
from mewcode.evolution.fork_reviewer_agent import (
    persist_fork_reviewer_failure,
    persist_fork_reviewer_opinion,
    run_fork_reviewer_agent,
)
from mewcode.evolution.fork_skill_proposer_flow import (
    run_fork_skill_proposer_for_usage,
)


log = logging.getLogger(__name__)


async def run_headless_self_evolution_review(
    *,
    client: LLMClient,
    project_root: str | Path,
    self_evolution_config: Any,
) -> dict:
    """Run proposer, deterministic gates, and reviewer for one CLI review pass."""
    started = start_fork_reviewer_run(project_root, self_evolution_config)
    if started.get("status") != "started":
        return started
    review_run = started.get("review_run")
    review_run_id = str(getattr(review_run, "id", "") or "").strip()
    if not review_run_id:
        return {
            "status": "failed",
            "requests": [],
            "error": "started self-evolution review has no id",
        }

    proposer_result: dict = {}
    try:
        proposer_result = await run_fork_skill_proposer_for_usage(
            client,
            project_root,
            review_run_id=review_run_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("Headless fork Skill proposer failed: %s", exc)
        proposer_result = {
            "status": "failed",
            "generated_candidates": [],
            "failures": [{
                "error_type": type(exc).__name__,
                "error": str(exc)[:4000],
            }],
            "outputs": [],
        }

    result = await asyncio.to_thread(
        complete_fork_reviewer_run,
        project_root,
        review_run_id,
        self_evolution_config,
    )
    result["fork_skill_proposer"] = proposer_result
    if not _result_has_candidates(result):
        return result

    completed_run = result.get("review_run")
    if completed_run is None:
        return result
    try:
        opinion = await run_fork_reviewer_agent(
            client,
            project_root,
            completed_run,
        )
        await asyncio.to_thread(
            persist_fork_reviewer_opinion,
            project_root,
            review_run_id,
            opinion,
        )
        result["fork_agent_review"] = opinion
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("Headless fork Agent review failed: %s", exc)
        result["fork_agent_review"] = {
            "status": "failed",
            "error": str(exc),
        }
        try:
            await asyncio.to_thread(
                persist_fork_reviewer_failure,
                project_root,
                review_run_id,
                exc,
            )
        except Exception as persist_exc:
            log.debug(
                "Headless fork Agent failure persistence failed: %s",
                persist_exc,
            )
    return result


def _result_has_candidates(result: dict) -> bool:
    return bool(
        result.get("requests")
        or result.get("generated_candidates")
        or result.get("blocked_generated_candidates")
    )

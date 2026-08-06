from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_headless_review_runs_proposer_then_complete_then_reviewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mewcode.evolution.headless_review as module

    order: list[str] = []
    review_run = SimpleNamespace(id="review_headless_1")

    monkeypatch.setattr(
        module,
        "start_fork_reviewer_run",
        lambda *_args, **_kwargs: {
            "status": "started",
            "review_run": review_run,
            "requests": [],
        },
    )

    async def fake_proposer(*_args, **_kwargs):
        order.append("proposer")
        return {"status": "generated", "generated_candidates": ["proposal_1"]}

    def fake_complete(*_args, **_kwargs):
        order.append("complete")
        return {
            "status": "submitted",
            "review_run": review_run,
            "requests": [SimpleNamespace(id="approval_1")],
            "generated_candidates": ["proposal_1"],
        }

    async def fake_reviewer(*_args, **_kwargs):
        order.append("reviewer")
        return {"recommendation": "ready-for-user-review"}

    def fake_persist(*_args, **_kwargs):
        order.append("persist")

    monkeypatch.setattr(module, "run_fork_skill_proposer_for_usage", fake_proposer)
    monkeypatch.setattr(module, "complete_fork_reviewer_run", fake_complete)
    monkeypatch.setattr(module, "run_fork_reviewer_agent", fake_reviewer)
    monkeypatch.setattr(module, "persist_fork_reviewer_opinion", fake_persist)

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module.asyncio, "to_thread", run_sync)

    result = await module.run_headless_self_evolution_review(
        client=SimpleNamespace(),
        project_root="/tmp/project",
        self_evolution_config=SimpleNamespace(enabled=True),
    )

    assert order == ["proposer", "complete", "reviewer", "persist"]
    assert result["fork_skill_proposer"]["status"] == "generated"
    assert result["fork_agent_review"]["recommendation"] == "ready-for-user-review"


@pytest.mark.asyncio
async def test_headless_review_proposer_failure_uses_deterministic_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mewcode.evolution.headless_review as module

    order: list[str] = []
    review_run = SimpleNamespace(id="review_headless_fallback")
    monkeypatch.setattr(
        module,
        "start_fork_reviewer_run",
        lambda *_args, **_kwargs: {
            "status": "started",
            "review_run": review_run,
            "requests": [],
        },
    )

    async def failing_proposer(*_args, **_kwargs):
        order.append("proposer-failed")
        raise RuntimeError("provider unavailable")

    def fake_complete(*_args, **_kwargs):
        order.append("complete")
        return {"status": "idle", "review_run": review_run, "requests": []}

    monkeypatch.setattr(module, "run_fork_skill_proposer_for_usage", failing_proposer)
    monkeypatch.setattr(module, "complete_fork_reviewer_run", fake_complete)

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module.asyncio, "to_thread", run_sync)

    result = await module.run_headless_self_evolution_review(
        client=SimpleNamespace(),
        project_root="/tmp/project",
        self_evolution_config=SimpleNamespace(enabled=True),
    )

    assert order == ["proposer-failed", "complete"]
    assert result["fork_skill_proposer"]["status"] == "failed"

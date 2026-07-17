"""Hermes-style self-evolution command.

Usage:
    /evolve
    /evolve observe <summary>
    /evolve propose <title> :: <memory change>
    /evolve list
    /evolve approve <proposal_id>
    /evolve reject <proposal_id>
    /evolve apply <proposal_id>
"""

from __future__ import annotations

from pathlib import Path

from mewcode.commands.registry import Command, CommandContext, CommandType
from mewcode.evolution import EvolutionEngine


def _engine(ctx: CommandContext) -> EvolutionEngine | None:
    agent = ctx.agent
    work_dir = getattr(agent, "work_dir", "") if agent is not None else ""
    if not work_dir:
        return None
    return EvolutionEngine(work_dir)


async def handle_evolve(ctx: CommandContext) -> None:
    engine = _engine(ctx)
    if engine is None:
        ctx.ui.add_system_message("Evolution system is not available.")
        return

    args = ctx.args.strip()
    if not args or args == "help":
        _show_help(ctx)
        return

    parts = args.split(None, 1)
    subcmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "observe":
        _handle_observe(ctx, engine, rest)
    elif subcmd == "propose":
        _handle_propose(ctx, engine, rest)
    elif subcmd == "list":
        _handle_list(ctx, engine)
    elif subcmd == "show":
        _handle_show(ctx, engine, rest)
    elif subcmd == "approve":
        _handle_approve(ctx, engine, rest)
    elif subcmd == "reject":
        _handle_reject(ctx, engine, rest)
    elif subcmd == "apply":
        _handle_apply(ctx, engine, rest)
    else:
        ctx.ui.add_system_message(f"Unknown evolve subcommand: {subcmd}. Use /evolve help.")


def _show_help(ctx: CommandContext) -> None:
    ctx.ui.add_system_message(
        "\n".join([
            "Hermes evolution workflow:",
            "  /evolve observe <summary>",
            "  /evolve propose <title> :: <memory change>",
            "  /evolve list",
            "  /evolve show <proposal_id>",
            "  /evolve approve <proposal_id>",
            "  /evolve reject <proposal_id>",
            "  /evolve apply <proposal_id>",
            "",
            "Only approved memory proposals are applied automatically. Code, prompt,",
            "tool, and skill evolution remain proposal-only until dedicated guards exist.",
        ])
    )


def _handle_observe(
    ctx: CommandContext, engine: EvolutionEngine, summary: str
) -> None:
    if not summary:
        ctx.ui.add_system_message("Usage: /evolve observe <summary>")
        return
    evidence = engine.record_evidence(summary, kind="manual", source="slash-command")
    ctx.ui.add_system_message(f"Evolution evidence recorded: {evidence.id}")


def _handle_propose(ctx: CommandContext, engine: EvolutionEngine, rest: str) -> None:
    if "::" not in rest:
        ctx.ui.add_system_message("Usage: /evolve propose <title> :: <memory change>")
        return
    title, change = [part.strip() for part in rest.split("::", 1)]
    try:
        proposal = engine.propose(
            title=title,
            change=change,
            target="memory",
            rationale="Manual Hermes evolution proposal from slash command.",
        )
    except ValueError as e:
        ctx.ui.add_system_message(str(e))
        return

    validation = engine.validate(proposal)
    warn = f" Warnings: {'; '.join(validation.warnings)}" if validation.warnings else ""
    ctx.ui.add_system_message(
        f"Evolution proposal created: {proposal.id} [{proposal.status}].{warn}"
    )


def _handle_list(ctx: CommandContext, engine: EvolutionEngine) -> None:
    proposals = engine.store.load_proposals()
    if not proposals:
        ctx.ui.add_system_message("No evolution proposals yet.")
        return
    lines = ["Evolution proposals:"]
    for proposal in proposals:
        lines.append(
            f"  {proposal.id} [{proposal.status}/{proposal.risk}] "
            f"{proposal.target}: {proposal.title}"
        )
    ctx.ui.add_system_message("\n".join(lines))


def _handle_show(ctx: CommandContext, engine: EvolutionEngine, proposal_id: str) -> None:
    if not proposal_id:
        ctx.ui.add_system_message("Usage: /evolve show <proposal_id>")
        return
    proposal = engine.store.get_proposal(proposal_id)
    if proposal is None:
        ctx.ui.add_system_message(f"Proposal not found: {proposal_id}")
        return
    validation = engine.validate(proposal)
    lines = [
        f"Proposal: {proposal.id}",
        f"Status: {proposal.status}",
        f"Target: {proposal.target}",
        f"Risk: {proposal.risk}",
        f"Title: {proposal.title}",
        f"Rationale: {proposal.rationale}",
        f"Change: {proposal.change}",
        f"Evidence: {', '.join(proposal.evidence_ids) or '(none)'}",
    ]
    if validation.errors:
        lines.append("Errors: " + "; ".join(validation.errors))
    if validation.warnings:
        lines.append("Warnings: " + "; ".join(validation.warnings))
    ctx.ui.add_system_message("\n".join(lines))


def _handle_approve(ctx: CommandContext, engine: EvolutionEngine, proposal_id: str) -> None:
    if not proposal_id:
        ctx.ui.add_system_message("Usage: /evolve approve <proposal_id>")
        return
    proposal = engine.approve(proposal_id)
    if proposal is None:
        ctx.ui.add_system_message(f"Proposal not found: {proposal_id}")
        return
    ctx.ui.add_system_message(f"Proposal {proposal.id} status: {proposal.status}")


def _handle_reject(ctx: CommandContext, engine: EvolutionEngine, proposal_id: str) -> None:
    if not proposal_id:
        ctx.ui.add_system_message("Usage: /evolve reject <proposal_id>")
        return
    proposal = engine.reject(proposal_id)
    if proposal is None:
        ctx.ui.add_system_message(f"Proposal not found: {proposal_id}")
        return
    ctx.ui.add_system_message(f"Proposal {proposal.id} status: {proposal.status}")


def _handle_apply(ctx: CommandContext, engine: EvolutionEngine, proposal_id: str) -> None:
    if not proposal_id:
        ctx.ui.add_system_message("Usage: /evolve apply <proposal_id>")
        return

    proposal = engine.store.get_proposal(proposal_id)
    if proposal is None:
        ctx.ui.add_system_message(f"Proposal not found: {proposal_id}")
        return
    if proposal.status != "approved":
        ctx.ui.add_system_message(
            f"Proposal {proposal.id} must be approved before apply."
        )
        return

    target_path = engine.project_memory_path
    _checkpoint_before_apply(ctx, target_path, proposal.title)
    ok, message = engine.apply(proposal_id)
    if not ok:
        ctx.ui.add_system_message(f"Evolution apply failed: {message}")
        return
    ctx.ui.add_system_message(
        f"Evolution proposal {proposal.id} applied to {message}."
    )


def _checkpoint_before_apply(
    ctx: CommandContext, target_path: Path, title: str
) -> None:
    agent = ctx.agent
    if agent is None:
        return
    file_history = getattr(agent, "file_history", None)
    if file_history is not None:
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not target_path.exists():
                target_path.write_text("", encoding="utf-8")
            file_history.track_edit(str(target_path))
        except OSError:
            pass

    cm = getattr(agent, "checkpoint_manager", None)
    if cm is None:
        return
    try:
        cm.create_checkpoint(
            label=f"Hermes evolution: {title[:48]}",
            trigger="manual",
            conversation=ctx.conversation,
            agent=agent,
        )
    except Exception:
        pass


EVOLVE_COMMAND = Command(
    name="evolve",
    description="Hermes-style self-evolution proposals",
    type=CommandType.LOCAL,
    handler=handle_evolve,
    usage="/evolve [observe|propose|list|show|approve|reject|apply]",
    aliases=["evolution"],
)

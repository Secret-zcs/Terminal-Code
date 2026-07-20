"""Hermes-style /learn command.

The command distills a reusable workflow into a skill proposal. It never writes
the skill directly; users still add eval cases, evaluate, approve, and promote
the proposal via /evolve.
"""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandType
from mewcode.evolution import EvolutionEngine


def _engine(ctx: CommandContext) -> EvolutionEngine | None:
    agent = ctx.agent
    work_dir = getattr(agent, "work_dir", "") if agent is not None else ""
    if not work_dir:
        return None
    return EvolutionEngine(work_dir)


async def handle_learn(ctx: CommandContext) -> None:
    engine = _engine(ctx)
    if engine is None:
        ctx.ui.add_system_message("Learning system is not available.")
        return

    args = ctx.args.strip()
    if not args or args == "help":
        _show_help(ctx)
        return

    parts = [part.strip() for part in args.split("::", 2)]
    if len(parts) != 3 or not all(parts):
        ctx.ui.add_system_message(
            "Usage: /learn <skill-name> :: <description> :: <skill body>"
        )
        return

    name, description, body = parts
    try:
        action = "patch" if engine.has_project_skill(name) else "create"
        evidence = engine.record_evidence(
            f"/learn {name}: {description}",
            kind="success",
            source="learn-command",
            metadata={"skill": name, "action": action},
        )
        if action == "patch":
            proposal = engine.propose_skill_patch(
                name=name,
                description=description,
                body=body,
                rationale=(
                    "Hermes /learn patched an existing project skill before "
                    "creating a duplicate."
                ),
                evidence_ids=[evidence.id],
            )
        else:
            proposal = engine.propose_skill(
                name=name,
                description=description,
                body=body,
                rationale="Hermes /learn distilled a reusable workflow into a skill.",
                evidence_ids=[evidence.id],
            )
    except ValueError as e:
        ctx.ui.add_system_message(str(e))
        return

    validation = engine.validate(proposal)
    details = []
    if validation.errors:
        details.append("Errors: " + "; ".join(validation.errors))
    if validation.warnings:
        details.append("Warnings: " + "; ".join(validation.warnings))
    suffix = " " + " ".join(details) if details else ""
    ctx.ui.add_system_message(
        f"Learning proposal created: {proposal.id} [{action}/{proposal.status}]."
        f"{suffix} Add an eval case with /evolve add-eval-case {proposal.id}, "
        f"run /evolve eval {proposal.id}, approve with /evolve approve {proposal.id}, "
        f"then promote with /evolve promote {proposal.id}."
    )


def _show_help(ctx: CommandContext) -> None:
    ctx.ui.add_system_message(
        "\n".join([
            "Hermes learning workflow:",
            "  /learn <skill-name> :: <description> :: <skill body>",
            "",
            "If a project skill with the same name exists, /learn creates a patch",
            "proposal. Otherwise it creates a new skill proposal. Both paths still",
            "require add-eval-case, /evolve eval, /evolve approve, and",
            "/evolve promote before writing a formal project skill.",
        ])
    )


LEARN_COMMAND = Command(
    name="learn",
    description="Distill reusable workflow into a Hermes skill proposal",
    type=CommandType.LOCAL,
    handler=handle_learn,
    usage="/learn <skill-name> :: <description> :: <skill body>",
)

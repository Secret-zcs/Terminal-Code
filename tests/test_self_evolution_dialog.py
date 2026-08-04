from __future__ import annotations

from types import SimpleNamespace

from mewcode.self_evolution_dialog import (
    InlineSelfEvolutionInboxWidget,
    InlineSkillApprovalWidget,
    SelfEvolutionInboxChoice,
    SkillApprovalChoice,
)


def test_skill_approval_widget_shows_review_materials() -> None:
    widget = InlineSkillApprovalWidget(
        request_id="approval_123",
        review_markdown="# Self-Evolution Skill Approval\n\nCandidate diff here.",
    )

    content = widget._build_content()

    assert "Self-evolution skill candidate needs approval" in content
    assert "approval_123" in content
    assert "Approve and enable skill" in content
    assert "Reject candidate" in content
    assert "Candidate diff here." in content


def test_skill_approval_widget_emits_approve_and_reject_choices() -> None:
    captured = []
    widget = InlineSkillApprovalWidget(
        request_id="approval_123",
        review_markdown="review",
    )
    widget.post_message = captured.append  # type: ignore[method-assign]

    widget.action_select()

    assert captured[-1].choice == SkillApprovalChoice.APPROVE
    assert captured[-1].request_id == "approval_123"

    widget.action_cursor_down()
    widget.on_key(SimpleNamespace(key="r", stop=lambda: None))
    widget.on_key(SimpleNamespace(key="i", stop=lambda: None))
    widget.action_select()

    assert captured[-1].choice == SkillApprovalChoice.REJECT
    assert captured[-1].reason == "ri"


def test_self_evolution_inbox_widget_shows_inbox_and_actions() -> None:
    widget = InlineSelfEvolutionInboxWidget(
        inbox_markdown="# Self-Evolution Inbox\n\n- generated candidate",
        report_detail_markdown="# Review Report Details\n\ncanary passed",
    )

    content = widget._build_content()

    assert "Self-evolution inbox" in content
    assert "generated candidate" in content
    assert "View report details" in content
    assert "Dismiss inbox" in content


def test_self_evolution_inbox_widget_emits_view_report_and_dismiss() -> None:
    captured = []
    widget = InlineSelfEvolutionInboxWidget(
        inbox_markdown="# Self-Evolution Inbox",
        report_detail_markdown="# Review Report Details\n\ncanary passed",
    )
    widget.post_message = captured.append  # type: ignore[method-assign]

    widget.action_select()

    assert captured[-1].choice == SelfEvolutionInboxChoice.VIEW_REPORT
    assert captured[-1].report_markdown == "# Review Report Details\n\ncanary passed"

    widget.action_cursor_down()
    widget.action_select()

    assert captured[-1].choice == SelfEvolutionInboxChoice.DISMISS
    assert captured[-1].report_markdown == ""

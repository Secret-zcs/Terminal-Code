from __future__ import annotations

from types import SimpleNamespace

from mewcode.self_evolution_dialog import (
    InlineSelfEvolutionMatchWidget,
    InlineSelfEvolutionInboxWidget,
    InlineSkillApprovalWidget,
    SelfEvolutionInboxChoice,
    SelfEvolutionMatchChoice,
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


def test_self_evolution_inbox_widget_hides_report_action_without_detail() -> None:
    captured = []
    widget = InlineSelfEvolutionInboxWidget(
        inbox_markdown="# Self-Evolution Inbox\n\n- generated candidate",
        report_detail_markdown="   ",
    )
    widget.post_message = captured.append  # type: ignore[method-assign]

    content = widget._build_content()

    assert "generated candidate" in content
    assert "View report details" not in content
    assert "Dismiss inbox" in content

    widget.action_cursor_down()
    widget.action_select()

    assert captured[-1].choice == SelfEvolutionInboxChoice.DISMISS
    assert captured[-1].report_markdown == ""


def test_self_evolution_match_widget_shows_match_and_audit_action() -> None:
    widget = InlineSelfEvolutionMatchWidget(
        match_markdown="# Self-Evolution Candidate Skill Matches\n\n- top candidate",
        audit_markdown="# Self-Evolution Candidate Skill Match Audit\n\n- all candidates",
    )

    content = widget._build_content()

    assert "Self-evolution candidate skill match" in content
    assert "top candidate" in content
    assert "View all matches" in content
    assert "Dismiss match hint" in content


def test_self_evolution_match_widget_shows_pending_approval_action() -> None:
    captured = []
    widget = InlineSelfEvolutionMatchWidget(
        match_markdown="# Self-Evolution Candidate Skill Matches\n\n- pending approval",
        approval_request_id="approval_123",
    )
    widget.post_message = captured.append  # type: ignore[method-assign]

    content = widget._build_content()
    widget.action_select()

    assert "Open pending approval" in content
    assert captured[-1].choice == SelfEvolutionMatchChoice.OPEN_APPROVAL
    assert captured[-1].approval_request_id == "approval_123"


def test_self_evolution_match_widget_emits_view_audit_and_dismiss() -> None:
    captured = []
    widget = InlineSelfEvolutionMatchWidget(
        match_markdown="# Self-Evolution Candidate Skill Matches",
        audit_markdown="# Self-Evolution Candidate Skill Match Audit\n\n- all candidates",
    )
    widget.post_message = captured.append  # type: ignore[method-assign]

    widget.action_select()

    assert captured[-1].choice == SelfEvolutionMatchChoice.VIEW_AUDIT
    assert captured[-1].audit_markdown == (
        "# Self-Evolution Candidate Skill Match Audit\n\n- all candidates"
    )

    widget.action_cursor_down()
    widget.action_select()

    assert captured[-1].choice == SelfEvolutionMatchChoice.DISMISS
    assert captured[-1].audit_markdown == ""

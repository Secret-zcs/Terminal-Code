from __future__ import annotations

from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static


class SkillApprovalChoice(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class SelfEvolutionInboxChoice(str, Enum):
    VIEW_REPORT = "view_report"
    DISMISS = "dismiss"


class SelfEvolutionMatchChoice(str, Enum):
    OPEN_APPROVAL = "open_approval"
    VIEW_AUDIT = "view_audit"
    DISMISS = "dismiss"


_OPTIONS = [
    ("Approve and enable skill", SkillApprovalChoice.APPROVE),
    ("Reject candidate", SkillApprovalChoice.REJECT),
]


class InlineSkillApprovalWidget(Vertical, can_focus=True):
    """Inline approval widget for self-evolution skill candidates."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "reject", "Reject", priority=True),
    ]

    class Responded(Message):
        def __init__(
            self,
            request_id: str,
            choice: SkillApprovalChoice,
            reason: str = "",
        ) -> None:
            super().__init__()
            self.request_id = request_id
            self.choice = choice
            self.reason = reason

    def __init__(
        self,
        request_id: str,
        review_markdown: str,
        **kwargs,
    ) -> None:
        super().__init__(id="skill-approval-inline", **kwargs)
        self._request_id = request_id
        self._review_markdown = review_markdown
        self._cursor = 0
        self._reason = ""

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="skill-approval-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        lines = [
            "",
            "[bold yellow]Self-evolution skill candidate needs approval[/bold yellow]",
            f"Request: {self._request_id}",
            "",
            self._review_markdown.strip(),
            "",
            "Do you want to apply this skill?",
            "",
        ]
        for index, (label, _choice) in enumerate(_OPTIONS):
            if index == self._cursor:
                lines.append(f" > {index + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {index + 1}. [dim]{label}[/dim]")

        if self._cursor == 1:
            display = self._reason if self._reason else "[dim]Optional rejection reason...[/dim]"
            lines.append(f"      {display}")

        return "\n".join(lines)

    def _refresh(self) -> None:
        try:
            self.query_one("#skill-approval-content", Static).update(self._build_content())
        except Exception:
            return

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(_OPTIONS) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        _label, choice = _OPTIONS[self._cursor]
        reason = self._reason if choice == SkillApprovalChoice.REJECT else ""
        self.post_message(self.Responded(self._request_id, choice, reason))

    def action_reject(self) -> None:
        self.post_message(
            self.Responded(
                self._request_id,
                SkillApprovalChoice.REJECT,
                self._reason,
            )
        )

    def on_key(self, event) -> None:
        if self._cursor != 1:
            return
        key = event.key
        if key == "backspace":
            if self._reason:
                self._reason = self._reason[:-1]
                self._refresh()
            event.stop()
        elif len(key) == 1 and key.isprintable():
            self._reason += key
            self._refresh()
            event.stop()


class InlineSelfEvolutionInboxWidget(Vertical, can_focus=True):
    """Inline review widget for the self-evolution inbox."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "dismiss", "Dismiss", priority=True),
    ]

    class Responded(Message):
        def __init__(
            self,
            choice: SelfEvolutionInboxChoice,
            report_markdown: str = "",
        ) -> None:
            super().__init__()
            self.choice = choice
            self.report_markdown = report_markdown

    def __init__(
        self,
        inbox_markdown: str,
        report_detail_markdown: str = "",
        **kwargs,
    ) -> None:
        super().__init__(id="self-evolution-inbox-inline", **kwargs)
        self._inbox_markdown = inbox_markdown
        self._report_detail_markdown = report_detail_markdown
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="self-evolution-inbox-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        lines = [
            "",
            "[bold yellow]Self-evolution inbox[/bold yellow]",
            "",
            self._inbox_markdown.strip(),
            "",
            "Choose next action:",
            "",
        ]
        for index, (label, _choice) in enumerate(self._options()):
            if index == self._cursor:
                lines.append(f" > {index + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {index + 1}. [dim]{label}[/dim]")
        return "\n".join(lines)

    def _options(self) -> list[tuple[str, SelfEvolutionInboxChoice]]:
        options: list[tuple[str, SelfEvolutionInboxChoice]] = []
        if self._report_detail_markdown.strip():
            options.append((
                "View report details",
                SelfEvolutionInboxChoice.VIEW_REPORT,
            ))
        options.append(("Dismiss inbox", SelfEvolutionInboxChoice.DISMISS))
        return options

    def _refresh(self) -> None:
        try:
            self.query_one("#self-evolution-inbox-content", Static).update(
                self._build_content()
            )
        except Exception:
            return

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._options()) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        _label, choice = self._options()[self._cursor]
        report = (
            self._report_detail_markdown
            if choice == SelfEvolutionInboxChoice.VIEW_REPORT
            else ""
        )
        self.post_message(self.Responded(choice, report))

    def action_dismiss(self) -> None:
        self.post_message(self.Responded(SelfEvolutionInboxChoice.DISMISS))


class InlineSelfEvolutionMatchWidget(Vertical, can_focus=True):
    """Inline widget for self-evolution candidate match hints."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "dismiss", "Dismiss", priority=True),
    ]

    class Responded(Message):
        def __init__(
            self,
            choice: SelfEvolutionMatchChoice,
            audit_markdown: str = "",
            approval_request_id: str = "",
        ) -> None:
            super().__init__()
            self.choice = choice
            self.audit_markdown = audit_markdown
            self.approval_request_id = approval_request_id

    def __init__(
        self,
        match_markdown: str,
        audit_markdown: str = "",
        approval_request_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(id="self-evolution-match-inline", **kwargs)
        self._match_markdown = match_markdown
        self._audit_markdown = audit_markdown
        self._approval_request_id = approval_request_id.strip()
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="self-evolution-match-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        lines = [
            "",
            "[bold yellow]Self-evolution candidate skill match[/bold yellow]",
            "",
            self._match_markdown.strip(),
            "",
            "Choose next action:",
            "",
        ]
        for index, (label, _choice) in enumerate(self._options()):
            if index == self._cursor:
                lines.append(f" > {index + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {index + 1}. [dim]{label}[/dim]")
        return "\n".join(lines)

    def _options(self) -> list[tuple[str, SelfEvolutionMatchChoice]]:
        options: list[tuple[str, SelfEvolutionMatchChoice]] = []
        if self._approval_request_id:
            options.append((
                "Open pending approval",
                SelfEvolutionMatchChoice.OPEN_APPROVAL,
            ))
        if self._audit_markdown.strip():
            options.append(("View all matches", SelfEvolutionMatchChoice.VIEW_AUDIT))
        options.append(("Dismiss match hint", SelfEvolutionMatchChoice.DISMISS))
        return options

    def _refresh(self) -> None:
        try:
            self.query_one("#self-evolution-match-content", Static).update(
                self._build_content()
            )
        except Exception:
            return

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._options()) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        _label, choice = self._options()[self._cursor]
        audit = (
            self._audit_markdown
            if choice == SelfEvolutionMatchChoice.VIEW_AUDIT
            else ""
        )
        request_id = (
            self._approval_request_id
            if choice == SelfEvolutionMatchChoice.OPEN_APPROVAL
            else ""
        )
        self.post_message(self.Responded(choice, audit, request_id))

    def action_dismiss(self) -> None:
        self.post_message(self.Responded(SelfEvolutionMatchChoice.DISMISS))

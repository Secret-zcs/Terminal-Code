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

"""Task drafts and confirmed Notion capture state."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TaskDraft:
    title: str
    due_date: date | None = None


@dataclass(frozen=True)
class TaskProposal:
    id: str
    update_id: int
    request_text: str
    draft: TaskDraft
    status: str
    page_url: str = ""


@dataclass(frozen=True)
class TaskButton:
    label: str
    callback_data: str


@dataclass(frozen=True)
class TaskResponse:
    text: str
    button_rows: tuple[tuple[TaskButton, ...], ...] = ()


class TaskWriteRejected(RuntimeError):
    """Notion explicitly rejected creating a task."""

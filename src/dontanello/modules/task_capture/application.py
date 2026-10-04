"""Idempotent personal task creation from Telegram into Notion."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime

from .models import TaskButton, TaskDraft, TaskProposal, TaskResponse, TaskWriteRejected
from .parser import parse_task_draft
from .ports import TaskCaptureRepository, TaskWriter


class TaskCaptureApplication:
    def __init__(self, repository: TaskCaptureRepository, writer: TaskWriter):
        self.repository = repository
        self.writer = writer

    def recover_inflight(self) -> None:
        self.repository.recover_inflight()

    def accepts_message(self, text: str, now: datetime, update_id: int) -> bool:
        return (
            self.repository.proposal_for_update(update_id) is not None
            or parse_task_draft(text, now.date()) is not None
        )

    def handle_message(
        self,
        update_id: int,
        text: str,
        now: datetime,
        *,
        title_override: str | None = None,
    ) -> TaskResponse | None:
        existing = self.repository.proposal_for_update(update_id)
        if existing is not None:
            return (
                self._create(existing)
                if existing.status == "pending"
                else self._proposal_response(existing)
            )
        draft = parse_task_draft(text, now.date())
        if draft is None:
            return None
        if not draft.title:
            return TaskResponse(
                "Напиши название задачи после «добавь задачу». Например: "
                "«добавь задачу прочитать презентацию до завтра»."
            )
        if title_override:
            title = " ".join(title_override.split())[:120]
            if title:
                draft = replace(draft, title=title)
        return self.handle_draft(update_id, text, draft)

    def handle_draft(self, update_id: int, request_text: str, draft: TaskDraft) -> TaskResponse:
        existing = self.repository.proposal_for_update(update_id)
        if existing is not None:
            return (
                self._create(existing)
                if existing.status == "pending"
                else self._proposal_response(existing)
            )
        title = " ".join(draft.title.split())[:120]
        if not title:
            return TaskResponse("Не получилось выделить название задачи. Ничего не записал.")
        draft = replace(draft, title=title)
        proposal_id = hashlib.sha256(f"{update_id}:{request_text}".encode()).hexdigest()[:32]
        proposal = TaskProposal(proposal_id, update_id, request_text, draft, "pending")
        self.repository.save_proposal(proposal)
        return self._create(proposal)

    def handle_callback(self, data: str) -> TaskResponse:
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "t":
            return TaskResponse("Эта кнопка уже недействительна.")
        proposal = self.repository.get_proposal(parts[1])
        if proposal is None:
            return TaskResponse("Не нашёл черновик задачи. Отправь запрос ещё раз.")
        action = parts[2]
        if action == "cancel":
            if proposal.status == "pending":
                proposal = replace(proposal, status="cancelled")
                self.repository.save_proposal(proposal)
            return TaskResponse("Добавление задачи отменено. Notion не менял.")
        if action != "add":
            return TaskResponse("Не понял действие кнопки.")
        return self._create(proposal)

    def _create(self, proposal: TaskProposal) -> TaskResponse:
        if proposal.status == "created":
            return self._created_response(proposal)
        if proposal.status == "uncertain":
            return TaskResponse(
                "⚠️ Не могу подтвердить, создалась ли задача. Проверь Tasks в Notion "
                "перед повтором, чтобы не создать дубль."
            )
        if proposal.status == "rejected":
            return TaskResponse(
                "Notion отклонил создание задачи. Проверь её название и попробуй снова."
            )
        if proposal.status == "cancelled":
            return TaskResponse("Добавление задачи отменено.")
        claimed = self.repository.claim_create(proposal.id)
        if claimed is None:
            current = self.repository.get_proposal(proposal.id)
            if current and current.status == "created":
                return self._created_response(current)
            return TaskResponse("Создание этой задачи уже обрабатывается. Подожди немного.")
        try:
            page_url = self.writer.create(claimed.draft)
        except TaskWriteRejected:
            self.repository.finish(proposal.id, "rejected")
            return TaskResponse(
                "Notion отклонил задачу. Проверь поля и попробуй создать её заново."
            )
        except Exception:
            self.repository.finish(proposal.id, "uncertain")
            return TaskResponse(
                "⚠️ Не могу подтвердить, создалась ли задача. Проверь Tasks в Notion "
                "перед повтором, чтобы не создать дубль."
            )
        self.repository.finish(proposal.id, "created", page_url)
        return self._created_response(replace(claimed, status="created", page_url=page_url))

    def _proposal_response(self, proposal: TaskProposal) -> TaskResponse:
        if proposal.status == "created":
            return self._created_response(proposal)
        if proposal.status == "cancelled":
            return TaskResponse("Добавление задачи отменено.")
        if proposal.status == "uncertain":
            return TaskResponse(
                "⚠️ Не могу подтвердить, создалась ли задача. Проверь Tasks в Notion "
                "перед повтором, чтобы не создать дубль."
            )
        if proposal.status == "rejected":
            return TaskResponse(
                "Notion отклонил создание задачи. Проверь её название и попробуй снова."
            )
        due = (
            f"\nСрок: {proposal.draft.due_date:%d.%m.%Y}"
            if proposal.draft.due_date
            else "\nСрок: не указан"
        )
        return TaskResponse(
            f"📋 Создать задачу?\n\n{proposal.draft.title}{due}\nСтатус: Backlog 🐛",
            (
                (
                    TaskButton("✅ Создать", f"t:{proposal.id}:add"),
                    TaskButton("❌ Отмена", f"t:{proposal.id}:cancel"),
                ),
            ),
        )

    @staticmethod
    def _created_response(proposal: TaskProposal) -> TaskResponse:
        due = f"\nСрок: {proposal.draft.due_date:%d.%m.%Y}" if proposal.draft.due_date else ""
        return TaskResponse(
            f"✅ Добавил задачу в Notion: {proposal.draft.title}{due}\n{proposal.page_url}"
        )

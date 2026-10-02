"""Collect and check source-backed evidence for progress reports."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, time, timezone

from .models import Period, ReportItem
from .progress_models import Analysis, Evidence, Finding, StrategySpec

_WHITESPACE = re.compile(r"\s+")
_PROJECT_KEY = re.compile(r"\bREP-\d+\b", re.IGNORECASE)
_INTENT = re.compile(
    r"(?:\bхочу\b|\bпланирую\b|\bпопробую\b|можно было бы|следовало бы|\bwould\b|\bcould\b|\bmight\b|want to|plan to|"
    r"\b(?:нужно|надо|необходимо|предстоит)\b|\b(?:сделаю|реализую|выполню|завершу)\b)",
    re.IGNORECASE,
)
_HYPOTHETICAL = re.compile(
    r"\b(?:возможно|вероятно|наверное|предположительно|perhaps|likely|maybe)\b", re.IGNORECASE
)
_COMPLETION = re.compile(
    r"\b(?:готово|завершил[аи]?|заверш[её]н[аоы]?|выполнил[аи]?|выполнен[аоы]?|доставил[аи]?|доставлен[аоы]?|"
    r"закрыл[аи]?|закрыт[аоы]?|решил[аи]?\s+(?:проблем\w*|задач\w*|ошибк\w*|вопрос\w*)|реш[её]н[аоы]?|реализовал[аи]?|реализован[аоы]?|"
    r"внедрил[аи]?|внедрен[аоы]?|запустил[аи]?|запущен[аоы]?|исправил[аи]?|исправлен[аоы]?|"
    r"наш[её]л\s+(?:и\s+)?подтвержденн\w*\s+причин\w*|работает|сохраняет|"
    r"completed|finished|shipped|delivered|resolved|implemented|fixed|works as intended)\b",
    re.IGNORECASE,
)
_NEGATED_COMPLETION = re.compile(
    r"\b(?:не|not|never|didn't|doesn't)\s+(?:\w+\s+){0,2}"
    r"(?:сделал|создал|создан|решил|реш[её]н|реализовал|реализован|выполнил|выполнен|завершил|заверш[её]н|исправил|исправлен|"
    r"закрыл|работает|сохраняет|готов|completed|implemented|fixed|work|save)\w*",
    re.IGNORECASE,
)
_EXTERNAL_BLOCKER = re.compile(
    r"(?:причин\w* ожидани\w*\s*[:—-]\s*\S.+|ожидани\w*\s*[:—-]\s*(?:SIM|corporate accounts|спис\w* корпоративн\w* аккаунт\w*|аккаунт\w*|одобр\w*|ответ\w*|постав\w*)|"
    r"жд(?:у|ёшь|ёт|ём|ут)\s+(?:ответ\w*|решен\w*|разрешен\w*|одобр\w*|постав\w*|внешн\w*|клиент\w*|команд\w*|человек\w*|SIM\b|corporate accounts\b)|"
    r"ожида(?:ю|ет|ем|ют)\s+(?:ответ\w*|решен\w*|разрешен\w*|одобр\w*|постав\w*|клиент\w*|команд\w*)|"
    r"зависит от\s+(?!меня\b)(?!самого себя\b)[\wА-Яа-я]|"
    r"заблокирован\w*\s+(?:команд|клиент|постав|внешн)|blocked by\s+\w+|waiting for\s+\w+|"
    r"awaiting\s+(?:approval|response|input|review)\s+from\s+\w+|pending approval from\s+\w+|"
    r"dependency on\s+\w+)",
    re.IGNORECASE,
)
_PLAN = re.compile(
    r"(?:\bпланирую\b|\bдоговорил(?:ся|ась)\b|решил(?:а)? продолжить|продолжить|вернуться к|"
    r"следующ(?:ий|им) шаг(?:ом)?\s*[:—-]\s*(?:проверить|добавить|создать|реализовать|внедрить|настроить|"
    r"продолжить|вернуться|завершить|обновить|выполнить|поговорить|написать|найти|определить|разобрать|посмотреть|проанализировать|изучить)|"
    r"next step\s*[:—-]\s*(?:check|add|create|implement|configure|continue|return|finish|update|talk|write)|"
    r"will continue|planned to|plan to|committed to)",
    re.IGNORECASE,
)
_LEARNING = re.compile(
    r"(?:научил\w*|понял\w*|освоил\w*|разобрал\w*|изучил\w*|learned|understood|mastered|figured out|studied)",
    re.IGNORECASE,
)
_EXPOSURE = re.compile(
    r"(?:посмотрел\w*\s+(?:\w+\s+){0,3}(?:курс|урок|разбор)|"
    r"прош[её]л\w*\s+(?:\w+\s+){0,3}(?:курс|урок)|"
    r"начал\w*\s+(?:разбират|изучат|осваиват)|watched\s+(?:a\s+)?(?:course|tutorial))",
    re.IGNORECASE,
)
_MASTERY = re.compile(
    r"(?:освоил\w*|овладел\w*|эксперт\w*|в совершенстве|полностью разобрал\w*|mastered|expert)",
    re.IGNORECASE,
)
_PERSONAL_EVALUATION = re.compile(
    r"(?:молодец|стал\w*\s+(?:более\s+)?(?:дисциплинирован|зрел|уверен|продуктивн)|"
    r"(?:продуктивность|эффективность)\s+(?:вырос|увеличил)|KPI|personality|more disciplined)",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(?:сделал\w*|создал\w*|создан\w*|реализовал\w*|реализован\w*|внедрил\w*|внедр[её]н\w*|обновил\w*|обновл[её]н\w*|настроил\w*|настроен\w*|"
    r"исправил\w*|исправлен\w*|проверил\w*|подтвердил\w*|нашёл\w*|нашел\w*|перевёл\w*|перевел\w*|"
    r"запустил\w*|сохранил\w*|работает|изменил\w*|built|created|implemented|updated|configured|"
    r"fixed|verified|found|launched|saved|works|changed|tested|debugged|completed)\b",
    re.IGNORECASE,
)
_STATUS_PROOF = {
    "external_blocked": _EXTERNAL_BLOCKER,
    "postponed": re.compile(r"(?:отлож\w*|перенес\w*|postponed|deferred)", re.IGNORECASE),
    "in_progress": re.compile(
        r"(?:в процессе|в работе|статус\s*[:—-]\s*(?:активно|в работе|в процессе)|состояние\s*[:—-]\s*(?:активно|в работе|в процессе)|активно|продолжа\w*|in progress|ongoing)",
        re.IGNORECASE,
    ),
    "abandoned": re.compile(
        r"(?:отмен\w*|отказал\w*|прекратил\w*|abandoned|cancelled|canceled)", re.IGNORECASE
    ),
}
_UNFINISHED = re.compile(
    r"(?:не заверш[её]н\w*|не закончен\w*|незакрыт\w*|unfinished|incomplete|still open)",
    re.IGNORECASE,
)
_PARTIAL_RESULT = re.compile(
    r"(?:почти готов|частично реализован|не полностью|partially implemented|almost finished)",
    re.IGNORECASE,
)
_DATE_OR_TIME = re.compile(
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?)"
)


def collect_evidence(items: Iterable[ReportItem]) -> tuple[Evidence, ...]:
    """Convert report items to stable, immutable source evidence."""
    evidence: list[Evidence] = []
    seen: set[str] = set()
    for item in items:
        content_key = "\0".join(
            (
                item.section,
                item.id,
                item.completed_on.isoformat(),
                item.recorded_at,
                item.title,
                item.details,
            )
        )
        revision = hashlib.sha256(content_key.encode("utf-8")).hexdigest()[:24]
        evidence_id = f"ev:{revision}"
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        project_match = _PROJECT_KEY.search(item.title)
        project = project_match.group(0).upper() if project_match else _project_name(item.title)
        text = item.title.strip()
        if item.details.strip():
            text = f"{text}\n{item.details.strip()}" if text else item.details.strip()
        evidence.append(
            Evidence(
                id=evidence_id,
                source_id=item.id,
                project=project,
                occurred_on=item.completed_on,
                recorded_at=item.recorded_at or item.completed_on.isoformat(),
                text=text,
                source_kind=item.section,
                url=item.url,
            )
        )
    return tuple(
        sorted(evidence, key=lambda entry: (entry.occurred_on, entry.recorded_at, entry.id))
    )


def validate_analysis(
    analysis: Analysis,
    strategy: StrategySpec,
    evidence: Sequence[Evidence],
    period: Period,
) -> Analysis:
    """Discard findings with ungrounded citations or unsupported semantic claims."""
    known = {entry.id: entry for entry in evidence}
    allowed_kinds = {kind for section in strategy.sections for kind in section.kinds}
    accepted: list[Finding] = []
    rejected = 0
    for finding in analysis.findings:
        if finding.kind not in allowed_kinds or not finding.citations:
            rejected += 1
            continue
        cited = [known.get(citation.evidence_id) for citation in finding.citations]
        if any(entry is None for entry in cited):
            raise ValueError(
                f"Finding {finding.kind!r} contains an unknown or inexact evidence citation"
            )
        concrete = [entry for entry in cited if entry is not None]
        if any(
            not _quote_is_grounded(citation.quote, entry.text)
            for citation, entry in zip(finding.citations, concrete, strict=True)
        ):
            raise ValueError(
                f"Finding {finding.kind!r} contains an unknown or inexact evidence citation"
            )
        if not _semantics_are_supported(finding, concrete, period):
            rejected += 1
            continue
        accepted.append(finding)
    notices = analysis.notices
    if rejected:
        notices = (*notices, f"Снято неподтверждённых или недопустимых выводов: {rejected}.")
    return Analysis(tuple(accepted), notices, analysis.metrics)


def _quote_is_grounded(quote: str, source: str) -> bool:
    return bool(quote.strip()) and quote in source


def _semantics_are_supported(
    finding: Finding, citations: Sequence[Evidence], period: Period
) -> bool:
    current = [entry for entry in citations if period.start <= entry.occurred_on < period.end]
    current_text = _cited_text(finding, current)
    narrative = f"{finding.text} {finding.before} {finding.action} {finding.after}"
    if not current or _PERSONAL_EVALUATION.search(narrative):
        return False
    if finding.kind != "idea" and _HYPOTHETICAL.search(narrative):
        return False
    if finding.kind in {"achievement", "transformation", "progress"}:
        # A historical intention may be the supported before-state. It must not
        # turn the explicitly observed after-state into an intention again.
        result_narrative = f"{finding.text} {finding.action} {finding.after}"
        if _INTENT.search(result_narrative):
            return False
    result_evidence = current
    if finding.kind in {"achievement", "transformation", "progress"} and finding.after_ids:
        known = {entry.id: entry for entry in current}
        if not set(finding.after_ids).issubset(known):
            return False
        if finding.before_ids:
            if not _ordered_transition(finding, citations, period):
                return False
        elif finding.before:
            return False
        result_evidence = [known[identifier] for identifier in finding.after_ids]
    result_text = _cited_text(finding, result_evidence)
    if finding.kind == "achievement":
        # Work logs describe activity unless their cited excerpt explicitly proves a result.
        return not _PARTIAL_RESULT.search(result_text) and (
            _has_result(result_text)
            or (
                all(
                    entry.source_kind in {"tasks", "goals", "completion"}
                    for entry in result_evidence
                )
                and not _INTENT.search(result_text)
                and not _NEGATED_COMPLETION.search(result_text)
            )
        )
    if finding.kind == "blocker":
        return bool(_EXTERNAL_BLOCKER.search(current_text))
    if finding.kind == "next_step":
        return bool(_PLAN.search(current_text))
    if finding.kind == "learning":
        if _MASTERY.search(narrative) and not _MASTERY.search(current_text):
            return False
        if re.search(r"\bне\s+(?:понял|научил|освоил|разобрал)\w*", current_text, re.IGNORECASE):
            return False
        return bool(_LEARNING.search(current_text) or _EXPOSURE.search(current_text)) or (
            _has_action(current_text) and not _NEGATED_COMPLETION.search(current_text)
        )
    if finding.kind in {"comparison", "trajectory"}:
        return _ordered_transition(finding, citations, period)
    if finding.kind == "transformation":
        return _has_action(result_text) or _has_result(result_text)
    if finding.kind == "idea":
        # An idea may be mentioned as an idea, but cannot be passed off as a completed result.
        return bool(re.search(r"\b(?:иде[яиюйе]\w*|idea|concept)\b", current_text, re.IGNORECASE))
    if finding.kind == "unfinished":
        if finding.status == "unknown":
            return bool(_UNFINISHED.search(current_text))
        proof = _STATUS_PROOF.get(finding.status)
        return (
            proof is not None
            and bool(proof.search(current_text))
            and not (
                finding.status == "external_blocked" and not _EXTERNAL_BLOCKER.search(current_text)
            )
        )
    if finding.kind == "pattern":
        return len({entry.id for entry in current}) >= 2
    if finding.kind == "progress":
        return _has_action(result_text) or _has_result(result_text)
    return bool(citations)


def _ordered_transition(finding: Finding, citations: Sequence[Evidence], period: Period) -> bool:
    if not finding.before_ids or not finding.after_ids:
        return False
    known = {entry.id: entry for entry in citations}
    if not set((*finding.before_ids, *finding.after_ids)).issubset(known):
        return False
    before = [known[identifier] for identifier in finding.before_ids]
    after = [known[identifier] for identifier in finding.after_ids]
    if not any(period.start <= entry.occurred_on < period.end for entry in after):
        return False
    projects = {entry.project.casefold().strip() for entry in (*before, *after)}
    return (
        len(projects) == 1
        and projects != {""}
        and (
            max(_recorded_order(entry) for entry in before)
            < min(_recorded_order(entry) for entry in after)
        )
    )


def _cited_text(finding: Finding, evidence: Sequence[Evidence]) -> str:
    identifiers = {entry.id for entry in evidence}
    return "\n".join(
        citation.quote for citation in finding.citations if citation.evidence_id in identifiers
    )


def _has_result(text: str) -> bool:
    """A planned or negated clause cannot prove completion."""
    return any(
        _COMPLETION.search(clause)
        and not _INTENT.search(clause)
        and not _NEGATED_COMPLETION.search(clause)
        and not _PARTIAL_RESULT.search(clause)
        for clause in _result_clauses(text)
    )


def _has_action(text: str) -> bool:
    return any(
        _ACTION.search(clause)
        and not _INTENT.search(clause)
        and not _NEGATED_COMPLETION.search(clause)
        for clause in _result_clauses(text)
    )


def _result_clauses(text: str) -> list[str]:
    """Separate an observed result from a trailing plan in informal notes."""
    return re.split(
        r"[;.!?\n]+|(?=\b(?:нужно|надо|необходимо|предстоит|планирую)\b)",
        text,
        flags=re.IGNORECASE,
    )


def _project_name(title: str) -> str:
    # Notion snapshots also use dates without a year. Strip them only in
    # timestamp positions, preserving meaningful numbers inside project names.
    value = re.sub(
        r"(?:\b(?:snapshot|снимок)\s+|[—–|]\s*)\d{1,2}[.]\d{2}(?![.]\d)(?:\s+\d{1,2}:\d{2})?",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    value = _DATE_OR_TIME.sub(" ", value)
    value = re.sub(r"\b(?:snapshot|снимок)\b", " ", value, flags=re.IGNORECASE)
    return _WHITESPACE.sub(" ", value).strip(" -–—|:[]()").casefold() or "Без названия"


def _recorded_order(entry: Evidence) -> datetime:
    try:
        value = datetime.fromisoformat(entry.recorded_at.replace("Z", "+00:00"))
    except ValueError:
        value = datetime.combine(entry.occurred_on, time.min)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

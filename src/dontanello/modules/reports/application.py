"""Report periods and presentation rules, independent of external services."""

from collections.abc import Sequence
from datetime import date, timedelta

from .models import Period, ReportItem
from .ports import ReportSource

_SECTION_ORDER = ("tasks", "goals", "work")
_MAX_COMPACT_ITEMS = 10
_MAX_COMPACT_TITLE = 200
_MAX_COMPACT_DETAILS = 300
_SECTION_LABELS = {
    "tasks": "Задачи",
    "goals": "Цели",
    "work": "Работа — активность",
}


def previous_week(today: date) -> Period:
    """Return the previous complete Monday-to-Monday week."""
    this_monday = today - timedelta(days=today.weekday())
    end = this_monday
    return Period("week", end - timedelta(days=7), end)


def previous_month(today: date) -> Period:
    """Return the previous complete calendar month."""
    this_month = today.replace(day=1)
    if this_month.month == 1:
        previous_month_start = this_month.replace(year=this_month.year - 1, month=12)
    else:
        previous_month_start = this_month.replace(month=this_month.month - 1)
    return Period("month", previous_month_start, this_month)


def render_report(period: Period, items: Sequence[ReportItem], compact: bool = True) -> str:
    """Render a concise Russian summary with an explicit empty state per section."""
    display_end = period.end - timedelta(days=1)
    lines = [
        f"Отчёт за {period.start:%d.%m.%Y} — {display_end:%d.%m.%Y}",
        f"Всего записей: {len(items)}",
    ]
    grouped: dict[str, list[ReportItem]] = {section: [] for section in _SECTION_ORDER}
    for item in items:
        grouped.setdefault(item.section, []).append(item)

    for section in _ordered_sections(grouped):
        section_items = sorted(grouped[section], key=_item_order)
        label = _SECTION_LABELS.get(section, section)
        lines.append("")
        lines.append(f"{label} ({len(section_items)}):")
        if not section_items:
            lines.append("  За этот период записей нет.")
            continue
        visible_items = section_items
        if compact and len(section_items) > _MAX_COMPACT_ITEMS:
            visible_items = section_items[-_MAX_COMPACT_ITEMS:]
            lines.append(
                f"  Показаны последние {_MAX_COMPACT_ITEMS} из {len(section_items)}; "
                f"полный список: /{period.kind} full"
            )
        for item in visible_items:
            title = _truncate(item.title, _MAX_COMPACT_TITLE) if compact else item.title
            details = _truncate(item.details, _MAX_COMPACT_DETAILS) if compact else item.details
            lines.append(f"  • {title} — {item.completed_on:%d.%m.%Y}")
            if details:
                lines.append(f"    {details}")
            if item.url:
                lines.append(f"    {item.url}")
    return "\n".join(lines)


def build_report(period: Period, sources: Sequence[ReportSource], compact: bool = True) -> str:
    """Read report items, remove repeated section identities and render them."""
    seen: set[tuple[str, str]] = set()
    items: list[ReportItem] = []
    for source in sources:
        for item in source.items(period):
            identity = (item.id, item.section)
            if identity not in seen:
                seen.add(identity)
                items.append(item)
    return render_report(period, items, compact=compact)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _item_order(item: ReportItem) -> tuple[date, str, str]:
    return item.completed_on, item.title.casefold(), item.id


def _ordered_sections(grouped: dict[str, list[ReportItem]]) -> list[str]:
    present = set(grouped)
    canonical = [section for section in _SECTION_ORDER if section in present]
    return canonical + sorted(present - set(canonical), key=str.casefold)

import unittest
from datetime import date, datetime, timezone

from dontanello.modules.reports.compaction import compact_observations
from dontanello.modules.reports.evidence import collect_evidence, validate_analysis
from dontanello.modules.reports.models import Period, ReportItem
from dontanello.modules.reports.progress import (
    ProgressReports,
    _period_events,
    _previous_month_report,
)
from dontanello.modules.reports.progress_models import (
    Analysis,
    AnalysisMetrics,
    Citation,
    Evidence,
    Finding,
    HistoricalContext,
    ProgressDocument,
    ReportSection,
)
from dontanello.modules.reports.strategies import MonthlyStrategy, WeeklyStrategy


class FakeSource:
    def __init__(self, by_period):
        self.by_period = by_period
        self.calls = []

    def items(self, period):
        self.calls.append(period)
        return self.by_period.get((period.start, period.end), ())


class FakeAnalyzer:
    def __init__(self, callback=None):
        self.callback = callback
        self.calls = []

    def analyze(self, period, evidence, history, strategy):
        self.calls.append((period, tuple(evidence), history, strategy))
        if self.callback:
            return self.callback(period, tuple(evidence), history, strategy)
        first = evidence[-1]
        return Analysis(
            (
                Finding(
                    "progress",
                    first.project,
                    "Подтверждённое изменение внедрено.",
                    (Citation(first.id, first.text),),
                ),
            )
        )


class FakeArchive:
    def __init__(self, cached=None, history=None, race_result=None):
        self.cached = cached
        self.history = history or HistoricalContext()
        self.race_result = race_result
        self.calls = []
        self.saved = {}

    def get(self, scope, period):
        self.calls.append(("get", scope, period))
        return self.cached or self.saved.get((scope, period))

    def context(self, scope, period):
        self.calls.append(("context", scope, period))
        return self.history

    def save(self, scope, document):
        self.calls.append(("save", scope, document.period))
        if self.race_result is not None:
            return self.race_result
        return self.saved.setdefault((scope, document.period), document)


def item(identifier, title, day, section="work", details="", recorded_at=""):
    occurred_on = date.fromisoformat(day)
    return ReportItem(identifier, title, occurred_on, "", section, details, recorded_at)


class ProgressReportsTests(unittest.TestCase):
    def setUp(self):
        self.week = Period("week", date(2026, 9, 21), date(2026, 9, 28))
        self.previous_week = Period("week", date(2026, 9, 14), date(2026, 9, 21))
        self.now = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)

    def test_cached_document_short_circuits_sources_analyzer_and_archive_context(self):
        cached = ProgressDocument(self.week, "cached", (), (), self.now)
        source = FakeSource({})
        analyzer = FakeAnalyzer()
        archive = FakeArchive(cached=cached)

        document = ProgressReports([source], analyzer, archive, "chat", lambda: self.now).build(
            self.week
        )

        self.assertIs(document, cached)
        self.assertEqual(source.calls, [])
        self.assertEqual(analyzer.calls, [])
        self.assertEqual([call[0] for call in archive.calls], ["get"])

    def test_analyzer_receives_compacted_events_while_archive_keeps_raw_records(self):
        rows = [
            item(
                "one",
                "REP-8: Guest flow works",
                "2026-09-22",
                details="Ready.",
                recorded_at="2026-09-22T09:00:00+06:00",
            ),
            item(
                "two",
                "REP-8: Guest flow works",
                "2026-09-22",
                details="Ready!",
                recorded_at="2026-09-22T09:05:00+06:00",
            ),
            item(
                "three",
                "REP-8: Guest flow works",
                "2026-09-22",
                details="Ready",
                recorded_at="2026-09-22T09:10:00+06:00",
            ),
        ]
        source = FakeSource({(self.week.start, self.week.end): rows})
        analyzer = FakeAnalyzer()
        archive = FakeArchive()

        document = ProgressReports([source], analyzer, archive, "chat", lambda: self.now).build(
            self.week
        )

        analyzed = analyzer.calls[0][1]
        self.assertEqual(len(analyzed), 1)
        self.assertEqual(analyzed[0].observation_count, 3)
        self.assertEqual(len(analyzed[0].source_ids), 3)
        self.assertEqual(sum(item.source_kind == "work" for item in document.evidence), 3)
        self.assertEqual(
            sum(item.source_kind == "normalized_event" for item in document.evidence), 1
        )

    def test_month_reuses_weekly_events_and_previous_month_snapshot(self):
        month = Period("month", date(2026, 9, 1), date(2026, 10, 1))
        raw = collect_evidence(
            [
                item("covered", "REP-8: Guest flow", "2026-09-02", details="Implemented flow"),
                item("uncovered", "REP-9: Cache", "2026-09-20", details="Updated cache"),
            ]
        )
        covered = compact_observations((raw[0],))[0]
        week = ProgressDocument(
            Period("week", date(2026, 8, 31), date(2026, 9, 7)),
            "week",
            (),
            (raw[0], covered),
            self.now,
        )
        baseline_event = Evidence(
            "baseline-event",
            "baseline-page",
            "REP-8",
            date(2026, 8, 20),
            "2026-08-20",
            "Initial state",
            "normalized_event",
            source_ids=("baseline-source",),
        )
        baseline = ProgressDocument(
            Period("month", date(2026, 8, 1), date(2026, 9, 1)),
            "month",
            (),
            (baseline_event,),
            self.now,
        )
        context = HistoricalContext(reports=(week, baseline))

        events = _period_events(raw, context, month)

        self.assertEqual(
            {entry.id for entry in events},
            {covered.id, compact_observations((raw[1],))[0].id},
        )
        self.assertIs(_previous_month_report(context, month), baseline)

    def test_passes_all_current_and_baseline_evidence_but_archives_only_current_and_cited_history(
        self,
    ):
        old = item(
            "old",
            "REP-3: Configured cache",
            "2026-09-20",
            details="Implemented in config",
            recorded_at="2026-09-20T15:20:00+06:00",
        )
        current = item(
            "new",
            "REP-3: Configured cache",
            "2026-09-22",
            details="Updated cache and it now works",
            recorded_at="2026-09-22T16:10:00+06:00",
        )
        future = Evidence(
            "future", "future", "rep-3", date(2026, 10, 1), "2026-10-01", "future", "work"
        )
        old_report = ProgressDocument(
            self.previous_week,
            "prior prose must not be evidence",
            (),
            collect_evidence([old]),
            self.now,
        )
        unused_history = Evidence(
            "unused", "u", "rep-99", date(2026, 9, 1), "2026-09-01", "historical fact", "work"
        )
        source = FakeSource(
            {
                (self.week.start, self.week.end): [current],
                (self.previous_week.start, self.previous_week.end): [old],
            }
        )

        def analysis(period, evidence, history, spec):
            self.assertEqual({entry.source_id for entry in evidence}, {"new"})
            self.assertEqual({entry.source_id for entry in history.evidence}, {"old", "u"})
            self.assertEqual(history.reports, (old_report,))
            new_evidence = evidence[0]
            old_evidence = next(entry for entry in history.evidence if entry.source_id == "old")
            return Analysis(
                (
                    Finding(
                        "comparison",
                        "REP-3",
                        "С 20.09 по 22.09 кеш теперь работает.",
                        (
                            Citation(old_evidence.id, "Implemented in config"),
                            Citation(new_evidence.id, "Updated cache and it now works"),
                        ),
                        before_ids=(old_evidence.id,),
                        after_ids=(new_evidence.id,),
                    ),
                )
            )

        archive = FakeArchive(history=HistoricalContext((unused_history, future), (old_report,)))
        reports = ProgressReports(
            [source], FakeAnalyzer(analysis), archive, "chat", lambda: self.now
        )

        document = reports.build(self.week)

        self.assertEqual({entry.source_id for entry in document.evidence}, {"new", "old"})
        self.assertEqual(
            {entry.source_kind for entry in document.evidence}, {"normalized_event", "work"}
        )
        self.assertNotIn(future.id, {entry.id for entry in document.evidence})
        self.assertEqual(source.calls, [self.week, self.previous_week])

    def test_cache_is_first_writer_wins_and_returns_archived_version(self):
        current = item("new", "REP-1: Feature", "2026-09-22", details="Implemented feature")
        winner = ProgressDocument(self.week, "winner", (), (), self.now)
        archive = FakeArchive(race_result=winner)

        document = ProgressReports(
            [FakeSource({(self.week.start, self.week.end): [current]})],
            FakeAnalyzer(),
            archive,
            "chat",
            lambda: self.now,
        ).build(self.week)

        self.assertIs(document, winner)
        self.assertEqual([call[0] for call in archive.calls], ["get", "context", "save"])

    def test_no_current_evidence_skips_analyzer_and_renders_honest_learning_gap(self):
        source = FakeSource({})
        analyzer = FakeAnalyzer()
        reports = ProgressReports([source], analyzer, FakeArchive(), "chat", lambda: self.now)

        document = reports.build(self.week)
        rendered = reports.render(document)

        self.assertEqual(analyzer.calls, [])
        self.assertEqual(document.evidence, ())
        learning = next(section for section in document.sections if section.key == "learning")
        self.assertIn("нет подтверждённых сведений", learning.note)
        self.assertIn("нет сопоставимых датированных данных", rendered.casefold())

    def test_invalid_citation_fails_closed_and_next_step_can_continue_existing_project(self):
        current = item("work", "REP-8: cache", "2026-09-22", details="Updated cache and it works")

        def invalid(period, evidence, history, spec):
            return Analysis(
                (Finding("progress", "REP-8", "Fabricated", (Citation("unknown", "invented"),)),)
            )

        source = FakeSource({(self.week.start, self.week.end): [current]})
        reports = ProgressReports(
            [source], FakeAnalyzer(invalid), FakeArchive(), "chat", lambda: self.now
        )
        with self.assertRaisesRegex(ValueError, "unknown or inexact"):
            reports.build(self.week)

        def unsupported(period, evidence, history, spec):
            evidence_item = evidence[0]
            return Analysis(
                (
                    Finding(
                        "next_step",
                        "REP-8",
                        "Migrate the cache next week.",
                        (Citation(evidence_item.id, evidence_item.text),),
                    ),
                )
            )

        doc = ProgressReports(
            [source], FakeAnalyzer(unsupported), FakeArchive(), "chat", lambda: self.now
        ).build(self.week)
        self.assertTrue(next(section for section in doc.sections if section.key == "next").findings)
        self.assertFalse(any("лимит анализа" in section.note for section in doc.sections))

    def test_duplicate_source_versions_get_distinct_stable_evidence_ids(self):
        first = item(
            "row",
            "REP-9: Snapshot",
            "2026-09-22",
            details="Created initial version",
            recorded_at="2026-09-22T10:00:00+06:00",
        )
        changed = item(
            "row",
            "REP-9: Snapshot",
            "2026-09-22",
            details="Implemented current version",
            recorded_at="2026-09-22T11:00:00+06:00",
        )
        from dontanello.modules.reports.evidence import collect_evidence

        one = collect_evidence([first, changed])
        two = collect_evidence([first, changed])
        self.assertEqual([entry.id for entry in one], [entry.id for entry in two])
        self.assertEqual(len({entry.id for entry in one}), 2)
        self.assertTrue(all(entry.id.startswith("ev:") for entry in one))

    def test_same_day_comparison_uses_recorded_timestamp_and_same_project(self):
        early = item(
            "early",
            "2026-09-22 09:00 — REP-5: Cache",
            "2026-09-22",
            details="Implemented cache",
            recorded_at="2026-09-22T09:00:00+06:00",
        )
        late = item(
            "late",
            "2026-09-22 17:00 — REP-5: Cache",
            "2026-09-22",
            details="Updated cache and it works",
            recorded_at="2026-09-22T17:00:00+06:00",
        )
        source = FakeSource({(self.week.start, self.week.end): [early, late]})

        def analysis(period, evidence, history, spec):
            before, after = evidence
            return Analysis(
                (
                    Finding(
                        "comparison",
                        "REP-5",
                        "Состояние кеша изменилось за день.",
                        (
                            Citation(before.id, "Implemented cache"),
                            Citation(after.id, "Updated cache and it works"),
                        ),
                        before_ids=(before.id,),
                        after_ids=(after.id,),
                    ),
                )
            )

        document = ProgressReports(
            [source], FakeAnalyzer(analysis), FakeArchive(), "chat", lambda: self.now
        ).build(self.week)
        comparisons = next(section for section in document.sections if section.key == "comparison")
        self.assertEqual(len(comparisons.findings), 1)

    def test_week_and_month_have_independent_headings_titles_and_limits(self):
        weekly = WeeklyStrategy()
        monthly = MonthlyStrategy()
        week_titles = [section.title for section in weekly.specification().sections]
        month_titles = [section.title for section in monthly.specification().sections]

        self.assertEqual(len(week_titles), 7)
        self.assertEqual(len(month_titles), 9)
        self.assertEqual(week_titles[0], "🏆 Главное изменение недели")
        self.assertEqual(month_titles[0], "🚀 Главная трансформация месяца")
        self.assertEqual(weekly.title(self.week), "Неделя 21.09.2026–27.09.2026")
        month = Period("month", date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(monthly.title(month), "📆 Сентябрь 2026 — Monthly Progress Review")
        self.assertEqual(weekly.specification().max_words, 700)
        self.assertEqual(monthly.specification().max_words, 1_500)

    def test_oversized_report_is_rejected_before_first_wins_archive_write(self):
        current = item("huge", "REP-12: Notes", "2026-09-22", details="Implemented cache")

        def large(period, evidence, history, spec):
            ref = evidence[0]
            text = "слово " * 800
            return Analysis(
                (Finding("progress", ref.project, text, (Citation(ref.id, "Implemented cache"),)),)
            )

        archive = FakeArchive()
        reports = ProgressReports(
            [FakeSource({(self.week.start, self.week.end): [current]})],
            FakeAnalyzer(large),
            archive,
            "chat",
            lambda: self.now,
        )
        with self.assertRaisesRegex(ValueError, "word limit"):
            reports.build(self.week)
        self.assertNotIn(("save", "chat", self.week), archive.calls)

    def test_analysis_cost_measurements_survive_validation_and_archival(self):
        metrics = AnalysisMetrics(
            "openai/gpt-oss-120b",
            "medium",
            1,
            0,
            100,
            30,
            20,
            records_total=1,
            records_processed=1,
            events_created=1,
            projects_covered=1,
        )
        current = item("work", "REP-1", "2026-09-22", details="Implemented cache")

        def analyze(period, evidence, history, spec):
            ref = evidence[0]
            return Analysis(
                (
                    Finding(
                        "progress",
                        ref.project,
                        "Кеш реализован.",
                        (Citation(ref.id, "Implemented cache"),),
                    ),
                ),
                metrics=metrics,
            )

        reports = ProgressReports(
            [FakeSource({(self.week.start, self.week.end): [current]})],
            FakeAnalyzer(analyze),
            FakeArchive(),
            "chat",
            lambda: self.now,
        )
        document = reports.build(self.week)
        self.assertEqual(document.analysis_metrics, metrics)
        self.assertNotIn(metrics.model, reports.render(document))

    def test_strategy_section_caps_are_independent_and_registry_is_extensible(self):
        findings = tuple(
            Finding(kind, "REP-1", f"Вывод {kind} {number}", ())
            for kind, count in (
                ("progress", 8),
                ("learning", 6),
                ("comparison", 4),
                ("next_step", 6),
            )
            for number in range(count)
        )
        sections = WeeklyStrategy().sections(self.week, Analysis(findings))
        counts = {section.key: len(section.findings) for section in sections}
        self.assertEqual(
            [counts[key] for key in ("progress", "learning", "comparison", "next")], [7, 5, 3, 5]
        )
        custom_period = Period("custom", self.week.start, self.week.end)
        reports = ProgressReports(
            [], FakeAnalyzer(), FakeArchive(), "chat", lambda: self.now, {"custom": WeeklyStrategy}
        )
        self.assertEqual(reports.build(custom_period).period, custom_period)


class EvidenceValidationTests(unittest.TestCase):
    def setUp(self):
        self.week = Period("week", date(2026, 9, 21), date(2026, 9, 28))
        self.month = Period("month", date(2026, 9, 1), date(2026, 10, 1))

    def finding(self, kind, source, text="Подтверждённый вывод.", **fields):
        evidence = collect_evidence([item("row", "REP-1", "2026-09-22", details=source)])[0]
        finding = Finding(kind, evidence.project, text, (Citation(evidence.id, source),), **fields)
        return finding, evidence

    def accepted(self, finding, evidence, month=False):
        strategy = MonthlyStrategy() if month else WeeklyStrategy()
        period = self.month if month else self.week
        return validate_analysis(
            Analysis((finding,)), strategy.specification(), (evidence,), period
        ).findings

    def test_intentions_and_negated_results_are_never_achievements(self):
        for source in (
            "Нужно решить проблему.",
            "Нужно выполнить настройку.",
            "Нужно завершить проект.",
            "Не реализовал кеш.",
            "Кеш не работает.",
            "Исправление нужно реализовать.",
            "Завершение работы запланировано.",
            "Решил продолжить разработку.",
            "Не сделал настройку.",
            "Почти готово.",
            "Кеш частично реализован.",
        ):
            with self.subTest(source=source):
                finding, evidence = self.finding("achievement", source)
                self.assertFalse(self.accepted(finding, evidence))

    def test_passive_completion_and_observed_functionality_are_results(self):
        for source in (
            "Кеш реализован.",
            "Дефект исправлен.",
            "Теперь прекрасно сохраняет изменения.",
        ):
            with self.subTest(source=source):
                finding, evidence = self.finding("achievement", source)
                self.assertEqual(self.accepted(finding, evidence), (finding,))

    def test_event_reference_is_enough_without_literal_quote_but_low_confidence_is_dropped(self):
        evidence = collect_evidence(
            [
                item(
                    "row",
                    "REP-1: Toad automation",
                    "2026-09-22",
                    details="Разбирал существующий .tas",
                )
            ]
        )[0]
        inferred = Finding(
            "learning",
            evidence.project,
            "Продвинулся в понимании структуры существующего .tas.",
            (Citation(evidence.id, ""),),
            confidence="medium",
        )
        speculative = Finding(
            "learning",
            evidence.project,
            "Стал экспертом по Toad.",
            (Citation(evidence.id, ""),),
            confidence="low",
        )

        self.assertEqual(self.accepted(inferred, evidence), (inferred,))
        self.assertFalse(self.accepted(speculative, evidence))

    def test_technical_work_and_course_exposure_support_modest_learning_without_mastery(self):
        course = (
            "Следующий шаг: Посмотрел небольшой курс по тоаду сейчас пытаюсь разобраться "
            "в самом tas файле. Взял diesle.tas, сделаю похожую; Состояние: Активно"
        )
        for source, text in (
            (
                "Настроил обработку файла и проверил сохранение.",
                "Получил практику настройки и проверки сохранения.",
            ),
            (course, "Посмотрел курс по Toad и начал разбираться в файле .tas."),
        ):
            with self.subTest(source=source):
                finding, evidence = self.finding("learning", source, text)
                self.assertEqual(self.accepted(finding, evidence), (finding,))
        finding, evidence = self.finding(
            "learning", course, "Освоил создание .tas файлов в совершенстве."
        )
        self.assertFalse(self.accepted(finding, evidence))

    def test_external_waiting_is_distinct_from_personal_unfinished_work(self):
        for source in (
            "Своя часть готова. Ожидание: SIM",
            "Ожидание: Список корпоративных аккаунтов",
        ):
            finding, evidence = self.finding("blocker", source)
            self.assertEqual(self.accepted(finding, evidence), (finding,))
        finding, evidence = self.finding("blocker", "Состояние: Активно. Продолжаю работу.")
        self.assertFalse(self.accepted(finding, evidence))

    def test_unfinished_logical_status_requires_the_corresponding_evidence(self):
        for status, source in (
            ("external_blocked", "Жду ответа клиента."),
            ("postponed", "Работа отложена."),
            ("in_progress", "Состояние: Активно"),
            ("abandoned", "Работа отменена."),
            ("unknown", "Работа не завершена."),
        ):
            with self.subTest(status=status):
                finding, evidence = self.finding("unfinished", source, status=status)
                self.assertEqual(self.accepted(finding, evidence, month=True), (finding,))
        finding, evidence = self.finding(
            "unfinished", "Состояние: Активно", status="external_blocked"
        )
        self.assertFalse(self.accepted(finding, evidence, month=True))

    def test_historical_learning_and_results_are_not_current_progress(self):
        old = Evidence(
            "old",
            "row-old",
            "REP-1",
            date(2026, 9, 20),
            "2026-09-20",
            "Реализовал кеш и научился настройке.",
            "work",
        )
        current = Evidence(
            "new", "row-new", "REP-1", date(2026, 9, 22), "2026-09-22", "Прочие записи.", "work"
        )
        for kind in ("achievement", "learning", "progress"):
            with self.subTest(kind=kind):
                for citations in (
                    (Citation(old.id, old.text),),
                    (Citation(old.id, old.text), Citation(current.id, current.text)),
                ):
                    finding = Finding(kind, "REP-1", "Новый результат.", citations)
                    result = validate_analysis(
                        Analysis((finding,)),
                        WeeklyStrategy().specification(),
                        (old, current),
                        self.week,
                    )
                    self.assertFalse(result.findings)

    def test_source_quotes_must_be_exact_and_known(self):
        finding, evidence = self.finding("progress", "Настроил кеш.")
        for citation in (
            Citation("missing", "Настроил кеш."),
            Citation(evidence.id, "Настроил  кеш."),
            Citation(evidence.id, "настроил кеш."),
        ):
            invalid = Finding("progress", "REP-1", finding.text, (citation,))
            with self.assertRaisesRegex(ValueError, "unknown or inexact"):
                self.accepted(invalid, evidence)

    def test_comparisons_need_ordered_same_project_current_after_evidence(self):
        before = Evidence(
            "before",
            "a",
            "REP-1",
            date(2026, 9, 22),
            "2026-09-22T12:00:00+06:00",
            "Сначала создан кеш.",
            "work",
        )
        after = Evidence(
            "after",
            "b",
            "REP-1",
            date(2026, 9, 22),
            "2026-09-22T08:00:00+00:00",
            "Теперь кеш проверен.",
            "work",
        )

        def validate(old, new):
            finding = Finding(
                "comparison",
                "REP-1",
                "Кеш создан, затем проверен.",
                (Citation(old.id, old.text), Citation(new.id, new.text)),
                before_ids=(old.id,),
                after_ids=(new.id,),
            )
            return validate_analysis(
                Analysis((finding,)), WeeklyStrategy().specification(), (old, new), self.week
            ).findings

        self.assertTrue(validate(before, after))
        self.assertFalse(validate(after, before))
        other_project = Evidence(
            "after", "b", "REP-2", after.occurred_on, after.recorded_at, after.text, "work"
        )
        self.assertFalse(validate(before, other_project))
        old_after = Evidence(
            "after", "b", "REP-1", date(2026, 9, 20), "2026-09-20", after.text, "work"
        )
        self.assertFalse(validate(before, old_after))

    def test_same_day_broken_to_fixed_result_uses_the_after_evidence(self):
        before, after = collect_evidence(
            [
                item(
                    "before",
                    "REP-1",
                    "2026-09-22",
                    details="Кеш не работает.",
                    recorded_at="2026-09-22T09:00:00+06:00",
                ),
                item(
                    "after",
                    "REP-1",
                    "2026-09-22",
                    details="Дефект исправлен. Теперь кеш работает.",
                    recorded_at="2026-09-22T17:00:00+06:00",
                ),
            ]
        )
        for kind in ("achievement", "progress", "transformation"):
            with self.subTest(kind=kind):
                finding = Finding(
                    kind,
                    "REP-1",
                    "Дефект кеша исправлен.",
                    (
                        Citation(before.id, "Кеш не работает."),
                        Citation(after.id, "Дефект исправлен. Теперь кеш работает."),
                    ),
                    before="Кеш не работает",
                    action="Дефект исправлен",
                    after="Кеш работает",
                    before_ids=(before.id,),
                    after_ids=(after.id,),
                )
                result = validate_analysis(
                    Analysis((finding,)),
                    WeeklyStrategy().specification(),
                    (before, after),
                    self.week,
                )
                self.assertEqual(result.findings, (finding,))
                reversed_finding = Finding(
                    kind,
                    "REP-1",
                    finding.text,
                    finding.citations,
                    before_ids=(after.id,),
                    after_ids=(before.id,),
                )
                reversed_result = validate_analysis(
                    Analysis((reversed_finding,)),
                    WeeklyStrategy().specification(),
                    (before, after),
                    self.week,
                )
                self.assertFalse(reversed_result.findings)

    def test_clause_specific_positive_result_does_not_reuse_negated_or_partial_completion(self):
        for kind in ("achievement", "progress", "transformation"):
            with self.subTest(kind=kind):
                finding, evidence = self.finding(
                    kind,
                    "Кеш не работает. Дефект исправлен. Теперь кеш работает.",
                    "Исправлена работа кеша.",
                )
                self.assertEqual(self.accepted(finding, evidence), (finding,))
                negative, negative_evidence = self.finding(
                    kind, "Не реализовал кеш.", "Кеш реализован."
                )
                self.assertFalse(self.accepted(negative, negative_evidence))
        partial, partial_evidence = self.finding(
            "achievement", "Кеш частично реализован. Исправлен один дефект.", "Кеш полностью готов."
        )
        self.assertFalse(self.accepted(partial, partial_evidence))

    def test_positive_before_cannot_prove_a_negative_after_result(self):
        before = Evidence(
            "before",
            "a",
            "REP-1",
            date(2026, 9, 22),
            "2026-09-22T09:00:00+06:00",
            "Кеш реализован.",
            "work",
        )
        after = Evidence(
            "after",
            "b",
            "REP-1",
            date(2026, 9, 22),
            "2026-09-22T17:00:00+06:00",
            "Теперь кеш не работает.",
            "work",
        )
        for kind in ("achievement", "progress", "transformation"):
            with self.subTest(kind=kind):
                finding = Finding(
                    kind,
                    "REP-1",
                    "Кеш исправлен.",
                    (Citation(before.id, before.text), Citation(after.id, after.text)),
                    before_ids=(before.id,),
                    after_ids=(after.id,),
                )
                result = validate_analysis(
                    Analysis((finding,)),
                    WeeklyStrategy().specification(),
                    (before, after),
                    self.week,
                )
                self.assertFalse(result.findings)


class ProgressRenderingTests(unittest.TestCase):
    def test_structured_movements_dates_groups_status_and_notices_without_internal_ids(self):
        from dontanello.modules.reports.rendering import render_progress

        period = Period("month", date(2026, 9, 1), date(2026, 10, 1))
        old = Evidence(
            "opaque-old",
            "a",
            "REP-1",
            date(2026, 8, 28),
            "2026-08-28",
            "Раньше ручная обработка.",
            "work",
        )
        new = Evidence(
            "opaque-new",
            "b",
            "REP-1",
            date(2026, 9, 22),
            "2026-09-22",
            "Теперь автоматическая обработка.",
            "work",
        )
        citations = (Citation(old.id, old.text), Citation(new.id, new.text))
        main = Finding("transformation", "REP-1", "Появилась автоматическая обработка.", citations)
        comparison = Finding(
            "comparison",
            "REP-1",
            "Процесс изменился.",
            citations,
            before="Ручная обработка",
            action="Добавлена автоматизация",
            after="Автоматическая обработка",
            before_ids=(old.id,),
            after_ids=(new.id,),
        )
        learning = Finding(
            "learning", "REP-1", "Получил опыт настройки.", citations, area="Автоматизация"
        )
        unfinished = Finding(
            "unfinished", "REP-1", "Осталась проверка.", citations, status="in_progress"
        )
        sections = MonthlyStrategy().sections(
            period, Analysis((main, comparison, learning, unfinished))
        )
        sections = (
            ReportSection(
                sections[0].key,
                sections[0].title,
                sections[0].findings,
                "Проверка: часть выводов снята.",
            ),
            *sections[1:],
        )
        document = ProgressDocument(
            period,
            MonthlyStrategy().title(period),
            sections,
            (old, new),
            datetime(2026, 10, 1, tzinfo=timezone.utc),
        )

        rendered = render_progress(document, MonthlyStrategy().specification())

        self.assertNotIn("opaque-", rendered)
        self.assertIn("28.08.2026 → 22.09.2026", rendered)
        self.assertIn(
            "Ручная обработка → Добавлена автоматизация → Автоматическая обработка", rendered
        )
        self.assertIn("Автоматизация:\n", rendered)
        self.assertIn("В работе:", rendered)
        self.assertIn("Проверка: часть выводов снята.", rendered)
        self.assertIn("🚀 Главная трансформация месяца\nПоявилась", rendered)


if __name__ == "__main__":
    unittest.main()


class InformalProgressRegressionTests(unittest.TestCase):
    def setUp(self):
        self.period = Period("week", date(2026, 9, 21), date(2026, 9, 28))
        self.strategy = WeeklyStrategy().specification()

    def test_snapshot_dates_do_not_split_one_project_into_different_projects(self):
        entries = collect_evidence(
            (
                item("first", "Outlook automation — снимок 21.09 12:21", "2026-09-21"),
                item("last", "Outlook automation — снимок 26.09 18:31", "2026-09-26"),
                item("version", "Oracle 12.01 migration", "2026-09-26"),
            )
        )
        self.assertEqual(entries[0].project, entries[1].project)
        self.assertEqual(entries[0].project, "outlook automation")
        self.assertIn("12.01", entries[2].project)

    def test_observed_result_before_trailing_plan_is_progress_not_completed_project(self):
        text = "Проверил все классно работает мне нравится надо посмотреть как можно еще улучшить"
        entry = collect_evidence(
            (item("outlook", "Outlook automation", "2026-09-22", details=text),)
        )[0]
        finding = Finding(
            "progress",
            entry.project,
            "Проверка показала, что получение писем работает.",
            (Citation(entry.id, text),),
        )
        accepted = validate_analysis(Analysis((finding,)), self.strategy, (entry,), self.period)
        self.assertEqual(accepted.findings, (finding,))
        for unsupported in (
            "Надо проверить все работает",
            "Не работает надо проверить",
            "Нужно реализовать сохранение",
        ):
            sample = collect_evidence(
                (item("negative", "Outlook automation", "2026-09-22", details=unsupported),)
            )[0]
            claim = Finding(
                "progress", sample.project, "Функция работает.", (Citation(sample.id, unsupported),)
            )
            self.assertFalse(
                validate_analysis(
                    Analysis((claim,)), self.strategy, (sample,), self.period
                ).findings
            )

    def test_explicit_investigation_plan_is_next_step_but_not_an_idea(self):
        text = "Следующий шаг: Найти текущий SQL и определить источник данных."
        entry = collect_evidence((item("sql", "REP-1", "2026-09-22", details=text),))[0]
        plan = Finding(
            "next_step",
            entry.project,
            "Надо найти SQL и определить источник данных.",
            (Citation(entry.id, text),),
        )
        idea = Finding("idea", entry.project, "Идея найти SQL.", (Citation(entry.id, text),))
        accepted = validate_analysis(Analysis((plan, idea)), self.strategy, (entry,), self.period)
        self.assertEqual(accepted.findings, (plan,))


class ProgressFieldLabelRegressionTests(unittest.TestCase):
    def test_field_label_and_task_creation_do_not_prove_project_progress(self):
        period = Period("week", date(2026, 9, 21), date(2026, 9, 28))
        for text in (
            "Что сделал: Просмотрел список чатов.",
            "Что сделал: Создал новую задачу.",
            "Подготовил идею автоматизации.",
            "Не подготовил описание проблемы.",
            "Не скопировал рабочую версию.",
        ):
            with self.subTest(text=text):
                source = collect_evidence((item("one", "REP-1", "2026-09-22", details=text),))[0]
                claim = Finding(
                    "progress", source.project, "Проект продвинулся.", (Citation(source.id, text),)
                )
                self.assertFalse(
                    validate_analysis(
                        Analysis((claim,)), WeeklyStrategy().specification(), (source,), period
                    ).findings
                )

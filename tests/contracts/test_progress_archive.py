import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from dontanello.modules.reports.adapters.sqlite_progress import SQLiteProgressArchive
from dontanello.modules.reports.models import Period
from dontanello.modules.reports.progress_models import (
    AnalysisMetrics,
    Citation,
    Evidence,
    Finding,
    ProgressDocument,
    ReportSection,
)


def document(
    kind: str,
    start: date,
    end: date,
    evidence_id: str,
    *,
    generated: int = 1,
    schema_version: int = 1,
) -> ProgressDocument:
    evidence = Evidence(
        id=evidence_id,
        source_id="notion-page-" + evidence_id,
        project="Project",
        occurred_on=start,
        recorded_at="2026-09-01T12:00:00+06:00",
        text="The original evidence quote.",
        source_kind="task",
        url="https://example.test/" + evidence_id,
    )
    finding = Finding(
        kind="completed",
        project="Project",
        text="Delivered a milestone.",
        citations=(Citation(evidence_id, "The original evidence quote."),),
        before="before",
        action="action",
        after="after",
        before_ids=(evidence_id,),
        after_ids=(evidence_id,),
        area="engineering",
        status="in_progress",
    )
    return ProgressDocument(
        period=Period(kind, start, end),
        title=f"{kind} report {generated}",
        sections=(ReportSection("results", "Results", (finding,), "section note"),),
        evidence=(evidence,),
        generated_at=datetime(2026, 9, generated, 8, 30, tzinfo=timezone.utc),
        strategy_version="strategy-v2",
        schema_version=schema_version,
    )


class ProgressArchiveContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "progress.sqlite3"
        self.archive = SQLiteProgressArchive(self.path)

    def test_round_trip_survives_restart_with_original_citations_and_evidence(self):
        expected = document("week", date(2026, 9, 7), date(2026, 9, 14), "source-1")
        expected = replace(
            expected,
            analysis_metrics=AnalysisMetrics(
                model="openai/gpt-oss-120b",
                reasoning="medium",
                api_requests=1,
                subagents=0,
                input_tokens=600,
                output_tokens=200,
                cached_input_tokens=500,
            ),
        )
        saved = self.archive.save("personal", expected)

        restarted = SQLiteProgressArchive(self.path)

        self.assertEqual(restarted.get("personal", expected.period), expected)
        self.assertEqual(saved.evidence[0].id, "source-1")
        finding = saved.sections[0].findings[0]
        self.assertEqual(finding.citations, (Citation("source-1", "The original evidence quote."),))
        self.assertEqual(finding.before_ids, ("source-1",))
        self.assertEqual(saved.generated_at, expected.generated_at)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_save_is_first_write_wins_for_scope_and_period(self):
        first = document("week", date(2026, 9, 7), date(2026, 9, 14), "first")
        second = document("week", date(2026, 9, 7), date(2026, 9, 14), "second", generated=2)

        self.assertEqual(self.archive.save("personal", first), first)
        self.assertEqual(self.archive.save("personal", second), first)
        self.assertEqual(self.archive.get("personal", first.period), first)
        self.assertIsNone(self.archive.get("other", first.period))

    def test_context_is_scoped_and_month_contains_its_weeks_prior_months_and_old_facts(self):
        # A boundary week belongs to September and must remain available even
        # when normal history is limited to the preceding twelve months.
        target = Period("month", date(2026, 9, 1), date(2026, 10, 1))
        current_week = document("week", date(2026, 9, 7), date(2026, 9, 14), "current-week")
        previous_month = document("month", date(2026, 8, 1), date(2026, 9, 1), "previous-month")
        older_week = document("week", date(2025, 10, 6), date(2025, 10, 13), "older-week")
        too_old = document("week", date(2025, 8, 4), date(2025, 8, 11), "too-old")
        current_month = document("month", date(2026, 9, 1), date(2026, 10, 1), "same-month")
        future_month = document("month", date(2026, 10, 1), date(2026, 11, 1), "future-month")
        future_week = document("week", date(2026, 9, 28), date(2026, 10, 5), "crossing-future")
        foreign_scope = document("week", date(2026, 9, 7), date(2026, 9, 14), "private")
        for item in (
            current_week,
            previous_month,
            older_week,
            too_old,
            current_month,
            future_month,
            future_week,
        ):
            self.archive.save("personal", item)
        self.archive.save("couples", foreign_scope)

        context = self.archive.context("personal", target)

        self.assertEqual(
            [item.title for item in context.reports],
            ["month report 1", "week report 1"],
        )
        self.assertEqual(
            [(item.period.kind, item.period.start) for item in context.reports],
            [
                ("month", date(2026, 8, 1)),
                ("week", date(2026, 9, 7)),
            ],
        )
        # Evidence attached to included reports stays available through the
        # reports; older facts stay separately available with their IDs intact.
        self.assertEqual([item.id for item in context.evidence], ["older-week"])
        self.assertNotIn("private", {item.id for item in context.evidence})
        self.assertNotIn(
            "future-month", {item.id for report in context.reports for item in report.evidence}
        )
        self.assertNotIn(
            "crossing-future", {item.id for report in context.reports for item in report.evidence}
        )

    def test_weekly_context_excludes_same_and_future_weeks_but_keeps_prior_evidence(self):
        target = Period("week", date(2026, 9, 14), date(2026, 9, 21))
        prior = document("week", date(2026, 9, 7), date(2026, 9, 14), "prior")
        current = document("week", date(2026, 9, 14), date(2026, 9, 21), "current")
        future = document("week", date(2026, 9, 21), date(2026, 9, 28), "future")
        for item in (prior, current, future):
            self.archive.save("personal", item)

        context = self.archive.context("personal", target)

        self.assertEqual(context.reports, (prior,))
        self.assertEqual(context.evidence, ())

    def test_unknown_schema_version_is_rejected_without_downgrade(self):
        initial = document("week", date(2026, 9, 7), date(2026, 9, 14), "source-1")
        self.archive.save("personal", initial)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA user_version = 77")

        with self.assertRaisesRegex(RuntimeError, "Unsupported progress archive schema"):
            self.archive.get("personal", Period("week", date(2026, 9, 7), date(2026, 9, 14)))

        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 77)

    def test_unsupported_document_schema_is_rejected_without_persisting_period(self):
        unsupported = document(
            "week", date(2026, 9, 7), date(2026, 9, 14), "source-1", schema_version=2
        )

        with self.assertRaisesRegex(RuntimeError, "Unsupported progress document schema"):
            self.archive.save("personal", unsupported)

        self.assertIsNone(self.archive.get("personal", unsupported.period))


if __name__ == "__main__":
    unittest.main()

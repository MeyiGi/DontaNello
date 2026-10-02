import copy
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import patch

from dontanello.integrations.groq.client import (
    CompletionResult,
    GroqRateLimitError,
    GroqRequestError,
)
from dontanello.modules.reports.adapters.groq_progress import GroqProgressAnalyzer
from dontanello.modules.reports.models import Period
from dontanello.modules.reports.progress_models import (
    Citation,
    Evidence,
    Finding,
    HistoricalContext,
    ProgressDocument,
    ReportSection,
)
from dontanello.modules.reports.strategies import MonthlyStrategy, WeeklyStrategy


def evidence(identifier, text="Implemented cache and it works", day="2026-09-22", project="REP-1"):
    return Evidence(identifier, identifier, project, date.fromisoformat(day), day, text, "work")


def finding(entry, **changes):
    value = {
        "kind": "progress",
        "project": entry.project,
        "text": "Implemented cache and it works.",
        "citations": [{"evidence_id": entry.id, "quote": entry.text}],
        "before": "",
        "action": "",
        "after": "",
        "before_ids": [],
        "after_ids": [],
        "area": "",
        "status": "",
    }
    value.update(changes)
    return value


def result(*findings, text=None, model="openai/gpt-oss-120b"):
    answer = text if text is not None else json.dumps({"findings": findings, "notices": []})
    return CompletionResult(
        answer,
        model,
        {
            "prompt_tokens": 600,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 500},
        },
    )


class FakeClient:
    model = "openai/gpt-oss-120b"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def structured_complete(self, system, messages, **kwargs):
        self.calls.append(copy.deepcopy({"instructions": system, "input": messages, **kwargs}))
        if not self.responses:
            raise AssertionError("Unexpected extra Groq request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def input_text(call):
    return json.dumps(call["input"], ensure_ascii=False)


def document(period, entries, *sections):
    return ProgressDocument(
        period,
        "Prior report",
        tuple(sections),
        tuple(entries),
        datetime(2026, 9, 21, tzinfo=timezone.utc),
    )


class GroqProgressTests(unittest.TestCase):
    def test_project_result_cannot_use_another_projects_evidence(self):
        entry = evidence("source", project="REP-1")
        client = FakeClient(result(finding(entry, project="REP-2")))
        analysis = self.analyze(client, entries=(entry,), max_rounds=1)
        self.assertEqual(analysis.findings, ())
        self.assertTrue(analysis.notices)

    def test_metrics_record_actual_groq_response_model(self):
        client = FakeClient(result(finding(evidence("source")), model="actual-served-model"))
        analysis = self.analyze(client, entries=(evidence("source"),))
        self.assertEqual(analysis.metrics.model, "actual-served-model")
        self.assertEqual(analysis.metrics.subagents, 0)

    def setUp(self):
        self.week = Period("week", date(2026, 9, 21), date(2026, 9, 28))
        self.month = Period("month", date(2026, 9, 1), date(2026, 10, 1))
        self.current = evidence("current")

    def analyze(self, client, entries=None, history=None, period=None, **options):
        selected_period = period or self.week
        strategy = MonthlyStrategy() if selected_period.kind == "month" else WeeklyStrategy()
        return GroqProgressAnalyzer(client, **options).analyze(
            selected_period,
            tuple(entries) if entries is not None else (self.current,),
            history or HistoricalContext(),
            strategy.specification(),
        )

    def test_key_rotation_attempts_count_toward_shared_request_budget(self):
        bad = finding(self.current, citations=[{"evidence_id": self.current.id, "quote": "absent"}])
        client = FakeClient(
            replace(result(bad), api_requests=2),
            replace(result(finding(self.current)), api_requests=2),
        )
        analysis = self.analyze(client, max_requests=4)
        self.assertEqual(analysis.metrics.api_requests, 4)
        self.assertEqual([c["remaining_requests"] for c in client.calls], [4, 2])
        self.assertEqual(len(analysis.findings), 1)

    def test_failed_rotated_calls_are_counted_when_preserving_verified_result(self):
        unsupported = finding(self.current, kind="comparison", before_ids=[], after_ids=[])
        client = FakeClient(
            result(finding(self.current), unsupported),
            GroqRequestError("Groq transport failed", api_requests=2),
        )
        analysis = self.analyze(client)
        self.assertEqual(analysis.metrics.api_requests, 3)
        self.assertEqual(len(analysis.findings), 1)
        self.assertTrue(analysis.notices)

    def test_valid_week_stops_after_one_call_and_records_usage_metrics(self):
        client = FakeClient(result(finding(self.current)))

        analysis = self.analyze(client)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(analysis.findings), 1)
        self.assertEqual(client.calls[0]["reasoning"], "medium")
        self.assertIsNotNone(analysis.metrics)
        self.assertEqual(analysis.metrics.model, "openai/gpt-oss-120b")
        self.assertEqual(analysis.metrics.api_requests, 1)
        self.assertEqual(analysis.metrics.subagents, 0)
        self.assertEqual(analysis.metrics.input_tokens, 600)
        self.assertEqual(analysis.metrics.output_tokens, 200)
        self.assertEqual(analysis.metrics.cached_input_tokens, 500)

    def test_month_uses_high_reasoning_and_full_raw_facts_without_weekly_prose(self):
        entries = tuple(
            evidence(f"month-{index}", f"Implemented component {index}") for index in range(12)
        )
        prior = document(
            Period("week", date(2026, 9, 14), date(2026, 9, 21)),
            (),
            ReportSection(
                "progress",
                "Progress",
                (Finding("progress", "REP-1", "WEEKLY_PROSE_AUTHORITY", ()),),
            ),
        )
        client = FakeClient(result(finding(entries[-1])))

        self.analyze(client, entries, HistoricalContext(reports=(prior,)), period=self.month)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["reasoning"], "high")
        supplied = input_text(client.calls[0])
        for entry in entries:
            self.assertIn(entry.id, supplied)
            self.assertIn(entry.text, supplied)
        self.assertNotIn("WEEKLY_PROSE_AUTHORITY", supplied)

    def test_reasoning_override_is_explicit_and_static_instructions_are_reusable(self):
        first = FakeClient(result(finding(self.current)))
        other = evidence("other-current", "Created a new checked configuration")
        second = FakeClient(result(finding(other)))

        self.analyze(first, reasoning={"week": "high", "month": "high"}, scope="private-chat-alpha")
        self.analyze(
            second,
            (other,),
            reasoning={"week": "high", "month": "high"},
            scope="private-chat-beta",
        )

        self.assertEqual(first.calls[0]["reasoning"], "high")
        self.assertEqual(first.calls[0]["instructions"], second.calls[0]["instructions"])
        self.assertNotIn(self.current.text, first.calls[0]["instructions"])
        self.assertIn(self.current.text, input_text(first.calls[0]))
        self.assertNotIn("private-chat", input_text(first.calls[0]))
        self.assertNotIn("private-chat", input_text(second.calls[0]))

    def test_all_current_records_are_supplied_even_when_historical_limit_is_small(self):
        entries = tuple(
            evidence(f"current-{index}", f"Implemented component {index}") for index in range(12)
        )
        old = tuple(evidence(f"old-{index}", day=f"2026-08-{index + 1:02d}") for index in range(20))
        client = FakeClient(result(finding(entries[-1])))

        self.analyze(client, entries, HistoricalContext(old), max_history_records=4)

        supplied = input_text(client.calls[0])
        for entry in entries:
            self.assertIn(entry.id, supplied)
            self.assertIn(entry.text, supplied)

    def test_oversized_current_evidence_fails_before_request_instead_of_dropping_records(self):
        huge = evidence("huge", "Implemented " + "source-proof " * 1_000)
        client = FakeClient(result(finding(huge)))

        with self.assertRaises(ValueError):
            self.analyze(client, (huge,), max_input_chars=4_000)

        self.assertEqual(client.calls, [])

    def test_project_endpoints_and_monthly_weekly_learning_keep_original_proof_available(self):
        oldest = evidence("oldest-proof", "Configured initial cache", "2026-07-01")
        latest = evidence("latest-proof", "Updated cache configuration", "2026-09-20")
        learning_proof = evidence("learning-proof", "Learned cache invalidation", "2026-08-31")
        learning = Finding(
            "learning",
            "REP-1",
            "Learned cache invalidation",
            (Citation(learning_proof.id, learning_proof.text),),
            area="caching",
        )
        prior = document(
            Period("month", date(2026, 8, 1), date(2026, 9, 1)),
            (learning_proof,),
            ReportSection("learning", "Learning", (learning,)),
        )
        weekly_proof = evidence("weekly-learning-proof", "Learned cache expiry", "2026-09-19")
        weekly_learning = Finding(
            "learning",
            "REP-1",
            "Learned cache expiry",
            (Citation(weekly_proof.id, weekly_proof.text),),
            area="caching",
        )
        ungrounded_learning = Finding(
            "learning",
            "REP-1",
            "UNGROUNDED_WEEKLY_LEARNING",
            (Citation(weekly_proof.id, "quote absent from original source"),),
        )
        prior_week = document(
            Period("week", date(2026, 9, 14), date(2026, 9, 21)),
            (weekly_proof,),
            ReportSection("learning", "Learning", (weekly_learning, ungrounded_learning)),
        )
        middle = tuple(
            evidence(f"middle-{index}", day=f"2026-08-{index + 1:02d}") for index in range(20)
        )
        client = FakeClient(result(finding(self.current)))

        self.analyze(
            client,
            history=HistoricalContext((oldest, *middle, latest), (prior, prior_week)),
        )

        supplied = input_text(client.calls[0])
        for entry in (oldest, latest, learning_proof, weekly_proof):
            self.assertIn(entry.id, supplied)
            self.assertIn(entry.text, supplied)
        self.assertNotIn("UNGROUNDED_WEEKLY_LEARNING", supplied)

    def test_invalid_quote_gets_targeted_repair_with_previous_answer_preserved(self):
        invalid = finding(
            self.current, citations=[{"evidence_id": self.current.id, "quote": "INVENTED_QUOTE"}]
        )
        first_response = result(invalid)
        client = FakeClient(first_response, result(finding(self.current)))

        analysis = self.analyze(client)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(analysis.findings), 1)
        continuation = client.calls[1]["input"]
        self.assertIsInstance(continuation, list)
        self.assertIn({"role": "assistant", "content": first_response.text}, continuation)
        self.assertEqual(client.calls[0]["instructions"], client.calls[1]["instructions"])
        feedback = continuation[-1]
        self.assertEqual(feedback.get("role"), "user")
        self.assertTrue(
            any(word in json.dumps(feedback).lower() for word in ("citation", "quote", "цитат"))
        )
        self.assertEqual(analysis.findings[0].citations[0].quote, self.current.text)

    def test_round_budget_returns_verified_subset_and_preserves_supported_previous_findings(self):
        unsupported = finding(
            self.current,
            text="Unsupported claim",
            citations=[{"evidence_id": "absent", "quote": "fabricated"}],
        )
        client = FakeClient(
            result(finding(self.current), unsupported),
            result(unsupported),
            result(unsupported),
        )

        analysis = self.analyze(client, max_rounds=3)

        self.assertEqual(len(client.calls), 3)
        self.assertEqual(
            [item.text for item in analysis.findings], ["Implemented cache and it works."]
        )
        self.assertTrue(analysis.notices)
        self.assertEqual(analysis.metrics.api_requests, 3)
        self.assertEqual(analysis.metrics.input_tokens, 1_800)
        self.assertEqual(analysis.metrics.output_tokens, 600)

    def test_malformed_only_responses_fail_closed_with_no_raw_body_in_error(self):
        client = FakeClient(*(result(text="PRIVATE_REMOTE_BODY not json") for _ in range(3)))

        with self.assertRaises((ValueError, RuntimeError)) as raised:
            self.analyze(client)

        self.assertLessEqual(len(client.calls), 3)
        self.assertNotIn("PRIVATE_REMOTE_BODY", str(raised.exception))

    def test_remote_failure_is_sanitized_and_has_no_summary_fallback(self):
        client = FakeClient(RuntimeError("PRIVATE_REMOTE_BODY fixture-key"))

        with self.assertRaises(RuntimeError) as raised:
            self.analyze(client)

        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("PRIVATE_REMOTE_BODY", str(raised.exception))
        self.assertNotIn("fixture-key", str(raised.exception))

    def test_comparison_requires_chronological_same_project_original_evidence(self):
        before = evidence("before", "Configured initial cache", "2026-09-20")
        comparison = finding(
            self.current,
            kind="comparison",
            text="From 2026-09-20 to 2026-09-22 the cache was implemented.",
            before="Configured initial cache",
            after=self.current.text,
            citations=[
                {"evidence_id": before.id, "quote": before.text},
                {"evidence_id": self.current.id, "quote": self.current.text},
            ],
            before_ids=[before.id],
            after_ids=[self.current.id],
        )
        client = FakeClient(result(comparison))
        analysis = self.analyze(client, history=HistoricalContext((before,)))
        self.assertEqual(len(analysis.findings), 1)

        for old, changes in (
            (replace(before, project="REP-OTHER"), {}),
            (before, {"before_ids": [self.current.id], "after_ids": [before.id]}),
        ):
            with self.subTest(old=old.project, changes=changes):
                bad = {**comparison, **changes}
                client = FakeClient(result(finding(self.current), bad))
                analysis = self.analyze(client, history=HistoricalContext((old,)), max_rounds=1)
                self.assertEqual([value.kind for value in analysis.findings], ["progress"])

    def test_bounded_relevant_history_is_supplied_without_tools(self):
        history = tuple(
            evidence(
                f"historical-{index}",
                f"Configured cache version {index}",
                f"2026-08-{index + 1:02d}",
            )
            for index in range(12)
        )
        other = evidence("private-other", "PRIVATE_OTHER_PROJECT", "2026-08-01", "OTHER")
        client = FakeClient(result(finding(self.current)))
        self.analyze(client, history=HistoricalContext((*history, other)), max_history_records=4)
        payload = json.loads(client.calls[0]["input"][0]["content"])
        supplied = payload["historical_facts"]
        self.assertEqual(len(supplied), 4)
        self.assertIn(history[0].id, {item["id"] for item in supplied})
        self.assertIn(history[-1].id, {item["id"] for item in supplied})
        self.assertNotIn("PRIVATE_OTHER_PROJECT", input_text(client.calls[0]))
        self.assertGreater(payload["historical_records_omitted"], 0)
        self.assertNotIn("tools", client.calls[0])

    def test_citation_to_unsupplied_history_is_rejected(self):
        history = tuple(
            evidence(f"old-{index}", day=f"2026-08-{index + 1:02d}") for index in range(5)
        )
        client = FakeClient(result(finding(history[2])))
        analysis = self.analyze(
            client, history=HistoricalContext(history), max_history_records=2, max_rounds=1
        )
        self.assertEqual(analysis.findings, ())
        self.assertTrue(analysis.notices)

    def test_future_history_and_conflicting_identities_are_rejected(self):
        future = evidence("future", "PRIVATE_FUTURE", "2026-10-02")
        client = FakeClient(result(finding(self.current)))
        self.analyze(client, history=HistoricalContext((future,)))
        self.assertNotIn("PRIVATE_FUTURE", input_text(client.calls[0]))
        conflict = replace(self.current, text="Conflicting original source")
        with self.assertRaisesRegex(ValueError, "identity conflict"):
            self.analyze(FakeClient(), history=HistoricalContext((conflict,)))

    def test_failure_after_verified_response_preserves_supported_findings(self):
        bad = finding(self.current, citations=[{"evidence_id": "absent", "quote": "invented"}])
        client = FakeClient(result(finding(self.current), bad), RuntimeError("PRIVATE_REMOTE_BODY"))
        analysis = self.analyze(client)
        self.assertEqual(
            [item.text for item in analysis.findings], ["Implemented cache and it works."]
        )
        self.assertTrue(analysis.notices)

    def test_configuration_budgets_and_reasoning_are_validated(self):
        for options in (
            {"max_rounds": 4},
            {"max_history_records": -1},
            {"max_input_chars": 0},
            {"reasoning": {"week": "unsupported"}},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                GroqProgressAnalyzer(FakeClient(), **options)

    def test_large_context_batches_every_original_and_synthesizes_from_exact_quotes(self):
        entries = tuple(
            evidence(
                f"batch-{index}",
                "Implemented cache " + "original detail " * 40,
                project=f"REP-{index % 3}",
            )
            for index in range(20)
        )
        by_id = {entry.id: entry for entry in entries}

        class BatchClient(FakeClient):
            def structured_complete(self, system, messages, **kwargs):
                self.calls.append(
                    copy.deepcopy({"instructions": system, "input": messages, **kwargs})
                )
                payload = json.loads(messages[0]["content"])
                entry = by_id[payload["current_facts"][0]["id"]]
                value = finding(
                    entry, citations=[{"evidence_id": entry.id, "quote": "Implemented cache"}]
                )
                if "verified_candidates" in payload:
                    value.update(
                        kind="transformation", project="", text="Implemented a working cache."
                    )
                return result(value)

        client = BatchClient()
        analysis = self.analyze(client, entries=entries, max_batch_chars=7000)
        seen = []
        for call in client.calls[:-1]:
            payload = json.loads(call["input"][0]["content"])
            seen.extend(item["id"] for item in payload["current_facts"])
            self.assertEqual(call["reasoning"], "low")
            self.assertNotIn(
                "transformation",
                call["output_schema"]["properties"]["findings"]["items"]["properties"]["kind"][
                    "enum"
                ],
            )
        self.assertCountEqual(seen, [entry.id for entry in entries])
        final = json.loads(client.calls[-1]["input"][0]["content"])
        self.assertIn("verified_candidates", final)
        self.assertEqual(client.calls[-1]["reasoning"], "medium")
        self.assertTrue(all(item["text"] == "Implemented cache" for item in final["current_facts"]))
        self.assertEqual(analysis.findings[0].kind, "transformation")
        self.assertEqual(analysis.metrics.api_requests, len(client.calls))
        self.assertLessEqual(len(client.calls), 48)

    def test_batch_preflight_fails_before_request_without_dropping_large_original(self):
        entries = (
            self.current,
            evidence("too-large", "Implemented " + "private original detail " * 300),
        )
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "original fact exceeds"):
            self.analyze(client, entries=entries, max_batch_chars=6000)
        self.assertEqual(client.calls, [])

    def test_batch_limit_is_preflighted_before_any_request(self):
        entries = tuple(
            evidence(f"batch-{index}", "Implemented " + "original " * 100) for index in range(12)
        )
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "batch budget"):
            self.analyze(client, entries=entries, max_batch_chars=6500, max_batches=1)
        self.assertEqual(client.calls, [])

    def test_rate_limit_waits_are_bounded_and_all_attempts_count(self):
        client = FakeClient(GroqRateLimitError(120), result(finding(self.current)))
        with patch("dontanello.modules.reports.adapters.groq_progress.time.sleep") as sleep:
            analysis = self.analyze(client)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60])
        self.assertEqual(analysis.metrics.api_requests, 2)
        self.assertEqual(client.calls[0]["input"], client.calls[1]["input"])

    def test_global_request_limit_cannot_be_extended_by_rate_limits(self):
        client = FakeClient(
            GroqRateLimitError(0), GroqRateLimitError(0), result(finding(self.current))
        )
        with self.assertRaises(RuntimeError):
            self.analyze(client, max_requests=2)
        self.assertEqual(len(client.calls), 2)

    def test_rate_limit_above_wait_budget_fails_closed_without_sleep(self):
        client = FakeClient(GroqRateLimitError(2225))
        with patch("dontanello.modules.reports.adapters.groq_progress.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                self.analyze(client)
        sleep.assert_not_called()

    def test_targeted_correction_discards_oversized_previous_output_but_preserves_originals(self):
        bad = finding(
            self.current, citations=[{"evidence_id": self.current.id, "quote": "invented"}]
        )
        large = json.dumps({"findings": [bad], "notices": ["private provider prose " * 800]})
        client = FakeClient(result(text=large), result(finding(self.current)))
        analysis = self.analyze(client, max_batch_chars=7000)
        self.assertEqual(len(client.calls), 2)
        corrected_context = client.calls[1]["input"]
        self.assertIn(self.current.text, input_text(client.calls[1]))
        self.assertTrue(any("targeted_correction" in item["content"] for item in corrected_context))
        self.assertNotIn("private provider prose", input_text(client.calls[1]))
        self.assertEqual(len(analysis.findings), 1)

    def test_later_batch_failure_returns_verified_results_with_explicit_coverage_gap(self):
        entries = tuple(
            evidence(f"batch-{index}", "Implemented cache " + "source detail " * 60)
            for index in range(20)
        )
        first = entries[0]
        proof = finding(first, citations=[{"evidence_id": first.id, "quote": "Implemented cache"}])
        client = FakeClient(
            result(proof),
            RuntimeError("PRIVATE_REMOTE_FAILURE"),
            result({**proof, "kind": "transformation", "project": ""}),
        )
        analysis = self.analyze(client, entries=entries, max_batch_chars=7000, max_rounds=1)
        self.assertTrue(analysis.findings)
        self.assertTrue(any("оставшиеся исходные записи" in notice for notice in analysis.notices))
        self.assertEqual(analysis.metrics.api_requests, 3)
        self.assertNotIn("PRIVATE_REMOTE_FAILURE", str(analysis))

    def test_batch_results_respect_section_and_whole_report_budgets(self):
        from dontanello.modules.reports.adapters.groq_progress import _limit_findings

        values = tuple(
            Finding(
                "progress",
                "REP-1",
                "Implemented " + "result " * 50,
                (Citation(f"id-{index}", "Implemented"),),
            )
            for index in range(20)
        )
        specification = WeeklyStrategy().specification()
        limited = _limit_findings(values, replace(specification, max_words=230))
        self.assertLessEqual(sum(len(item.text.split()) for item in limited), 130)
        self.assertLessEqual(
            len(limited),
            next(
                section.limit for section in specification.sections if "progress" in section.kinds
            ),
        )

    def test_canonical_project_must_match_exactly(self):
        client = FakeClient(result(finding(self.current, project="rep-1")))
        analysis = self.analyze(client, max_rounds=1)
        self.assertEqual(analysis.findings, ())

    def test_synthesis_cannot_cite_unsupplied_substring_of_known_original(self):
        from dontanello.modules.reports.adapters.groq_progress import _verified_response

        entry = evidence("source", "Implemented cache. PRIVATE_UNSUPPLIED_ORIGINAL")
        value = finding(
            entry,
            kind="transformation",
            project="",
            citations=[{"evidence_id": entry.id, "quote": "PRIVATE_UNSUPPLIED_ORIGINAL"}],
        )
        analysis, problems, parsed = _verified_response(
            json.dumps({"findings": [value], "notices": []}),
            WeeklyStrategy().specification(),
            {entry.id: entry},
            {entry.id},
            self.week,
            {entry.id: "Implemented cache"},
        )
        self.assertTrue(parsed)
        self.assertEqual(analysis.findings, ())
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
